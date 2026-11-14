"""Resumable public media inventory and incremental, hash-addressed archive.

Separate from crawl snapshots: HTTP response provenance is unrelated to these
downloadable media assets. GitHub Actions stores incremental media packs and a
cumulative URL/status index in immutable media-* releases.

Only downloads from known public media CDN hosts, never arbitrary links or
private network targets supplied through forum posts. Every unavailable or
unsupported URL remains visible as a coverage gap, not a false success.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
from html.parser import HTMLParser
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import tarfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPCookieProcessor,
                            Request, build_opener)

# Fixed provider CDNs. Arbitrary external HTML links must not become SSRF.
EXACT_HOSTS = frozenset({
    "uploads-eu-west-1.insided.com",
    "uploads-eu-west-1.almostinsided.com",
    "d2r1vs3d9006ap.cloudfront.net",
    "d3odp2r1osuwn0.cloudfront.net",
    "cdn.jsdelivr.net",
    "assets.sonos.com",
    "media.sonos.com",
    "www.sonos.com",
    "cdn.sanity.io",
    "api.getguru.com",
    "content.api.getguru.com",
})
SUFFIX_HOSTS = (".googleusercontent.com", ".cloudfront.net")
EXTENSIONS = re.compile(
    r"\.(?:jpe?g|png|gif|webp|avif|svg|bmp|ico|tiff?|pdf|mp[34]|mov|"
    r"webm|ogg|wav|zip|txt|docx?|pptx?|xlsx?)(?:$|[?#])", re.I)
MAX_ITEM_BYTES = 20 * 1024 * 1024
RETRY_AFTER_SECONDS = 7 * 86400
TRANSIENT_RETRY_SECONDS = 1800  # next scheduled batch, not a week
MAX_REDIRECTS = 4


def normalize_url(url: str, base: str) -> str | None:
    url = html.unescape(url or "").strip()
    if not url or url.startswith(("data:", "javascript:", "blob:", "mailto:")):
        return None
    try:
        result = urlsplit(urljoin(base, url))
        host = (result.hostname or "").lower()
        if result.scheme not in ("https", "http") or not host:
            return None
        if result.username or result.password or result.port not in (None, 80, 443):
            return None
        try:
            # Exclude IPv4 and IPv6 literals entirely, including private,
            # metadata, loopback, and link-local destinations.
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
            return None
        path = quote(result.path, safe="/%:@!$&'()*+,;=-._~")
        query = quote(result.query, safe="/%:@!$&'()*+,;=?-._~")
        return f"{result.scheme}://{host}{path}" + (f"?{query}" if query else "")
    except (ValueError, UnicodeError):
        return None


def approved_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in EXACT_HOSTS or any(host.endswith(s) for s in SUFFIX_HOSTS)


class MediaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.assets = set()
        self._anchors = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "a":
            self._anchors.append([d.get("href"), False])
        if tag in ("img", "video", "audio", "source"):
            for key in ("src", "poster", "data-src"):
                if d.get(key):
                    self.assets.add(d[key])
            if tag == "img" and self._anchors:
                self._anchors[-1][1] = True
        if tag == "a" and d.get("href") and (
                EXTENSIONS.search(d["href"]) or "/attachment/" in d["href"]
                or "/s3_images/" in d["href"]
                or "/files/view/" in d["href"]):
            self.assets.add(d["href"])

    def handle_endtag(self, tag):
        if tag == "a" and self._anchors:
            href, had_img = self._anchors.pop()
            if href and had_img:
                self.assets.add(href)


def discover_media(db: sqlite3.Connection) -> dict[str, dict]:
    """Inspect source HTML and attachment metadata without network requests."""
    result: dict[str, dict] = {}
    for table in ("topics", "replies"):
        for host, body, topic_url in db.execute(
                f"SELECT host,raw_json,"
                + ("url" if table == "topics" else "NULL")
                + f" FROM {table} WHERE raw_json IS NOT NULL"):
            try:
                data = json.loads(body)
            except (ValueError, TypeError):
                continue
            base = topic_url or f"https://{host}/"
            parser = MediaParser()
            parser.feed(data.get("content") or "")
            for raw in parser.assets:
                u = normalize_url(raw, base)
                if u:
                    result.setdefault(u, {"origin": "embedded", "host": host})
            locale = host.partition(".")[0]
            for att in data.get("attachments") or ():
                if not isinstance(att, dict):
                    continue
                for field in ("source", "source_thumb"):
                    name = att.get(field)
                    if not isinstance(name, str) or not name:
                        continue
                    if "/" in name or "\\" in name or name in (".", ".."):
                        # Full URLs and nested paths need explicit review,
                        # never blindly concatenate untrusted attachment names.
                        continue
                    root = f"https://uploads-eu-west-1.insided.com/sonos-{locale}/attachment/"
                    u = normalize_url(root + quote(name), root)
                    if u:
                        result.setdefault(u, {"origin": "attachment", "host": host})
    return result


class AssetTooLarge(ValueError):
    """The source asset exceeds the archive per-item safety limit."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_asset(url, opener=None, max_bytes=MAX_ITEM_BYTES):
    """Return (bytes, mime) or raise; never follow arbitrary redirects."""
    if not approved_host(url):
        raise ValueError("unsupported media host")
    opener = opener or build_opener(_NoRedirect())
    visited = set()
    for _ in range(MAX_REDIRECTS + 1):
        if url in visited:
            raise ValueError("redirect loop")
        visited.add(url)
        req = Request(url, headers={
            "User-Agent": "SonosCommunityArchive/2.0 (public archive)",
            "Accept": "image/*,video/*,audio/*,application/pdf,application/octet-stream;q=0.9,*/*;q=0.5",
        })
        try:
            response = opener.open(req, timeout=20)
        except HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            response = exc
        try:
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("redirect missing Location")
                url = normalize_url(location, url)
                if not url or not approved_host(url):
                    raise ValueError("redirect to unsupported host")
                continue
            if response.status != 200:
                raise ValueError(f"unexpected HTTP {response.status}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type.startswith("text/html") or content_type in (
                    "application/xhtml+xml", "text/javascript", "application/javascript"):
                raise ValueError("received page rather than media")
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise AssetTooLarge("asset exceeds item size limit")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise AssetTooLarge("asset exceeds item size limit")
            if not body:
                raise ValueError("empty media asset")
            return body, content_type
        finally:
            response.close()
    raise ValueError("too many redirects")


