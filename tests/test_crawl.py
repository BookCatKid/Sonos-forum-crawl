"""Offline end-to-end crawler tests via a scripted transport."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from sonos_archive.config import load_config
from sonos_archive.crawler import Crawler, HostGate
from sonos_archive.errors import NetworkError
from sonos_archive.net import HttpResponse
from sonos_archive.store import Archive
from sonos_archive.export import write_exports

from .helpers import (
    BASE,
    EN_HOST,
    FakeClock,
    HOME_HTML,
    ROBOTS_TXT,
    SITEMAP_INDEX,
    SITEMAP_STRUCTURE,
    ScriptedTransport,
    categories_payload,
    replies_payload,
    reply_obj,
    topic_obj,
    topics_payload,
    write_config,
)


def html_headers():
    return {"content-type": "text/html; charset=utf-8"}


def json_headers():
    return {"content-type": "application/json"}


def text_headers():
    return {"content-type": "text/plain"}


class CrawlCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = load_config(write_config(self.root))
        self.transport = ScriptedTransport()
        self.clock = FakeClock()
        self.archive = Archive(self.root / "state")

    def tearDown(self):
        try:
            self.archive.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def _crawler(self, transport=None):
        transport = transport or self.transport
        return Crawler(
            self.archive,
            self.config,
            transport_factory=lambda _hc: transport,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )

    def _queue_bootstrap(self, robots=ROBOTS_TXT):
        self.transport.add("GET", BASE + "/robots.txt", robots, headers=text_headers())
        self.transport.add("GET", BASE + "/", HOME_HTML, headers=html_headers())

    def _queue_full_happy_path(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        self.transport.add(
            "POST",
            BASE + "/contentNavigation/topics",
            topics_payload(
                [
                    topic_obj(9001, reply_count=2),
                    topic_obj(7777, reply_count=5),  # on the do-not-crawl list
                    topic_obj(9003, reply_count=0),
                ],
                total=3,
            ),
        )
        self.transport.add(
            "POST",
            BASE + "/topic/9001/replies",
            replies_payload([reply_obj(101), reply_obj(102)], total=2),
        )

    def run_crawler(self, **kwargs):
        return self._crawler().run(only_hosts=[EN_HOST], **kwargs)


class HappyPathTest(CrawlCase):
    def test_capture_and_index(self):
        self._queue_full_happy_path()
        report = self.run_crawler()
        self.assertEqual(report.requests, 5)
        self.assertEqual(report.exit_code(), 0)
        self.assertFalse(report.budget_hit)

        conn = self.archive.conn
        topics = {
            r["public_id"]: r
            for r in conn.execute("SELECT * FROM topics WHERE host=?", (EN_HOST,))
        }
        self.assertEqual(sorted(topics), ["9001", "9003"])
        self.assertEqual(topics["9001"]["title"], "Topic 9001")
        self.assertEqual(topics["9001"]["author_name"], "tester")
        self.assertEqual(topics["9001"]["replies_state"], "done")
        self.assertEqual(topics["9001"]["replies_captured"], 2)
        self.assertEqual(topics["9001"]["reply_count"], 2)
        self.assertEqual(topics["9003"]["replies_state"], "done")

        replies = list(conn.execute("SELECT * FROM replies WHERE host=?", (EN_HOST,)))
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["topic_public_id"], "9001")
        self.assertTrue(replies[0]["content"].startswith("<p>reply"))
        self.assertTrue(json.loads(replies[0]["attachments_json"]))

        # do-not-crawl topic excluded, never fetched, recorded as excluded
        self.assertNotIn("7777", topics)
        excluded = list(conn.execute("SELECT * FROM excluded_topics WHERE host=?", (EN_HOST,)))
        self.assertEqual([r["public_id"] for r in excluded], ["7777"])
        self.assertEqual(self.transport.calls("POST", BASE + "/topic/7777/replies"), 0)

        # captures: every request stored verbatim, content addressed
        captures = list(conn.execute("SELECT * FROM captures ORDER BY seq"))
        self.assertEqual(len(captures), 5)
        for cap in captures:
            blob = self.archive.blob_path(cap["sha256"])
            self.assertTrue(blob.is_file(), cap["url"])
            self.assertEqual(cap["sha256"], blob.name[:-3])
            gzip.decompress(blob.read_bytes())  # valid gzip

        # provenance links topic -> capture -> request
        cap = self.archive.capture_by_seq(topics["9001"]["last_capture_seq"])
        self.assertEqual(cap["kind"], "topics")
        self.assertEqual(cap["url"], BASE + "/contentNavigation/topics")

    def test_exports(self):
        self._queue_full_happy_path()
        self.run_crawler()
        out = self.root / "export"
        result = write_exports(self.archive, out)
        self.assertEqual(result["topics"], 2)
        self.assertEqual(result["replies"], 2)
        topics = [
            json.loads(l) for l in (out / "topics.jsonl").read_text().splitlines()
        ]
        record = next(t for t in topics if t["id"] == "9001")
        self.assertEqual(record["host"], EN_HOST)
        self.assertEqual(record["author"], "tester")
        self.assertEqual(record["reply_count"], 2)
        self.assertEqual(record["content_type"], "question")
        self.assertEqual(record["category"]["id"], "2")
        prov = record["provenance"]
        self.assertEqual(prov["request"]["method"], "POST")
        self.assertIn("sha256", prov)
        replies = [
            json.loads(l) for l in (out / "replies.jsonl").read_text().splitlines()
        ]
        self.assertEqual(replies[0]["topic_id"], "9001")
        self.assertEqual(replies[0]["author"], "tester")
        coverage = json.loads((out / "coverage.json").read_text())
        self.assertTrue(coverage["is_complete_mirror"])
        prov_lines = (out / "raw_provenance.jsonl").read_text().splitlines()
        self.assertEqual(len(prov_lines), 5)

    def test_second_run_is_idle(self):
        self._queue_full_happy_path()
        self.run_crawler()
        # No pending work, robots is cached -> zero requests on a fresh run.
        report = self.run_crawler()
        self.assertEqual(report.requests, 0)
        self.assertEqual(report.exit_code(), 0)


class DenialTest(CrawlCase):
    def test_403_stops_host_and_persists(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST",
            BASE + "/categoryV2/getAccessibleCategoriesTree",
            b"forbidden",
            status=403,
        )
        report = self.run_crawler()
        self.assertEqual(report.denials, 1)
        self.assertEqual(report.exit_code(), 3)
        host = self.archive.get_host(EN_HOST)
        self.assertTrue(host["stopped_reason"].startswith("denied:"))
        self.assertTrue(host["stopped_at"])
        denials = list(self.archive.conn.execute("SELECT * FROM denials"))
        self.assertEqual(len(denials), 1)
        self.assertEqual(denials[0]["status"], 403)

        # A later run does not contact the host at all.
        self.transport.requests.clear()
        report2 = self.run_crawler()
        self.assertEqual(report2.requests, 0)
        self.assertEqual(report2.exit_code(), 3)
        self.assertEqual(len(self.transport.requests), 0)

        # An operator can clear the stop deliberately.
        self.assertTrue(self.archive.reset_host(EN_HOST))
        self.assertIsNone(self.archive.get_host(EN_HOST)["stopped_reason"])

    def test_429_is_a_denial(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", b"slow down", status=429
        )
        report = self.run_crawler()
        self.assertEqual(report.exit_code(), 3)
        self.assertIn("429", self.archive.get_host(EN_HOST)["stopped_reason"])

    def test_html_challenge_is_a_denial(self):
        self.transport.add("GET", BASE + "/robots.txt", ROBOTS_TXT, headers=text_headers())
        challenge = "<html><title>Attention Required</title>verify you are human</html>"
        self.transport.add("GET", BASE + "/", challenge, headers=html_headers())
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        report = self.run_crawler()
        self.assertEqual(report.exit_code(), 3)
        self.assertIsNotNone(self.archive.get_host(EN_HOST)["stopped_reason"])

    def test_no_retry_around_denial(self):
        # After the denial the same endpoint is never re-requested in this run.
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", b"x", status=403
        )
        self.run_crawler()
        self.assertEqual(
            self.transport.calls("POST", BASE + "/categoryV2/getAccessibleCategoriesTree"), 1
        )


class GatingTest(CrawlCase):
    def test_min_spacing_between_requests(self):
        self._queue_full_happy_path()
        self.run_crawler()
        # 5 requests on one host => 4 sleeps, each >= 1.0s floor.
        self.assertEqual(len(self.clock.sleeps), 4)
        for s in self.clock.sleeps:
            self.assertGreaterEqual(s, 1.0)

    def test_crawl_delay_raises_spacing(self):
        self._queue_full_happy_path()
        robots = "User-agent: *\nCrawl-delay: 2.5\nDisallow: /members/\n"
        self.transport.queues[("GET", BASE + "/robots.txt")][0] = HttpResponse(
            200, text_headers(), robots.encode(), BASE + "/robots.txt"
        )
        self.run_crawler()
        self.assertEqual(len(self.clock.sleeps), 4)
        for s in self.clock.sleeps:
            self.assertAlmostEqual(s, 2.5)

    def test_robots_disallow_blocks_tasks(self):
        self.transport.add(
            "GET", BASE + "/robots.txt", "User-agent: *\nDisallow: /\n", headers=text_headers()
        )
        report = self.run_crawler()
        # Only the robots fetch happened; every task got blocked by robots rules.
        self.assertEqual(report.requests, 1)
        self.assertGreater(report.blocked_tasks, 0)
        self.assertEqual(
            self.transport.calls("POST", BASE + "/categoryV2/getAccessibleCategoriesTree"), 0
        )
        tasks = list(self.archive.conn.execute("SELECT * FROM tasks"))
        self.assertTrue(tasks)
        self.assertTrue(all(t["state"] == "blocked" for t in tasks))

    def test_hostgate_floor(self):
        clock = FakeClock()
        gate = HostGate(0.2, clock.sleep, clock.monotonic)  # floor is 1.0 anyway
        gate.wait()
        gate.wait()
        self.assertEqual(clock.sleeps, [1.0])


class FailureModesTest(CrawlCase):
    def test_unknown_schema_fails_closed_and_stops(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        self.transport.add(
            "POST", BASE + "/contentNavigation/topics", {"totally": "unexpected"}
        )
        report = self.run_crawler()
        self.assertEqual(report.protocol_errors, 1)
        self.assertEqual(report.exit_code(), 4)
        self.assertTrue(
            self.archive.get_host(EN_HOST)["stopped_reason"].startswith("protocol:")
        )
        errors = list(self.archive.conn.execute("SELECT * FROM protocol_errors"))
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["kind"], "schema")

    def test_invalid_json_fails_closed(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", b"not json{"
        )
        report = self.run_crawler()
        self.assertEqual(report.exit_code(), 4)
        self.assertIsNotNone(self.archive.get_host(EN_HOST)["stopped_reason"])

    def test_5xx_is_transient_not_a_stop(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        self.transport.add("POST", BASE + "/contentNavigation/topics", b"boom", status=500)
        report = self.run_crawler()
        self.assertEqual(report.network_errors, 1)
        self.assertEqual(report.exit_code(), 4)
        # Host is NOT permanently stopped; the task stays pending for next run.
        self.assertIsNone(self.archive.get_host(EN_HOST)["stopped_reason"])
        pending = self.archive.pending_tasks(EN_HOST)
        self.assertTrue(any(t["kind"] == "topics" for t in pending))

    def test_replies_404_marks_topic_gone(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        self.transport.add(
            "POST",
            BASE + "/contentNavigation/topics",
            topics_payload([topic_obj(9001, reply_count=3)], total=1),
        )
        self.transport.add(
            "POST", BASE + "/topic/9001/replies", b"gone", status=404
        )
        report = self.run_crawler()
        self.assertEqual(report.exit_code(), 0)
        topic = self.archive.get_topic(EN_HOST, "9001")
        self.assertEqual(topic["replies_state"], "gone")
        self.assertIsNone(self.archive.get_host(EN_HOST)["stopped_reason"])

    def test_transport_error_retries_task(self):
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        self.transport.add_error(
            "POST", BASE + "/contentNavigation/topics", NetworkError("connection reset")
        )
        report = self.run_crawler()
        self.assertEqual(report.network_errors, 1)
        task = next(t for t in self.archive.pending_tasks(EN_HOST) if t["kind"] == "topics")
        self.assertEqual(task["attempts"], 1)


class ResumeTest(CrawlCase):
    def _queue_resume_scenario(self, transport):
        transport.add("GET", BASE + "/robots.txt", ROBOTS_TXT, headers=text_headers())
        transport.add("GET", BASE + "/", HOME_HTML, headers=html_headers())
        transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        transport.add(
            "POST",
            BASE + "/contentNavigation/topics",
            topics_payload([topic_obj(9001, reply_count=4)], total=1),
        )
        transport.add(
            "POST",
            BASE + "/topic/9001/replies",
            replies_payload(
                [reply_obj(101), reply_obj(102)],
                has_more=True,
                offset=0,
                page_size=2,
                total=4,
            ),
        )

    def test_resume_across_archive_sessions(self):
        self._queue_resume_scenario(self.transport)
        report = self.run_crawler(max_requests=5)
        # robots, home, categories, topics, replies@offset0 => replies task pending at offset 2
        self.assertEqual(report.requests, 5)
        self.assertTrue(report.budget_hit)
        self.archive.close(checkpoint=True)

        # Reopen the same archive (simulating a later CI run on restored state).
        self.archive = Archive(self.root / "state")
        transport2 = ScriptedTransport()
        transport2.add("GET", BASE + "/", HOME_HTML, headers=html_headers())
        transport2.add(
            "POST",
            BASE + "/topic/9001/replies",
            replies_payload(
                [reply_obj(103), reply_obj(104)],
                has_more=False,
                offset=2,
                page_size=2,
                total=4,
            ),
        )
        report2 = self._crawler(transport2).run(only_hosts=[EN_HOST])
        self.assertEqual(report2.requests, 2)  # home (csrf) + replies page 2
        replies_req = [
            r for r in transport2.requests if r.url.endswith("/topic/9001/replies")
        ]
        self.assertEqual(len(replies_req), 1)
        body = json.loads(replies_req[0].body.decode())
        self.assertEqual(body["offset"], 2)
        replies = list(
            self.archive.conn.execute(
                "SELECT reply_id FROM replies WHERE host=? ORDER BY reply_id", (EN_HOST,)
            )
        )
        self.assertEqual([r["reply_id"] for r in replies], ["101", "102", "103", "104"])
        topic = self.archive.get_topic(EN_HOST, "9001")
        self.assertEqual(topic["replies_state"], "done")
        self.assertEqual(topic["replies_captured"], 4)

    def test_close_without_checkpoint_keeps_data(self):
        # Simulate a killed run: crash mid-crawl, reopen, everything committed survives.
        self._queue_bootstrap()
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        report = self.run_crawler(max_requests=3)
        self.assertTrue(report.budget_hit)
        self.archive.conn.close()  # no checkpoint, simulate kill after committed work
        self.archive = Archive(self.root / "state")
        counts = self.archive.counts()
        self.assertEqual(counts["captures"], 3)
        self.assertEqual(counts["categories"], 2)
        # The next run picks up where the killed one left off.
        transport2 = ScriptedTransport()
        transport2.add("GET", BASE + "/", HOME_HTML, headers=html_headers())
        transport2.add(
            "POST",
            BASE + "/contentNavigation/topics",
            topics_payload([topic_obj(9001, reply_count=0)], total=1),
        )
        report2 = self._crawler(transport2).run(only_hosts=[EN_HOST])
        self.assertEqual(report2.exit_code(), 0)
        self.assertIsNotNone(self.archive.get_topic(EN_HOST, "9001"))


class SitemapTest(CrawlCase):
    def test_sitemap_structure_seeds_categories(self):
        config_path = write_config(
            self.root, {"discovery": {"use_sitemap_structure": True}}
        )
        self.config = load_config(config_path)
        self.transport.add("GET", BASE + "/robots.txt", ROBOTS_TXT, headers=text_headers())
        self.transport.add("GET", BASE + "/", HOME_HTML, headers=html_headers())
        self.transport.add(
            "POST", BASE + "/categoryV2/getAccessibleCategoriesTree", categories_payload()
        )
        self.transport.add(
            "GET", BASE + "/sitemap.xml", SITEMAP_INDEX, headers=text_headers()
        )
        self.transport.add(
            "GET", BASE + "/sitemap-structure.xml", SITEMAP_STRUCTURE, headers=text_headers()
        )
        self.transport.add(
            "POST",
            BASE + "/contentNavigation/topics",
            topics_payload([topic_obj(9001, reply_count=0)], total=1),
        )
        report = self.run_crawler()
        self.assertEqual(report.exit_code(), 0)
        host = self.archive.get_host(EN_HOST)
        self.assertEqual(host["sitemap_state"], "done")
        categories = self.archive.categories(EN_HOST)
        ids = {c["category_id"] for c in categories}
        self.assertIn("2", ids)


if __name__ == "__main__":
    unittest.main()
