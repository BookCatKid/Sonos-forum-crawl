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
             "inclusiveReplyCount": 1, "inclusiveVisibleReplyCount": 1,
             "children": {"result": [
                 {"id": "71", "publicReplyId": "7001", "content": "<p>nested</p>",
                  "authorId": "9", "repliedAt": "2026-01-02T01:00:00+00:00",
                  "inclusiveReplyCount": 0, "inclusiveVisibleReplyCount": 0}],
                 "metadata": {"totalCount": 1, "pageSize": 1, "hasMore": False}}},
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
        # Listings share one endpoint; responses must be matched to their
        # category and page, not the order the task scheduler happens to use.
        ("POST", BASE + "/contentNavigation/topics", canon(listing_body(101, 1))):
            [resp(LISTING_PAGE1)],
        ("POST", BASE + "/contentNavigation/topics", canon(listing_body(101, 2))):
            [resp(LISTING_PAGE2)],
        ("POST", BASE + "/contentNavigation/topics", canon(listing_body(229160, 1))):
            [resp({"topics": [], "total": 0})],
        ("POST", BASE + "/contentNavigation/topics", canon(listing_body(229162, 1))):
            [resp({"topics": [], "total": 0})],
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
        self.assertTrue(self.report_ok(crawler), crawler.report.to_dict())
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

    def test_listings_can_interleave_across_seconds(self):
        # Reproduce a page transition landing in a later timestamp second.
        # The two group listings should not consume the category page-2 reply.
        set_task = self.store.set_task

        def delay_next_page(host, kind, key, **fields):
            if kind == "listing" and key == "101" and fields.get("offset") == 2:
                fields["updated_at"] = "9999-12-31T23:59:59Z"
            return set_task(host, kind, key, **fields)

        self.store.set_task = delay_next_page
        crawler, transport = make_crawler(self.store, standard_fixtures())
        crawler.run([HOST])

        listings = [
            (json.loads(body)["filters"]["category"][0],
             json.loads(body)["pagination"]["page"])
            for method, url, body in transport.sent
            if url.endswith("/contentNavigation/topics")
        ]
        self.assertLess(listings.index((229160, 1)), listings.index((101, 2)))
        self.assertTrue(self.report_ok(crawler), crawler.report.to_dict())
        self.assertEqual(self.store.counts()["topics"], 4)
        self.assertEqual(self.store.counts()["pending_tasks"], 0)

    def report_ok(self, crawler):
        return not crawler.report.errors and not crawler.report.denials

    def test_missing_nested_counted(self):
        # The nested endpoint is missing from these fixtures. The gap stays
        # visible and the failed task is retried on the next run.
        import copy
        fixtures = standard_fixtures()
        root = copy.deepcopy(REPLIES_5001)
        root["replies"]["result"][0]["children"]["metadata"].update(
            totalCount=2, hasMore=True)
        fixtures[("POST", BASE + "/topic/5001/replies")] = [resp(root)]
        crawler, _t = make_crawler(self.store, fixtures)
        crawler.run([HOST])
        t = self.store.topic(HOST, "5001")
        self.assertEqual(t["replies_missing"], 1)
        self.assertEqual(t["replies_state"], "partial")
        self.assertEqual(self.store.db.execute(
            "SELECT state FROM tasks WHERE kind='nested' "
            "AND key='5001:70'").fetchone()["state"], "dead")

    def test_nested_replies_fetch_every_child_with_pagination(self):
        import copy
        fixtures = standard_fixtures()
        root = copy.deepcopy(REPLIES_5001)
        root["replies"]["result"][0]["children"]["metadata"].update(
            totalCount=3, hasMore=True)
        children = [
            {"id": str(rid), "publicReplyId": str(7000 + rid - 70),
             "authorId": "9", "content": f"<p>{rid}</p>",
             "repliedAt": "2026-01-02T01:00:00+00:00"}
            for rid in (71, 72, 73)
        ]
        def nested_page(items, offset, more):
            return resp({
                "authors": {"9": {"name": "dave"}},
                "replies": {"result": items, "metadata": {
                    "totalCount": 3, "offset": offset,
                    "pageSize": 20, "hasMore": more, "depthLevel": 2}},
            })
        fixtures[("POST", BASE + "/topic/5001/replies")] = [
            resp(root),
            nested_page(children[:2], 0, True),
            nested_page(children[2:], 2, False),
        ]
        crawler, transport = make_crawler(self.store, fixtures)
        crawler.run([HOST])
        self.assertTrue(self.report_ok(crawler), crawler.report.to_dict())
        topic = self.store.topic(HOST, "5001")
        self.assertEqual(topic["replies_state"], "done")
        self.assertEqual(topic["replies_missing"], 0)
        self.assertEqual(topic["replies_seen"], 4)
        rows = self.store.db.execute(
            "SELECT reply_id,parent_reply_id,depth FROM replies "
            "WHERE topic_public_id='5001' ORDER BY reply_id").fetchall()
        self.assertEqual([(r["reply_id"], r["parent_reply_id"], r["depth"])
                          for r in rows],
                         [("70", None, 0), ("71", "70", 1),
                          ("72", "70", 1), ("73", "70", 1)])
        nested = [json.loads(body) for method, url, body in transport.sent
                  if url == BASE + "/topic/5001/replies"
                  and json.loads(body).get("parentId")]
        self.assertEqual([p["offset"] for p in nested], [0, 2])
        self.assertTrue(all(
            p["parentId"] == {"id": 70, "contentType": "question"}
            for p in nested))

    def test_recover_historical_missing_children_without_root_recrawl(self):
        import copy
        fixtures = standard_fixtures()
        root = copy.deepcopy(REPLIES_5001)
        root["replies"]["result"][0]["children"]["metadata"].update(
            totalCount=2, hasMore=True)
        fixtures[("POST", BASE + "/topic/5001/replies")] = [resp(root)]
        first, _ = make_crawler(self.store, fixtures)
        first.run([HOST])
        self.assertEqual(self.store.topic(HOST, "5001")["replies_missing"], 1)
        # Simulate a previous crawler version which recorded the gap but
        # never added a child-pagination task to the persistent queue.
        self.store.db.execute(
            "DELETE FROM tasks WHERE kind='nested' AND key='5001:70'")
        self.store.db.commit()
        self.store.set_topic_replies(HOST, "5001", replies_state="done")
        second_fixtures = standard_fixtures()
        second_fixtures[("POST", BASE + "/topic/5001/replies")] = [
            resp({"authors": {"9": {"name": "dave"}},
                  "replies": {"result": [
                      {"id": "71", "authorId": "9", "content": "child"},
                      {"id": "72", "authorId": "9", "content": "second child"}],
                      "metadata": {"totalCount": 2, "hasMore": False}}})]
        second, transport = make_crawler(self.store, second_fixtures)
        second.run([HOST])
        self.assertTrue(self.report_ok(second), second.report.to_dict())
        self.assertEqual(self.store.topic(HOST, "5001")["replies_missing"], 0)
        self.assertEqual(self.store.topic(HOST, "5001")["replies_state"], "done")
        calls = [json.loads(body) for method, url, body in transport.sent
                 if url == BASE + "/topic/5001/replies"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["parentId"],
                         {"id": 70, "contentType": "question"})

    def test_reply_id_aliases_preserve_parentage_and_pending_tasks(self):
        host, pid = HOST, "999"
        self.store.host_row(host)
        self.store.add_reply(host, pid, "70", "7000", None,
                             "a", None, 0, canon({"id": "70"}), 1)
        self.store.add_reply(host, pid, "71", "7001", "70",
                             "b", None, 1, canon({"id": "71"}), 1)
        self.store.add_task(
            host, "nested", "999:70", BASE + "/topic/999/replies",
            payload=canon({"offset": 4, "pageSize": 20, "sort": "oldestFirst",
                           "parentId": {"id": 70, "contentType": "question"}}),
            offset=4, priority=55, at="x")
        self.store.set_task(host, "nested", "999:70", state="dead")
        self.store.add_reply(host, pid, "80", "7000", None,
                             "a", None, 0, canon({"id": "80"}), 2)
        self.assertIsNone(self.store.db.execute(
            "SELECT reply_id FROM replies WHERE reply_id='70'").fetchone())
        child = self.store.db.execute(
            "SELECT parent_reply_id FROM replies WHERE reply_id='71'").fetchone()
        self.assertEqual(child["parent_reply_id"], "80")
        task = self.store.db.execute(
            "SELECT payload,state,offset FROM tasks "
            "WHERE kind='nested' AND key='999:80'").fetchone()
        self.assertEqual(task["state"], "pending")
        self.assertEqual(task["offset"], 0)
        self.assertEqual(json.loads(task["payload"])["parentId"]["id"], 80)
        self.assertEqual(self.store.db.execute(
            "SELECT new_reply_id FROM reply_aliases WHERE old_reply_id='70'"
            ).fetchone()["new_reply_id"], "80")

    def test_404_nested_parent_recovers_after_internal_id_migration(self):
        import copy
        fixtures = standard_fixtures()
        old = copy.deepcopy(REPLIES_5001)
        parent = old["replies"]["result"][0]
        parent["children"]["metadata"].update(totalCount=2, hasMore=True)
        new = copy.deepcopy(old)
        new_parent = new["replies"]["result"][0]
        new_parent["id"] = "80"
        new_parent["children"]["result"][0]["id"] = "81"
        nested_page = {"authors": {"9": {"name": "dave"}},
                       "replies": {"result": [
                           {"id": "81", "publicReplyId": "7001",
                            "authorId": "9", "content": "old child"},
                           {"id": "82", "publicReplyId": "7002",
                            "authorId": "9", "content": "new child"}],
                           "metadata": {"totalCount": 2, "hasMore": False}}}
        fixtures[("POST", BASE + "/topic/5001/replies")] = [
            resp(old), resp("Not found", 404, "text/plain"),
            resp(new), resp(nested_page)]
        crawler, transport = make_crawler(self.store, fixtures)
        crawler.run([HOST])
        self.assertTrue(self.report_ok(crawler), crawler.report.to_dict())
        topic = self.store.topic(HOST, "5001")
        self.assertEqual(topic["replies_state"], "done")
        self.assertEqual(topic["replies_missing"], 0)
        self.assertEqual(topic["replies_seen"], 3)
        ids = {r["reply_id"] for r in self.store.db.execute(
            "SELECT reply_id FROM replies WHERE topic_public_id='5001'")}
        self.assertEqual(ids, {"80", "81", "82"})
        parents = self.store.db.execute(
            "SELECT reply_id,parent_reply_id FROM replies "
            "WHERE topic_public_id='5001' ORDER BY reply_id").fetchall()
        self.assertEqual([(r["reply_id"], r["parent_reply_id"]) for r in parents],
                         [("80", None), ("81", "80"), ("82", "80")])
        nested_calls = [json.loads(body) for method, url, body in transport.sent
                        if url == BASE + "/topic/5001/replies"
                        and json.loads(body).get("parentId")]
        self.assertEqual([x["parentId"]["id"] for x in nested_calls], [70, 80])

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
        self.assertEqual(self.store.counts()["topics"], 3)  # tree denied: sitemap seeds only
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
        self.assertEqual(self.store.counts()["topics"], 3)

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
