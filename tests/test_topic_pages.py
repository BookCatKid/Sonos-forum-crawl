"""Opening-post recovery and safe canonical topic redirects (offline)."""
import html
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sonos_archive.crawl import Crawler
from sonos_archive.errors import ShapeError
from sonos_archive.forum import FixtureTransport, Session
from sonos_archive.publish import coverage
from sonos_archive.store import Store, SCHEMA, canon

HOST = "en.community.sonos.com"
BASE = "https://" + HOST


def reply_page(pid):
    topic = {
        "id": "741", "publicId": str(pid), "title": "A & B",
        "content": "<p>Original <strong>post</strong></p>",
        "contentType": "conversation", "authorId": "17",
        "categoryId": "123", "replyCount": 0,
    }
    props = html.escape(json.dumps({"topic": topic}), quote=True)
    return ('<div data-preact="destination/modules/Content/TopicView/V2/index.sync" '
            'data-props="' + props + '"></div>')


class TopicPageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.store = Store(self.root)
        self.store.host_row(HOST)
        self.store.seed_topics(
            HOST, [BASE + "/forum-123/old-title-158"],
            "2026-01-01T00:00:00Z")
        self.store.queue_topic_pages_bulk(HOST, ["158"], "a")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def crawler(self, responses):
        fixtures = {
            ("GET", BASE + "/forum-123/old-title-158"): responses
        }
        transport = FixtureTransport(fixtures)
        crawler = Crawler(self.store, {
            "hosts": [HOST], "timeout_seconds": 15,
            "user_agent": "test-agent", "refresh_listings_after_seconds": 60,
        }, lambda _host: transport, log=lambda _message: None)
        return crawler, transport

    def test_topic_content_after_same_host_redirect(self):
        crawler, transport = self.crawler([
            (301, {"Location": BASE + "/forum-123/canonical-title-158"}, ""),
        ])
        transport._q[("GET", BASE + "/forum-123/canonical-title-158")] = [
            (200, {"content-type": "text/html"}, reply_page(158))]
        crawler.execute(HOST, self.store.next_task(HOST))
        row = self.store.topic(HOST, "158")
        self.assertEqual(row["topic_body_state"], "done")
        self.assertEqual(row["title"], "A & B")
        self.assertEqual(json.loads(row["raw_json"])["content"],
                         "<p>Original <strong>post</strong></p>")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM captures").fetchone()[0], 2)

    def test_redirect_outside_host_fails_closed(self):
        crawler, _ = self.crawler([
            (301, {"Location": "https://elsewhere.example/topic-158"}, ""),
        ])
        with self.assertRaisesRegex(ShapeError, "out-of-scope redirect"):
            crawler.execute(HOST, self.store.next_task(HOST))
        self.assertIsNone(self.store.topic(HOST, "158")["raw_json"])

    def test_topic_404_classified_unavailable(self):
        crawler, _ = self.crawler([(404, {}, "Not Found")])
        crawler.execute(HOST, self.store.next_task(HOST))
        row = self.store.topic(HOST, "158")
        self.assertIsNone(row["raw_json"])
        self.assertEqual(row["topic_body_state"], "gone")
        cov = coverage(self.store, [HOST])["hosts"][0]
        self.assertEqual(cov["missing_topic_bodies"], 0)
        self.assertEqual(cov["unavailable_topic_bodies"], 1)

    def test_unresolved_topic_body_is_incomplete(self):
        cov = coverage(self.store, [HOST])["hosts"][0]
        self.assertEqual(cov["missing_topic_bodies"], 1)
        self.assertFalse(cov["complete"])

    def test_incompatible_topic_page_rejected(self):
        crawler, _ = self.crawler([
            (200, {"content-type": "text/html"}, reply_page(999))])
        with self.assertRaisesRegex(ShapeError, "no matching first-post"):
            crawler.execute(HOST, self.store.next_task(HOST))
        self.assertIsNone(self.store.topic(HOST, "158")["raw_json"])

    def test_migrates_old_v2_index_with_sitemap_stubs(self):
        self.store.close()
        root = self.root
        (root / "index.sqlite").unlink()
        old_schema = SCHEMA.replace(
            "    topic_body_state TEXT NOT NULL DEFAULT 'pending',\n", "")
        conn = sqlite3.connect(root / "index.sqlite")
        conn.executescript(old_schema)
        conn.execute(
            "INSERT INTO meta VALUES('schema_version','2')")
        conn.execute(
            "INSERT INTO topics(host,public_id,url,first_seen_at,last_seen_at) "
            "VALUES(?,?,?,?,?)",
            (HOST,"158",BASE+"/forum-123/topic-158","x","x"))
        conn.execute(
            "INSERT INTO topics(host,public_id,raw_json,first_seen_at,last_seen_at) "
            "VALUES(?,?,?,?,?)",
            (HOST,"159",canon({"content":"present"}),"x","x"))
        conn.commit()
        conn.close()
        self.store = Store(root)
        rows = self.store.db.execute(
            "SELECT public_id,topic_body_state FROM topics ORDER BY public_id").fetchall()
        self.assertEqual([(x[0],x[1]) for x in rows],
                         [("158","pending"),("159","done")])


if __name__ == "__main__":
    unittest.main()
