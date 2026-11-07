"""Bounded, resumable crawler.

The crawler is deliberately a small state machine over the SQLite task queue.
Each HTTP response is stored verbatim before its projection is attempted. A
denial stops the host immediately and persists that fact; a schema mismatch
fails closed and stops the host. No code path retries a denial.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from . import util
from .config import Config, HostConfig
from .errors import (
    BudgetExhausted,
    DenialError,
    NetworkError,
    PolicyError,
    ProtocolError,
    RobotsDenied,
    SchemaError,
)
from .net import (
    ForumClient,
    HttpResponse,
    LiveTransport,
    decode_body,
    decode_text,
    denial_reason,
    denial_snippet,
    safe_response_headers,
)
from .robots import RobotsRules, parse_robots
from .schema import validate_categories, validate_replies, validate_topics
from .scope import Scope
from .store import Archive

PRIORITY_CATEGORIES = 10
PRIORITY_SITEMAP = 15
PRIORITY_REFRESH = 20
PRIORITY_REPLIES = 30
PRIORITY_BACKFILL = 40


class HostGate:
    """Per-host politeness gate. At least one second between requests."""

    def __init__(self, min_delay: float, sleep: Callable[[float], None], monotonic: Callable[[], float]):
        self.min_delay = max(1.0, float(min_delay))
        self.sleep = sleep
        self.monotonic = monotonic
        self.crawl_delay: Optional[float] = None
        self.last: Optional[float] = None

    @property
    def delay(self) -> float:
        return max(self.min_delay, self.crawl_delay or 0.0)

    def set_crawl_delay(self, delay: Optional[float]) -> None:
        self.crawl_delay = delay

    def wait(self) -> None:
        if self.last is None:
            self.last = self.monotonic()
            return
        target = self.last + self.delay
        now = self.monotonic()
        if now < target:
            self.sleep(target - now)
            now = target
        self.last = now


@dataclass
class CaptureRef:
    seq: int
    sha256: str


@dataclass
class RunReport:
    run_id: str
    started_at: str
    requests: int = 0
    task_pages: int = 0
    denials: int = 0
    protocol_errors: int = 0
    network_errors: int = 0
    blocked_tasks: int = 0
    budget_hit: bool = False
    stopped_hosts: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    finished_at: Optional[str] = None

    def exit_code(self) -> int:
        if self.protocol_errors or any(
            item["reason"].startswith(("protocol:", "schema:", "internal:"))
            for item in self.stopped_hosts
        ):
            return 4
        if self.denials or any(item["reason"].startswith("denied:") for item in self.stopped_hosts):
            return 3
        if self.network_errors:
            return 4
        return 0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at or util.now_iso(),
            "requests": self.requests,
            "task_pages": self.task_pages,
            "denials": self.denials,
            "protocol_errors": self.protocol_errors,
            "network_errors": self.network_errors,
            "blocked_tasks": self.blocked_tasks,
            "budget_hit": self.budget_hit,
            "stopped_hosts": self.stopped_hosts,
            "errors": self.errors,
            "exit_code": self.exit_code(),
        }


class Crawler:
    def __init__(
        self,
        archive: Archive,
        config: Config,
        transport_factory: Optional[Callable[[HostConfig], Any]] = None,
        *,
        now: Callable[[], Any] = util.utcnow,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        logger: Callable[[str], None] = lambda _m: None,
        run_id: str = "manual",
    ):
        self.archive = archive
        self.config = config
        self.scope = Scope(config)
        self.transport_factory = transport_factory or (
            lambda _hc: LiveTransport(timeout=config.politeness.timeout_seconds)
        )
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self.logger = logger
        self.run_id = util.safe_component(run_id)
        self.clients: Dict[str, ForumClient] = {}
        self.gates: Dict[str, HostGate] = {}
        self.robots: Dict[str, RobotsRules] = {}
        self.report = RunReport(run_id=self.run_id, started_at=util.iso(self.now()))
        self._max_requests = config.budget.default_max_requests
        self._max_seconds = config.budget.default_max_seconds
        self._start_monotonic = 0.0

    # ---- budget / gate -------------------------------------------------
    def _before_request(self, host: str, method: str, url: str) -> None:
        if self.report.requests >= self._max_requests:
            raise BudgetExhausted("request budget reached")
        if self._max_seconds is not None:
            elapsed = self.monotonic() - self._start_monotonic
            if elapsed >= self._max_seconds:
                raise BudgetExhausted("time budget reached")
        rules = self.robots.get(host)
        if rules is not None:
            path = urlsplit(url).path or "/"
            if not rules.allowed(path):
                raise RobotsDenied(f"robots.txt disallows {method} {path}", url=url)
        gate = self.gates.setdefault(
            host, HostGate(self.config.politeness.min_delay_seconds, self.sleep, self.monotonic)
        )
        gate.wait()
        self.report.requests += 1
        self.archive.update_host(host, last_request_at=util.iso(self.now()))

    def _client(self, host_config: HostConfig) -> ForumClient:
        client = self.clients.get(host_config.host)
        if client:
            return client
        client = ForumClient(
            host_config.host,
            host_config.base_url,
            self.config,
            self.scope,
            self.transport_factory(host_config),
            lambda method, url: self._before_request(host_config.host, method, url),
            capture=lambda method, url, body, response, kind, context: self._store_capture(
                host_config.host, method, url, body, response, kind, context
            ),
            logger=self.logger,
        )
        self.clients[host_config.host] = client
        return client

    def _store_capture(
        self,
        host: str,
        method: str,
        url: str,
        request_body: Optional[str],
        response: HttpResponse,
        kind: str,
        context: Dict[str, Any],
    ) -> CaptureRef:
        blob = self.archive.put_raw(response.body, response.header("content-encoding"))
        seq = self.archive.insert_capture(
            blob=blob,
            host=host,
            method=method,
            url=url,
            request_body=request_body,
            status=response.status,
            headers=safe_response_headers(response.headers),
            fetched_at=util.iso(self.now()),
            kind=kind,
            context=context,
        )
        return CaptureRef(seq, blob.sha256)

    # ---- denial / protocol --------------------------------------------
    def _record_denial(self, host: str, response: HttpResponse, url: str, reason: str) -> None:
        self.archive.add_denial(
            host,
            url,
            response.status,
            reason,
            denial_snippet(response),
            util.iso(self.now()),
        )
        self.archive.stop_host(host, f"denied:{reason}")
        self.report.denials += 1
        self.report.errors.append(f"{host}: denied {reason} at {url}")

    def _record_protocol(
        self,
        host: str,
        url: str,
        kind: str,
        message: str,
        capture_sha: Optional[str] = None,
    ) -> None:
        self.archive.add_protocol_error(
            host, url, kind, message, capture_sha, util.iso(self.now())
        )
        self.archive.stop_host(host, f"protocol:{kind}")
        self.archive.update_host(host, protocol_error=message)
        self.report.protocol_errors += 1
        self.report.errors.append(f"{host}: {kind}: {message}")

    def _call_json(
        self,
        host: HostConfig,
        method: str,
        path: str,
        payload: Optional[dict],
        kind: str,
        context: Dict[str, Any],
        *,
        allowed_statuses: Sequence[int] = (200,),
    ) -> Tuple[CaptureRef, int, Any]:
        client = self._client(host)
        url = host.base_url + path
        if method == "POST":
            response, ref = client.post(path, payload or {}, kind=kind, context=context)
        else:
            response, ref = client.get(path, accept="application/json", kind=kind, context=context)
        reason = denial_reason(response)
        if reason:
            self._record_denial(host.host, response, url, reason)
            err = DenialError(f"denied ({reason})", url=url)
            err.recorded = True  # type: ignore[attr-defined]
            raise err
        if response.status not in allowed_statuses:
            if response.status >= 500:
                # Transient server-side failure: leave the task pending for a
                # later run instead of stopping the host permanently.
                raise NetworkError(f"HTTP {response.status} for {url}", url=url)
            message = f"unexpected HTTP {response.status}"
            self._record_protocol(host.host, url, "http-status", message, ref.sha256)
            raise ProtocolError(message, url=url)
        if response.status != 200:
            # An allowed non-200 status (e.g. 404 for a deleted topic): the
            # capture is stored, the caller interprets it. No JSON body.
            return ref, response.status, None
        if "json" not in response.content_type:
            message = f"expected JSON, got content-type {response.content_type or '(none)'}"
            self._record_protocol(host.host, url, "content-type", message, ref.sha256)
            raise SchemaError(message, url=url)
        try:
            data = json.loads(decode_text(response))
        except (ProtocolError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            message = f"invalid JSON response: {exc}"
            self._record_protocol(host.host, url, "schema", message, ref.sha256)
            raise SchemaError(message, url=url)
        return ref, response.status, data

    def _call_text(
        self,
        host: HostConfig,
        path: str,
        kind: str,
        context: Dict[str, Any],
        *,
        allowed_statuses: Sequence[int] = (200,),
    ) -> Tuple[CaptureRef, int, str]:
        client = self._client(host)
        url = host.base_url + path
        response, ref = client.get(
            path, accept="text/plain, application/xml, text/xml", kind=kind, context=context
        )
        reason = denial_reason(response)
        if reason:
            self._record_denial(host.host, response, url, reason)
            err = DenialError(f"denied ({reason})", url=url)
            err.recorded = True  # type: ignore[attr-defined]
            raise err
        if response.status not in allowed_statuses:
            if response.status >= 500:
                raise NetworkError(f"HTTP {response.status} for {url}", url=url)
            message = f"unexpected HTTP {response.status}"
            self._record_protocol(host.host, url, "http-status", message, ref.sha256)
            raise ProtocolError(message, url=url)
        if response.status != 200:
            # Allowed non-200 (e.g. 404/410): stored, interpreted by the caller.
            return ref, response.status, ""
        try:
            text = decode_text(response)
        except ProtocolError as exc:
            self._record_protocol(host.host, url, "encoding", str(exc), ref.sha256)
            raise
        return ref, response.status, text

    # ---- robots --------------------------------------------------------
    def _robots_age(self, row) -> Optional[float]:
        if not row["robots_fetched_at"]:
            return None
        try:
            return (self.now() - util.parse_iso(row["robots_fetched_at"])).total_seconds()
        except ValueError:
            return None

    def _ensure_robots(self, host_config: HostConfig) -> None:
        row = self.archive.get_host(host_config.host)
        if row is None:
            raise ProtocolError(f"host row missing for {host_config.host}")
        gate = self.gates.setdefault(
            host_config.host,
            HostGate(self.config.politeness.min_delay_seconds, self.sleep, self.monotonic),
        )
        age = self._robots_age(row)
        if row["robots_json"] and age is not None and age < self.config.politeness.robots_cache_seconds:
            try:
                rules = RobotsRules.from_dict(json.loads(row["robots_json"]))
            except (ValueError, KeyError, TypeError) as exc:
                message = f"stored robots rules are corrupt: {exc}"
                self._record_protocol(host_config.host, host_config.base_url + "/robots.txt", "robots", message)
                raise ProtocolError(message)
            gate.set_crawl_delay(rules.crawl_delay)
            self.robots[host_config.host] = rules
            self._check_robots_scope(host_config, rules)
            self.logger(f"{host_config.host}: using cached robots.txt")
            return

        self.logger(f"{host_config.host}: fetching robots.txt")
        ref, status, text = self._call_text(
            host_config,
            "/robots.txt",
            "robots",
            {"kind": "robots"},
            allowed_statuses=(200, 404, 410),
        )
        if status in (404, 410):
            rules = RobotsRules()
            self.archive.add_note(host_config.host, "robots", f"robots.txt returned HTTP {status}; allow-all")
        else:
            rules = parse_robots(text, self.config.robots_product_token, self.config.user_agent)
        gate.set_crawl_delay(rules.crawl_delay)
        self.robots[host_config.host] = rules
        self.archive.update_host(
            host_config.host,
            robots_json=util.canonical_json(rules.to_dict()),
            robots_sha=ref.sha256,
            robots_fetched_at=util.iso(self.now()),
            crawl_delay=rules.crawl_delay,
        )
        self.archive.mark_capture_projected(ref.seq)
        self.logger(
            f"{host_config.host}: robots rules={len(rules.rules)} crawl_delay={rules.crawl_delay}"
        )
        self._check_robots_scope(host_config, rules)

    def _check_robots_scope(self, host_config: HostConfig, rules: RobotsRules) -> None:
        """Warn early if robots.txt forbids any endpoint we rely on.

        The per-request check in ``_before_request`` enforces this too; this is
        only to leave a clear note explaining why tasks will all block.
        """
        paths = [
            self.config.endpoints.categories_path,
            self.config.endpoints.topics_path,
            self.scope.replies_path("1"),
        ]
        blocked = [p for p in paths if not rules.allowed(p)]
        if blocked:
            self.archive.add_note(
                host_config.host,
                "robots-scope",
                f"robots.txt disallows required endpoint(s): {', '.join(blocked)}; "
                "tasks for this host will be blocked",
            )
            self.logger(f"{host_config.host}: robots.txt disallows {blocked}")

    # ---- seeding -------------------------------------------------------
    def _seed_host(self, host_config: HostConfig) -> None:
        host = host_config.host
        row = self.archive.get_host(host)
        if row is None or row["stopped_reason"]:
            return

        if row["categories_state"] == "pending":
            if self.archive.pending_task_prefix(host, "categories", "tree") is None:
                self.archive.ensure_task(
                    host, "categories", "tree", {"kind": "categories"}, PRIORITY_CATEGORIES
                )

        if self.config.discovery.use_sitemap_structure:
            if row["sitemap_state"] == "pending":
                if (
                    self.archive.pending_task_prefix(host, "sitemap", "index") is None
                    and self.archive.pending_task_prefix(host, "sitemap", "structure") is None
                ):
                    self.archive.ensure_task(
                        host, "sitemap", "index", {"kind": "sitemap", "stage": "index"}, PRIORITY_SITEMAP
                    )
        elif row["sitemap_state"] != "skipped":
            self.archive.update_host(host, sitemap_state="skipped")
            self.archive.add_note(host, "sitemap", "sitemap structure discovery disabled by config")

        now = self.now()
        for category in self.archive.categories(host):
            if int(category["is_container"]):
                continue
            if self.archive.has_pending_topics(host, category["category_id"]):
                continue
            if not int(category["backfill_done"]):
                self.archive.ensure_task(
                    host,
                    "topics",
                    f"backfill:{category['category_id']}:{int(category['next_page'])}",
                    {
                        "kind": "topics",
                        "category_id": category["category_id"],
                        "page": int(category["next_page"]),
                        "mode": "backfill",
                    },
                    PRIORITY_BACKFILL,
                )
                continue
            last_full = category["last_full_scan_at"]
            if not last_full or self._age_seconds(last_full) >= self.config.crawl.full_rescan_seconds:
                self.archive.update_category_after_page(
                    host,
                    category["category_id"],
                    title=None,
                    backfill_done=False,
                    next_page=1,
                    last_scan_at=None,
                    last_full_scan_at=None,
                )
                self.archive.ensure_task(
                    host,
                    "topics",
                    f"backfill:{category['category_id']}:1",
                    {
                        "kind": "topics",
                        "category_id": category["category_id"],
                        "page": 1,
                        "mode": "backfill",
                    },
                    PRIORITY_BACKFILL,
                )
                continue
            last_scan = category["last_scan_at"]
            if not last_scan or self._age_seconds(last_scan) >= self.config.crawl.refresh_interval_seconds:
                self.archive.ensure_task(
                    host,
                    "topics",
                    f"refresh:{category['category_id']}:1",
                    {
                        "kind": "topics",
                        "category_id": category["category_id"],
                        "page": 1,
                        "mode": "refresh",
                    },
                    PRIORITY_REFRESH,
                )

    def _age_seconds(self, isoTimestamp: str) -> float:
        try:
            return (self.now() - util.parse_iso(isoTimestamp)).total_seconds()
        except (ValueError, TypeError):
            return 10**12

    # ---- dispatch ------------------------------------------------------
    def _execute_task(self, task) -> None:
        host_config = self.config.host(task["host"])
        if host_config is None:
            raise ProtocolError(f"task references unknown host {task['host']}")
        kind = task["kind"]
        if task["host"] not in self.active_hosts:
            raise BudgetExhausted("host is no longer active")
        if kind == "categories":
            self._execute_categories(host_config, task)
        elif kind == "sitemap":
            self._execute_sitemap(host_config, task)
        elif kind == "topics":
            self._execute_topics(host_config, task)
        elif kind == "replies":
            self._execute_replies(host_config, task)
        else:
            raise ProtocolError(f"unknown task kind {kind}")

    def _execute_categories(self, host: HostConfig, task) -> None:
        ref, _status, data = self._call_json(
            host,
            "POST",
            self.config.endpoints.categories_path,
            self.config.category_types_body(),
            "categories",
            {"kind": "categories"},
        )
        try:
            nodes = validate_categories(data)
        except SchemaError as exc:
            self._record_protocol(host.host, host.base_url + self.config.endpoints.categories_path, "schema", str(exc), ref.sha256)
            exc.recorded = True  # type: ignore[attr-defined]
            raise
        now = util.iso(self.now())
        for node in nodes:
            self.archive.upsert_category_authoritative(host.host, node, ref.sha256, now)
        self.archive.finish_task(host.host, "categories", task["key"])
        self.archive.update_host(host.host, categories_state="done")
        self.archive.mark_capture_projected(ref.seq)
        self.report.task_pages += 1
        self.logger(f"{host.host}: category tree captured ({len(nodes)} nodes)")
        self._seed_host(host)

    def _execute_topics(self, host: HostConfig, task) -> None:
        context = json.loads(task["context"])
        category_id = str(context["category_id"])
        page = int(context["page"])
        mode = str(context["mode"])
        page_size = self.config.discovery.topics_page_size
        payload = {
            "enableLanguageBasedCategoryFilter": False,
            "filters": {"category": [int(category_id)], "contentType": [], "productArea": []},
            "pagination": {"page": page, "pageSize": page_size},
            "sort": {"lastActivityAt": "desc", "sticky": "desc"},
            "stripHtmlFromContent": False,
            "trimContentLength": 0,
        }
        ref, _status, data = self._call_json(
            host,
            "POST",
            self.config.endpoints.topics_path,
            payload,
            "topics",
            {"kind": "topics", "category_id": category_id, "page": page, "mode": mode},
        )
        try:
            topic_page = validate_topics(data)
        except SchemaError as exc:
            self._record_protocol(host.host, host.base_url + self.config.endpoints.topics_path, "schema", str(exc), ref.sha256)
            exc.recorded = True  # type: ignore[attr-defined]
            raise
        now = util.iso(self.now())
        for author in topic_page.authors.values():
            self.archive.upsert_author(host.host, author, ref.sha256, now)

        for topic in topic_page.topics:
            if self.config.is_excluded_topic(
                host.host, topic.public_id
            ) or self.config.is_excluded_topic(host.host, topic.internal_id):
                self.archive.insert_exclusion(
                    host.host, topic.public_id, "do_not_crawl.topic_ids config", now
                )
                self.archive.delete_topic_and_replies(host.host, topic.public_id)
                continue
            existing, changed = self.archive.upsert_topic(host.host, topic, ref.sha256, ref.seq, now)
            if changed:
                if topic.reply_count > 0:
                    self.archive.set_topic_reply_state(host.host, topic.public_id, "pending", 0)
                    self.archive.reset_task(
                        host.host,
                        "replies",
                        topic.public_id,
                        {"kind": "replies", "topic_public_id": topic.public_id},
                        PRIORITY_REPLIES,
                        0,
                    )
                else:
                    self.archive.set_topic_reply_state(host.host, topic.public_id, "done", 0, 0)
            elif topic.reply_count > 0 and existing is not None:
                if existing["replies_state"] != "done" or int(existing["replies_captured"]) < topic.reply_count:
                    self.archive.ensure_task(
                        host.host,
                        "replies",
                        topic.public_id,
                        {"kind": "replies", "topic_public_id": topic.public_id},
                        PRIORITY_REPLIES,
                        int(existing["replies_offset"]),
                    )

        total = topic_page.total
        has_more = bool(topic_page.topics) and page * page_size < total and page < self.config.crawl.max_pages_per_category
        category_title = topic_page.topics[0].category_name if topic_page.topics else None
        if mode == "backfill":
            if has_more:
                self.archive.update_category_after_page(
                    host.host,
                    category_id,
                    title=category_title,
                    backfill_done=False,
                    next_page=page + 1,
                    last_scan_at=None,
                    last_full_scan_at=None,
                )
                self.archive.ensure_task(
                    host.host,
                    "topics",
                    f"backfill:{category_id}:{page + 1}",
                    {"kind": "topics", "category_id": category_id, "page": page + 1, "mode": "backfill"},
                    PRIORITY_BACKFILL,
                )
            else:
                if page * page_size < total:
                    self.archive.add_note(
                        host.host,
                        f"category-page-cap:{category_id}",
                        f"stopped at page {page} (page size {page_size}) while total={total}; page cap or empty page",
                    )
                if total >= 10000 and page * page_size < total:
                    self.archive.add_note(
                        host.host,
                        f"category-total-cap:{category_id}",
                        f"API total for category {category_id} is {total}; may be capped",
                    )
                self.archive.update_category_after_page(
                    host.host,
                    category_id,
                    title=category_title,
                    backfill_done=True,
                    next_page=1,
                    last_scan_at=now,
                    last_full_scan_at=now,
                )
        else:
            self.archive.update_category_after_page(
                host.host,
                category_id,
                title=category_title,
                backfill_done=None,
                next_page=None,
                last_scan_at=now,
                last_full_scan_at=None,
            )
            if topic_page.topics and page * page_size < total:
                pass  # a refresh intentionally inspects page 1 only

        self.archive.finish_task(host.host, "topics", task["key"])
        self.archive.mark_capture_projected(ref.seq)
        self.report.task_pages += 1
        self.logger(
            f"{host.host}: topics category={category_id} page={page} mode={mode} "
            f"captured={len(topic_page.topics)} total={total}"
        )

    def _execute_replies(self, host: HostConfig, task) -> None:
        context = json.loads(task["context"])
        public_id = str(context["topic_public_id"])
        self.scope.assert_topic_allowed(host.host, public_id)
        topic = self.archive.get_topic(host.host, public_id)
        if topic is None:
            self.archive.add_note(
                host.host, f"missing-topic:{public_id}", "reply task referenced a topic not in the index"
            )
            self.archive.finish_task(host.host, "replies", task["key"])
            return
        if topic["internal_id"] and self.config.is_excluded_topic(host.host, topic["internal_id"]):
            # Defense in depth: the public id was not listed but the internal id is.
            self.archive.delete_topic_and_replies(host.host, public_id)
            self.archive.insert_exclusion(
                host.host, public_id, "do_not_crawl.topic_ids config (internal id)", util.iso(self.now())
            )
            self.archive.finish_task(host.host, "replies", task["key"])
            return
        offset = int(task["cursor"])
        page_size = self.config.discovery.replies_page_size
        # depthLevel unlocks nested replies beyond the default level; the API
        # still truncates each level, which we account for via children_missing.
        payload = {
            "offset": offset,
            "pageSize": page_size,
            "sort": "oldestFirst",
            "depthLevel": 10,
        }
        ref, status, data = self._call_json(
            host,
            "POST",
            self.scope.replies_path(public_id),
            payload,
            "replies",
            {"kind": "replies", "topic_public_id": public_id, "offset": offset},
            allowed_statuses=(200, 404),
        )
        if status == 404:
            # The topic vanished between listing and reply capture. Record it
            # honestly instead of fabricating an empty thread or stopping.
            self.archive.set_topic_reply_state(host.host, public_id, "gone", 0, 0)
            self.archive.add_note(
                host.host, f"topic-gone:{public_id}", "replies endpoint returned 404"
            )
            self.archive.finish_task(host.host, "replies", task["key"])
            self.archive.mark_capture_projected(ref.seq)
            return
        try:
            reply_page = validate_replies(data)
        except SchemaError as exc:
            self._record_protocol(
                host.host,
                host.base_url + self.scope.replies_path(public_id),
                "schema",
                str(exc),
                ref.sha256,
            )
            exc.recorded = True  # type: ignore[attr-defined]
            raise
        now = util.iso(self.now())
        for author in reply_page.authors.values():
            self.archive.upsert_author(host.host, author, ref.sha256, now)
        captured_children: Dict[str, int] = {}
        for reply in reply_page.replies:
            if reply.parent_reply_id is not None:
                captured_children[reply.parent_reply_id] = (
                    captured_children.get(reply.parent_reply_id, 0) + 1
                )
        for reply in reply_page.replies:
            author = reply_page.authors.get(reply.author_id)
            name = author.name if author else self.archive.author_name(host.host, reply.author_id)
            url = author.url if author else None
            missing = max(
                0, reply.children_expected - captured_children.get(reply.reply_id, 0)
            )
            self.archive.upsert_reply(
                host.host,
                public_id,
                reply,
                name,
                url,
                ref.sha256,
                ref.seq,
                now,
                children_missing=missing,
            )
        captured = self.archive.update_topic_reply_counts(host.host, public_id)

        if reply_page.has_more:
            next_offset = reply_page.offset + reply_page.page_size
            if next_offset <= offset or not reply_page.replies:
                message = "reply pagination did not advance"
                self._record_protocol(
                    host.host,
                    host.base_url + self.scope.replies_path(public_id),
                    "pagination",
                    message,
                    ref.sha256,
                )
                raise ProtocolError(message)
            self.archive.set_topic_reply_state(
                host.host, public_id, "in_progress", next_offset, reply_page.total_count
            )
            self.archive.update_task(
                host.host,
                "replies",
                task["key"],
                state="pending",
                cursor=next_offset,
                priority=PRIORITY_REPLIES,
                updated_at=now,
            )
        else:
            self.archive.set_topic_reply_state(
                host.host, public_id, "done", 0, reply_page.total_count
            )
            self.archive.finish_task(host.host, "replies", task["key"])
        self.archive.mark_capture_projected(ref.seq)
        self.report.task_pages += 1
        self.logger(
            f"{host.host}: replies topic={public_id} offset={offset} "
            f"captured={len(reply_page.replies)} total={reply_page.total_count} "
            f"more={reply_page.has_more} indexed={captured}"
        )

    def _execute_sitemap(self, host: HostConfig, task) -> None:
        stage = json.loads(task["context"]).get("stage", task["key"])
        if stage == "index":
            ref, status, text = self._call_text(
                host,
                "/sitemap.xml",
                "sitemap",
                {"kind": "sitemap", "stage": "index"},
                allowed_statuses=(200, 404, 410),
            )
            if status in (404, 410):
                self.archive.add_note(
                    host.host, "sitemap", f"/sitemap.xml returned HTTP {status}; structure discovery skipped"
                )
                self.archive.update_host(host.host, sitemap_state="done")
                self.archive.finish_task(host.host, "sitemap", task["key"])
                self.archive.mark_capture_projected(ref.seq)
                self.report.task_pages += 1
                self._seed_host(host)
                return
            try:
                root_tag = _sitemap_root_tag(text)
            except SchemaError as exc:
                self._record_protocol(host.host, host.base_url + "/sitemap.xml", "schema", str(exc), ref.sha256)
                exc.recorded = True  # type: ignore[attr-defined]
                raise
            if root_tag == "urlset":
                # Some deployments publish a flat urlset rather than an index.
                # Category URLs in it are still useful discovery seeds.
                self._ingest_sitemap_urlset(host, text, ref)
                self.archive.update_host(host.host, sitemap_state="done")
            elif root_tag == "sitemapindex":
                locs = _sitemap_locs(text, expected_root="sitemapindex")
                structure_url = None
                for loc in locs:
                    parts = urlsplit(loc)
                    if parts.scheme == "https" and parts.hostname == host.host and parts.path == "/sitemap-structure.xml":
                        structure_url = loc
                        break
                if structure_url is None:
                    self.archive.add_note(
                        host.host, "sitemap", "sitemap index did not list /sitemap-structure.xml"
                    )
                    self.archive.update_host(host.host, sitemap_state="done")
                else:
                    self.archive.ensure_task(
                        host.host,
                        "sitemap",
                        "structure",
                        {"kind": "sitemap", "stage": "structure"},
                        PRIORITY_SITEMAP,
                    )
            else:
                self.archive.add_note(
                    host.host, "sitemap", f"sitemap root element is {root_tag!r}; not used"
                )
                self.archive.update_host(host.host, sitemap_state="done")
            self.archive.finish_task(host.host, "sitemap", task["key"])
            self.archive.mark_capture_projected(ref.seq)
            self.report.task_pages += 1
            self._seed_host(host)
            return

        if stage == "structure":
            ref, status, text = self._call_text(
                host,
                "/sitemap-structure.xml",
                "sitemap",
                {"kind": "sitemap", "stage": "structure"},
                allowed_statuses=(200, 404, 410),
            )
            if status in (404, 410):
                self.archive.add_note(
                    host.host,
                    "sitemap",
                    f"/sitemap-structure.xml returned HTTP {status}; only the category tree is used",
                )
                self.archive.update_host(host.host, sitemap_state="done")
                self.archive.finish_task(host.host, "sitemap", task["key"])
                self.archive.mark_capture_projected(ref.seq)
                self.report.task_pages += 1
                self._seed_host(host)
                return
            try:
                locs = _sitemap_locs(text, expected_root="urlset")
            except SchemaError as exc:
                self._record_protocol(
                    host.host, host.base_url + "/sitemap-structure.xml", "schema", str(exc), ref.sha256
                )
                exc.recorded = True  # type: ignore[attr-defined]
                raise
            added, ignored = self._ingest_category_locs(host, locs, ref)
            if ignored:
                self.archive.add_note(
                    host.host,
                    "sitemap",
                    f"ignored {ignored} non-category URLs in sitemap-structure.xml",
                )
            self.archive.update_host(host.host, sitemap_state="done")
            self.archive.finish_task(host.host, "sitemap", task["key"])
            self.archive.mark_capture_projected(ref.seq)
            self.report.task_pages += 1
            self.logger(f"{host.host}: sitemap structure categories seen={added} ignored={ignored}")
            self._seed_host(host)
            return

        raise ProtocolError(f"unknown sitemap stage {stage}")

    _CATEGORY_URL_RE = re.compile(r"^/([a-z0-9][a-z0-9-]*)-([0-9]{1,20})/?$", re.I)

    def _ingest_category_locs(
        self, host: HostConfig, locs: List[str], ref: CaptureRef
    ) -> Tuple[int, int]:
        """Seed categories from sitemap URLs of the form /<slug>-<id>."""
        now = util.iso(self.now())
        added = 0
        ignored = 0
        for loc in locs:
            parts = urlsplit(loc)
            if parts.scheme != "https" or parts.hostname != host.host or parts.query:
                ignored += 1
                continue
            match = self._CATEGORY_URL_RE.match(parts.path)
            if not match:
                ignored += 1
                continue
            slug, category_id = match.group(1), match.group(2)
            title = slug.replace("-", " ")
            self.archive.ensure_category_from_sitemap(
                host.host, category_id, title, loc, ref.sha256, now
            )
            added += 1
        return added, ignored

    def _ingest_sitemap_urlset(self, host: HostConfig, text: str, ref: CaptureRef) -> None:
        locs = _sitemap_locs(text, expected_root="urlset")
        added, ignored = self._ingest_category_locs(host, locs, ref)
        self.archive.add_note(
            host.host,
            "sitemap",
            f"sitemap.xml was a flat urlset; seeded {added} categories, ignored {ignored} URLs",
        )

    # ---- main loop -----------------------------------------------------
    def run(
        self,
        *,
        max_requests: Optional[int] = None,
        max_seconds: Optional[float] = None,
        only_hosts: Optional[Sequence[str]] = None,
    ) -> RunReport:
        if max_requests is not None:
            self._max_requests = int(max_requests)
        if max_seconds is not None:
            self._max_seconds = float(max_seconds)
        self._start_monotonic = self.monotonic()
        self.archive.init_hosts(self.config.hosts)
        self.active_hosts: List[str] = []
        wanted = set(only_hosts) if only_hosts else None

        for host_config in self.config.hosts:
            if wanted is not None and host_config.host not in wanted:
                continue
            row = self.archive.get_host(host_config.host)
            if row and row["stopped_reason"]:
                self.report.stopped_hosts.append(
                    {"host": host_config.host, "reason": row["stopped_reason"]}
                )
                self.logger(f"{host_config.host}: stopped ({row['stopped_reason']}); not contacted")
                continue
            try:
                self._ensure_robots(host_config)
                self._seed_host(host_config)
                row = self.archive.get_host(host_config.host)
                if row and row["stopped_reason"]:
                    self.report.stopped_hosts.append(
                        {"host": host_config.host, "reason": row["stopped_reason"]}
                    )
                    continue
                self.active_hosts.append(host_config.host)
            except BudgetExhausted:
                self.report.budget_hit = True
                return self._finish()
            except DenialError as exc:
                self._handle_denial_exception(host_config, exc)
            except SchemaError as exc:
                self._handle_protocol_exception(host_config, "schema", exc)
            except ProtocolError as exc:
                self._handle_protocol_exception(host_config, "http", exc)
            except NetworkError as exc:
                self.report.network_errors += 1
                self.report.errors.append(f"{host_config.host}: {exc}")
            except PolicyError as exc:
                self._handle_protocol_exception(host_config, "policy", exc)

        while self.active_hosts:
            if self._max_requests is not None and self.report.requests >= self._max_requests:
                self.report.budget_hit = True
                break
            if self._max_seconds is not None and self.monotonic() - self._start_monotonic >= self._max_seconds:
                self.report.budget_hit = True
                break
            task = self.archive.next_task(self.active_hosts)
            if task is None:
                break
            host_config = self.config.host(task["host"])
            if host_config is None:
                raise ProtocolError(f"task references unknown host {task['host']}")
            try:
                self._execute_task(task)
            except BudgetExhausted:
                self.report.budget_hit = True
                break
            except RobotsDenied as exc:
                self.report.blocked_tasks += 1
                self.archive.block_task(task["host"], task["kind"], task["key"], str(exc))
                self.archive.add_note(task["host"], "robots", str(exc))
                self.logger(f"{task['host']}: robots.txt blocked {task['kind']} {task['key']}")
            except DenialError as exc:
                self._handle_denial_exception(host_config, exc)
                self._deactivate(host_config.host)
            except SchemaError as exc:
                self._handle_protocol_exception(host_config, "schema", exc)
                self._deactivate(host_config.host)
            except ProtocolError as exc:
                self._handle_protocol_exception(host_config, "http", exc)
                self._deactivate(host_config.host)
            except NetworkError as exc:
                self.report.network_errors += 1
                self.report.errors.append(f"{task['host']}: {exc}")
                attempts = self.archive.task_error(
                    host_config.host, task["kind"], task["key"], str(exc)
                )
                if attempts >= self.config.crawl.max_task_attempts:
                    self.archive.block_task(
                        task["host"],
                        task["kind"],
                        task["key"],
                        f"failed {attempts} times: {exc}",
                    )
                self._deactivate(host_config.host)
            except PolicyError as exc:
                self._handle_protocol_exception(host_config, "policy", exc)
                self._deactivate(host_config.host)
            except Exception as exc:  # fail closed on internal surprises
                self._handle_protocol_exception(
                    host_config, f"internal:{type(exc).__name__}", exc
                )
                self._deactivate(host_config.host)

        return self._finish()

    def _deactivate(self, host: str) -> None:
        if host in self.active_hosts:
            self.active_hosts.remove(host)

    def _handle_denial_exception(self, host_config: HostConfig, exc: DenialError) -> None:
        if getattr(exc, "recorded", False):
            return
        response = getattr(exc, "response", None)
        url = exc.url or host_config.base_url + "/"
        if response is not None:
            reason = denial_reason(response) or f"http-{response.status}"
            self._record_denial(host_config.host, response, url, reason)
        else:
            self._record_protocol(host_config.host, url, "denial", str(exc))

    def _handle_protocol_exception(self, host_config: HostConfig, kind: str, exc: Exception) -> None:
        if getattr(exc, "recorded", False):
            return
        self._record_protocol(host_config.host, exc.url or host_config.base_url, kind, str(exc))

    def _finish(self) -> RunReport:
        self.report.finished_at = util.iso(self.now())
        for row in self.archive.hosts():
            if row["stopped_reason"] and not any(
                item["host"] == row["host"] for item in self.report.stopped_hosts
            ):
                self.report.stopped_hosts.append(
                    {"host": row["host"], "reason": row["stopped_reason"]}
                )
        return self.report


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _sitemap_root_tag(text: str) -> str:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SchemaError(f"sitemap is not valid XML: {exc}")
    return _local_name(root.tag)


def _sitemap_locs(text: str, *, expected_root: str) -> List[str]:
    root_tag = _sitemap_root_tag(text)
    if root_tag != expected_root:
        raise SchemaError(
            f"sitemap root is {root_tag!r}, expected {expected_root!r}"
        )
    root = ET.fromstring(text)
    out: List[str] = []
    for node in root:
        if _local_name(node.tag) != "url" and expected_root == "urlset":
            continue
        for child in node:
            if _local_name(child.tag) == "loc" and child.text:
                out.append(child.text.strip())
    if expected_root == "sitemapindex" and not out:
        # A sitemap index with no children is unusual but structurally valid.
        return out
    if not out:
        raise SchemaError("sitemap contained no <loc> entries")
    return out
