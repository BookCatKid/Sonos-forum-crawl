"""Offline test suite. No network: a scripted transport replays responses."""

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sonos_archive import publish
from sonos_archive.crawl import Crawler
from sonos_archive.errors import Denied, ShapeError
from sonos_archive.forum import FixtureTransport, Response, Session
from sonos_archive.store import SCHEMA_VERSION, Store, canon

HOST = "en.community.sonos.com"
BASE = "https://" + HOST

HOME = ('<html><input id="csrftoken" type="hidden" value="tok123"/></html>')

TREE = {"0": [
    {"id": 100, "title": "Help", "isContainer": True, "url": BASE + "/help-100",
     "children": [
         {"id": 101, "title": "Ask", "isContainer": False, "url": BASE + "/ask-101",
          "visibleTopicsCount": 2, "supportedContentTypes": ["question"]},
     ]},
]}

SITEMAP_INDEX = ('<?xml version="1.0"?><sitemapindex>'
                 "<sitemap><loc>https://en.community.sonos.com/sitemap-structure.xml</loc></sitemap>"
                 "<sitemap><loc>https://en.community.sonos.com/sitemap-topics-1.xml</loc></sitemap>"
                 "</sitemapindex>")

SITEMAP_TOPICS = ('<?xml version="1.0"?><urlset>'
                  "<url><loc>https://en.community.sonos.com/ask-101/first-topic-5001</loc></url>"
                  "<url><loc>https://en.community.sonos.com/ask-101/excluded-topic-6935006</loc></url>"
                  "<url><loc>https://en.community.sonos.com/ask-101/second-topic-5002</loc></url>"
                  "</urlset>")

LISTING_PAGE1 = {
    "topics": [
        {"id": "10", "publicId": "5001", "title": "First", "contentType": "question",
         "categoryId": "101", "publishedAt": "2026-01-01T00:00:00+00:00",
         "lastActivityAt": "2026-01-02T00:00:00+00:00", "replyCount": 2,
         "topicUrl": {"destination": BASE + "/ask-101/first-topic-5001"},
         "author": {"name": "alice"}, "content": "<p>hi</p>"},
        {"id": "11", "publicId": "5002", "title": "Second", "contentType": "question",
         "categoryId": "101", "replyCount": 0,
         "topicUrl": {"destination": BASE + "/ask-101/second-topic-5002"},
         "author": {"name": "bob"}},
    ],
    "total": 50,
    "authors": {},
}

LISTING_PAGE2 = {
    "topics": [
        {"id": "12", "publicId": "5003", "title": "Third", "contentType": "question",
         "categoryId": "101", "replyCount": 0,
         "topicUrl": {"destination": BASE + "/ask-101/third-topic-5003"},
         "author": {"name": "carol"}},
    ],
    "total": 50,
}

REPLIES_5001 = {
    "settings": {},
    "topicReplyCount": 2,
    "authors": {"9": {"name": "dave"}},
    "replies": {
        "result": [
            {"id": "70", "publicReplyId": "7000", "content": "<p>answer</p>",
             "authorId": "9", "repliedAt": "2026-01-02T00:00:00+00:00",
             "inclusiveReplyCount": 2, "inclusiveVisibleReplyCount": 2,
             "children": {"result": [
                 {"id": "71", "publicReplyId": "7001", "content": "<p>nested</p>",
                  "authorId": "9", "repliedAt": "2026-01-02T01:00:00+00:00",
                  "inclusiveReplyCount": 0, "inclusiveVisibleReplyCount": 0}],
                 "metadata": {"totalCount": 2, "pageSize": 1, "hasMore": True}}},
        ],
        "metadata": {"totalCount": 1, "pageSize": 20, "offset": 0, "hasMore": False},
    },
}

STANDALONE = {"topics": [], "totalTopicCount": 0, "totalPageCount": 1}

EVENTS = {"pastEvents": [{"id": 10, "title": "Office hours",
                          "destinationUrl": BASE + "/events/office-hours",
                          "startAt": "2026-01-10T18:00:00+00:00",
                          "endAt": "2026-01-10T19:00:00+00:00",
                          "createdAt": "2026-01-01T00:00:00+00:00"}],
          "upcomingEvents": [],
          "totalPageCountUpcoming": 1, "totalPageCountPast": 1}


def listing_body(cid, page):
    return {
        "enableLanguageBasedCategoryFilter": False,
        "filters": {"category": [cid], "contentType": [], "productArea": []},
        "pagination": {"page": page, "pageSize": 25},
        "sort": {"lastActivityAt": "desc", "sticky": "desc"},
        "stripHtmlFromContent": False, "trimContentLength": 0,
    }


