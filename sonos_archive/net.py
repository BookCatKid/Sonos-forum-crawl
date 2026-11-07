"""HTTP transport, anonymous session handling and denial detection."""

from __future__ import annotations

import base64
import gzip
import http.cookiejar
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .config import Config
from .errors import DenialError, NetworkError, ProtocolError, SchemaError
from .scope import Scope


@dataclass
class HttpRequest:
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None


@dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    url: str

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    @property
    def content_type(self) -> str:
        return self.header("content-type", "").lower()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


class LiveTransport:
    """One anonymous cookie jar per host. Redirects are not followed."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            _NoRedirect(),
        )

    def send(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(
            request.url,
            data=request.body,
            headers=request.headers,
            method=request.method,
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                body = response.read()
                headers = {k.lower(): v for k, v in response.headers.items()}
                return HttpResponse(response.status, headers, body, response.geturl())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            return HttpResponse(exc.code, headers, body, request.url)
        except urllib.error.URLError as exc:
            raise NetworkError(f"network error for {request.url}: {exc.reason}", url=request.url)
        except TimeoutError:
            raise NetworkError(f"timeout for {request.url}", url=request.url)
        except OSError as exc:
            raise NetworkError(f"connection error for {request.url}: {exc}", url=request.url)


class FixtureTransport:
    """Offline replay transport.

    Fixture JSON format::

        {"responses": [
          {"method": "GET", "url": "https://...", "status": 200,
           "headers": {"content-type": "text/html"}, "body": "..."}
        ]}

    ``body_base64`` may be used instead of ``body`` for binary payloads.
    Matching is by method and URL; entries are consumed in order.
    """

    def __init__(self, path: Path):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise NetworkError(f"fixture file not found: {path}")
        except json.JSONDecodeError as exc:
            raise NetworkError(f"invalid fixture JSON: {exc}")
        entries = raw.get("responses", [])
        if not isinstance(entries, list):
            raise NetworkError("fixture 'responses' must be a list")
        self._queues: Dict[Tuple[str, str], List[dict]] = {}
        for entry in entries:
            key = (str(entry.get("method", "GET")).upper(), str(entry.get("url", "")))
            self._queues.setdefault(key, []).append(entry)

    def send(self, request: HttpRequest) -> HttpResponse:
        key = (request.method.upper(), request.url)
        queue = self._queues.get(key)
        if not queue:
            raise NetworkError(f"no recorded fixture for {request.method} {request.url}", url=request.url)
        entry = queue.pop(0)
        if "body_base64" in entry:
            body = base64.b64decode(entry["body_base64"])
        else:
            body = str(entry.get("body", "")).encode("utf-8")
        headers = {str(k).lower(): str(v) for k, v in (entry.get("headers") or {}).items()}
        return HttpResponse(int(entry.get("status", 200)), headers, body, request.url)


def decode_body(response: HttpResponse) -> bytes:
    encoding = response.header("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        return response.body
    if encoding in ("gzip", "x-gzip"):
        try:
            return gzip.decompress(response.body)
        except (OSError, EOFError) as exc:
            raise ProtocolError(f"invalid gzip response body: {exc}", url=response.url)
    raise ProtocolError(f"unsupported content-encoding: {encoding}", url=response.url)


def decode_text(response: HttpResponse) -> str:
    try:
        return decode_body(response).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"response is not valid UTF-8: {exc}", url=response.url)


# Deliberately specific: these phrases appear on anti-bot interstitials but not
# in ordinary forum pages (the home page legitimately contains the bare word
# "captcha" inside an API path name, so that word alone is not a marker).
_DENIAL_MARKERS = (
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


def denial_reason(response: HttpResponse) -> Optional[str]:
    """Return a reason string if this response is an access denial/challenge."""
    if response.status in (401, 403, 407, 429, 451):
        return f"http-{response.status}"
    if response.header("cf-mitigated"):
        return "cloudflare-mitigated"
    marker_hit = None
    try:
        text = decode_text(response).lower()
        for marker in _DENIAL_MARKERS:
            if marker in text:
                marker_hit = marker
                break
    except ProtocolError:
        text = response.body[:65536].decode("latin-1", "ignore").lower()
        for marker in _DENIAL_MARKERS:
            if marker in text:
                marker_hit = marker
                break
    if marker_hit:
        if response.status == 200 and "html" in response.content_type:
            return f"challenge:{marker_hit}"
        if response.status == 503:
            return f"retryable-challenge:{marker_hit}"
    return None


def denial_snippet(response: HttpResponse, limit: int = 500) -> str:
    try:
        text = decode_text(response)
    except ProtocolError:
        text = response.body.decode("utf-8", "replace")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# Response headers worth keeping in the archive for provenance/debugging.
# Anything session-shaped (set-cookie, authorization echoes, request ids that
# could identify the anonymous session) is deliberately excluded so published
# snapshots never carry session state.
ARCHIVED_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "cache-control",
        "etag",
        "last-modified",
        "date",
        "server",
        "retry-after",
        "cf-ray",
        "x-cache",
    }
)


def safe_response_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Filter response headers down to a non-sensitive archival set."""
    return {
        name.lower(): value
        for name, value in headers.items()
        if name.lower() in ARCHIVED_RESPONSE_HEADERS
    }


