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
    report = {"generated_at": now_iso(), "hosts": [], "is_complete_mirror": False}
    complete = True
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
        if pending or denied_tasks or tc["pending"] or tc["missing"] \
                or (leaf and len(done) < len(leaf)):
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
            "pending_tasks": pending,
            "denied_tasks": denied_tasks,
            "dead_tasks": dead_tasks,
            "complete": bool(leaf) and len(done) == len(leaf) and not pending
                        and not tc["pending"] and not tc["missing"],
        })
    report["is_complete_mirror"] = complete
    report["note"] = ("'complete' requires every leaf category fully listed, "
                      "zero pending/denied tasks and zero known-missing "
                      "nested replies. Anything less is partial.")
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

    write_exports(store, stage)
    cov = coverage(store, hosts)
    (stage / "coverage.json").write_text(json.dumps(cov, indent=2), encoding="utf-8")

    with tarfile.open(stage / "state.tar.gz", "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for name in STATE_FILES:
            tf.add(stage / name, arcname=name)
        for name in STATE_FILES:
            (stage / name).unlink()

    blobs = store.captures_after(since_seq)
    with tarfile.open(stage / "raw.tar.gz", "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for row in blobs:
            path = store.blob_path(row["sha"])
            if path.exists():
                tf.add(path, arcname=f"raw/sha256/{row['sha'][:2]}/{row['sha']}.gz")

    manifest = {
        "format": 2,
        "sequence": None,
        "tag": None,
        "previous": previous_tag,
        "created_at": now_iso(),
        "version": __version__,
        "run_id": run_id,
        "counts": store.counts(),
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


def verify_dir(stage):
    """Check a snapshot directory against its manifest. Returns manifest."""
    stage = Path(stage)
    manifest = json.loads((stage / "manifest.json").read_text())
    if manifest.get("format") != SNAPSHOT_FORMAT or "assets" not in manifest:
        raise ArchiveError("incompatible snapshot format")
    digest = (stage / "manifest.sha256").read_text().strip()
    if digest != hashlib.sha256(json.dumps(manifest, indent=2, sort_keys=True).encode()).hexdigest():
        raise ArchiveError("manifest.sha256 does not match manifest.json")
    for name, meta in manifest["assets"].items():
        p = stage / name
        if not p.exists():
            raise ArchiveError(f"missing asset {name}")
        if p.stat().st_size != meta["size"] or sha256_file(p) != meta["sha256"]:
            raise ArchiveError(f"asset {name} hash/size mismatch")
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


def restore_into(store_root, state_tar, raw_tars=()):
    """Extract index.sqlite (and any raw blobs) into a fresh archive root."""
    root = Path(store_root)
    with tarfile.open(state_tar) as tf:
        _safe_extract(tf, root)
    index = root / "index.sqlite"
    if index.exists():
        try:
            db = sqlite3.connect(index)
            row = db.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            db.close()
            if not row or int(row[0]) != SCHEMA_VERSION:
                raise ArchiveError("snapshot schema version mismatch")
        except sqlite3.Error:
            raise ArchiveError("snapshot index is not a valid database")
    for raw in raw_tars or []:
        if Path(raw).exists() and Path(raw).stat().st_size > 0:
            with tarfile.open(raw) as tf:
                _safe_extract(tf, root)


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

    def restore(self, work_root):
        latest = self.latest()
        if latest is None:
            return None, -1
        verify_dir(latest)
        manifest = json.loads((latest / "manifest.json").read_text())
        restore_into(work_root, latest / "state.tar.gz", [latest / "raw.tar.gz"])
        return manifest, manifest["counts"]["captures"]

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

    def restore(self, work_root):
        latest = self.latest()
        if latest is None:
            return None, -1
        _seq, rel = latest
        with tempfile.TemporaryDirectory() as td:
            state = self._download_asset(rel, "state.tar.gz", Path(td) / "state.tar.gz")
            raw = self._download_asset(rel, "raw.tar.gz", Path(td) / "raw.tar.gz")
            manifest_path = self._download_asset(rel, "manifest.json", Path(td) / "manifest.json")
            manifest = json.loads(Path(manifest_path).read_text())
            if manifest.get("format") != SNAPSHOT_FORMAT or "assets" not in manifest:
                raise ArchiveError("incompatible snapshot format")
            if manifest["assets"]["state.tar.gz"]["sha256"] != sha256_file(state):
                raise ArchiveError("downloaded state.tar.gz hash mismatch")
            if manifest["assets"]["raw.tar.gz"]["sha256"] != sha256_file(raw):
                raise ArchiveError("downloaded raw.tar.gz hash mismatch")
            restore_into(work_root, state, [raw])
        return manifest, manifest["counts"]["captures"]

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
