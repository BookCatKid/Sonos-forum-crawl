"""Transport helpers: denial detection, CSRF extraction, gzip, fixtures."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from sonos_archive.errors import NetworkError, ProtocolError
from sonos_archive.net import (
    FixtureTransport,
    HttpRequest,
    HttpResponse,
    decode_body,
    decode_text,
    denial_reason,
    denial_snippet,
    extract_csrf,
)


def resp(status=200, body=b"", headers=None, url="https://en.community.sonos.com/x"):
    return HttpResponse(status, {k.lower(): v for k, v in (headers or {}).items()}, body, url)


class DenialDetectionTest(unittest.TestCase):
    def test_status_denials(self):
        for status in (401, 403, 407, 429, 451):
            self.assertIsNotNone(denial_reason(resp(status=status)))

    def test_clean_responses(self):
        self.assertIsNone(denial_reason(resp(200, b"{}", {"content-type": "application/json"})))
        self.assertIsNone(denial_reason(resp(404, b"nope", {"content-type": "text/html"})))
        self.assertIsNone(denial_reason(resp(500, b"err")))

    def test_challenge_markers(self):
        body = b"<html>verify you are human</html>"
        reason = denial_reason(resp(200, body, {"content-type": "text/html"}))
        self.assertTrue(reason.startswith("challenge:"))
        reason = denial_reason(
            resp(503, b"g-recaptcha", {"content-type": "text/html"})
        )
        self.assertTrue(reason.startswith("retryable-challenge:"))

    def test_ordinary_page_containing_captcha_word_is_not_denial(self):
        # The live home page contains "RateLimiter-HideCaptcha" in a JSON blob;
        # that must not trip challenge detection.
        body = b'<html>{"RateLimiter":"/on/demandware.store/Sites/RateLimiter-HideCaptcha"}</html>'
        self.assertIsNone(denial_reason(resp(200, body, {"content-type": "text/html"})))

    def test_cf_mitigated_header(self):
        self.assertEqual(
            denial_reason(resp(200, b"{}", {"cf-mitigated": "challenge"})),
            "cloudflare-mitigated",
        )

    def test_json_body_with_marker_is_not_challenge(self):
        body = json.dumps({"error": "captcha required"}).encode()
        # status 200 + json content type: marker inside JSON is content, not a wall
        self.assertIsNone(
            denial_reason(resp(200, body, {"content-type": "application/json"}))
        )


class CsrfTest(unittest.TestCase):
    def test_extracts_input_token(self):
        html = '<input id="csrftoken" type="hidden" value="ABC123"/>'
        self.assertEqual(extract_csrf(html), "ABC123")

    def test_reversed_attribute_order(self):
        html = '<input value="ZZZ" id="csrftoken"/>'
        self.assertEqual(extract_csrf(html), "ZZZ")

    def test_meta_tag(self):
        html = '<meta name="csrf-token" content="META1">'
        self.assertEqual(extract_csrf(html), "META1")

    def test_missing_token(self):
        self.assertIsNone(extract_csrf("<html>none</html>"))


class EncodingTest(unittest.TestCase):
    def test_gzip_body(self):
        payload = gzip.compress(b'{"ok": true}')
        r = resp(200, payload, {"content-encoding": "gzip"})
        self.assertEqual(decode_body(r), b'{"ok": true}')
        self.assertEqual(decode_text(r), '{"ok": true}')

    def test_bad_gzip_fails(self):
        r = resp(200, b"not-gzip", {"content-encoding": "gzip"})
        with self.assertRaises(ProtocolError):
            decode_body(r)

    def test_unknown_encoding_fails(self):
        r = resp(200, b"x", {"content-encoding": "br"})
        with self.assertRaises(ProtocolError):
            decode_body(r)


class FixtureTransportTest(unittest.TestCase):
    def test_replays_in_order_and_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fx.json"
            path.write_text(
                json.dumps(
                    {
                        "responses": [
                            {"method": "GET", "url": "https://x/1", "status": 200,
                             "headers": {"content-type": "text/plain"}, "body": "a"},
                            {"method": "GET", "url": "https://x/1", "status": 404,
                             "body": "b"},
                            {"method": "POST", "url": "https://x/1",
                             "body_base64": "e30=", "status": 200},
                        ]
                    }
                )
            )
            fx = FixtureTransport(path)
            r1 = fx.send(HttpRequest("GET", "https://x/1"))
            self.assertEqual((r1.status, r1.body), (200, b"a"))
            r2 = fx.send(HttpRequest("GET", "https://x/1"))
            self.assertEqual(r2.status, 404)
            r3 = fx.send(HttpRequest("POST", "https://x/1"))
            self.assertEqual(r3.body, b"{}")
            with self.assertRaises(NetworkError):
                fx.send(HttpRequest("GET", "https://x/1"))
            with self.assertRaises(NetworkError):
                fx.send(HttpRequest("GET", "https://x/never"))


if __name__ == "__main__":
    unittest.main()
