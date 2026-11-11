"""Failure-injection tests for archive durability and recovery."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from sonos_archive import publish
from sonos_archive.cli import cmd_capture
from sonos_archive.errors import ArchiveError
from sonos_archive.store import Store
from tests.test_archive import BASE, HOST, make_crawler, resp, standard_fixtures


class DurabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "work")
        self.store.host_row(HOST)
        self.remote = publish.LocalRemote(self.root / "releases")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def capture(self, text):
        _, sha = self.store.capture(
            HOST, "GET", BASE + "/" + text, None, 200,
            text.encode(), "test", "2026-01-01T00:00:00Z")
        return sha

    def snapshot(self, tag=None, cursor=-1, name="stage"):
        stage, manifest = publish.build_snapshot(
            self.store, [HOST], self.root / name, tag, cursor, "test")
        return self.remote.publish(stage, manifest)

    def test_historical_raw_files_are_restored_from_full_lineage(self):
        old_sha = self.capture("old")
        first = self.snapshot()
        new_sha = self.capture("new")
        latest = self.snapshot(first["tag"], first["capture_cursor"], "stage2")

        partial = self.root / "live"
        manifest, cursor = self.remote.restore(partial, include_historical_raw=False)
        self.assertEqual(cursor, 2)
        self.assertEqual(manifest["tag"], latest["tag"])
        self.assertFalse((partial / "raw/sha256" / old_sha[:2] / (old_sha + ".gz")).exists())
        self.assertTrue((partial / "raw/sha256" / new_sha[:2] / (new_sha + ".gz")).exists())

        full = self.root / "recovered"
        restored, cursor = self.remote.restore(full)
        self.assertEqual(restored["tag"], latest["tag"])
        self.assertEqual(cursor, 2)
        recovered = Store(full)
        self.assertEqual(recovered.read_blob(old_sha), b"old")
        self.assertEqual(recovered.read_blob(new_sha), b"new")
        recovered.close()

    def test_checkpoint_cursor_frozen_during_concurrent_crawl(self):
        self.capture("first")
        original_write = publish.write_exports
        written = []

        def concurrent_capture(frozen, stage):
            written.append(self.capture("arrived-during-snapshot"))
            return original_write(frozen, stage)

        with patch.object(publish, "write_exports", concurrent_capture):
            first = self.snapshot()
        self.assertEqual(first["counts"]["captures"], 1)
        self.assertEqual(first["capture_cursor"], 1)
        self.assertEqual(first["new_capture_blobs"], 1)
        later = self.snapshot(first["tag"], first["capture_cursor"], "second")
        self.assertEqual(later["counts"]["captures"], 2)
        self.assertEqual(later["capture_cursor"], 2)
        self.assertEqual(later["new_capture_blobs"], 1)
        full = self.root / "recovered"
        self.remote.restore(full)
        recovered = Store(full)
        self.assertEqual(recovered.read_blob(written[0]), b"arrived-during-snapshot")
        recovered.close()

    def test_unavailable_historical_raw_pack_is_fatal_on_full_restore(self):
        self.capture("one")
        first = self.snapshot()
        self.capture("two")
        self.snapshot(first["tag"], first["capture_cursor"], "second")
        (self.remote.snaps / first["tag"] / "raw.tar.gz").unlink()
        with self.assertRaises(ArchiveError):
            self.remote.restore(self.root / "recovered")
        # Fast crawler restores still work because they only need the newest
        # index and incremental pack; full recovery correctly reports loss.
        self.remote.restore(self.root / "live", include_historical_raw=False)

    def test_staging_rejects_missing_new_raw(self):
        sha = self.capture("missing")
        self.store.blob_path(sha).unlink()
        with self.assertRaisesRegex(ArchiveError, "missing new raw"):
            publish.build_snapshot(
                self.store, [HOST], self.root / "stage", None, -1, "test")

    def test_coverage_fails_closed_without_categories_or_with_dead_tasks(self):
        self.assertFalse(publish.coverage(self.store, [HOST])["is_complete_mirror"])
        self.assertFalse(publish.coverage(self.store, [])["is_complete_mirror"])
        self.store.upsert_category(
            HOST, {"id": 101, "title": "Forum", "isContainer": False},
            "2026-01-01T00:00:00Z")
        self.store.set_category(HOST, "101", done=1)
        self.store.add_task(HOST, "replies", "5001",
                            BASE + "/topic/5001/replies", priority=50, at="x")
        self.store.set_task(HOST, "replies", "5001", state="dead")
        coverage = publish.coverage(self.store, [HOST])
        self.assertFalse(coverage["is_complete_mirror"])
        self.assertFalse(coverage["hosts"][0]["complete"])

    def test_cli_refuses_to_rebuild_on_restore_failure(self):
        args = SimpleNamespace(
            config=str(Path(__file__).resolve().parents[1] / "config.json"),
            remote="local", archive_dir=str(self.root / "archive"),
            host=None, fixtures=None, run_id="test", checkpoint_seconds=300,
            no_publish=False)
        with patch.object(publish.LocalRemote, "restore",
                          side_effect=ArchiveError("corrupt snapshot")):
            self.assertEqual(cmd_capture(args), 4)
        self.assertIsNone(publish.LocalRemote(args.archive_dir).latest())


class RecoveryTests(unittest.TestCase):
    def test_dead_reply_task_is_retried_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "work")
            fixtures = standard_fixtures()
            fixtures[("POST", BASE + "/topic/5002/replies")] = [
                resp({"unexpected": "shape"})]
            first, _transport = make_crawler(store, fixtures)
            first.run([HOST])
            row = store.db.execute(
                "SELECT state FROM tasks WHERE kind='replies' AND key='5002'").fetchone()
            self.assertEqual(row["state"], "dead")
            second, _transport = make_crawler(store, standard_fixtures())
            second.run([HOST])
            row = store.db.execute(
                "SELECT state FROM tasks WHERE kind='replies' AND key='5002'").fetchone()
            self.assertEqual(row["state"], "done")
            self.assertEqual(store.topic(HOST, "5002")["replies_state"], "done")
            store.close()


if __name__ == "__main__":
    unittest.main()
