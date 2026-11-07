"""robots.txt parsing, rule precedence and crawl-delay."""

import unittest
from pathlib import Path

from sonos_archive.robots import RobotsRules, parse_robots

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UA = "TestArchive/1.0 (anonymous archive; test@example.com)"


class RobotsTest(unittest.TestCase):
    def test_real_en_robots(self):
        text = (FIXTURES / "real_robots_en.txt").read_text(encoding="utf-8")
        rules = parse_robots(text, "testarchive", UA)
        self.assertFalse(rules.allowed("/members/someone"))
        self.assertFalse(rules.allowed("/topic/new"))
        self.assertFalse(rules.allowed("/badges"))
        self.assertFalse(rules.allowed("/x?sort=asc"))
        self.assertFalse(rules.allowed("/x/search?q=abc"))
        # our endpoints are not disallowed
        self.assertTrue(rules.allowed("/categoryV2/getAccessibleCategoriesTree"))
        self.assertTrue(rules.allowed("/contentNavigation/topics"))
        self.assertTrue(rules.allowed("/topic/6934361/replies"))
        self.assertTrue(rules.allowed("/sitemap.xml"))

    def test_longest_match_and_allow_tie(self):
        rules = parse_robots(
            "User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /a/b/c\n",
            "testarchive",
            UA,
        )
        self.assertFalse(rules.allowed("/a"))
        self.assertFalse(rules.allowed("/a/x"))
        self.assertTrue(rules.allowed("/a/b"))
        self.assertFalse(rules.allowed("/a/b/c"))

    def test_wildcards_and_anchors(self):
        rules = parse_robots(
            "User-agent: *\nDisallow: /private$\nDisallow: */tmp/*\n",
            "testarchive",
            UA,
        )
        self.assertFalse(rules.allowed("/private"))
        self.assertTrue(rules.allowed("/private/page"))  # $ anchor prevents match
        self.assertFalse(rules.allowed("/x/tmp/y"))

    def test_crawl_delay(self):
        rules = parse_robots(
            "User-agent: *\nCrawl-delay: 5\nDisallow: /x\n",
            "testarchive",
            UA,
        )
        self.assertEqual(rules.crawl_delay, 5.0)

    def test_group_selection_prefers_specific(self):
        rules = parse_robots(
            "User-agent: *\nDisallow: /all\n\n"
            "User-agent: testarchive\nDisallow: /mine\n",
            "testarchive",
            UA,
        )
        self.assertTrue(rules.allowed("/all"))  # specific group wins
        self.assertFalse(rules.allowed("/mine"))

    def test_empty_disallow_means_allow(self):
        rules = parse_robots("User-agent: *\nDisallow:\n", "testarchive", UA)
        self.assertTrue(rules.allowed("/anything"))

    def test_roundtrip_dict(self):
        rules = parse_robots(
            "User-agent: *\nCrawl-delay: 3\nDisallow: /x\n", "testarchive", UA
        )
        restored = RobotsRules.from_dict(rules.to_dict())
        self.assertEqual(restored.crawl_delay, 3.0)
        self.assertFalse(restored.allowed("/x"))
        self.assertTrue(restored.allowed("/y"))

    def test_no_groups_allows_all(self):
        rules = parse_robots("", "testarchive", UA)
        self.assertTrue(rules.allowed("/anything"))
        self.assertIsNone(rules.crawl_delay)


if __name__ == "__main__":
    unittest.main()
