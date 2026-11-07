"""HTTP transport and the anonymous forum session.

One ``Session`` per host: a fresh cookie jar plus the site's own public CSRF
token, exactly like an unauthenticated browser. Any denial
(401/403/407/429/451 or an anti-bot challenge page) raises ``Denied`` and is
never retried or routed around — if the site says no, that host stops.
"""

import base64
import gzip
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .errors import Denied, HttpError, NetworkError, ShapeError

MAX_BODY = 20 * 1024 * 1024  # topic sitemaps run ~8 MB

# Phrases that appear on anti-bot interstitials but not on ordinary forum
# pages. The bare word "captcha" is NOT a marker: the real home page contains
# it inside an API path name.
DENIAL_MARKERS = (
    "request blocked",
    "verify you are human",
    "unusual traffic",
    "challenge-platform",
    "cf-browser-verification",
    "cf-chl-",
    "g-recaptcha",
    "px-captcha",
    "attention required!",
    "just a moment",
    "access denied",
    "cf-mitigated",
)

DENIAL_STATUSES = {401, 403, 407, 429, 451}

_CSRF_RE = (
    re.compile(r'id=["\']csrftoken["\'][^>]*value=["\']([^"\']+)["\']', re.I),
    re.compile(r'value=["\']([^"\']+)["\'][^>]*id=["\']csrftoken["\']', re.I),
    re.compile(r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']', re.I),
)


class Response:
    __slots__ = ("status", "headers", "body", "url")

    def __init__(self, status, headers, body, url):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body
        self.url = url

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    def text(self):
        body = self.body
        if self.header("content-encoding") in ("gzip", "x-gzip"):
            body = gzip.decompress(body)
        return body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.text())
        except ValueError as exc:
            raise ShapeError(f"response is not JSON: {exc}")

    def denial_reason(self):
        """A reason string if this response is a denial/challenge, else None."""
        if self.status in DENIAL_STATUSES:
            return f"http-{self.status}"
        if self.header("cf-mitigated"):
            return "cloudflare-mitigated"
        text = self.text()[:262144].lower()
        for marker in DENIAL_MARKERS:
            if marker in text:
                if self.status == 200 and "html" in self.header("content-type"):
                    return f"challenge:{marker}"
                if self.status == 503:
                    return f"retryable-challenge:{marker}"
        return None

    def snippet(self, limit=400):
        return re.sub(r"\s+", " ", self.text()).strip()[:limit]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LiveTransport:
    """urllib over a per-session anonymous cookie jar. No redirects."""

    def __init__(self, timeout=30.0):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            _NoRedirect(),
        )

    def send(self, method, url, headers, body):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return Response(r.status, dict(r.headers.items()), r.read(MAX_BODY + 1), url)
        except urllib.error.HTTPError as e:
            return Response(e.code, dict(e.headers.items()) if e.headers else {},
                            e.read(MAX_BODY + 1), url)
        except (OSError, urllib.error.URLError) as e:
            raise NetworkError(f"{e} ({url})")


class FixtureTransport:
    """Offline replay: {(METHOD, url): [(status, headers, body), ...]} consumed in order."""

    def __init__(self, responses=None):
        self._q = dict(responses or {})
        self.sent = []

    def send(self, method, url, headers, body):
        self.sent.append((method, url, body))
        key = (method.upper(), url)
        queue = self._q.get(key)
        if not queue:
            raise NetworkError(f"no fixture for {method} {url}")
        status, h, b = queue.pop(0)
        if isinstance(b, str):
            b = b.encode()
        return Response(status, h, b, url)


class Session:
    """Anonymous session for one host: fresh cookie jar + the site's own
    public CSRF token. ``capture`` records every response verbatim."""

    def __init__(self, host, transport, user_agent, capture):
        self.host = host
        self.base = "https://" + host
        self.transport = transport
        self.user_agent = user_agent
        self.capture = capture
        self._csrf = None

    def _send(self, method, path, payload, kind, accept):
        url = self.base + path
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            if self._csrf:
                headers["X-CSRF-TOKEN"] = self._csrf
                headers["X-Requested-With"] = "XMLHttpRequest"
            data = json.dumps(payload).encode()
        r = self.transport.send(method, url, headers, data)
        if len(r.body) > MAX_BODY:
            raise ShapeError(f"response over {MAX_BODY} bytes at {url}")
        body_text = json.dumps(payload, sort_keys=True) if payload is not None else None
        self.capture(method, url, body_text, r.status, r.body, kind)
        reason = r.denial_reason()
        if reason:
            raise Denied(reason, url=url, status=r.status, snippet=r.snippet())
        if r.status == 404:
            raise HttpError(404, url)
        if r.status != 200:
            raise HttpError(r.status, url)
        return r

    def get(self, path, kind="get", accept="text/html"):
        return self._send("GET", path, None, kind, accept)

    def post(self, path, payload, kind="post"):
        if self._csrf is None:
            self._bootstrap()
        return self._send("POST", path, payload, kind, "application/json")

    def _bootstrap(self):
        r = self._send("GET", "/", None, "bootstrap", "text/html")
        text = r.text()
        for rx in _CSRF_RE:
            m = rx.search(text)
            if m:
                self._csrf = m.group(1)
                return
        raise ShapeError("home page did not contain a recognisable csrftoken field")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
