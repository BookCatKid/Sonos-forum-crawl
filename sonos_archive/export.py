"""Readable JSONL exports derived from the SQLite index.

Every record carries provenance: the exact capture (request + response) that
produced it, including the request body and the content-addressed raw blob.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .store import Archive


def _request_body(value: Any):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _json_field(value: Any, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _provenance(capture) -> Optional[dict]:
    if capture is None:
        return None
    return {
        "capture_seq": capture["seq"],
        "sha256": capture["sha256"],
        "blob": f"raw/sha256/{capture['sha256'][:2]}/{capture['sha256']}.gz",
        "request": {
            "method": capture["method"],
            "url": capture["url"],
            "body": _request_body(capture["request_body"]),
            "kind": capture["kind"],
        },
        "response": {
            "status": capture["status"],
            "byte_size": capture["byte_size"],
        },
        "captured_at": capture["fetched_at"],
    }


def _topic_record(topic, locale: str, capture) -> dict:
    return {
        "id": topic["public_id"],
        "internal_id": topic["internal_id"],
        "host": topic["host"],
        "locale": locale,
        "category": {
            "id": topic["category_id"],
            "name": topic["category_name"],
            "url": topic["category_url"],
        },
        "title": topic["title"],
        "author": topic["author_name"],
        "author_id": topic["author_id"],
        "published_at": topic["published_at"],
        "last_activity_at": topic["last_activity_at"],
        "url": topic["url"],
        "content_type": topic["content_type"],
        "reply_count": topic["reply_count"],
        "content": topic["content"],
        "content_format": "html",
        "sticky": bool(topic["sticky"]),
        "replies_state": topic["replies_state"],
        "replies_captured": topic["replies_captured"],
        "first_seen_at": topic["first_seen_at"],
        "last_seen_at": topic["last_seen_at"],
        "provenance": _provenance(capture),
    }


def _reply_record(reply, locale: str, capture) -> dict:
    return {
        "id": reply["public_reply_id"] or reply["reply_id"],
        "internal_id": reply["reply_id"],
        "topic_id": reply["topic_public_id"],
        "host": reply["host"],
        "locale": locale,
        "parent_id": reply["parent_reply_id"],
        "author": reply["author_name"],
        "author_id": reply["author_id"],
        "replied_at": reply["replied_at"],
        "url": reply["url"],
        "body": reply["content"],
        "body_format": "html",
        "is_best_answer": bool(reply["is_best_answer"]),
        "highlighted": bool(reply["highlighted"]),
        "attachment_references": _json_field(reply["attachments_json"], []),
        "first_seen_at": reply["first_seen_at"],
        "last_seen_at": reply["last_seen_at"],
        "provenance": _provenance(capture),
    }


def _capture_record(capture) -> dict:
    return {
        "capture_seq": capture["seq"],
        "sha256": capture["sha256"],
        "blob": f"raw/sha256/{capture['sha256'][:2]}/{capture['sha256']}.gz",
        "host": capture["host"],
        "kind": capture["kind"],
        "context": _json_field(capture["context"], {}),
        "request": {
            "method": capture["method"],
            "url": capture["url"],
            "body": _request_body(capture["request_body"]),
        },
        "response": {
            "status": capture["status"],
            "byte_size": capture["byte_size"],
            "stored_encoding": capture["stored_encoding"],
        },
        "captured_at": capture["fetched_at"],
        "projected": bool(capture["projected"]),
    }


def _write_jsonl(path: Path, records) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            count += 1
    return count


def write_exports(store: Archive, out_dir) -> Dict[str, int]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    locales: Dict[str, str] = {}
    for row in store.hosts():
        locales[row["host"]] = row["locale"]

    def locale_for(host: str) -> str:
        return locales.get(host, "")

    def capture_for(seq) -> Optional[Any]:
        if seq is None:
            return None
        return store.capture_by_seq(int(seq))

    topics = _write_jsonl(
        out / "topics.jsonl",
        (
            _topic_record(t, locale_for(t["host"]), capture_for(t["last_capture_seq"]))
            for t in store.iter_topics()
        ),
    )
    replies = _write_jsonl(
        out / "replies.jsonl",
        (
            _reply_record(r, locale_for(r["host"]), capture_for(r["last_capture_seq"]))
            for r in store.iter_replies()
        ),
    )
    provenance = _write_jsonl(
        out / "raw_provenance.jsonl",
        (_capture_record(c) for c in store.scan_captures()),
    )
    coverage = store.coverage()
    (out / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "topics": topics,
        "replies": replies,
        "captures": provenance,
        "coverage_complete": 1 if coverage["is_complete_mirror"] else 0,
    }
