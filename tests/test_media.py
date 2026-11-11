"""Media extraction, safety and durable incremental archive tests (offline)."""
import hashlib
import gzip
import json
from unittest.mock import patch
from pathlib import Path
import tarfile
import tempfile
import unittest
from sonos_archive import media
from sonos_archive.store import Store, canon

HOST = "de.community.sonos.com"
CDN = "https://uploads-eu-west-1.insided.com/sonos-de/attachment/"

class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "work")
        self.store.host_row(HOST)
        self.store.upsert_topic(HOST, "15",
            {"title":"Images","url":f"https://{HOST}/forum/photos-15"},
            canon({"content":'<p><a href="'+CDN+'full.jpg">'
                '<img src="'+CDN+'thumb.jpg"></a>'
                '<img src="http://127.0.0.1/private.jpg"></p>'}),
            1,"2026-01-01")
        self.store.add_reply(HOST,"15","1","1001",None,"bob","2026-01-01",0,
            canon({"content":"<p>photo attached</p>",
                "attachments":[{"source":"image.png",
                                "source_thumb":"image_thumb.png"}]}),1)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_extracts_full_images_and_metadata_attachments(self):
        refs=media.discover_media(self.store.db)
        self.assertEqual(len(refs),4)
        for name in ("full.jpg","thumb.jpg","image.png","image_thumb.png"):
            self.assertTrue(any(u.endswith("/"+name) for u in refs))
        self.assertFalse(any("127.0.0.1" in u for u in refs))

    def test_rejects_private_and_untrusted_sources(self):
        self.assertIsNone(media.normalize_url("http://127.0.0.1/x","https://x/"))
        self.assertIsNone(media.normalize_url("http://[::1]/","https://x/"))
        self.assertIsNone(media.normalize_url("https://metadata.google.internal/x","https://x/"))
        self.assertFalse(media.approved_host("https://uploads-eu-west-1.insided.com.attacker.example/a"))
        self.assertTrue(media.approved_host(CDN+"image.png"))

    def test_cumulative_index_skips_existing_and_preserves_hashes(self):
        calls=[]
        def download(url,max_bytes):
            calls.append(url)
            return ("bytes for "+url).encode(),"image/png"
        first=self.root/"stage1"
        m1=media.batch(self.store.root/"index.sqlite",None,first,
                       max_assets=2,downloader=download)
        self.assertEqual(m1["counts"]["discovered"],4)
        self.assertEqual(m1["counts"]["downloaded"],2)
        self.assertEqual(m1["counts"]["unprocessed"],2)
        with tarfile.open(first/"media.tar.gz","r:gz") as archive:
            members=[x for x in archive if x.isfile()]
            self.assertEqual(len(members),2)
            for item in members:
                b=archive.extractfile(item).read()
                self.assertTrue(item.name.endswith(hashlib.sha256(b).hexdigest()))
        second=self.root/"stage2"
        m2=media.batch(self.store.root/"index.sqlite",
                       first/"media-index.json.gz",second,
                       max_assets=4,downloader=download)
        self.assertEqual(m2["counts"]["downloaded"],4)
        self.assertEqual(m2["counts"]["new_downloads"],2)
        self.assertEqual(m2["counts"]["unprocessed"],0)
        self.assertEqual(len(calls),4)

    def test_unavailable_is_not_marked_downloaded(self):
        def fail(url,max_bytes):
            raise ValueError("HTTP 404")
        stage=self.root/"bad"
        m1=media.batch(self.store.root/"index.sqlite",None,stage,
                       max_assets=4,downloader=fail)
        self.assertEqual(m1["counts"]["unavailable"],4)
        m2=media.batch(self.store.root/"index.sqlite",
                       stage/"media-index.json.gz",self.root/"second",
                       max_assets=4,downloader=fail)
        self.assertEqual(m2["counts"]["attempts"],0)
        self.assertEqual(m2["counts"]["unavailable"],4)

    def test_unsupported_inventory_not_truncated_by_budget(self):
        discovered = {
            CDN + "first.jpg": {"origin": "embedded", "host": HOST},
            "https://zzzz.other.example/last.jpg":
                {"origin": "embedded", "host": HOST},
        }
        with patch.object(media, "discover_media", return_value=discovered):
            result = media.batch(
                self.store.root / "index.sqlite", None, self.root / "limited",
                max_assets=0, downloader=lambda *_args, **_kw:
                    self.fail("No download should occur"))
        self.assertEqual(result["counts"]["unsupported"], 1)
        self.assertEqual(result["counts"]["unprocessed"], 1)
        with gzip.open(self.root / "limited" / "media-index.json.gz", "rt") as f:
            index = json.load(f)
        self.assertEqual(index["https://zzzz.other.example/last.jpg"]["status"],
                         "unsupported")

    def test_parses_anchor_original_and_thumbnail(self):
        p=media.MediaParser()
        p.feed('<a href="'+CDN+'full.jpg"><img src="'+CDN+'thumb.jpg"></a>')
        self.assertEqual(len(p.assets),2)
