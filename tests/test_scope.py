"""Scope validation and do-not-crawl enforcement."""

import unittest

from sonos_archive.config import load_config
from sonos_archive.errors import PolicyError
from sonos_archive.scope import Scope

from .helpers import BASE, EN_HOST, write_config


class ScopeTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.config = load_config(write_config(self.tmp.name))
        self.scope = Scope(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def _bad(self, method, url):
        with self.assertRaises(PolicyError):
            self.scope.validate(method, url)

    def test_allowed_get_paths(self):
        for path in ("/", "/robots.txt", "/sitemap.xml", "/sitemap-structure.xml"):
            host, got = self.scope.validate("GET", BASE + path)
            self.assertEqual(host, EN_HOST)
            self.assertEqual(got, path)

    def test_allowed_post_paths(self):
        for path in (
            "/categoryV2/getAccessibleCategoriesTree",
            "/contentNavigation/topics",
            "/topic/6934361/replies",
        ):
            host, got = self.scope.validate("POST", BASE + path)
            self.assertEqual(got, path)

    def test_other_hosts_rejected(self):
        self._bad("GET", "https://de.community.sonos.com/")  # not in this config
        self._bad("GET", "https://example.com/")
        self._bad("GET", "https://en.community.sonos.com.evil.com/")

    def test_disallowed_paths(self):
        self._bad("GET", "/members/foo" and BASE + "/members/foo")
        self._bad("GET", BASE + "/admin")
        self._bad("POST", BASE + "/contentNavigation/other")
        self._bad("POST", BASE + "/topic/abc/replies")

    def test_forbidden_route_and_queries(self):
        self._bad("GET", BASE + "/ideas/topics?page=1")
        self._bad("GET", BASE + "/ideas/topics")
        self._bad("GET", BASE + "/?page=1")
        self._bad("GET", BASE + "/robots.txt?x=1")

    def test_scheme_userinfo_fragment_port(self):
        self._bad("GET", "http://en.community.sonos.com/")
        self._bad("GET", "https://user:pw@en.community.sonos.com/")
        self._bad("GET", BASE + "/#frag")
        self._bad("GET", "https://en.community.sonos.com:8443/")

    def test_explicit_443_is_ok(self):
        host, path = self.scope.validate("GET", "https://en.community.sonos.com:443/")
        self.assertEqual(path, "/")

    def test_methods(self):
        self._bad("PUT", BASE + "/")
        self._bad("DELETE", BASE + "/")
        self._bad("GET", BASE + "/contentNavigation/topics")
        self._bad("POST", BASE + "/robots.txt")

    def test_do_not_crawl_topic_ids(self):
        # 7777 is on the test do-not-crawl list
        self._bad("POST", BASE + "/topic/7777/replies")
        with self.assertRaises(PolicyError):
            self.scope.assert_topic_allowed(EN_HOST, "7777")
        self.assertTrue(self.scope.is_excluded_topic(EN_HOST, "7777"))
        self.assertFalse(self.scope.is_excluded_topic(EN_HOST, "9001"))

    def test_replies_path_template(self):
        self.assertEqual(self.scope.replies_path("9001"), "/topic/9001/replies")


if __name__ == "__main__":
    unittest.main()
