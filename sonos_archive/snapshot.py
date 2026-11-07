"""Immutable snapshots, local/GitHub publishing and verification.

A snapshot contains:

* ``state.tar.gz`` - SQLite index plus readable JSONL exports + coverage report
* ``raw.tar.gz``   - only the raw capture blobs added since the previous
                     published snapshot (content addressed by SHA-256)
* ``manifest.json``- hashes, counts, lineage, coverage, scope declaration

Publishing is all-or-nothing. Locally, a fully written and verified staging
directory is renamed into place. On GitHub, a draft release is created, every
asset is uploaded, and only then is the draft published. Published releases are
never edited or deleted; draft cleanup is only ever attempted after confirming
the release is still a draft.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import __version__, util
from .config import Config
from .errors import SnapshotError
from .export import write_exports
from .net import HttpRequest, LiveTransport
from .store import Archive

SNAPSHOT_FORMAT = 1
TAG_RE = re.compile(r"^snapshot-([0-9]{6,})$")
MANIFEST_BODY_RE = re.compile(r"^manifest_sha256\s*[:=]\s*([0-9a-f]{64})\s*$", re.MULTILINE)
STATE_REQUIRED = (
    "index.sqlite",
    "topics.jsonl",
    "replies.jsonl",
    "raw_provenance.jsonl",
    "coverage.json",
)
SNAPSHOT_ASSETS = ("state.tar.gz", "raw.tar.gz", "manifest.json")


def tag_for(sequence: int) -> str:
    if sequence < 1:
        raise SnapshotError("snapshot sequence must be positive")
    return f"snapshot-{sequence:06d}"


def sequence_from_tag(tag: str) -> Optional[int]:
    match = TAG_RE.match(tag or "")
    return int(match.group(1)) if match else None


def _tar_add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def _tar_add_file(tar: tarfile.TarFile, name: str, path: Path) -> None:
    info = tarfile.TarInfo(name)
    info.size = path.stat().st_size
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mode = 0o644
    with path.open("rb") as fh:
        tar.addfile(info, fh)


@dataclass
class SnapshotRef:
    sequence: int
    tag: str
    manifest: Dict[str, Any]
    manifest_sha256: str
    location: Optional[str] = None
    release: Optional[dict] = None


def build_snapshot(
    store: Archive,
    stage_dir,
    *,
    sequence: int,
    previous: Optional[SnapshotRef],
    config: Config,
    runner: Optional[dict] = None,
    created_at: Optional[str] = None,
) -> dict:
    """Write a complete, self-consistent snapshot to ``stage_dir``.

    Returns ``{"manifest", "manifest_bytes", "manifest_sha256", "body",
    "assets"}``. No network access happens here.
    """
    stage = Path(stage_dir)
    if stage.exists() and any(stage.iterdir()):
        raise SnapshotError(f"staging directory is not empty: {stage}")
    stage.mkdir(parents=True, exist_ok=True)

    exports = write_exports(store, stage)
    store.checkpoint()
    db_out = stage / "index.sqlite"
    store.backup_to(db_out)

    state_tar = stage / "state.tar.gz"
    with tarfile.open(state_tar, "w:gz") as tar:
        for name in STATE_REQUIRED:
            _tar_add_file(tar, name, stage / name)

    previous_last = 0
    if previous is not None:
        previous_last = int(previous.manifest["raw"]["last_capture_seq"])
    new_captures = store.captures_after(previous_last)
    seen: set = set()
    raw_tar = stage / "raw.tar.gz"
    first_new = new_captures[0]["seq"] if new_captures else previous_last + 1
    last_new = new_captures[-1]["seq"] if new_captures else previous_last
    with tarfile.open(raw_tar, "w:gz") as tar:
        for row in new_captures:
            sha = row["sha256"]
            if sha in seen:
                continue
            seen.add(sha)
            blob = store.blob_path(sha)
            if not blob.is_file():
                raise SnapshotError(
                    f"raw blob missing for capture {row['seq']} ({sha}); refusing to publish"
                )
            _tar_add_file(tar, f"raw/sha256/{sha[:2]}/{sha}.gz", blob)

    coverage = json.loads((stage / "coverage.json").read_text(encoding="utf-8"))
    counts = {
        "topics": exports["topics"],
        "replies": exports["replies"],
        "captures": exports["captures"],
        "categories": sum(h["leaf_categories"] for h in coverage["hosts"]),
        "hosts": len(coverage["hosts"]),
        "is_complete_mirror": bool(exports["coverage_complete"]),
    }
    manifest = {
        "format": SNAPSHOT_FORMAT,
        "sequence": sequence,
        "tag": tag_for(sequence),
        "created_at": created_at or util.now_iso(),
        "tool": {"name": "sonos_archive", "version": __version__},
        "previous": None
        if previous is None
        else {
            "sequence": previous.sequence,
            "tag": previous.tag,
            "manifest_sha256": previous.manifest_sha256,
        },
        "state": {
            "asset": "state.tar.gz",
            "sha256": util.sha256_file(state_tar),
            "bytes": state_tar.stat().st_size,
            "contains": list(STATE_REQUIRED),
        },
        "raw": {
            "asset": "raw.tar.gz",
            "sha256": util.sha256_file(raw_tar),
            "bytes": raw_tar.stat().st_size,
            "first_capture_seq": first_new,
            "last_capture_seq": last_new,
            "row_count": len(new_captures),
            "blob_count": len(seen),
        },
        "counts": counts,
        "coverage": coverage,
        "runner": runner or {},
        "scope": {
            "hosts": [h.host for h in config.hosts],
            "anonymous": True,
            "media_bytes_downloaded": False,
            "do_not_crawl_topic_ids": {
                host: sorted(ids) for host, ids in config.do_not_crawl_topics.items()
            },
            "do_not_crawl_routes": sorted(config.forbidden_routes),
        },
        "notes": (
            "Partial incremental archive unless counts.is_complete_mirror and "
            "coverage.is_complete_mirror are both true."
        ),
    }
    validate_manifest(manifest)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (stage / "manifest.json").write_bytes(manifest_bytes)
    manifest_sha = util.sha256_bytes(manifest_bytes)
    (stage / "manifest.sha256").write_text(manifest_sha + "\n", encoding="utf-8")
    body = _release_body(manifest, manifest_sha)
    return {
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "manifest_sha256": manifest_sha,
        "body": body,
        "assets": [state_tar.name, raw_tar.name],
    }


def _release_body(manifest: dict, manifest_sha: str) -> str:
    counts = manifest["counts"]
    raw = manifest["raw"]
    prev = manifest["previous"]["tag"] if manifest["previous"] else "none"
    return (
        f"Sonos community archive snapshot {manifest['sequence']:06d}\n"
        f"\n"
        f"tag: {manifest['tag']}\n"
        f"previous: {prev}\n"
        f"manifest_sha256: {manifest_sha}\n"
        f"created_at: {manifest['created_at']}\n"
        f"topics: {counts['topics']}\n"
        f"replies: {counts['replies']}\n"
        f"captures: {counts['captures']} "
        f"(new rows: {raw['row_count']}, new blobs: {raw['blob_count']})\n"
        f"complete_mirror: {str(counts['is_complete_mirror']).lower()}\n"
        f"\n"
        f"This is an immutable, incremental snapshot. Later snapshots add data; "
        f"published snapshots are never rewritten. See coverage.json.\n"
    )


def validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise SnapshotError("manifest must be an object")
    if manifest.get("format") != SNAPSHOT_FORMAT:
        raise SnapshotError(f"unsupported manifest format: {manifest.get('format')!r}")
    sequence = manifest.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise SnapshotError("manifest sequence must be a positive integer")
    if manifest.get("tag") != tag_for(sequence):
        raise SnapshotError("manifest tag does not match sequence")
    if not isinstance(manifest.get("created_at"), str):
        raise SnapshotError("manifest created_at is missing")
    previous = manifest.get("previous")
    if previous is not None:
        if not isinstance(previous, dict) or previous.get("sequence") != sequence - 1:
            raise SnapshotError("manifest lineage is not sequential")
        if not isinstance(previous.get("manifest_sha256"), str):
            raise SnapshotError("previous manifest hash is missing")
    for key in ("state", "raw"):
        asset = manifest.get(key)
        if not isinstance(asset, dict):
            raise SnapshotError(f"manifest {key} section is missing")
        name = asset.get("asset")
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or "\\" in name
            or name in (".", "..")
        ):
            raise SnapshotError(f"manifest {key}.asset is not a safe filename")
        if not isinstance(asset.get("sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", asset["sha256"]
        ):
            raise SnapshotError(f"manifest {key}.sha256 is invalid")
        if not isinstance(asset.get("bytes"), int) or asset["bytes"] < 0:
            raise SnapshotError(f"manifest {key}.bytes is invalid")
    if manifest["state"]["asset"] != "state.tar.gz":
        raise SnapshotError("state asset must be named state.tar.gz")
    if manifest["raw"]["asset"] != "raw.tar.gz":
        raise SnapshotError("raw asset must be named raw.tar.gz")
    raw = manifest["raw"]
    for key in ("first_capture_seq", "last_capture_seq", "row_count", "blob_count"):
        if not isinstance(raw.get(key), int) or raw[key] < 0:
            raise SnapshotError(f"manifest raw.{key} is invalid")
    if raw["last_capture_seq"] < raw["first_capture_seq"] - 1:
        raise SnapshotError("manifest raw id range is invalid")
    if not isinstance(manifest.get("counts"), dict):
        raise SnapshotError("manifest counts are missing")
    if not isinstance(manifest.get("coverage"), dict):
        raise SnapshotError("manifest coverage is missing")


# -- safe state extraction --------------------------------------------------


def extract_state(state_tar, dest_dir) -> Path:
    """Extract a state archive into ``dest_dir`` (an Archive root).

    Returns the destination directory; the caller opens it with ``Archive``.
    Only the exact expected member set is extracted, with path checks.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(state_tar, "r:gz") as tar:
            names = tar.getnames()
            if set(names) != set(STATE_REQUIRED):
                raise SnapshotError(
                    f"state archive has unexpected members: {sorted(set(names) ^ set(STATE_REQUIRED))}"
                )
            for member in tar.getmembers():
                if not member.isfile() or member.name not in STATE_REQUIRED:
                    raise SnapshotError(f"unsafe state archive member: {member.name!r}")
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest.resolve()) + os.sep):
                    raise SnapshotError(
                        f"state archive member escapes destination: {member.name!r}"
                    )
                source = tar.extractfile(member)
                if source is None:
                    raise SnapshotError(f"state archive member is unreadable: {member.name!r}")
                with target.open("wb") as out:
                    shutil.copyfileobj(source, out)
    except (tarfile.TarError, OSError) as exc:
        raise SnapshotError(f"cannot extract state archive: {exc}") from exc
    if not (dest / "index.sqlite").is_file():
        raise SnapshotError("state archive did not contain index.sqlite")
    return dest


