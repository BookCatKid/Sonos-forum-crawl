"""Snapshot build, publish, immutability, recovery and verification."""

import gzip
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from sonos_archive import util
from sonos_archive.config import load_config
from sonos_archive.errors import SnapshotError
from sonos_archive.net import HttpResponse
from sonos_archive.snapshot import (
    GithubRemote,
    LocalRemote,
    build_snapshot,
    extract_state,
    tag_for,
    verify_snapshot_dir,
)
from sonos_archive.store import Archive

from .helpers import EN_HOST, write_config


def populated_store(root: Path, config) -> Archive:
    store = Archive(root)
    store.init_hosts(config.hosts)
    blob = store.put_raw(b'{"hello": "world"}')
    store.insert_capture(
        blob=blob,
        host=EN_HOST,
        method="POST",
        url="https://en.community.sonos.com/categoryV2/getAccessibleCategoriesTree",
        request_body='{"type":[0]}',
        status=200,
        headers={"content-type": "application/json"},
        fetched_at=util.now_iso(),
        kind="categories",
        context={"kind": "categories"},
    )
    return store


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = load_config(write_config(self.root))
        self.store = populated_store(self.root / "state", self.config)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def build(self, sequence, previous):
        stage = self.root / f"stage-{sequence}"
        return build_snapshot(
            self.store,
            stage,
            sequence=sequence,
            previous=previous,
            config=self.config,
        ), stage