def batch(index_path, previous_path, stage, max_assets=1000,
          max_bytes=350 * 1024 * 1024, max_seconds=12000, downloader=fetch_asset):
    """Build one immutable incremental pack and cumulative status index.

    Previous status index is cumulative and hash checked by the workflow.
    Offline tests may inject a fake downloader without external requests.
    """
    stage = Path(stage)
    stage.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(index_path)
    try:
        discovered = discover_media(db)
    finally:
        db.close()
    previous = {}
    if previous_path and Path(previous_path).exists():
        with gzip.open(previous_path, "rt", encoding="utf8") as f:
            previous = json.load(f)
        if not isinstance(previous, dict):
            raise ValueError("invalid previous media index")
    now = int(time.time())
    index = dict(previous)
    new_downloads = 0
    new_bytes = 0
    attempts = 0
    new_failures = 0
    archive_path = stage / "media.tar.gz"
    start = time.monotonic()
    stored_shas = set()
    throttled_hosts = {
        urlsplit(url).hostname or ""
        for url, record in previous.items()
        if (record.get("status") == "retryable"
            and record.get("http_status") in (429, 503)
            and now - record.get("checked", 0) < TRANSIENT_RETRY_SECONDS)
    }

    def priority(item):
        # The user's goal is a forum mirror: preserve the forum's own
        # attachments and embedded uploads before optional external links.
        url, meta = item
        host = urlsplit(url).hostname or ""
        first_party = ("insided.com" in host or "almostinsided.com" in host)
        return (0 if meta.get("origin") == "attachment" or first_party else 1,
                url)

    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for url, meta in sorted(discovered.items(), key=priority):
            if (urlsplit(url).hostname or "") in throttled_hosts:
                # Do not hammer a CDN after it has explicitly rate limited
                # this batch. Remaining links stay unprocessed for the next run.
                continue
            prior = index.get(url)
            if prior and prior.get("status") == "downloaded":
                continue
            if not approved_host(url):
                index[url] = {"status": "unsupported", **meta}
                continue
            cooldown = (TRANSIENT_RETRY_SECONDS
                        if prior and prior.get("status") == "retryable"
                        else RETRY_AFTER_SECONDS)
            # Older batches mislabeled assets as unavailable when the
            # *batch* byte budget was nearly exhausted. Recheck those now.
            budget_misclassified = (prior and prior.get("status") == "unavailable"
                                    and prior.get("error") ==
                                    "asset exceeds item size limit")
            if (prior and not budget_misclassified and
                    now - prior.get("checked", 0) < cooldown):
                continue
            if (attempts >= max_assets or max_bytes - new_bytes < MAX_ITEM_BYTES or
                    time.monotonic() - start >= max_seconds):
                # Still enumerate remaining unsupported links in this batch:
                # the status index must describe every known media reference,
                # even when the download budget has been reached.
                continue
            attempts += 1
            try:
                body, content_type = downloader(
                    url, max_bytes=MAX_ITEM_BYTES)
                sha = hashlib.sha256(body).hexdigest()
                if sha not in stored_shas:
                    ti = tarfile.TarInfo(f"media/sha256/{sha[:2]}/{sha}")
                    ti.mtime = 0
                    ti.size = len(body)
                    archive.addfile(ti, io.BytesIO(body))
                    stored_shas.add(sha)
                index[url] = {
                    "status": "downloaded", "sha256": sha,
                    "size": len(body), "mime": content_type,
                    "checked": now, **meta,
                }
                new_bytes += len(body)
                new_downloads += 1
            except (ValueError, HTTPError, URLError, TimeoutError, OSError) as exc:
                new_failures += 1
                if isinstance(exc, HTTPError) and exc.code in (429, 503):
                    throttled_hosts.add(urlsplit(url).hostname or "")
                # HTTP 429, temporary origin failures and transport errors
                # must not be treated like permanently missing public files.
                transient = (
                    isinstance(exc, HTTPError) and
                    (exc.code in (408, 425, 429) or exc.code >= 500)
                ) or (isinstance(exc, (URLError, TimeoutError, OSError))
                      and not isinstance(exc, HTTPError))
                index[url] = {
                    "status": ("oversized" if isinstance(exc, AssetTooLarge)
                               else "retryable" if transient else "unavailable"),
                    "error": str(exc)[:240],
                    "http_status": (exc.code if isinstance(exc, HTTPError) else None),
                    "checked": now, **meta,
                }
    with gzip.open(stage / "media-index.json.gz", "wt", encoding="utf8") as f:
        json.dump(index, f, sort_keys=True, separators=(",", ":"))
    counts = {
        "discovered": len(discovered),
        "downloaded": sum(x.get("status") == "downloaded" for u, x in index.items()
                          if u in discovered),
        "unsupported": sum(not approved_host(u) for u in discovered),
        "unavailable": sum(x.get("status") == "unavailable"
                           for u, x in index.items() if u in discovered),
        "oversized": sum(x.get("status") == "oversized"
                         for u, x in index.items() if u in discovered),
        "retryable": sum(x.get("status") == "retryable"
                         for u, x in index.items() if u in discovered),
        "unprocessed": sum(approved_host(u) and u not in index
                           for u in discovered),
        "new_downloads": new_downloads,
        "new_bytes": new_bytes,
        "attempts": attempts,
        "new_failures": new_failures,
    }
    files = {}
    for name in ("media.tar.gz", "media-index.json.gz"):
        f = stage / name
        files[name] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                       "size": f.stat().st_size}
    report = {"format": 1, "counts": counts, "assets": files, "created_at": now}
    (stage / "media-manifest.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf8")
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--index", required=True)
    p.add_argument("--previous")
    p.add_argument("--stage", required=True)
    p.add_argument("--max-assets", type=int, default=1000)
    p.add_argument("--max-bytes", type=int, default=350 * 1024 * 1024)
    p.add_argument("--max-seconds", type=int, default=12000)
    args = p.parse_args()
    report = batch(args.index, args.previous, args.stage,
                   args.max_assets, args.max_bytes, args.max_seconds)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
