"""A small, strict robots.txt parser (RFC 9309 subset).

Only the rules needed by this project are implemented: user-agent groups,
Allow/Disallow with * and $ wildcards, and Crawl-delay. The parser is
deliberately conservative: if a matching pattern is ambiguous, the longest
matching rule wins and Allow wins ties.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RobotsRule:
    allow: bool
    pattern: str


@dataclass(frozen=True)
class RobotsRules:
    rules: Tuple[RobotsRule, ...] = ()
    crawl_delay: Optional[float] = None

    def allowed(self, path_with_query: str) -> bool:
        if not path_with_query.startswith("/"):
            path_with_query = "/" + path_with_query
        best_length = -1
        best_allow = True
        for rule in self.rules:
            if not rule.pattern:
                continue
            if _matches(rule.pattern, path_with_query):
                length = len(rule.pattern)
                if length > best_length or (length == best_length and rule.allow):
                    best_length = length
                    best_allow = rule.allow
        return best_allow

    def to_dict(self) -> dict:
        return {
            "rules": [{"allow": r.allow, "pattern": r.pattern} for r in self.rules],
            "crawl_delay": self.crawl_delay,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "RobotsRules":
        rules = []
        for item in value.get("rules", []):
            rules.append(RobotsRule(bool(item["allow"]), str(item["pattern"])))
        delay = value.get("crawl_delay")
        return cls(tuple(rules), float(delay) if delay is not None else None)


def _matches(pattern: str, path: str) -> bool:
    anchored_end = pattern.endswith("$")
    body = pattern[:-1] if anchored_end else pattern
    regex_bits = []
    for char in body:
        if char == "*":
            regex_bits.append(".*")
        else:
            regex_bits.append(re.escape(char))
    regex = "^" + "".join(regex_bits)
    if anchored_end:
        regex += "$"
    return re.match(regex, path) is not None


def _agent_matches(field: str, product_token: str, user_agent: str) -> bool:
    field = field.strip().lower()
    if field == "*":
        return True
    token = product_token.lower()
    ua = user_agent.lower()
    return bool(field) and (token.startswith(field) or field in ua)


def parse_robots(text: str, product_token: str, user_agent: str) -> RobotsRules:
    """Parse robots.txt and select the most specific matching group set."""
    groups = []
    agents: List[str] = []
    rules: List[RobotsRule] = []
    delay: Optional[float] = None

    def flush() -> None:
        nonlocal agents, rules, delay
        if agents:
            groups.append((tuple(agents), tuple(rules), delay))
        agents = []
        rules = []
        delay = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules or delay is not None:
                flush()
            agents.append(value.lower())
        elif key in ("allow", "disallow"):
            if not agents:
                continue
            if key == "disallow" and value == "":
                continue
            rules.append(RobotsRule(key == "allow", value))
        elif key == "crawl-delay":
            if not agents:
                continue
            try:
                parsed = float(value)
            except ValueError:
                continue
            if parsed >= 0:
                delay = parsed
    flush()

    specific = [g for g in groups if any(a != "*" and _agent_matches(a, product_token, user_agent) for a in g[0])]
    chosen = specific if specific else [g for g in groups if "*" in g[0]]
    merged_rules: List[RobotsRule] = []
    delays: List[float] = []
    for _agents, group_rules, group_delay in chosen:
        merged_rules.extend(group_rules)
        if group_delay is not None:
            delays.append(group_delay)
    return RobotsRules(tuple(merged_rules), max(delays) if delays else None)
