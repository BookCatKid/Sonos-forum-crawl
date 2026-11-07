"""Explicit request-scope validation.

Nothing is fetched unless it passes every check here. The scope is deliberately
narrow: three hosts, a fixed method/path allow-list, no query strings, and an
explicit do-not-crawl list from config.json.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from .config import Config
from .errors import PolicyError


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern
    template: str

    def matches(self, path: str) -> bool:
        return bool(self.pattern.fullmatch(path))


def _template_to_regex(template: str) -> str:
    parts = re.split(r"(\{public_id\})", template)
    out = []
    for part in parts:
        if part == "{public_id}":
            out.append(r"(?P<public_id>[0-9]{1,20})")
        else:
            out.append(re.escape(part))
    return "".join(out)


def _canonical_route(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


class Scope:
    def __init__(self, config: Config):
        self.config = config
        self.allowed_hosts = frozenset(config.allowed_hosts)
        self.get_paths = frozenset(config.allowed_get_paths)
        self.post_routes: List[Route] = [
            Route("POST", re.compile(_template_to_regex(t)), t)
            for t in config.allowed_post_paths
        ]
        self.forbidden = frozenset(_canonical_route(u) for u in config.forbidden_routes)
        self.excluded = {host: set(ids) for host, ids in config.do_not_crawl_topics.items()}
        self._reply_template = config.endpoints.replies_path_template
        self._topic_re = re.compile(_template_to_regex(self._reply_template))

    def validate(self, method: str, url: str) -> Tuple[str, str]:
        """Validate a URL/method pair. Returns (host, path).

        Raises PolicyError for anything outside the allow-list, including the
        explicit do-not-crawl route and topic ids.
        """
        method = method.upper()
        if method not in ("GET", "POST"):
            raise PolicyError(f"method not allowed: {method}", url=url)
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise PolicyError("only https is allowed", url=url)
        if parts.username or parts.password:
            raise PolicyError("URLs with userinfo are not allowed", url=url)
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise PolicyError(f"host outside scope: {host or '(none)'}", url=url)
        if parts.port not in (None, 443):
            raise PolicyError("non-standard ports are not allowed", url=url)
        if parts.fragment:
            raise PolicyError("URL fragments are not allowed", url=url)
        path = parts.path or "/"
        if len(path) + len(parts.query) > self.config.max_path_length:
            raise PolicyError("path is longer than scope.max_path_length", url=url)
        if _canonical_route(url) in self.forbidden:
            raise PolicyError("URL is on the explicit do-not-crawl route list", url=url)
        if parts.query:
            raise PolicyError("query strings are not allowed on archive endpoints", url=url)

        if method == "GET":
            if path not in self.get_paths:
                raise PolicyError(f"GET path outside scope: {path}", url=url)
        else:
            route = next((r for r in self.post_routes if r.matches(path)), None)
            if route is None:
                raise PolicyError(f"POST path outside scope: {path}", url=url)
            match = route.pattern.fullmatch(path)
            if match and "public_id" in match.groupdict():
                public_id = match.group("public_id")
                if self.is_excluded_topic(host, public_id):
                    raise PolicyError(f"topic {public_id} is on the do-not-crawl list", url=url)
        return host, path

    def is_allowed(self, method: str, url: str) -> bool:
        try:
            self.validate(method, url)
            return True
        except PolicyError:
            return False

    def is_excluded_topic(self, host: str, public_id: str) -> bool:
        return str(public_id) in self.excluded.get(host, set())

    def assert_topic_allowed(self, host: str, public_id: str) -> None:
        if self.is_excluded_topic(host, public_id):
            raise PolicyError(
                f"topic {public_id} on {host} is on the do-not-crawl list"
            )

    def replies_path(self, public_id: str) -> str:
        return self._reply_template.format(public_id=public_id)
