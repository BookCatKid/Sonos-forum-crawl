"""Immutable snapshot publishing.

A snapshot is a directory (local backend) or a GitHub release tagged
``snapshot-NNNNNN`` (github backend) containing:

  state.tar.gz    index.sqlite + topics.jsonl + replies.jsonl + coverage.json
  raw.tar.gz      raw capture blobs added since the previous snapshot
  manifest.json   format, sequence, lineage, asset hashes, counts, coverage
  manifest.sha256 sha256 of manifest.json

Publishing is all-or-nothing: locally, a complete staging directory is
renamed into place; on GitHub a draft release gets every asset uploaded and
digest-checked before the draft flag is cleared. Published snapshots are
never modified or deleted. Restoring downloads the newest published snapshot
and rebuilds the working archive root.
"""

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import __version__
from .errors import ArchiveError
from .forum import now_iso
from .store import SCHEMA_VERSION, Store, canon

TAG_RE = re.compile(r"^snapshot-(\d{6,})$")
ASSETS = ("state.tar.gz", "raw.tar.gz", "manifest.json", "manifest.sha256")
STATE_FILES = ("index.sqlite", "topics.jsonl", "replies.jsonl", "events.jsonl",
               "coverage.json")


def tag_for(seq):
    return f"snapshot-{seq:06d}"