class LocalPublishTest(SnapshotCase):
    def test_build_publish_verify(self):
        remote = LocalRemote(self.root / "archive")
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        self.assertEqual(ref.tag, "snapshot-000001")
        result = verify_snapshot_dir(remote._dir(ref.tag), deep=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sequence"], 1)
        self.assertEqual(result["manifest_sha256"], ref.manifest_sha256)

        latest = remote.latest()
        self.assertEqual(latest.sequence, 1)

        # State round-trip: fetch + extract + reopen.
        dest = self.root / "restored.tar.gz"
        remote.fetch_state(latest, dest)
        restored_root = extract_state(dest, self.root / "restored")
        restored = Archive(restored_root)
        self.assertEqual(restored.counts()["captures"], 1)
        restored.close()

    def test_immutable_tag(self):
        remote = LocalRemote(self.root / "archive")
        build, stage = self.build(1, None)
        remote.publish(stage, build)
        with self.assertRaises(SnapshotError):
            remote.publish(stage, build)

    def test_lineage_enforced(self):
        remote = LocalRemote(self.root / "archive")
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        # A new manifest claiming sequence 2 but with previous=None is rejected.
        build2, stage2 = self.build(2, ref)
        build2["manifest"]["previous"] = None
        with self.assertRaises(SnapshotError):
            remote.publish(stage2, build2)

    def test_incremental_raw_delta(self):
        remote = LocalRemote(self.root / "archive")
        build1, stage1 = self.build(1, None)
        ref1 = remote.publish(stage1, build1)
        self.assertEqual(ref1.manifest["raw"]["row_count"], 1)

        blob = self.store.put_raw(b"second response")
        self.store.insert_capture(
            blob=blob,
            host=EN_HOST,
            method="GET",
            url="https://en.community.sonos.com/robots.txt",
            request_body=None,
            status=200,
            headers={},
            fetched_at=util.now_iso(),
            kind="robots",
            context={},
        )
        build2, stage2 = self.build(2, ref1)
        ref2 = remote.publish(stage2, build2)
        self.assertEqual(ref2.manifest["previous"]["sequence"], 1)
        self.assertEqual(ref2.manifest["raw"]["first_capture_seq"], 2)
        self.assertEqual(ref2.manifest["raw"]["row_count"], 1)
        self.assertEqual(ref2.manifest["raw"]["blob_count"], 1)
        result = verify_snapshot_dir(remote._dir(ref2.tag), deep=True)
        self.assertTrue(result["ok"])

    def test_interrupted_publish_recovery(self):
        remote = LocalRemote(self.root / "archive")
        # A crashed earlier attempt left a partial staging directory behind.
        leftover = remote.snapshots / ".staging-deadbeef"
        leftover.mkdir()
        (leftover / "manifest.json").write_text("{")
        self.assertIsNone(remote.latest())
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        self.assertEqual(ref.sequence, 1)
        # An in-place snapshot directory missing its manifest is ignored too.
        bad = remote.snapshots / "snapshot-000099"
        bad.mkdir()
        (bad / "state.tar.gz").write_bytes(b"junk")
        self.assertEqual(remote.latest().sequence, 1)

    def test_verify_detects_tampering(self):
        remote = LocalRemote(self.root / "archive")
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        snap = remote._dir(ref.tag)
        data = (snap / "state.tar.gz").read_bytes()
        (snap / "state.tar.gz").write_bytes(data[:-10] + b"0123456789")
        with self.assertRaises(SnapshotError):
            verify_snapshot_dir(snap, deep=True)

    def test_verify_detects_manifest_tampering(self):
        remote = LocalRemote(self.root / "archive")
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        snap = remote._dir(ref.tag)
        manifest = json.loads((snap / "manifest.json").read_text())
        manifest["counts"]["topics"] = 999999
        (snap / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        with self.assertRaises(SnapshotError):
            verify_snapshot_dir(snap, deep=True)  # manifest.sha256 mismatch

    def test_extract_state_rejects_traversal(self):
        bad_tar = self.root / "evil.tar.gz"
        with tarfile.open(bad_tar, "w:gz") as tar:
            payload = b"payload"
            for name in ("index.sqlite", "topics.jsonl", "replies.jsonl",
                         "raw_provenance.jsonl", "coverage.json", "../escape"):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        with self.assertRaises(SnapshotError):
            extract_state(bad_tar, self.root / "out")


class FakeGithub:
    """In-memory GitHub Releases API with the net transport interface."""

    def __init__(self):
        self.releases: dict = {}  # id -> dict
        self.deleted: set = set()
        self.next_id = 1
        self.blobs: dict = {}  # (release_id, name) -> bytes
        self.fail_once = None  # callable(request) -> Optional[HttpResponse]

    def _json(self, status, payload, url=""):
        return HttpResponse(
            status,
            {"content-type": "application/json"},
            json.dumps(payload).encode(),
            url,
        )

    def send(self, request):
        if self.fail_once is not None:
            resp = self.fail_once(request)
            if resp is not None:
                self.fail_once = None
                return resp
        url = request.url
        api = "https://api.github.com/repos/o/r"
        upl = "https://uploads.github.com/repos/o/r"
        if url.startswith(api + "/releases?") or url == api + "/releases" and request.method == "GET":
            live = [self._public(r) for r in self.releases.values() if r["id"] not in self.deleted]
            return self._json(200, live, url)
        if request.method == "POST" and url == api + "/releases":
            payload = json.loads(request.body.decode())
            rid = self.next_id
            self.next_id += 1
            release = {
                "id": rid,
                "tag_name": payload["tag_name"],
                "body": payload.get("body", ""),
                "draft": payload.get("draft", False),
                "assets": [],
            }
            self.releases[rid] = release
            return self._json(201, release, url)
        if url.startswith(api + "/releases/tags/"):
            tag = url.rsplit("/", 1)[-1]
            for r in self.releases.values():
                if r["tag_name"] == tag and r["id"] not in self.deleted:
                    return self._json(200, self._public(r), url)
            return self._json(404, {"message": "Not Found"}, url)
        if url.startswith(upl + "/releases/") and request.method == "POST":
            rid = int(url.split("/releases/", 1)[1].split("/")[0])
            name = url.split("name=", 1)[1]
            self.blobs[(rid, name)] = request.body
            asset = {
                "name": name,
                "size": len(request.body),
                "digest": "sha256:" + util.sha256_bytes(request.body),
                "browser_download_url": f"https://files.example/{rid}/{name}",
            }
            self.releases[rid]["assets"].append(asset)
            return self._json(201, asset, url)
        if url.startswith("https://files.example/"):
            rid = int(url.split("/")[3])
            name = url.rsplit("/", 1)[-1]
            data = self.blobs.get((rid, name))
            if data is None:
                return HttpResponse(404, {}, b"", url)
            return HttpResponse(200, {}, data, url)
        if url.startswith(api + "/releases/"):
            rid = int(url.rsplit("/", 1)[-1])
            release = self.releases.get(rid)
            if release is None or rid in self.deleted:
                return self._json(404, {"message": "Not Found"}, url)
            if request.method == "GET":
                return self._json(200, self._public(release), url)
            if request.method == "PATCH":
                payload = json.loads(request.body.decode())
                release.update(payload)
                return self._json(200, self._public(release), url)
            if request.method == "DELETE":
                self.deleted.add(rid)
                return HttpResponse(204, {}, b"", url)
        return self._json(500, {"message": f"unhandled {request.method} {url}"}, url)

    def _public(self, release):
        return {k: v for k, v in release.items()}


class GithubPublishTest(SnapshotCase):
    def _remote(self, api):
        return GithubRemote(
            "o/r",
            "token",
            transport=api,
            user_agent="TestArchive/1.0",
        )

    def test_publish_and_latest(self):
        api = FakeGithub()
        remote = self._remote(api)
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        self.assertEqual(ref.tag, "snapshot-000001")
        release = next(iter(api.releases.values()))
        self.assertFalse(release["draft"])
        names = {a["name"] for a in release["assets"]}
        self.assertEqual(names, {"state.tar.gz", "raw.tar.gz", "manifest.json"})

        latest = remote.latest()
        self.assertEqual(latest.sequence, 1)
        self.assertEqual(latest.manifest_sha256, ref.manifest_sha256)
        dest = self.root / "state-backup.tar.gz"
        remote.fetch_state(latest, dest)
        root = extract_state(dest, self.root / "restored2")
        restored = Archive(root)
        self.assertEqual(restored.counts()["captures"], 1)
        restored.close()

    def test_published_tag_is_immutable(self):
        api = FakeGithub()
        remote = self._remote(api)
        build, stage = self.build(1, None)
        remote.publish(stage, build)
        with self.assertRaises(SnapshotError):
            remote.publish(stage, build)
        # And the release still exists, untouched.
        self.assertEqual(len(api.releases), 1)

    def test_interrupted_publish_leaves_no_false_snapshot(self):
        api = FakeGithub()
        remote = self._remote(api)
        build, stage = self.build(1, None)
        # Fail the publishing PATCH once, mid-flight.
        def fail_patch(request):
            if request.method == "PATCH":
                return HttpResponse(500, {}, b"boom", request.url)
            return None
        api.fail_once = fail_patch
        with self.assertRaises(Exception):
            remote.publish(stage, build)
        # The leftover draft is cleaned up or retried cleanly; no published snapshot exists.
        self.assertIsNone(remote.latest())
        # A retry succeeds.
        ref = remote.publish(stage, build)
        self.assertEqual(ref.sequence, 1)
        live = [r for r in api.releases.values() if r["id"] not in api.deleted]
        published = [r for r in live if not r["draft"]]
        self.assertEqual(len(published), 1)

    def test_never_deletes_a_published_release(self):
        api = FakeGithub()
        remote = self._remote(api)
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        rid = ref.release["id"]
        remote._delete_draft(rid)
        self.assertNotIn(rid, api.deleted)

    def test_latest_rejects_unverifiable_manifest(self):
        api = FakeGithub()
        remote = self._remote(api)
        build, stage = self.build(1, None)
        ref = remote.publish(stage, build)
        api.releases[ref.release["id"]]["body"] = "tampered body without the hash line"
        with self.assertRaises(SnapshotError):
            remote.latest()


class ManifestTest(unittest.TestCase):
    def test_tag_format(self):
        self.assertEqual(tag_for(7), "snapshot-000007")
        with self.assertRaises(SnapshotError):
            tag_for(0)


if __name__ == "__main__":
    unittest.main()
