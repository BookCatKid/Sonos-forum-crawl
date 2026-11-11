"""Offline GitHub-release reconstruction using its actual restore path."""

import shutil
import tempfile
import unittest
from pathlib import Path

from sonos_archive import publish
from sonos_archive.cli import main
from sonos_archive.store import Store
from tests.test_archive import HOST


class MockGithubRemote(publish.GithubRemote):
    """Supply real release assets from LocalRemote, never contact GitHub."""

    def __init__(self, local):
        super().__init__("BookCatKid/Sonos-forum-crawl", "test-token")
        self.local = local
        self.ancestor_requests = []

    def release(self, tag):
        folder = self.local.snaps / tag
        return {"tag_name": tag, "draft": False, "assets": [
            {"name": p.name, "url": str(p)}
            for p in folder.iterdir() if p.is_file()
        ]}

    def latest(self):
        path = self.local.latest()
        return (publish.seq_of(path.name), self.release(path.name)) if path else None

    def _req(self, method, url, body=None, raw=None, extra_headers=None):
        assert method == "GET" and "/releases/tags/" in url
        tag = url.rsplit("/", 1)[1]
        self.ancestor_requests.append(tag)
        return 200, self.release(tag), {}

    def _download_asset(self, rel, name, dest):
        source = self.local.snaps / rel["tag_name"] / name
        shutil.copyfile(source, dest)
        return dest


class GithubRestoreTests(unittest.TestCase):
    def test_restore_verifies_latest_and_hydrates_all_ancestor_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = publish.LocalRemote(root / "remote")
            store = Store(root / "work")
            store.host_row(HOST)
            for n in range(3):
                _, sha = store.capture(
                    HOST, "GET", f"https://{HOST}/{n}", None, 200,
                    f"payload-{n}".encode(), "test", "x")
                stage, manifest = publish.build_snapshot(
                    store, [HOST], root / f"stage{n}",
                    publish.tag_for(n) if n else None, n if n else -1, "test")
                local.publish(stage, manifest)
            store.close()

            github = MockGithubRemote(local)
            manifest, seq = github.restore(root / "recovered", include_historical_raw=True)
            self.assertEqual(manifest["tag"], "snapshot-000003")
            self.assertEqual(seq, 3)
            self.assertEqual(github.ancestor_requests,
                             ["snapshot-000002", "snapshot-000001"])
            restored = Store(root / "recovered")
            self.assertEqual(restored.counts()["captures"], 3)
            for n in range(3):
                sha = restored.db.execute(
                    "SELECT sha FROM captures WHERE url=?",
                    (f"https://{HOST}/{n}",)).fetchone()["sha"]
                self.assertEqual(restored.read_blob(sha), f"payload-{n}".encode())
            restored.close()

            out = root / "cli-recovered"
            self.assertEqual(main([
                "restore", "--remote", "local",
                "--archive-dir", str(root / "remote"), "--out", str(out)]), 0)
            self.assertTrue((out / "index.sqlite").exists())
            self.assertEqual(main([
                "restore", "--remote", "local",
                "--archive-dir", str(root / "remote"), "--out", str(out)]), 4)


if __name__ == "__main__":
    unittest.main()