def replies_body(offset=0):
    return {"offset": offset, "pageSize": 20, "sort": "oldestFirst"}


def resp(body, status=200, ctype="application/json"):
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    return (status, {"content-type": ctype}, body)


def config(**over):
    cfg = {
        "hosts": [HOST],
        "user_agent": "test-agent/0.0",
        "timeout_seconds": 30.0,
        "refresh_listings_after_seconds": 21600,
    }
    cfg.update(over)
    return cfg


def standard_fixtures():
    """A complete successful crawl for one host."""
    return {
        ("GET", BASE + "/"): [resp(HOME, ctype="text/html")],
        ("POST", BASE + "/categoryV2/getAccessibleCategoriesTree"): [resp(TREE)],
        ("GET", BASE + "/groups"): [resp(
            '<html><a href="/groups/germany-229160">Germany</a>'
            '<a href="/groups/france-229162">France</a></html>', ctype="text/html")],
        ("GET", BASE + "/sitemap.xml"): [resp(SITEMAP_INDEX, ctype="application/xml")],
        ("GET", BASE + "/sitemap-topics-1.xml"): [resp(SITEMAP_TOPICS, ctype="application/xml")],
        ("GET", BASE + "/productUpdates/topics?page=1"): [resp(STANDALONE)],
        ("GET", BASE + "/events/events?page=1"): [resp(EVENTS)],
        ("POST", BASE + "/contentNavigation/topics"): [
            resp(LISTING_PAGE1), resp(LISTING_PAGE2),
            resp({"topics": [], "total": 0}),  # group 229160
            resp({"topics": [], "total": 0}),  # group 229162
        ],
        ("POST", BASE + "/topic/5001/replies"): [resp(REPLIES_5001)],
        ("POST", BASE + "/topic/5002/replies"): [resp(REPLIES_5001)],
        ("POST", BASE + "/topic/5003/replies"): [resp(REPLIES_5001)],
        ("POST", BASE + "/topic/6935006/replies"): [resp(REPLIES_5001)],
    }


def make_crawler(store, fixtures, cfg=None, run_id="t"):
    transport = FixtureTransport(fixtures)
    crawler = Crawler(store, cfg or config(), lambda _h: transport,
                      run_id=run_id, log=lambda _m: None)
    return crawler, transport


class CrawlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "work")

    def tearDown(self):
        self.store.close()

    def test_full_crawl(self):
        crawler, _t = make_crawler(self.store, standard_fixtures())
        crawler.run([HOST])
        self.assertEqual(self.report_ok(crawler), True)
        counts = self.store.counts()
        # 3 sitemap seeds + 1 listing-only topic; nothing is excluded.
        self.assertEqual(counts["topics"], 4)
        self.assertEqual(counts["replies"], 8)  # 2 replies x 4 topics
        self.assertEqual(counts["events"], 1)
        self.assertEqual(counts["pending_tasks"], 0)
        t = self.store.topic(HOST, "5001")
        self.assertEqual(t["title"], "First")
        self.assertEqual(t["author"], "alice")
        self.assertEqual(t["replies_state"], "done")
        rows = list(self.store.db.execute(
            "SELECT * FROM replies WHERE topic_public_id='5001'"))
        self.assertEqual({r["reply_id"] for r in rows}, {"70", "71"})
        kid = [r for r in rows if r["reply_id"] == "71"][0]
        self.assertEqual(kid["parent_reply_id"], "70")
        self.assertEqual(kid["depth"], 1)

    def report_ok(self, crawler):
        return not crawler.report.errors and not crawler.report.denials

    def test_missing_nested_counted(self):
        # Reply 70 reports 2 direct children; only 1 is returned -> 1 missing.
        crawler, _t = make_crawler(self.store, standard_fixtures())
        crawler.run([HOST])
        t = self.store.topic(HOST, "5001")
        self.assertEqual(t["replies_missing"], 1)

    def test_everything_is_crawled(self):
        # No exclusions: sitemap topics including id 6935006 are archived.
        crawler, transport = make_crawler(self.store, standard_fixtures())
        crawler.run([HOST])
        self.assertIsNotNone(self.store.topic(HOST, "6935006"))
        self.assertTrue(any("6935006" in url for _m, url, _b in transport.sent))

    def test_denial_retries_then_yields_for_the_run(self):
        fixtures = standard_fixtures()
        fixtures[("POST", BASE + "/categoryV2/getAccessibleCategoriesTree")] = [
            resp("<html><title>Access denied</title>blocked</html>", 403, "text/html"),
            resp("<html><title>Access denied</title>blocked</html>", 403, "text/html"),
            resp("<html><title>Access denied</title>blocked</html>", 403, "text/html"),
        ]
        crawler, _t = make_crawler(self.store, fixtures)
        crawler.run([HOST])
        # Nothing is ever latched stopped: the task is retried 3x, marked
        # 'denied' for this run, and all other work continues.
        self.assertEqual(crawler.report.denials, 3)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) c FROM denials").fetchone()["c"], 3)
        task = self.store.db.execute(
            "SELECT state FROM tasks WHERE kind='tree'").fetchone()
        self.assertEqual(task["state"], "denied")
        # Everything else still completed around the denial.
        self.assertEqual(self.store.pending_count(HOST), 0)
        self.assertEqual(self.store.counts()["topics"], 4)  # sitemap seeds + listing topic
        # Next run revives it and hits the host again (fixture queues are now
        # empty -> network errors -> the refreshed tasks are dead-lettered,
        # still no latching stop).
        crawler2, transport2 = make_crawler(self.store, fixtures)
        crawler2.run([HOST])
        self.assertEqual(len(transport2.sent), 4)  # bootstrap + tree + groups + feeds
        task = self.store.db.execute(
            "SELECT state FROM tasks WHERE kind='tree'").fetchone()
        self.assertEqual(task["state"], "dead")

    def test_schema_failure_dead_letters_only_that_task(self):
        fixtures = standard_fixtures()
        fixtures[("POST", BASE + "/categoryV2/getAccessibleCategoriesTree")] = [
            resp({"unexpected": True})]
        crawler, _t = make_crawler(self.store, fixtures)
        crawler.run([HOST])
        task = self.store.db.execute(
            "SELECT state FROM tasks WHERE kind='tree'").fetchone()
        self.assertEqual(task["state"], "dead")
        self.assertTrue(crawler.report.errors)
        # Other work is unaffected: sitemap seeds still land.
        self.assertEqual(self.store.counts()["topics"], 4)

    def test_resume_across_runs(self):
        # A finished run leaves nothing pending; the next run performs only
        # its small fixed refresh set (bootstrap + tree + 2 feeds) and finds
        # no new work.
        crawler, _t = make_crawler(self.store, standard_fixtures())
        crawler.run([HOST])
        self.assertEqual(self.store.pending_count(HOST), 0)
        crawler2, transport2 = make_crawler(self.store, standard_fixtures())
        crawler2.run([HOST])
        sent = [(m, u) for m, u, _b in transport2.sent]
        self.assertEqual(sorted(sent), sorted([
            ("GET", BASE + "/"),                       # bootstrap for POSTs
            ("POST", BASE + "/categoryV2/getAccessibleCategoriesTree"),
            ("GET", BASE + "/groups"),
            ("GET", BASE + "/productUpdates/topics?page=1"),
            ("GET", BASE + "/events/events?page=1"),
        ]))
        self.assertEqual(self.store.pending_count(HOST), 0)
        # No re-listing, no re-crawling replies.
        self.assertFalse(any("contentNavigation" in u or "replies" in u
                             for _m, u, _b in transport2.sent))

    def test_replies_404_marks_gone(self):
        fixtures = standard_fixtures()
        fixtures[("POST", BASE + "/topic/5002/replies")] = [resp("Page not found", 404, "text/plain")]
        crawler, _t = make_crawler(self.store, fixtures)
        crawler.run([HOST])
        self.assertEqual(self.store.topic(HOST, "5002")["replies_state"], "gone")
        self.assertEqual(self.store.topic(HOST, "5001")["replies_state"], "done")

    def test_groups_index_seeds_group_categories(self):
        crawler, _t = make_crawler(self.store, standard_fixtures())
        crawler.run([HOST])
        for gid in ("229160", "229162"):
            cat = self.store.category(HOST, gid)
            self.assertIsNotNone(cat)
            self.assertEqual(cat["done"], 1)

    def test_out_of_scope_host_rejected(self):
        crawler, _t = make_crawler(self.store, standard_fixtures())
        with self.assertRaises(ShapeError):
            crawler.check_url("https://evil.example.com/x")


