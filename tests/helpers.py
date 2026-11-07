"""Shared offline test helpers: scripted transport and synthetic fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from sonos_archive.errors import NetworkError
from sonos_archive.net import HttpRequest, HttpResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASE = "https://en.community.sonos.com"
EN_HOST = "en.community.sonos.com"


class ScriptedTransport:
    """Deterministic transport for offline tests.

    Responses are queued per ``(method, url)`` and consumed in order. Queue
    entries may be ``HttpResponse`` objects or exceptions to raise. An optional
    ``fallback`` callable handles anything not queued.
    """

    def __init__(self, fallback: Optional[Callable[[HttpRequest], Optional[HttpResponse]]] = None):
        self.queues: Dict[Tuple[str, str], List[object]] = {}
        self.requests: List[HttpRequest] = []
        self.fallback = fallback

    def add(self, method: str, url: str, body=b"", status: int = 200,
            headers: Optional[dict] = None) -> HttpResponse:
        hdrs = {k.lower(): v for k, v in (headers or {"content-type": "application/json"}).items()}
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        resp = HttpResponse(status, hdrs, body, url)
        self.queues.setdefault((method.upper(), url), []).append(resp)
        return resp

    def add_error(self, method: str, url: str, exc: Exception) -> None:
        self.queues.setdefault((method.upper(), url), []).append(exc)

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        queue = self.queues.get((request.method.upper(), request.url))
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.fallback is not None:
            resp = self.fallback(request)
            if resp is not None:
                return resp
        raise NetworkError(f"no scripted response for {request.method} {request.url}")

    def calls(self, method: str, url: str) -> int:
        return sum(
            1 for r in self.requests if r.method == method.upper() and r.url == url
        )


HOME_HTML = (
    "<html><head><title>forum</title></head><body>"
    '<input id="csrftoken" type="hidden" value="TEST-CSRF-TOKEN"/>'
    "</body></html>"
)

ROBOTS_TXT = "User-agent: *\nDisallow: /members/\nCrawl-delay: 0\n"

SITEMAP_INDEX = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<sitemap><loc>{BASE}/sitemap-structure.xml</loc></sitemap>"
    "</sitemapindex>"
)

SITEMAP_STRUCTURE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<url><loc>{BASE}/leaf-category-2</loc></url>"
    f"<url><loc>{BASE}/members/someone-5</loc></url>"
    "</urlset>"
)


def categories_payload(leaf_ids=(2,), container_id=1) -> dict:
    children = [
        {
            "id": cid,
            "type": 0,
            "title": f"Leaf {cid}",
            "description": "leaf",
            "parentId": container_id,
            "isContainer": False,
            "children": [],
            "visibleTopicsCount": 2,
            "url": f"{BASE}/leaf-{cid}-{cid}",
        }
        for cid in leaf_ids
    ]
    return {
        "0": [
            {
                "id": container_id,
                "type": 0,
                "title": "Container",
                "description": "top",
                "parentId": None,
                "isContainer": True,
                "children": children,
            }
        ]
    }


def topic_obj(public_id, internal_id=None, category_id=2, reply_count=1, title=None) -> dict:
    return {
        "id": internal_id if internal_id is not None else int(public_id) - 100000,
        "publicId": str(public_id),
        "categoryId": category_id,
        "categoryName": "Leaf",
        "categoryUrl": f"{BASE}/leaf-2",
        "title": title or f"Topic {public_id}",
        "content": f"<p>body of {public_id}</p>",
        "contentType": "question",
        "author": {"id": "42", "username": "tester", "avatar": ""},
        "publishedAt": "2026-01-02T03:04:05+00:00",
        "lastActivityAt": "2026-01-03T03:04:05+00:00",
        "replyCount": reply_count,
        "topicUrl": {"destination": f"{BASE}/leaf-2/topic-slug-{public_id}"},
        "sticky": False,
    }


def topics_payload(topics=None, total=None) -> dict:
    topics = topics if topics is not None else [topic_obj(9001), topic_obj(9002)]
    return {
        "topics": topics,
        "total": total if total is not None else len(topics),
        "authors": {
            "42": {
                "id": 42,
                "name": "tester",
                "url": f"{BASE}/members/tester-42",
                "avatar": "",
                "userTitle": "Member",
                "userLevel": 1,
            }
        },
    }


def reply_obj(reply_id, author_id="42", parent=None, children=None) -> dict:
    reply = {
        "id": reply_id,
        "publicReplyId": str(reply_id) + "9",
        "authorId": int(author_id),
        "content": f"<p>reply {reply_id}</p>",
        "repliedAt": "2026-01-02T04:05:06+00:00",
        "isBestAnswer": False,
        "highlighted": False,
        "attachments": [{"id": "att-1", "fileName": "x.png", "url": "https://cdn.example/x.png"}],
        "url": f"{BASE}/leaf-2/topic-slug-9001/index.html#reply-{reply_id}",
    }
    if children is not None:
        reply["children"] = children
    return reply


def replies_payload(replies, has_more=False, offset=0, page_size=20, total=None) -> dict:
    return {
        "replies": {
            "result": replies,
            "metadata": {
                "totalCount": total if total is not None else len(replies),
                "offset": offset,
                "pageSize": page_size,
                "hasMore": has_more,
            },
        },
        "authors": {
            "42": {
                "id": 42,
                "name": "tester",
                "url": f"{BASE}/members/tester-42",
                "avatar": "",
                "userTitle": "Member",
                "userLevel": 1,
            }
        },
    }


def base_config_dict() -> dict:
    return {
        "schema_version": 1,
        "project": {
            "name": "Test Archive",
            "contact": "test@example.com",
            "user_agent": "TestArchive/1.0 (anonymous archive; test@example.com)",
            "robots_product_token": "testarchive",
        },
        "hosts": [
            {
                "host": EN_HOST,
                "base_url": BASE,
                "locale": "en",
            }
        ],
        "politeness": {
            "min_delay_seconds": 1.0,
            "timeout_seconds": 30.0,
            "robots_cache_seconds": 86400,
        },
        "budget": {"default_max_requests": 300, "default_max_seconds": 780},
        "discovery": {
            "category_types": [0],
            "use_sitemap_structure": False,
            "topics_page_size": 25,
            "replies_page_size": 20,
        },
        "crawl": {
            "refresh_interval_seconds": 900,
            "full_rescan_seconds": 86400,
            "max_pages_per_category": 2000,
            "max_task_attempts": 3,
        },
        "endpoints": {
            "categories_path": "/categoryV2/getAccessibleCategoriesTree",
            "topics_path": "/contentNavigation/topics",
            "replies_path_template": "/topic/{public_id}/replies",
        },
        "scope": {
            "allowed_hosts": [EN_HOST],
            "allowed_get_paths": ["/", "/robots.txt", "/sitemap.xml", "/sitemap-structure.xml"],
            "allowed_post_paths": [
                "/categoryV2/getAccessibleCategoriesTree",
                "/contentNavigation/topics",
                "/topic/{public_id}/replies",
            ],
            "forbidden_routes": [f"{BASE}/ideas/topics?page=1"],
            "max_path_length": 512,
        },
        "do_not_crawl": {
            "topic_ids": {EN_HOST: ["7777"]},
            "note": "test exclusion",
        },
        "publish": {
            "release_prefix": "snapshot",
            "bundle_asset": "snapshot.tar.gz",
            "manifest_asset": "manifest.json",
            "manifest_hash_asset": "manifest.sha256",
        },
    }


def write_config(tmpdir: Path, overrides: Optional[dict] = None) -> Path:
    data = base_config_dict()
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value
    path = Path(tmpdir) / "config.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


class FakeClock:
    """Controllable monotonic clock; sleep() advances it instantly."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.sleeps: List[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def quiet(_msg: str) -> None:
    pass
