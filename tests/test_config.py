"""Config loading and validation."""

import json
import tempfile
import unittest
from pathlib import Path

from sonos_archive.config import load_config
from sonos_archive.errors import ConfigError

from .helpers import base_config_dict, write_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self, data) -> None:
        path = Path(self.tmp.name) / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_config(path)

    def test_valid_config(self):
        config = self._load(base_config_dict())
        self.assertEqual(len(config.hosts), 1)
        self.assertEqual(config.hosts[0].host, "en.community.sonos.com")
        self.assertTrue(config.is_excluded_topic("en.community.sonos.com", "7777"))

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(Path(self.tmp.name) / "nope.json")

    def test_bad_schema_version(self):
        data = base_config_dict()
        data["schema_version"] = 99
        with self.assertRaises(ConfigError):
            self._load(data)

    def test_user_agent_must_identify_archive(self):
        data = base_config_dict()
        data["project"]["user_agent"] = "curl"
        with self.assertRaises(ConfigError):
            self._load(data)

    def test_allowed_hosts_must_match(self):
        data = base_config_dict()
        data["scope"]["allowed_hosts"] = ["other.example.com"]
        with self.assertRaises(ConfigError):
            self._load(data)

    def test_do_not_crawl_unknown_host(self):
        data = base_config_dict()
        data["do_not_crawl"]["topic_ids"] = {"unknown.example.com": ["1"]}
        with self.assertRaises(ConfigError):
            self._load(data)

    def test_do_not_crawl_non_numeric(self):
        data = base_config_dict()
        data["do_not_crawl"]["topic_ids"]["en.community.sonos.com"] = ["abc"]
        with self.assertRaises(ConfigError):
            self._load(data)

    def test_template_needs_placeholder(self):
        data = base_config_dict()
        data["endpoints"]["replies_path_template"] = "/topic/replies"
        data["scope"]["allowed_post_paths"][-1] = "/topic/replies"
        with self.assertRaises(ConfigError):
            self._load(data)

    def test_repo_config_loads(self):
        repo_config = Path(__file__).resolve().parent.parent / "config.json"
        config = load_config(repo_config)
        self.assertEqual(len(config.hosts), 3)
        en = "en.community.sonos.com"
        for tid in ("6935006", "4479975", "5219128", "5487455", "5936784", "6014182"):
            self.assertTrue(config.is_excluded_topic(en, tid), tid)
        self.assertIn(
            "https://en.community.sonos.com/ideas/topics?page=1",
            config.forbidden_routes,
        )


if __name__ == "__main__":
    unittest.main()
