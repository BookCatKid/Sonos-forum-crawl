"""Configuration loading and validation.

All scope, politeness and do-not-crawl rules live in config.json so they can be
reviewed without reading crawler code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import ConfigError


@dataclass(frozen=True)
class HostConfig:
    host: str
    base_url: str
    locale: str


@dataclass(frozen=True)
class Politeness:
    min_delay_seconds: float
    timeout_seconds: float
    robots_cache_seconds: int


@dataclass(frozen=True)
class Budget:
    default_max_requests: int
    default_max_seconds: float


@dataclass(frozen=True)
class Discovery:
    category_types: Tuple[int, ...]
    use_sitemap_structure: bool
    topics_page_size: int
    replies_page_size: int


@dataclass(frozen=True)
class CrawlSettings:
    refresh_interval_seconds: int
    full_rescan_seconds: int
    max_pages_per_category: int
    max_task_attempts: int


@dataclass(frozen=True)
class Endpoints:
    categories_path: str
    topics_path: str
    replies_path_template: str


@dataclass(frozen=True)
class PublishSettings:
    release_prefix: str
    bundle_asset: str
    manifest_asset: str
    manifest_hash_asset: str


@dataclass
class Config:
    path: Path
    project_name: str
    contact: str
    user_agent: str
    robots_product_token: str
    hosts: List[HostConfig]
    politeness: Politeness
    budget: Budget
    discovery: Discovery
    crawl: CrawlSettings
    endpoints: Endpoints
    allowed_hosts: Tuple[str, ...]
    allowed_get_paths: Tuple[str, ...]
    allowed_post_paths: Tuple[str, ...]
    forbidden_routes: Tuple[str, ...]
    max_path_length: int
    do_not_crawl_topics: Dict[str, frozenset]
    do_not_crawl_note: str
    publish: PublishSettings

    def host(self, name: str) -> Optional[HostConfig]:
        for item in self.hosts:
            if item.host == name:
                return item
        return None

    def is_excluded_topic(self, host: str, public_id: str) -> bool:
        return str(public_id) in self.do_not_crawl_topics.get(host, frozenset())

    def category_types_body(self) -> Dict[str, Any]:
        return {"type": list(self.discovery.category_types)}


def _need(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict):
        raise ConfigError(f"{where} must be a JSON object")
    if key not in mapping:
        raise ConfigError(f"{where}.{key} is required")
    return mapping[key]


def _number(value: Any, where: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} must be a number")
    if value < minimum:
        raise ConfigError(f"{where} must be >= {minimum}")
    return float(value)


def _integer(value: Any, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} must be an integer")
    if value < minimum:
        raise ConfigError(f"{where} must be >= {minimum}")
    return value


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config is not valid JSON: {exc}")

    if not isinstance(raw, dict):
        raise ConfigError("config root must be an object")
    if _integer(raw.get("schema_version", 0), "schema_version") != 1:
        raise ConfigError("unsupported config.schema_version (expected 1)")

    project = _need(raw, "project", "config")
    name = str(_need(project, "name", "project"))
    contact = str(_need(project, "contact", "project")).strip()
    ua = str(_need(project, "user_agent", "project")).strip()
    token = str(project.get("robots_product_token", "sonoscommunityarchive")).strip().lower()
    if not contact:
        raise ConfigError("project.contact must not be empty")
    if len(ua) < 20 or "archive" not in ua.lower():
        raise ConfigError("project.user_agent must identify the archive honestly")
    if not token:
        raise ConfigError("project.robots_product_token must not be empty")

    host_items = _need(raw, "hosts", "config")
    if not isinstance(host_items, list) or not host_items:
        raise ConfigError("hosts must be a non-empty list")
    hosts: List[HostConfig] = []
    seen = set()
    for idx, item in enumerate(host_items):
        where = f"hosts[{idx}]"
        host = str(_need(item, "host", where)).strip().lower()
        base_url = str(_need(item, "base_url", where)).strip().rstrip("/")
        locale = str(_need(item, "locale", where)).strip().lower()
        if host in seen:
            raise ConfigError(f"duplicate host: {host}")
        if not base_url.startswith("https://") or base_url.split("://", 1)[1] != host:
            raise ConfigError(f"{where}.base_url must be https://{host}")
        if not locale:
            raise ConfigError(f"{where}.locale must not be empty")
        seen.add(host)
        hosts.append(HostConfig(host=host, base_url=base_url, locale=locale))

    politeness = _need(raw, "politeness", "config")
    min_delay = _number(_need(politeness, "min_delay_seconds", "politeness"), "politeness.min_delay_seconds", 1.0)
    timeout = _number(_need(politeness, "timeout_seconds", "politeness"), "politeness.timeout_seconds", 1.0)
    robots_cache = _integer(
        _need(politeness, "robots_cache_seconds", "politeness"),
        "politeness.robots_cache_seconds",
        0,
    )

    budget = _need(raw, "budget", "config")
    default_requests = _integer(
        _need(budget, "default_max_requests", "budget"),
        "budget.default_max_requests",
        1,
    )
    default_seconds = _number(
        _need(budget, "default_max_seconds", "budget"),
        "budget.default_max_seconds",
        1.0,
    )

    discovery = _need(raw, "discovery", "config")
    types = _need(discovery, "category_types", "discovery")
    if not isinstance(types, list) or not types:
        raise ConfigError("discovery.category_types must be a non-empty list")
    category_types = tuple(_integer(v, "discovery.category_types[]") for v in types)
    topics_page_size = _integer(
        _need(discovery, "topics_page_size", "discovery"),
        "discovery.topics_page_size",
        1,
    )
    replies_page_size = _integer(
        _need(discovery, "replies_page_size", "discovery"),
        "discovery.replies_page_size",
        1,
    )
    if topics_page_size > 200 or replies_page_size > 200:
        raise ConfigError("page sizes above 200 are not supported")

    crawl = _need(raw, "crawl", "config")
    refresh = _integer(
        _need(crawl, "refresh_interval_seconds", "crawl"),
        "crawl.refresh_interval_seconds",
        1,
    )
    full = _integer(
        _need(crawl, "full_rescan_seconds", "crawl"),
        "crawl.full_rescan_seconds",
        refresh,
    )
    max_pages = _integer(
        _need(crawl, "max_pages_per_category", "crawl"),
        "crawl.max_pages_per_category",
        1,
    )
    max_attempts = _integer(
        _need(crawl, "max_task_attempts", "crawl"),
        "crawl.max_task_attempts",
        1,
    )

    endpoints_raw = _need(raw, "endpoints", "config")
    endpoints = Endpoints(
        categories_path=str(_need(endpoints_raw, "categories_path", "endpoints")),
        topics_path=str(_need(endpoints_raw, "topics_path", "endpoints")),
        replies_path_template=str(_need(endpoints_raw, "replies_path_template", "endpoints")),
    )
    if not endpoints.replies_path_template.count("{public_id}") == 1:
        raise ConfigError("endpoints.replies_path_template must contain {public_id}")

    scope = _need(raw, "scope", "config")
    allowed_hosts = tuple(
        str(x).strip().lower() for x in _need(scope, "allowed_hosts", "scope")
    )
    if set(allowed_hosts) != seen:
        raise ConfigError("scope.allowed_hosts must exactly match configured hosts")
    allowed_get = tuple(str(x) for x in _need(scope, "allowed_get_paths", "scope"))
    allowed_post = tuple(str(x) for x in _need(scope, "allowed_post_paths", "scope"))
    forbidden = tuple(str(x) for x in _need(scope, "forbidden_routes", "scope"))
    max_path_length = _integer(_need(scope, "max_path_length", "scope"), "scope.max_path_length", 16)
    if endpoints.categories_path not in allowed_post:
        raise ConfigError("endpoints.categories_path must be listed in scope.allowed_post_paths")
    if endpoints.topics_path not in allowed_post:
        raise ConfigError("endpoints.topics_path must be listed in scope.allowed_post_paths")
    if endpoints.replies_path_template not in allowed_post:
        raise ConfigError("endpoints.replies_path_template must be listed in scope.allowed_post_paths")

    dnc = _need(raw, "do_not_crawl", "config")
    dnc_raw = _need(dnc, "topic_ids", "do_not_crawl")
    if not isinstance(dnc_raw, dict):
        raise ConfigError("do_not_crawl.topic_ids must be an object keyed by host")
    dnc_topics: Dict[str, frozenset] = {}
    for host, ids in dnc_raw.items():
        if host not in seen:
            raise ConfigError(f"do_not_crawl.topic_ids references unknown host: {host}")
        if not isinstance(ids, list):
            raise ConfigError(f"do_not_crawl.topic_ids.{host} must be a list")
        cleaned = []
        for value in ids:
            text = str(value).strip()
            if not text.isdigit():
                raise ConfigError(f"do_not_crawl.topic_ids.{host} contains a non-numeric id")
            cleaned.append(text)
        dnc_topics[host] = frozenset(cleaned)
    dnc_note = str(dnc.get("note", ""))

    publish_raw = _need(raw, "publish", "config")
    publish = PublishSettings(
        release_prefix=str(_need(publish_raw, "release_prefix", "publish")),
        bundle_asset=str(_need(publish_raw, "bundle_asset", "publish")),
        manifest_asset=str(_need(publish_raw, "manifest_asset", "publish")),
        manifest_hash_asset=str(_need(publish_raw, "manifest_hash_asset", "publish")),
    )
    if publish.release_prefix != "snapshot":
        raise ConfigError("publish.release_prefix must be 'snapshot' for compatibility with snapshots")

    return Config(
        path=Path(path),
        project_name=name,
        contact=contact,
        user_agent=ua,
        robots_product_token=token,
        hosts=hosts,
        politeness=Politeness(min_delay, timeout, robots_cache),
        budget=Budget(default_requests, default_seconds),
        discovery=Discovery(category_types, bool(discovery.get("use_sitemap_structure", True)), topics_page_size, replies_page_size),
        crawl=CrawlSettings(refresh, full, max_pages, max_attempts),
        endpoints=endpoints,
        allowed_hosts=allowed_hosts,
        allowed_get_paths=allowed_get,
        allowed_post_paths=allowed_post,
        forbidden_routes=forbidden,
        max_path_length=max_path_length,
        do_not_crawl_topics=dnc_topics,
        do_not_crawl_note=dnc_note,
        publish=publish,
    )