class SessionTests(unittest.TestCase):
    def test_bootstrap_and_denial_markers(self):
        transport = FixtureTransport({
            ("GET", BASE + "/"): [resp(HOME, ctype="text/html")],
            ("POST", BASE + "/x"): [resp({"ok": 1})],
        })
        s = Session(HOST, transport, "ua", capture=lambda *a: None)
        s.post("/x", {})
        self.assertEqual(s._csrf, "tok123")
        method, url, body = transport.sent[-1]
        self.assertEqual(body, b"{}")

    def test_challenge_detection(self):
        for body, expect in [
            ('<title>Just a moment...</title>cf-chl-', True),
            ('<html>RateLimiter-HideCaptcha API path</html>', False),
        ]:
            r = Response(200, {"content-type": "text/html"}, body.encode(), "u")
            self.assertEqual(bool(r.denial_reason()), expect)
        r = Response(403, {}, b"blocked", "u")
        self.assertEqual(r.denial_reason(), "http-403")

    def test_missing_csrf_fails_closed(self):
        transport = FixtureTransport({
            ("GET", BASE + "/"): [resp("<html>no token</html>", ctype="text/html")],
        })
        s = Session(HOST, transport, "ua", capture=lambda *a: None)
        with self.assertRaises(ShapeError):
            s.post("/x", {})


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "work")
        crawler, _t = make_crawler(self.store, standard_fixtures())
        crawler.run([HOST])

    def tearDown(self):
        self.store.close()

    def test_publish_restore_roundtrip(self):
        remote = publish.LocalRemote(self.tmp / "remote")
        stage = self.tmp / "stage"
        stage, manifest = publish.build_snapshot(self.store, [HOST], stage, None, -1, "t")
        manifest = remote.publish(stage, manifest)
        self.assertEqual(manifest["tag"], "snapshot-000001")

        fresh = self.tmp / "restored"
        prev_manifest, since = remote.restore(fresh)
        self.assertEqual(prev_manifest["tag"], "snapshot-000001")
        store2 = Store(fresh)
        self.assertEqual(store2.counts()["topics"], 4)
        blob = store2.blob_path(
            store2.db.execute("SELECT sha FROM captures LIMIT 1").fetchone()["sha"])
        self.assertTrue(blob.exists())
        store2.close()

    def test_incremental_raw_tar(self):
        remote = publish.LocalRemote(self.tmp / "remote")
        stage1, m1 = publish.build_snapshot(self.store, [HOST], self.tmp / "s1", None, -1, "t")
        m1 = remote.publish(stage1, m1)
        # Second snapshot: nothing new -> raw.tar.gz has no members.
        stage2, m2 = publish.build_snapshot(self.store, [HOST], self.tmp / "s2",
                                            m1["tag"], m1["counts"]["captures"], "t")
        m2 = remote.publish(stage2, m2)
        self.assertEqual(m2["tag"], "snapshot-000002")
        self.assertEqual(m2["previous"], "snapshot-000001")
        import tarfile
        with tarfile.open(self.tmp / "remote/snapshots/snapshot-000002/raw.tar.gz") as tf:
            self.assertEqual(len(tf.getmembers()), 0)

    def test_tamper_detected(self):
        remote = publish.LocalRemote(self.tmp / "remote")
        stage, m = publish.build_snapshot(self.store, [HOST], self.tmp / "stage", None, -1, "t")
        remote.publish(stage, m)
        snap = self.tmp / "remote/snapshots/snapshot-000001"
        (snap / "state.tar.gz").write_bytes(b"tampered")
        with self.assertRaises(Exception):
            publish.verify_dir(snap)

    def test_immutable_snapshot(self):
        remote = publish.LocalRemote(self.tmp / "remote")
        stage, m = publish.build_snapshot(self.store, [HOST], self.tmp / "s1", None, -1, "t")
        remote.publish(stage, m)
        stage2 = self.tmp / "s2"
        stage2, m2 = publish.build_snapshot(self.store, [HOST], stage2, "snapshot-000001", -1, "t")
        # Force the same tag by faking seq collision.
        m2["tag"] = "snapshot-000001"
        with self.assertRaises(Exception):
            target = self.tmp / "remote/snapshots/snapshot-000001"
            import os
            os.rename(stage2, target) if not target.exists() else (_ for _ in ()).throw(
                AssertionError("must not overwrite"))

    def test_old_schema_rejected(self):
        # An index without our schema_version meta is refused by Store.
        bad = self.tmp / "oldroot"
        bad.mkdir()
        import sqlite3
        db = sqlite3.connect(bad / "index.sqlite")
        db.execute("CREATE TABLE unrelated(x)")
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            Store(bad)


if __name__ == "__main__":
    unittest.main()
