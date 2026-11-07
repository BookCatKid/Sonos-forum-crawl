"""CLI end-to-end: offline capture via recorded fixtures, status/verify/export."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from sonos_archive import cli

from .helpers import (
    BASE,
    EN_HOST,
    HOME_HTML,
    ROBOTS_TXT,
    categories_payload,
    replies_payload,
    reply_obj,
    topic_obj,
    topics_payload,
    write_config,
)


def fixture_file(path: Path) -> Path:
    entries = [
        {"method": "GET", "url": f"{BASE}/robots.txt", "status": 200,
         "headers": {"content-type": "text/plain"}, "body": ROBOTS_TXT},
        {"method": "GET", "url": f"{BASE}/", "status": 200,
         "headers": {"content-type": "text/html"}, "body": HOME_HTML},
        {"method": "POST", "url": f"{BASE}/categoryV2/getAccessibleCategoriesTree",
         "status": 200, "headers": {"content-type": "application/json"},
         "body": json.dumps(categories_payload())},
        {"method": "POST", "url": f"{BASE}/contentNavigation/topics",
         "status": 200, "headers": {"content-type": "application/json"},
         "body": json.dumps(topics_payload([topic_obj(9001, reply_count=1)], total=1))},
        {"method": "POST", "url": f"{BASE}/topic/9001/replies",
         "status": 200, "headers": {"content-type": "application/json"},
         "body": json.dumps(replies_payload([reply_obj(101)], total=1))},
    ]
    path.write_text(json.dumps({"responses": entries}), encoding="utf-8")
    return path


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = write_config(self.root)
        self.fixtures = fixture_file(self.root / "fixtures.json")
        self.archive_dir = self.root / "archive"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_capture_status_verify_export(self):
        code, out, err = self._run(
            "capture",
            "--config", str(self.config),
            "--remote", "local",
            "--archive-dir", str(self.archive_dir),
            "--fixtures", str(self.fixtures),
            "--host", EN_HOST,
        )
        self.assertEqual(code, 0, err)
        summary = json.loads(out)
        self.assertEqual(summary["requests"], 5)
        self.assertEqual(summary["published"]["tag"], "snapshot-000001")
        self.assertTrue(summary["coverage"]["is_complete_mirror"])

        snap_dir = self.archive_dir / "snapshots" / "snapshot-000001"
        self.assertTrue((snap_dir / "manifest.json").is_file())
        self.assertTrue((snap_dir / "state.tar.gz").is_file())
        self.assertTrue((snap_dir / "raw.tar.gz").is_file())

        code, out, _ = self._run(
            "status",
            "--config", str(self.config),
            "--remote", "local",
            "--archive-dir", str(self.archive_dir),
        )
        self.assertEqual(code, 0)
        status = json.loads(out)
        self.assertEqual(status["latest"]["tag"], "snapshot-000001")
        self.assertEqual(status["counts"]["topics"], 1)

        code, out, _ = self._run("verify", "--config", str(self.config),
                                 "--snapshot-dir", str(snap_dir))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

        out_dir = self.root / "exported"
        code, out, _ = self._run(
            "export",
            "--config", str(self.config),
            "--snapshot-dir", str(snap_dir),
            "--out", str(out_dir),
        )
        self.assertEqual(code, 0)
        topics = (out_dir / "topics.jsonl").read_text().splitlines()
        self.assertEqual(len(topics), 1)
        record = json.loads(topics[0])
        self.assertEqual(record["id"], "9001")
        self.assertEqual(record["provenance"]["request"]["method"], "POST")

    def test_second_capture_publishes_next_snapshot(self):
        self.test_capture_status_verify_export()
        # Everything is already captured; a second run performs no requests
        # and publishes nothing new.
        code, out, err = self._run(
            "capture",
            "--config", str(self.config),
            "--remote", "local",
            "--archive-dir", str(self.archive_dir),
            "--fixtures", str(self.fixtures),
            "--host", EN_HOST,
        )
        self.assertEqual(code, 0, err)
        summary = json.loads(out)
        self.assertIsNone(summary["published"])
        self.assertEqual(summary["requests"], 0)


if __name__ == "__main__":
    unittest.main()