_CSRF_PATTERNS = (
    re.compile(r'id=["\']csrftoken["\'][^>]*value=["\']([^"\']+)["\']', re.I),
    re.compile(r'value=["\']([^"\']+)["\'][^>]*id=["\']csrftoken["\']', re.I),
    re.compile(r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']', re.I),
)


def extract_csrf(html: str) -> Optional[str]:
    for pattern in _CSRF_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


class ForumClient:
    """Anonymous client for a single host.

    The client obtains the public CSRF token from the home page on demand. It
    never retries a denial and never sends credentials.
    """

    def __init__(
        self,
        host: str,
        base_url: str,
        config: Config,
        scope: Scope,
        transport,
        before_request: Callable[[str, str], None],
        capture=None,
        logger: Callable[[str], None] = lambda _msg: None,
    ):
        self.host = host
        self.base_url = base_url.rstrip("/")
        self.config = config
        self.scope = scope
        self.transport = transport
        self.before_request = before_request
        # capture(method, url, request_body, response, kind, context) -> ref | None
        self.capture = capture
        self.logger = logger
        self.csrf: Optional[str] = None

    def _headers(self, accept: str, json_body: bool = False) -> Dict[str, str]:
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": accept,
            "Accept-Encoding": "gzip",
            "Connection": "close",
        }
        if json_body:
            headers.update(
                {
                    "Content-Type": "application/json;charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": self.base_url,
                    "Referer": self.base_url + "/",
                    "X-CSRF-TOKEN": self.csrf or "",
                }
            )
        return headers

    def get(
        self,
        path: str,
        accept: str = "text/html",
        *,
        kind: str = "get",
        context: Optional[dict] = None,
    ):
        url = self.base_url + path
        self.scope.validate("GET", url)
        self.before_request("GET", url)
        response = self.transport.send(HttpRequest("GET", url, self._headers(accept)))
        ref = None
        if self.capture is not None:
            ref = self.capture("GET", url, None, response, kind, context or {})
        return response, ref

    def post(self, path: str, payload: dict, *, kind: str = "post", context: Optional[dict] = None):
        url = self.base_url + path
        self.scope.validate("POST", url)
        self._ensure_csrf()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.before_request("POST", url)
        headers = self._headers("application/json, text/plain, */*", json_body=True)
        response = self.transport.send(HttpRequest("POST", url, headers, body))
        ref = None
        if self.capture is not None:
            ref = self.capture("POST", url, body.decode("utf-8"), response, kind, context or {})
        return response, ref

    def _ensure_csrf(self) -> None:
        if self.csrf:
            return
        self.logger(f"{self.host}: fetching anonymous CSRF token from /")
        response, _ref = self.get("/", accept="text/html", kind="bootstrap")
        reason = denial_reason(response)
        if reason:
            err = DenialError(f"home page denied ({reason})", url=self.base_url + "/")
            err.response = response  # type: ignore[attr-defined]
            raise err
        if response.status != 200:
            raise ProtocolError(
                f"home page returned HTTP {response.status} while obtaining CSRF token",
                url=self.base_url + "/",
            )
        token = extract_csrf(decode_text(response))
        if not token:
            raise SchemaError(
                "home page did not contain a recognisable csrftoken field", url=self.base_url + "/"
            )
        self.csrf = token