def seq_of(tag):
    m = TAG_RE.match(tag or "")
    return int(m.group(1)) if m else None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tar_bytes(tf, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    tf.addfile(info, io.BytesIO(data))


def write_exports(store, out):
    """Human-readable exports derived from the index."""
    out = Path(out)
    with open(out / "topics.jsonl", "w", encoding="utf-8") as f:
        for t in store.iter_topics():
            f.write(canon({
                "host": t["host"], "public_id": t["public_id"],
                "internal_id": t["internal_id"], "category_id": t["category_id"],
                "title": t["title"], "content_type": t["content_type"],
                "url": t["url"], "author": t["author"],
                "published_at": t["published_at"],
                "last_activity_at": t["last_activity_at"],
                "reply_count": t["reply_count"],
                "replies_state": t["replies_state"],
                "topic_body_state": t["topic_body_state"],
                "replies_seen": t["replies_seen"],
                "replies_missing_nested": t["replies_missing"],
                "content_html": (json.loads(t["raw_json"]).get("content")
                                 if t["raw_json"] else None),
                "capture_seq": t["capture_seq"],
            }) + "\n")
    with open(out / "replies.jsonl", "w", encoding="utf-8") as f:
        for r in store.iter_replies():
            raw = json.loads(r["raw_json"]) if r["raw_json"] else {}
            f.write(canon({
                "host": r["host"], "topic_public_id": r["topic_public_id"],
                "reply_id": r["reply_id"], "public_reply_id": r["public_reply_id"],
                "parent_reply_id": r["parent_reply_id"], "author": r["author"],
                "replied_at": r["replied_at"], "depth": r["depth"],
                "content_html": raw.get("content"),
                "capture_seq": r["capture_seq"],
            }) + "\n")
    with open(out / "events.jsonl", "w", encoding="utf-8") as f:
        for e in store.db.execute("SELECT * FROM events ORDER BY host, event_id"):
            f.write(canon({
                "host": e["host"], "event_id": e["event_id"], "title": e["title"],
                "destination_url": e["destination_url"], "start_at": e["start_at"],
                "end_at": e["end_at"], "created_at": e["created_at"],
                "capture_seq": e["capture_seq"],
            }) + "\n")


def coverage(store, hosts):
    """Honest coverage report. Never claims completeness it cannot prove."""
    report = {
        "generated_at": now_iso(), "hosts": [],
        "is_complete_mirror": False,
        "is_complete_public_text_archive": False,
        # Image/attachment URLs are indexed in content HTML; their binary
        # assets are not yet embedded in any published snapshot.
        "media_assets_archived": False,
    }
    complete = bool(hosts)
    for host in hosts:
        h = store.host_row(host)
        cats = store.categories(host)
        tc = store.topic_counts(host)
        pending = store.pending_count(host)
        captures = store.db.execute(
            "SELECT COUNT(*) c FROM captures WHERE host=?", (host,)).fetchone()["c"]
        leaf = [c for c in cats if not c["is_container"]]
        done = [c for c in leaf if c["done"]]
        denials = store.db.execute(
            "SELECT COUNT(*) c FROM denials WHERE host=?", (host,)).fetchone()["c"]
        denied_tasks = store.db.execute(
            "SELECT COUNT(*) c FROM tasks WHERE host=? AND state='denied'",
            (host,)).fetchone()["c"]
        dead_tasks = store.db.execute(
            "SELECT COUNT(*) c FROM tasks WHERE host=? AND state='dead'",
            (host,)).fetchone()["c"]
        missing_bodies = store.db.execute(
            "SELECT COUNT(*) FROM topics WHERE host=? AND raw_json IS NULL "
            "AND topic_body_state='pending'", (host,)).fetchone()[0]
        unavailable_bodies = store.db.execute(
            "SELECT COUNT(*) FROM topics WHERE host=? AND raw_json IS NULL "
            "AND topic_body_state IN ('gone','forbidden')",
            (host,)).fetchone()[0]
        forbidden_bodies = store.db.execute(
            "SELECT COUNT(*) FROM topics WHERE host=? AND raw_json IS NULL "
            "AND topic_body_state='forbidden'", (host,)).fetchone()[0]
        host_complete = (bool(leaf) and len(done) == len(leaf)
                         and not missing_bodies
                         and not pending and not denied_tasks and not dead_tasks
                         and not tc["pending"] and not tc["missing"])
        if not host_complete:
            complete = False
        report["hosts"].append({
            "host": host,
            "last_request_at": h["last_request_at"],
            "captures": captures,
            "denials": denials,
            "leaf_categories": len(leaf),
            "leaf_categories_done": len(done),
            "topics": tc["total"],
            "topics_replies_done": tc["done"],
            "topics_replies_pending": tc["pending"],
            "topics_replies_gone": tc["gone"],
            "missing_nested_replies": tc["missing"],
            "missing_topic_bodies": missing_bodies,
            "unavailable_topic_bodies": unavailable_bodies,
            "forbidden_topic_bodies": forbidden_bodies,
            "pending_tasks": pending,
            "denied_tasks": denied_tasks,
            "dead_tasks": dead_tasks,
            "complete": host_complete,
        })
    report["is_complete_public_text_archive"] = complete
    report["is_complete_mirror"] = complete and report["media_assets_archived"]
    report["note"] = ("A host's 'complete' refers to public text content: "
                      "all discovered leaf categories listed, "
                      "zero pending/denied/dead tasks, zero known-missing "
                      "nested replies, and zero unresolved opening posts. "
                      "Unavailable HTTP 403/404 topics are reported separately. "
                      "The full mirror flag remains false until linked media "
                      "assets are independently preserved. No check can prove "
                      "that undiscovered content is absent.")
    return report


def build_snapshot(store, hosts, stage_dir, previous_tag, since_seq, run_id):
    """Stage a complete snapshot in ``stage_dir``. Returns the manifest dict."""
    stage = Path(stage_dir)
    stage.mkdir(parents=True, exist_ok=True)

    # Consistent copy of the live WAL database.
    tmp_db = stage / "index.sqlite.tmp"
    dest = sqlite3.connect(tmp_db)
    store.db.backup(dest)
    dest.close()
    (stage / "index.sqlite.tmp").rename(stage / "index.sqlite")

    # All exports, metadata and the raw-blob cursor must refer to the exact
    # same SQLite backup. The live crawler may keep writing during staging.
    frozen = Store(stage)
    frozen.raw_root = store.raw_root
    try:
        write_exports(frozen, stage)
        cov = coverage(frozen, hosts)
        counts = frozen.counts()
        capture_cursor = frozen.last_capture_seq()
        if since_seq > capture_cursor:
            raise ArchiveError("snapshot capture cursor moved backwards")
        blobs = frozen.captures_after(since_seq)
    finally:
        frozen.close()
    (stage / "coverage.json").write_text(json.dumps(cov, indent=2), encoding="utf-8")

    with tarfile.open(stage / "state.tar.gz", "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for name in STATE_FILES:
            tf.add(stage / name, arcname=name)
        for name in STATE_FILES:
            (stage / name).unlink()

    with tarfile.open(stage / "raw.tar.gz", "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for row in blobs:
            path = store.blob_path(row["sha"])
            if not path.is_file():
                raise ArchiveError(f"missing new raw capture blob: {row['sha']}")
            tf.add(path, arcname=f"raw/sha256/{row['sha'][:2]}/{row['sha']}.gz")

    manifest = {
        "format": 2,
        "sequence": None,
        "tag": None,
        "previous": previous_tag,
        "created_at": now_iso(),
        "version": __version__,
        "run_id": run_id,
        "counts": counts,
        "capture_cursor": capture_cursor,
        "new_capture_blobs": len(blobs),
        "coverage": cov,
        "assets": {},
    }
    return stage, manifest


def finalize_manifest(stage, manifest, seq):
    manifest["sequence"] = seq
    manifest["tag"] = tag_for(seq)
    for name in ("state.tar.gz", "raw.tar.gz"):
        manifest["assets"][name] = {
            "sha256": sha256_file(stage / name),
            "size": (stage / name).stat().st_size,
        }
    raw = json.dumps(manifest, indent=2, sort_keys=True).encode()
    (stage / "manifest.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (stage / "manifest.sha256").write_text(digest + "\n", encoding="ascii")
    return manifest


SNAPSHOT_FORMAT = 2


def verified_manifest(stage):
    """Read a manifest after authenticating its separately stored checksum."""
    stage = Path(stage)
    raw = (stage / "manifest.json").read_bytes()
    digest = (stage / "manifest.sha256").read_text().strip()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ArchiveError("manifest.sha256 does not match manifest.json")
    manifest = json.loads(raw)
    if manifest.get("format") != SNAPSHOT_FORMAT or "assets" not in manifest:
        raise ArchiveError("incompatible snapshot format")
    return manifest


def verify_asset(path, metadata):
    path = Path(path)
    if (not path.is_file() or path.stat().st_size != metadata["size"]
            or sha256_file(path) != metadata["sha256"]):
        raise ArchiveError(f"asset {path.name} missing or hash/size mismatch")


def verify_dir(stage):
    """Check all assets in a complete snapshot directory."""
    stage = Path(stage)
    manifest = verified_manifest(stage)
    for name, meta in manifest["assets"].items():
        verify_asset(stage / name, meta)
    return manifest


def _safe_extract(tf, root):
    """Minimal traversal-safe tar extraction (works on Python 3.11 too)."""
    root = Path(root).resolve()
    for member in tf.getmembers():
        if not member.isfile():
            continue
        dest = (root / member.name).resolve()
        if not str(dest).startswith(str(root) + os.sep):
            raise ArchiveError(f"tar member escapes root: {member.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(tf.extractfile(member).read())


def restore_raw_into(store_root, raw_tar):
    """Hydrate one verified incremental raw pack."""
    with tarfile.open(raw_tar, "r:gz") as tf:
        for member in tf.getmembers():
            if member.isfile() and not re.fullmatch(
                    r"raw/sha256/[0-9a-f]{2}/[0-9a-f]{64}\.gz", member.name):
                raise ArchiveError(f"unexpected raw member: {member.name}")
        _safe_extract(tf, store_root)


def verify_raw_coverage(store_root):
    """Ensure every captured SHA exists after complete chain hydration."""
    db = sqlite3.connect(Path(store_root) / "index.sqlite")
    try:
        missing = [
            sha for (sha,) in db.execute("SELECT DISTINCT sha FROM captures")
            if not (Path(store_root) / "raw" / "sha256" / sha[:2] / (sha + ".gz")).is_file()
        ]
    finally:
        db.close()
    if missing:
        raise ArchiveError(
            f"{len(missing)} historical raw capture blobs missing from release lineage "
            f"(first SHA {missing[0]}); existing snapshots may contain data gaps")


def restore_into(store_root, state_tar, raw_tars=()):
    """Extract the latest index and supplied incremental raw packs."""
    root = Path(store_root)
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(state_tar, "r:gz") as tf:
        _safe_extract(tf, root)
    index = root / "index.sqlite"
    if index.exists():
        try:
            db = sqlite3.connect(index)
            try:
                row = db.execute(
                    "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            finally:
                db.close()
            if not row or int(row[0]) != SCHEMA_VERSION:
                raise ArchiveError("snapshot schema version mismatch")
        except sqlite3.Error as exc:
            raise ArchiveError("snapshot index is not a valid database") from exc
    for raw in raw_tars or []:
        restore_raw_into(root, raw)


# ---------------------------------------------------------------- remotes

class LocalRemote:
    def __init__(self, root):
        self.root = Path(root)
        self.snaps = self.root / "snapshots"

    def latest(self):
        if not self.snaps.exists():
            return None
        best = None
        for d in self.snaps.iterdir():
            s = seq_of(d.name)
            if s and (best is None or s > best) and (d / "manifest.json").exists():
                best = s
        return self.snaps / tag_for(best) if best else None

    def restore(self, work_root, *, include_historical_raw=True):
        latest = self.latest()
        if latest is None:
            return None, -1
        manifest = verify_dir(latest)
        restore_into(work_root, latest / "state.tar.gz", [latest / "raw.tar.gz"])
        if include_historical_raw:
            seen = {manifest["tag"]}
            previous = manifest.get("previous")
            while previous:
                if previous in seen or seq_of(previous) is None:
                    raise ArchiveError(f"invalid/cyclic snapshot lineage: {previous}")
                seen.add(previous)
                ancestor = self.snaps / previous
                old = verified_manifest(ancestor)
                if old.get("tag") != previous:
                    raise ArchiveError(f"snapshot lineage mismatch: {previous}")
                verify_asset(ancestor / "raw.tar.gz", old["assets"]["raw.tar.gz"])
                restore_raw_into(work_root, ancestor / "raw.tar.gz")
                previous = old.get("previous")
            verify_raw_coverage(work_root)
        return manifest, manifest.get("capture_cursor", manifest["counts"]["captures"])

    def publish(self, stage, manifest):
        latest = self.latest()
        seq = (seq_of(latest.name) or 0) + 1 if latest else 1
        manifest = finalize_manifest(stage, manifest, seq)
        verify_dir(stage)
        target = self.snaps / manifest["tag"]
        if target.exists():
            raise ArchiveError(f"snapshot {manifest['tag']} already exists; refusing to overwrite")
        self.snaps.mkdir(parents=True, exist_ok=True)
        os.rename(stage, target)
        return manifest


class GithubRemote:
    """Snapshot releases via the GitHub REST API. Draft -> upload -> publish."""

    def __init__(self, repo, token, api_base="https://api.github.com",
                 upload_base="https://uploads.github.com", user_agent="sonos-archive",
                 target_commitish=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
            raise ArchiveError("GITHUB_REPOSITORY must be owner/repo")
        if not token:
            raise ArchiveError("GITHUB_TOKEN is required for the GitHub backend")
        self.repo, self.token = repo, token
        self.api_base, self.upload_base = api_base, upload_base
        self.user_agent = user_agent
        self.target_commitish = target_commitish

    def _req(self, method, url, body=None, raw=None, extra_headers=None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        headers.update(extra_headers or {})
        data = json.dumps(body).encode() if body is not None else raw
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if payload else {}), dict(r.headers)
        except urllib.error.HTTPError as e:
            raise ArchiveError(f"GitHub API {method} {url} -> HTTP {e.code}: "
                               f"{e.read()[:300]!r}")

    def _releases(self):
        out = []
        for page in (1, 2, 3, 4, 5):
            _s, rels, _h = self._req(
                "GET", f"{self.api_base}/repos/{self.repo}/releases?per_page=100&page={page}")
            out.extend(rels)
            if len(rels) < 100:
                break
        return out

    def latest(self):
        best = None
        for rel in self._releases():
            s = seq_of(rel.get("tag_name"))
            if s and not rel.get("draft") and (best is None or s > best[0]):
                best = (s, rel)
        return best

    def _download_asset(self, rel, name, dest):
        for asset in rel.get("assets", []):
            if asset.get("name") == name:
                req = urllib.request.Request(
                    asset["url"],
                    headers={"Authorization": f"Bearer {self.token}",
                             "Accept": "application/octet-stream",
                             "User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=120) as r:
                    Path(dest).write_bytes(r.read())
                return dest
        raise ArchiveError(f"release {rel.get('tag_name')} missing asset {name}")

    def restore(self, work_root, *, include_historical_raw=True):
        latest = self.latest()
        if latest is None:
            return None, -1
        _seq, rel = latest
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td)
            state = self._download_asset(rel, "state.tar.gz", stage / "state.tar.gz")
            raw = self._download_asset(rel, "raw.tar.gz", stage / "raw.tar.gz")
            self._download_asset(rel, "manifest.json", stage / "manifest.json")
            self._download_asset(rel, "manifest.sha256", stage / "manifest.sha256")
            manifest = verified_manifest(stage)
            if manifest.get("tag") != rel.get("tag_name"):
                raise ArchiveError("latest release/manifest tag mismatch")
            verify_asset(state, manifest["assets"]["state.tar.gz"])
            verify_asset(raw, manifest["assets"]["raw.tar.gz"])
            restore_into(work_root, state, [raw])
            if include_historical_raw:
                seen = {manifest["tag"]}
                previous = manifest.get("previous")
                while previous:
                    if previous in seen or seq_of(previous) is None:
                        raise ArchiveError(f"invalid/cyclic snapshot lineage: {previous}")
                    seen.add(previous)
                    _status, ancestor_rel, _headers = self._req(
                        "GET", f"{self.api_base}/repos/{self.repo}/releases/tags/{previous}")
                    if ancestor_rel.get("draft"):
                        raise ArchiveError(f"snapshot {previous} is not published")
                    self._download_asset(ancestor_rel, "manifest.json", stage / "manifest.json")
                    self._download_asset(ancestor_rel, "manifest.sha256", stage / "manifest.sha256")
                    old = verified_manifest(stage)
                    if old.get("tag") != previous:
                        raise ArchiveError(f"snapshot lineage mismatch: {previous}")
                    self._download_asset(ancestor_rel, "raw.tar.gz", stage / "raw.tar.gz")
                    verify_asset(stage / "raw.tar.gz", old["assets"]["raw.tar.gz"])
                    restore_raw_into(work_root, stage / "raw.tar.gz")
                    previous = old.get("previous")
                verify_raw_coverage(work_root)
        return manifest, manifest.get("capture_cursor", manifest["counts"]["captures"])

    def publish(self, stage, manifest):
        seq = (self.latest()[0] + 1) if self.latest() else 1
        manifest = finalize_manifest(stage, manifest, seq)
        verify_dir(stage)
        tag = manifest["tag"]

        # A leftover draft for the same tag is safe to remove; a published one
        # is never touched.
        for rel in self._releases():
            if rel.get("tag_name") == tag:
                if not rel.get("draft"):
                    raise ArchiveError(f"{tag} already published; refusing to overwrite")
                self._req("DELETE", f"{self.api_base}/repos/{self.repo}/releases/{rel['id']}")

        manifest_sha = (stage / "manifest.sha256").read_text().strip()
        body = {"tag_name": tag, "name": f"Sonos community archive {tag[9:]}",
                "draft": True, "prerelease": False,
                "body": f"{tag}\nprevious: {manifest['previous']}\n"
                        f"manifest_sha256: {manifest_sha}\n"
                        f"counts: {json.dumps(manifest['counts'], sort_keys=True)}\n"
                        f"complete_mirror: {manifest['coverage']['is_complete_mirror']}"}
        if self.target_commitish:
            body["target_commitish"] = self.target_commitish
        _s, rel, _h = self._req("POST", f"{self.api_base}/repos/{self.repo}/releases", body)
        rid = rel["id"]
        upload_url = f"{self.upload_base}/repos/{self.repo}/releases/{rid}/assets"
        try:
            for name in ASSETS:
                data = (stage / name).read_bytes()
                _s, asset, _h = self._req(
                    "POST", f"{upload_url}?name={name}", raw=data,
                    extra_headers={"Content-Type": "application/octet-stream"})
                if asset.get("size") != len(data):
                    raise ArchiveError(f"asset {name} uploaded with wrong size")
            _s, done, _h = self._req(
                "PATCH", f"{self.api_base}/repos/{self.repo}/releases/{rid}",
                {"draft": False})
            if done.get("draft"):
                raise ArchiveError("GitHub did not clear the draft flag")
        except Exception:
            # Best-effort cleanup of the half-published draft, only while it
            # is still confirmed to be a draft.
            try:
                _s, current, _h = self._req(
                    "GET", f"{self.api_base}/repos/{self.repo}/releases/{rid}")
                if current.get("draft"):
                    self._req("DELETE", f"{self.api_base}/repos/{self.repo}/releases/{rid}")
            except ArchiveError:
                pass
            raise
        return manifest


def remote_from_env(config, archive_dir, force_local=False):
    """Pick the GitHub backend when credentials exist (as on Actions), else local."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if force_local or not (token and repo):
        return LocalRemote(archive_dir)
    return GithubRemote(repo, token, user_agent=config["user_agent"],
                        target_commitish=os.environ.get("GITHUB_SHA") or None)


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    cfg.setdefault("timeout_seconds", 30.0)
    cfg.setdefault("refresh_listings_after_seconds", 21600)
    return cfg