# -- verification -----------------------------------------------------------


def verify_snapshot_dir(path, *, deep: bool = True) -> dict:
    root = Path(path)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise SnapshotError(f"manifest.json is missing from {root}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    validate_manifest(manifest)
    actual_hash = util.sha256_bytes(manifest_bytes)
    hash_file = root / "manifest.sha256"
    if hash_file.is_file():
        recorded = hash_file.read_text(encoding="utf-8").strip().split()[0]
        if recorded != actual_hash:
            raise SnapshotError("manifest.sha256 does not match manifest.json")
    for section in ("state", "raw"):
        asset = manifest[section]
        asset_path = root / asset["asset"]
        if not asset_path.is_file():
            raise SnapshotError(f"missing asset {asset['asset']}")
        if asset_path.stat().st_size != asset["bytes"]:
            raise SnapshotError(f"size mismatch for {asset['asset']}")
        if util.sha256_file(asset_path) != asset["sha256"]:
            raise SnapshotError(f"sha256 mismatch for {asset['asset']}")
    if deep:
        _verify_state_contents(root / "state.tar.gz", manifest)
        _verify_raw_contents(root / "raw.tar.gz")
    return {
        "ok": True,
        "sequence": manifest["sequence"],
        "tag": manifest["tag"],
        "manifest_sha256": actual_hash,
        "topics": manifest["counts"].get("topics"),
        "replies": manifest["counts"].get("replies"),
    }


def _verify_state_contents(state_tar: Path, manifest: dict) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="sonos-verify-"))
    try:
        root = extract_state(state_tar, tmp)
        conn = sqlite3.connect(str(root / "index.sqlite"))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise SnapshotError(f"SQLite integrity check failed: {integrity}")
            counts = manifest["counts"]
            topics = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
            replies = conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0]
            captures = conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
            if (topics, replies, captures) != (
                counts.get("topics"),
                counts.get("replies"),
                counts.get("captures"),
            ):
                raise SnapshotError("manifest counts do not match the SQLite index")
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _verify_raw_contents(raw_tar: Path) -> None:
    try:
        with tarfile.open(raw_tar, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    raise SnapshotError(f"unexpected raw archive member: {member.name!r}")
                match = re.fullmatch(
                    r"raw/sha256/[0-9a-f]{2}/([0-9a-f]{64})\.gz", member.name
                )
                if not match:
                    raise SnapshotError(
                        f"raw archive member is not content-addressed: {member.name!r}"
                    )
                source = tar.extractfile(member)
                if source is None:
                    raise SnapshotError(f"raw archive member is unreadable: {member.name!r}")
                compressed = source.read()
                if util.sha256_bytes(compressed) != match.group(1):
                    raise SnapshotError(f"raw blob hash mismatch in {member.name}")
    except tarfile.TarError as exc:
        raise SnapshotError(f"cannot read raw archive: {exc}") from exc


# -- remote backends --------------------------------------------------------


class LocalRemote:
    """Filesystem backend used for tests, local smoke runs and offline use.

    Snapshots live at ``<root>/snapshots/snapshot-NNNNNN/``.
    """

    name = "local"

    def __init__(self, root):
        self.root = Path(root)
        self.snapshots = self.root / "snapshots"
        self.snapshots.mkdir(parents=True, exist_ok=True)

    def _dir(self, tag: str) -> Path:
        return self.snapshots / tag

    def latest(self) -> Optional[SnapshotRef]:
        best: Optional[SnapshotRef] = None
        for child in sorted(self.snapshots.iterdir()):
            if not child.is_dir() or child.name.startswith(".staging-"):
                continue
            seq = sequence_from_tag(child.name)
            if seq is None or not (child / "manifest.json").is_file():
                continue
            manifest_bytes = (child / "manifest.json").read_bytes()
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            validate_manifest(manifest)
            digest = util.sha256_bytes(manifest_bytes)
            if best is None or seq > best.sequence:
                best = SnapshotRef(
                    sequence=seq,
                    tag=child.name,
                    manifest=manifest,
                    manifest_sha256=digest,
                    location=str(child),
                )
        return best

    def fetch_state(self, ref: SnapshotRef, dest_tar) -> Path:
        source = self._dir(ref.tag) / ref.manifest["state"]["asset"]
        if util.sha256_file(source) != ref.manifest["state"]["sha256"]:
            raise SnapshotError("local state asset hash mismatch")
        shutil.copyfile(source, dest_tar)
        return Path(dest_tar)

    def publish(self, stage_dir, build: dict) -> SnapshotRef:
        stage = Path(stage_dir)
        manifest = build["manifest"]
        latest = self.latest()
        expected = manifest["previous"]
        if latest is None:
            if expected is not None:
                raise SnapshotError("cannot publish: previous snapshot is missing")
        elif expected is None or expected["sequence"] != latest.sequence:
            raise SnapshotError(
                "cannot publish: lineage changed since this run started (concurrent run?)"
            )
        target = self._dir(manifest["tag"])
        if target.exists():
            raise SnapshotError(f"snapshot already exists (immutable): {manifest['tag']}")
        tmp = self.snapshots / f".staging-{uuid.uuid4().hex}"
        tmp.mkdir()
        try:
            for name in build["assets"]:
                shutil.copyfile(stage / name, tmp / name)
            (tmp / "manifest.json").write_bytes(build["manifest_bytes"])
            (tmp / "manifest.sha256").write_text(
                build["manifest_sha256"] + "\n", encoding="utf-8"
            )
            (tmp / "release.json").write_text(
                json.dumps(
                    {"tag_name": manifest["tag"], "body": build["body"]},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            verify_snapshot_dir(tmp, deep=True)
            if target.exists():
                raise SnapshotError(
                    f"snapshot already exists (immutable): {manifest['tag']}"
                )
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        return SnapshotRef(
            sequence=manifest["sequence"],
            tag=manifest["tag"],
            manifest=manifest,
            manifest_sha256=build["manifest_sha256"],
            location=str(target),
        )

    def download_snapshot(self, ref: SnapshotRef, dest) -> Path:
        source = self._dir(ref.tag)
        out = Path(dest)
        out.mkdir(parents=True, exist_ok=True)
        for name in SNAPSHOT_ASSETS + ("manifest.sha256",):
            if (source / name).is_file():
                shutil.copyfile(source / name, out / name)
        return out


class GithubRemote:
    """GitHub Releases backend. Published releases are treated as immutable.

    All HTTP goes through an injected transport with the ``net.LiveTransport``
    interface (``send(HttpRequest) -> HttpResponse``) so tests can run offline.
    """

    name = "github"

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        api_base: str = "https://api.github.com",
        upload_base: str = "https://uploads.github.com",
        transport=None,
        user_agent: str = "SonosCommunityArchive",
        target_commitish: Optional[str] = None,
    ):
        if not repo or "/" not in repo:
            raise SnapshotError("GITHUB_REPOSITORY must be owner/repo")
        if not token:
            raise SnapshotError("GITHUB_TOKEN is required for the GitHub backend")
        self.repo = repo
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.upload_base = upload_base.rstrip("/")
        self.transport = transport or LiveTransport()
        self.user_agent = user_agent
        self.target_commitish = target_commitish

    # -- HTTP helpers ------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self.user_agent,
        }

    def _send(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes]):
        return self.transport.send(HttpRequest(method, url, headers, body))

    def _api(self, method: str, path: str, payload: Optional[dict] = None, *, ok=(200,)):
        url = path if path.startswith("http") else self.api_base + path
        headers = self._headers()
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        response = self._send(method, url, headers, body)
        if response.status not in ok:
            text = response.body[:500].decode("utf-8", "replace")
            raise SnapshotError(f"GitHub API {method} {url} returned {response.status}: {text}")
        if not response.body:
            return None
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotError(f"GitHub API returned invalid JSON from {url}") from exc

    def _releases(self) -> List[dict]:
        releases: List[dict] = []
        for page in range(1, 21):
            data = self._api(
                "GET",
                f"/repos/{self.repo}/releases?per_page=100&page={page}",
            )
            if not isinstance(data, list):
                raise SnapshotError("GitHub releases response was not a list")
            releases.extend(data)
            if len(data) < 100:
                break
        return releases

    def _latest_published_release(self) -> Optional[dict]:
        best = None
        best_seq = -1
        for release in self._releases():
            if release.get("draft"):
                continue
            seq = sequence_from_tag(release.get("tag_name", ""))
            if seq is not None and seq > best_seq:
                best, best_seq = release, seq
        return best

    def _release_by_tag(self, tag: str) -> Optional[dict]:
        try:
            return self._api(
                "GET",
                f"/repos/{self.repo}/releases/tags/{urllib.parse.quote(tag)}",
            )
        except SnapshotError as exc:
            if "returned 404" in str(exc):
                return None
            raise

    def _asset(self, release: dict, name: str) -> dict:
        for asset in release.get("assets", []):
            if asset.get("name") == name:
                return asset
        raise SnapshotError(f"release {release.get('tag_name')} has no asset {name}")

    def _download_asset(self, asset: dict, dest) -> Path:
        url = asset.get("browser_download_url") or asset.get("url")
        if not url:
            raise SnapshotError(f"asset {asset.get('name')!r} has no download URL")
        response = self._send(
            "GET", url, {"Accept": "*/*", "User-Agent": self.user_agent}, None
        )
        if response.status != 200:
            raise SnapshotError(f"downloading {asset.get('name')} returned {response.status}")
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(response.body)
        return out

    def latest(self) -> Optional[SnapshotRef]:
        release = self._latest_published_release()
        if release is None:
            return None
        seq = sequence_from_tag(release.get("tag_name", ""))
        if seq is None:
            return None
        asset = self._asset(release, "manifest.json")
        tmp = Path(tempfile.mkdtemp(prefix="sonos-manifest-"))
        try:
            path = self._download_asset(asset, tmp / "manifest.json")
            manifest_bytes = path.read_bytes()
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            validate_manifest(manifest)
            if manifest["sequence"] != seq:
                raise SnapshotError("manifest sequence does not match release tag")
            digest = util.sha256_bytes(manifest_bytes)
            body = release.get("body") or ""
            match = MANIFEST_BODY_RE.search(body)
            if not match:
                raise SnapshotError(
                    "release body has no manifest_sha256; refusing unverifiable snapshot"
                )
            if match.group(1) != digest:
                raise SnapshotError("manifest hash does not match the release body")
            return SnapshotRef(
                sequence=seq,
                tag=release["tag_name"],
                manifest=manifest,
                manifest_sha256=digest,
                release=release,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def fetch_state(self, ref: SnapshotRef, dest_tar) -> Path:
        release = ref.release or self._release_by_tag(ref.tag)
        if release is None:
            raise SnapshotError(f"release {ref.tag} not found")
        asset = self._asset(release, ref.manifest["state"]["asset"])
        path = self._download_asset(asset, dest_tar)
        if util.sha256_file(path) != ref.manifest["state"]["sha256"]:
            raise SnapshotError("state asset hash mismatch")
        if path.stat().st_size != ref.manifest["state"]["bytes"]:
            raise SnapshotError("state asset size mismatch")
        return path

    def publish(self, stage_dir, build: dict) -> SnapshotRef:
        stage = Path(stage_dir)
        manifest = build["manifest"]
        tag = manifest["tag"]
        latest = self._latest_published_release()
        expected = manifest["previous"]
        if latest is None:
            if expected is not None:
                raise SnapshotError("cannot publish: previous snapshot is absent on GitHub")
        else:
            latest_seq = sequence_from_tag(latest.get("tag_name", ""))
            if expected is None or expected["sequence"] != latest_seq:
                raise SnapshotError(
                    "cannot publish: lineage changed since this run started (concurrent run?)"
                )
        existing = self._release_by_tag(tag)
        if existing is not None and not existing.get("draft"):
            raise SnapshotError(f"snapshot tag already published (immutable): {tag}")
        self._delete_drafts_for_tag(tag)

        created = self._api(
            "POST",
            f"/repos/{self.repo}/releases",
            {
                "tag_name": tag,
                "name": f"Sonos community archive {manifest['sequence']:06d}",
                "body": build["body"],
                "draft": True,
                "prerelease": False,
                **({"target_commitish": self.target_commitish} if self.target_commitish else {}),
            },
            ok=(201,),
        )
        release_id = created.get("id")
        if not release_id:
            raise SnapshotError("GitHub did not return a release id")
        published = False
        try:
            for name in list(build["assets"]) + ["manifest.json"]:
                path = stage / name
                data = path.read_bytes()
                digest = util.sha256_bytes(data)
                if name == "manifest.json" and digest != build["manifest_sha256"]:
                    raise SnapshotError("manifest changed while publishing")
                url = (
                    f"{self.upload_base}/repos/{self.repo}/releases/{release_id}/assets"
                    f"?name={urllib.parse.quote(name)}"
                )
                response = self._send(
                    "POST",
                    url,
                    {**self._headers(), "Content-Type": "application/octet-stream"},
                    data,
                )
                if response.status != 201:
                    raise SnapshotError(
                        f"uploading {name} returned {response.status}: "
                        f"{response.body[:300].decode('utf-8', 'replace')}"
                    )
                try:
                    uploaded = json.loads(response.body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SnapshotError(f"upload of {name} returned invalid JSON") from exc
                if uploaded.get("name") not in (None, name):
                    raise SnapshotError(f"uploaded asset name mismatch for {name}")
                if uploaded.get("size") not in (None, len(data)):
                    raise SnapshotError(f"uploaded asset size mismatch for {name}")
                remote_digest = uploaded.get("digest")
                if remote_digest and remote_digest != f"sha256:{digest}":
                    raise SnapshotError(f"uploaded asset digest mismatch for {name}")
            patched = None
            try:
                patched = self._api(
                    "PATCH",
                    f"/repos/{self.repo}/releases/{release_id}",
                    {"draft": False},
                )
                if not patched or patched.get("draft"):
                    raise SnapshotError("GitHub did not publish the draft release")
                published = True
            except Exception:
                # The PATCH may have succeeded even if the response was lost.
                # Never delete unless the release is still a draft.
                current = self._release_by_tag(tag)
                if current is not None and not current.get("draft"):
                    published = True
                else:
                    raise
        except Exception:
            if not published:
                self._delete_draft(release_id)
            raise
        return SnapshotRef(
            sequence=manifest["sequence"],
            tag=tag,
            manifest=manifest,
            manifest_sha256=build["manifest_sha256"],
            release=patched or created,
        )

    def _delete_drafts_for_tag(self, tag: str) -> None:
        for release in self._releases():
            if release.get("draft") and release.get("tag_name") == tag:
                self._delete_draft(release.get("id"))

    def _delete_draft(self, release_id) -> None:
        """Delete a release only after confirming it is still a draft.

        Deliberately paranoid: a lost PATCH response or an ambiguous failure
        must never result in deleting an already-published snapshot.
        """
        if not release_id:
            return
        try:
            release = self._api(
                "GET", f"/repos/{self.repo}/releases/{release_id}", ok=(200, 404)
            )
            if not release or not release.get("draft"):
                return
            self._api(
                "DELETE",
                f"/repos/{self.repo}/releases/{release_id}",
                ok=(204, 200, 404),
            )
        except Exception:
            # Best effort only. A leftover draft is harmless; a deleted
            # published release would not be.
            pass

    def download_snapshot(self, ref: SnapshotRef, dest) -> Path:
        release = ref.release or self._release_by_tag(ref.tag)
        if release is None:
            raise SnapshotError(f"release {ref.tag} not found")
        out = Path(dest)
        out.mkdir(parents=True, exist_ok=True)
        for name in (
            ref.manifest["state"]["asset"],
            ref.manifest["raw"]["asset"],
            "manifest.json",
        ):
            asset = self._asset(release, name)
            self._download_asset(asset, out / name)
        return out


def remote_from_env(
    *,
    config: Config,
    archive_dir="archive",
    force_local: bool = False,
    transport=None,
    api_base: str = "https://api.github.com",
    upload_base: str = "https://uploads.github.com",
):
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if force_local or not (token and repo):
        return LocalRemote(archive_dir)
    return GithubRemote(
        repo,
        token,
        api_base=api_base,
        upload_base=upload_base,
        transport=transport,
        user_agent=config.user_agent,
        target_commitish=os.environ.get("GITHUB_SHA") or None,
    )
