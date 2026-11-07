"""Strict, fail-closed schema validation — synthetic and real fixtures."""

import json
import unittest
from pathlib import Path

from sonos_archive.errors import SchemaError
from sonos_archive.schema import (
    validate_categories,
    validate_replies,
    validate_topics,
)

from .helpers import (
    categories_payload,
    replies_payload,
    reply_obj,
    topic_obj,
    topics_payload,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RealFixtureTest(unittest.TestCase):
    """Recorded live responses must satisfy our strict validators."""

    def test_real_categories(self):
        nodes = validate_categories(_load("real_categories.json"))
        self.assertEqual(len(nodes), 31)
        containers = [n for n in nodes if n.is_container]
        leaves = [n for n in nodes if not n.is_container]
        self.assertTrue(containers and leaves)

    def test_real_topics(self):
        page = validate_topics(_load("real_topics.json"))
        self.assertEqual(page.total, 6653)
        self.assertEqual(len(page.topics), 2)
        topic = page.topics[0]
        self.assertTrue(topic.public_id.isdigit())
        self.assertTrue(topic.url.startswith("https://en.community.sonos.com/"))
        self.assertEqual(topic.content_type, "question")
        self.assertTrue(page.authors)

    def test_real_replies(self):
        page = validate_replies(_load("real_replies.json"))
        self.assertEqual(page.total_count, 12)
        self.assertTrue(page.has_more)
        self.assertEqual(len(page.replies), 5)
        for reply in page.replies:
            self.assertTrue(reply.content)
            self.assertTrue(reply.author_id)


class FailClosedTest(unittest.TestCase):
    def test_categories_bad_shapes(self):
        for bad in (
            [],
            {"0": "nope"},
            {"0": [{"id": 1}]},  # missing title
            {"0": [{"id": 1, "title": 5, "children": [], "isContainer": False}]},
            {"0": [{"id": 1, "title": "x", "children": "nope", "isContainer": False}]},
        ):
            with self.assertRaises(SchemaError, msg=repr(bad)):
                validate_categories(bad)

    def test_categories_ok(self):
        nodes = validate_categories(categories_payload(leaf_ids=(2, 3)))
        self.assertEqual(len(nodes), 3)
        self.assertEqual(nodes[1].parent_id, "1")

    def test_topics_bad_shapes(self):
        good = topics_payload()
        for bad in (
            {"topics": []},  # missing total
            {"topics": "x", "total": 0, "authors": {}},
            {"topics": [{"publicId": "1"}], "total": 1, "authors": {}},
            {"topics": [dict(topic_obj(1), topicUrl=None)], "total": 1, "authors": {}},
            {"topics": [dict(topic_obj(1), replyCount="x")], "total": 1, "authors": {}},
            {"topics": [dict(topic_obj(1), author="bob")], "total": 1, "authors": {}},
        ):
            with self.assertRaises(SchemaError, msg=repr(bad)[:120]):
                validate_topics(bad)
        # sanity: the good payload passes
        page = validate_topics(good)
        self.assertEqual(len(page.topics), 2)

    def test_replies_bad_shapes(self):
        good = replies_payload([reply_obj(101)])
        for bad in (
            {"replies": {"result": []}},  # no metadata
            {"replies": {"result": [], "metadata": {"totalCount": 0}}},
            {"replies": {"result": [], "metadata": {
                "totalCount": 0, "offset": 0, "pageSize": 20, "hasMore": "yes"}},
            },
            {"replies": {"result": [{"id": 1}], "metadata": {
                "totalCount": 1, "offset": 0, "pageSize": 20, "hasMore": False}}},
            {"replies": {"result": "x", "metadata": {
                "totalCount": 0, "offset": 0, "pageSize": 20, "hasMore": False}}},
        ):
            with self.assertRaises(SchemaError, msg=repr(bad)[:120]):
                validate_replies(bad)
        page = validate_replies(good)
        self.assertEqual(len(page.replies), 1)

    def test_nested_children_flatten(self):
        child = reply_obj(102)
        parent = reply_obj(101, children=[child])
        page = validate_replies(replies_payload([parent], total=2))
        self.assertEqual(len(page.replies), 2)
        self.assertEqual(page.replies[1].parent_reply_id, "101")


if __name__ == "__main__":
    unittest.main()
