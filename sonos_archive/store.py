"""SQLite state + queryable index.

The database is the resumable crawl state and the index. Raw response bytes are
stored separately in a content-addressed directory and are never rewritten.
Every projection is reproducible from the raw captures.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from . import util
from .schema import AuthorInfo, CategoryNode, ReplyRecord, TopicRecord

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hosts (
    host TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    locale TEXT NOT NULL,
    robots_json TEXT,
    robots_sha TEXT,
    robots_fetched_at TEXT,
    crawl_delay REAL,
    categories_state TEXT NOT NULL DEFAULT 'pending',
    sitemap_state TEXT NOT NULL DEFAULT 'pending',
    stopped_reason TEXT,
    stopped_at TEXT,
    protocol_error TEXT,
    last_request_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    host TEXT NOT NULL,
    category_id TEXT NOT NULL,
    parent_id TEXT,
    title TEXT NOT NULL,
    url TEXT,
    is_container INTEGER NOT NULL DEFAULT 0,
    visible_topics_count INTEGER,
    description TEXT,
    raw_sha TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    backfill_done INTEGER NOT NULL DEFAULT 0,
    next_page INTEGER NOT NULL DEFAULT 1,
    last_scan_at TEXT,
    last_full_scan_at TEXT,
    PRIMARY KEY (host, category_id)
);

CREATE TABLE IF NOT EXISTS topics (
    host TEXT NOT NULL,
    public_id TEXT NOT NULL,
    internal_id TEXT,
    category_id TEXT,
    category_name TEXT,
    category_url TEXT,
    title TEXT,
    content TEXT,
    content_type TEXT,
    author_id TEXT,
    author_name TEXT,
    author_url TEXT,
    published_at TEXT,
    last_activity_at TEXT,
    reply_count INTEGER NOT NULL DEFAULT 0,
    url TEXT,
    sticky INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_sha TEXT,
    last_capture_seq INTEGER,
    replies_state TEXT NOT NULL DEFAULT 'pending',
    replies_offset INTEGER NOT NULL DEFAULT 0,
    replies_total INTEGER,
    replies_captured INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (host, public_id)
);

CREATE TABLE IF NOT EXISTS replies (
    host TEXT NOT NULL,
    topic_public_id TEXT NOT NULL,
    reply_id TEXT NOT NULL,
    public_reply_id TEXT,
    parent_reply_id TEXT,
    author_id TEXT,
    author_name TEXT,
    author_url TEXT,
    content TEXT,
    replied_at TEXT,
    url TEXT,
    is_best_answer INTEGER NOT NULL DEFAULT 0,
    highlighted INTEGER NOT NULL DEFAULT 0,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    position INTEGER NOT NULL DEFAULT 0,
    children_expected INTEGER NOT NULL DEFAULT 0,
    children_missing INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_sha TEXT,
    last_capture_seq INTEGER,
    PRIMARY KEY (host, topic_public_id, reply_id)
);

CREATE TABLE IF NOT EXISTS authors (
    host TEXT NOT NULL,
    author_id TEXT NOT NULL,
    name TEXT,
    url TEXT,
    avatar TEXT,
    user_title TEXT,
    user_level INTEGER,
    raw_sha TEXT,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (host, author_id)
);

CREATE TABLE IF NOT EXISTS captures (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL,
    decoded_sha256 TEXT,
    host TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    path TEXT NOT NULL,
    request_body TEXT,
    status INTEGER NOT NULL,
    response_headers TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    decoded_size INTEGER,
    stored_encoding TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    context TEXT NOT NULL,
    projected INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS captures_sha_idx ON captures(sha256);
CREATE INDEX IF NOT EXISTS captures_host_kind_idx ON captures(host, kind);

CREATE TABLE IF NOT EXISTS denials (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER NOT NULL,
    reason TEXT NOT NULL,
    snippet TEXT,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS protocol_errors (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    capture_sha TEXT,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS excluded_topics (
    host TEXT NOT NULL,
    public_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (host, public_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    host TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    cursor INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    context TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (host, kind, key)
);
CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, priority, updated_at);

CREATE TABLE IF NOT EXISTS notes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    key TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass
class RawBlob:
    sha256: str
    path: Path
    decoded_sha256: Optional[str]
    byte_size: int
    decoded_size: Optional[int]
    encoding: str = "gzip"


class Archive:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_root = self.root / "raw" / "sha256"
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.exports_dir = self.root / "exports"
        self.db_path = self.root / "index.sqlite"
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),)
            )
        elif int(row["value"]) != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported archive schema version {row['value']}")
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self, *, checkpoint: bool = True) -> None:
        try:
            if checkpoint:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self.conn.close()

    def checkpoint(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def backup_to(self, destination: Path) -> None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_name(destination.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        dest = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()
        tmp.replace(destination)

    # ---- hosts ---------------------------------------------------------
    def init_hosts(self, host_configs: Sequence[Any]) -> None:
        now = util.now_iso()
        with self.tx() as conn:
            for item in host_configs:
                conn.execute(
                    """INSERT INTO hosts(host,base_url,locale,created_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(host) DO UPDATE SET
                         base_url=excluded.base_url, locale=excluded.locale""",
                    (item.host, item.base_url, item.locale, now),
                )

    def get_host(self, host: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM hosts WHERE host=?", (host,)).fetchone()

    def hosts(self) -> List[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM hosts ORDER BY host"))

    def update_host(self, host: str, **fields: Any) -> None:
        allowed = {
            "robots_json",
            "robots_sha",
            "robots_fetched_at",
            "crawl_delay",
            "categories_state",
            "sitemap_state",
            "stopped_reason",
            "stopped_at",
            "protocol_error",
            "last_request_at",
        }
        keys = [key for key in fields if key in allowed]
        if not keys:
            return
        assignments = ", ".join(f"{key}=?" for key in keys)
        values = [fields[key] for key in keys]
        values.append(host)
        with self.tx() as conn:
            conn.execute(f"UPDATE hosts SET {assignments} WHERE host=?", values)

    def stop_host(self, host: str, reason: str) -> None:
        now = util.now_iso()
        with self.tx() as conn:
            conn.execute(
                "UPDATE hosts SET stopped_reason=?, stopped_at=? WHERE host=?",
                (reason, now, host),
            )

    def reset_host(self, host: str) -> bool:
        with self.tx() as conn:
            cur = conn.execute(
                "UPDATE hosts SET stopped_reason=NULL, stopped_at=NULL, protocol_error=NULL WHERE host=?",
                (host,),
            )
            return cur.rowcount > 0

    # ---- raw captures --------------------------------------------------
    def put_raw(self, body: bytes, content_encoding: str = "") -> RawBlob:
        encoding = (content_encoding or "").strip().lower()
        decoded: Optional[bytes]
        if encoding in ("gzip", "x-gzip"):
            stored = body
            try:
                decoded = util.gunzip_bytes(body)
            except (OSError, EOFError):
                decoded = None
        else:
            decoded = body
            stored = util.gzip_bytes(body)
        digest = util.sha256_bytes(stored)
        path = self.raw_root / digest[:2] / (digest + ".gz")
        if path.exists():
            if path.stat().st_size != len(stored):
                raise RuntimeError(f"raw blob size mismatch for {digest}")
        else:
            util.atomic_write_bytes(path, stored)
        return RawBlob(
            sha256=digest,
            path=path,
            decoded_sha256=util.sha256_bytes(decoded) if decoded is not None else None,
            byte_size=len(stored),
            decoded_size=len(decoded) if decoded is not None else None,
        )

    def insert_capture(
        self,
        *,
        blob: RawBlob,
        host: str,
        method: str,
        url: str,
        request_body: Optional[str],
        status: int,
        headers: Dict[str, str],
        fetched_at: str,
        kind: str,
        context: Dict[str, Any],
    ) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                """INSERT INTO captures(
                     sha256,decoded_sha256,host,method,url,path,request_body,status,
                     response_headers,byte_size,decoded_size,stored_encoding,fetched_at,
                     kind,context,projected)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (
                    blob.sha256,
                    blob.decoded_sha256,
                    host,
                    method,
                    url,
                    urlsplit(url).path,
                    request_body,
                    status,
                    util.canonical_json(headers),
                    blob.byte_size,
                    blob.decoded_size,
                    blob.encoding,
                    fetched_at,
                    kind,
                    util.canonical_json(context),
                ),
            )
            return int(cur.lastrowid)

    def mark_capture_projected(self, seq: int) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE captures SET projected=1 WHERE seq=?", (seq,))

    def scan_captures(self) -> Iterable[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM captures ORDER BY seq")

    def capture_by_seq(self, seq: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM captures WHERE seq=?", (seq,)).fetchone()

    def captures_after(self, seq: int) -> List[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM captures WHERE seq > ? ORDER BY seq", (seq,))
        )

    def last_capture_seq(self) -> int:
        row = self.conn.execute("SELECT MAX(seq) AS m FROM captures").fetchone()
        return int(row["m"] or 0)

    def blob_path(self, sha256: str) -> Path:
        return self.raw_root / sha256[:2] / (sha256 + ".gz")

    # ---- index iteration (export) -------------------------------------
    def iter_topics(self) -> Iterable[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM topics ORDER BY host, public_id")

    def iter_replies(self) -> Iterable[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM replies ORDER BY host, topic_public_id, position, reply_id"
        )

    def iter_denials(self) -> Iterable[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM denials ORDER BY seq")

    # ---- coverage ------------------------------------------------------
    def coverage(self) -> Dict[str, Any]:
        """An honest coverage report. ``is_complete_mirror`` is only true when
        every host is unstopped, fully discovered and fully captured."""
        hosts: List[Dict[str, Any]] = []
        complete = True

        def one(sql: str, args: Tuple[Any, ...]) -> int:
            row = self.conn.execute(sql, args).fetchone()
            return int(row[0])

        for row in self.hosts():
            host = row["host"]
            leaf_categories = one(
                "SELECT COUNT(*) FROM categories WHERE host=? AND is_container=0", (host,)
            )
            backfilled = one(
                "SELECT COUNT(*) FROM categories WHERE host=? AND is_container=0 AND backfill_done=1",
                (host,),
            )
            topics = one("SELECT COUNT(*) FROM topics WHERE host=?", (host,))
            replies = one("SELECT COUNT(*) FROM replies WHERE host=?", (host,))
            replies_done = one(
                "SELECT COUNT(*) FROM topics WHERE host=? AND replies_state='done'", (host,)
            )
            replies_gone = one(
                "SELECT COUNT(*) FROM topics WHERE host=? AND replies_state='gone'", (host,)
            )
            replies_pending = one(
                "SELECT COUNT(*) FROM topics WHERE host=? AND replies_state IN ('pending','in_progress')",
                (host,),
            )
            missing_nested = one(
                "SELECT COALESCE(SUM(children_missing),0) FROM replies WHERE host=?", (host,)
            )
            pending_tasks = one(
                "SELECT COUNT(*) FROM tasks WHERE host=? AND state='pending'", (host,)
            )
            blocked_tasks = one(
                "SELECT COUNT(*) FROM tasks WHERE host=? AND state='blocked'", (host,)
            )
            denials = one("SELECT COUNT(*) FROM denials WHERE host=?", (host,))
            excluded = one("SELECT COUNT(*) FROM excluded_topics WHERE host=?", (host,))
            captures = one("SELECT COUNT(*) FROM captures WHERE host=?", (host,))
            stopped = row["stopped_reason"]
            categories_state = row["categories_state"]
            sitemap_state = row["sitemap_state"]
            host_complete = (
                stopped is None
                and categories_state == "done"
                and sitemap_state in ("done", "skipped")
                and leaf_categories > 0
                and leaf_categories == backfilled
                and replies_pending == 0
                and pending_tasks == 0
                and blocked_tasks == 0
                and missing_nested == 0
            )
            if not host_complete:
                complete = False
            hosts.append(
                {
                    "host": host,
                    "locale": row["locale"],
                    "stopped_reason": stopped,
                    "stopped_at": row["stopped_at"],
                    "categories_state": categories_state,
                    "sitemap_state": sitemap_state,
                    "leaf_categories": leaf_categories,
                    "leaf_categories_backfilled": backfilled,
                    "topics": topics,
                    "replies": replies,
                    "topics_replies_done": replies_done,
                    "topics_replies_gone": replies_gone,
                    "topics_replies_pending": replies_pending,
                    "missing_nested_replies": missing_nested,
                    "excluded_topics": excluded,
                    "denials": denials,
                    "pending_tasks": pending_tasks,
                    "blocked_tasks": blocked_tasks,
                    "captures": captures,
                    "complete": host_complete,
                }
            )
        return {
            "generated_at": util.now_iso(),
            "is_complete_mirror": complete,
            "note": (
                "A 'complete mirror' requires every host to be unstopped, fully "
                "discovered and fully captured. Anything less is a partial archive."
            ),
            "hosts": hosts,
        }

    # ---- tasks ---------------------------------------------------------
    def ensure_task(
        self,
        host: str,
        kind: str,
        key: str,
        context: Dict[str, Any],
        priority: int,
        cursor: int = 0,
    ) -> bool:
        now = util.now_iso()
        with self.tx() as conn:
            cur = conn.execute(
                """INSERT INTO tasks(host,kind,key,state,cursor,priority,context,attempts,updated_at)
                   VALUES(?,?,?,'pending',?,?,?,0,?)
                   ON CONFLICT(host,kind,key) DO NOTHING""",
                (host, kind, key, cursor, priority, util.canonical_json(context), now),
            )
            return cur.rowcount > 0

    def reset_task(
        self,
        host: str,
        kind: str,
        key: str,
        context: Dict[str, Any],
        priority: int,
        cursor: int = 0,
    ) -> None:
        now = util.now_iso()
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO tasks(host,kind,key,state,cursor,priority,context,attempts,updated_at)
                   VALUES(?,?,?,'pending',?,?,?,0,?)
                   ON CONFLICT(host,kind,key) DO UPDATE SET
                     state='pending', cursor=excluded.cursor, priority=excluded.priority,
                     context=excluded.context, attempts=0, last_error=NULL, updated_at=excluded.updated_at""",
                (host, kind, key, cursor, priority, util.canonical_json(context), now),
            )

    def update_task(self, host: str, kind: str, key: str, **fields: Any) -> None:
        allowed = {"state", "cursor", "priority", "context", "attempts", "last_error", "updated_at"}
        keys = [k for k in fields if k in allowed]
        if not keys:
            return
        values = [fields[k] for k in keys]
        values.extend([host, kind, key])
        with self.tx() as conn:
            conn.execute(
                f"UPDATE tasks SET {', '.join(f'{k}=?' for k in keys)} WHERE host=? AND kind=? AND key=?",
                values,
            )

    def finish_task(self, host: str, kind: str, key: str) -> None:
        self.update_task(host, kind, key, state="done", updated_at=util.now_iso())

    def block_task(self, host: str, kind: str, key: str, reason: str) -> None:
        self.update_task(
            host, kind, key, state="blocked", last_error=reason, updated_at=util.now_iso()
        )

    def task_error(self, host: str, kind: str, key: str, error: str) -> int:
        row = self.conn.execute(
            "SELECT attempts FROM tasks WHERE host=? AND kind=? AND key=?", (host, kind, key)
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        self.update_task(
            host,
            kind,
            key,
            state="pending",
            attempts=attempts,
            last_error=error,
            updated_at=util.now_iso(),
        )
        return attempts

    def next_task(self, hosts: Sequence[str]) -> Optional[sqlite3.Row]:
        if not hosts:
            return None
        placeholders = ",".join("?" for _ in hosts)
        return self.conn.execute(
            f"""SELECT * FROM tasks WHERE state='pending' AND host IN ({placeholders})
                ORDER BY priority ASC, updated_at ASC, host ASC, key ASC LIMIT 1""",
            tuple(hosts),
        ).fetchone()

    def pending_tasks(self, host: Optional[str] = None) -> List[sqlite3.Row]:
        if host:
            return list(
                self.conn.execute(
                    "SELECT * FROM tasks WHERE state='pending' AND host=?", (host,)
                )
            )
        return list(self.conn.execute("SELECT * FROM tasks WHERE state='pending'"))

    def has_pending_topics(self, host: str, category_id: str) -> bool:
        row = self.conn.execute(
            """SELECT 1 FROM tasks WHERE host=? AND kind='topics' AND state='pending'
               AND (key LIKE ? OR key LIKE ?) LIMIT 1""",
            (host, f"backfill:{category_id}:%", f"refresh:{category_id}:%"),
        ).fetchone()
        return row is not None

    def pending_task_prefix(self, host: str, kind: str, prefix: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tasks WHERE host=? AND kind=? AND state='pending' AND key LIKE ? LIMIT 1",
            (host, kind, prefix + "%"),
        ).fetchone()

    # ---- projections ---------------------------------------------------
    def upsert_author(self, host: str, author: AuthorInfo, raw_sha: str, now: str) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO authors(host,author_id,name,url,avatar,user_title,user_level,raw_sha,last_seen_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(host,author_id) DO UPDATE SET
                     name=COALESCE(excluded.name,authors.name),
                     url=COALESCE(excluded.url,authors.url),
                     avatar=COALESCE(excluded.avatar,authors.avatar),
                     user_title=COALESCE(excluded.user_title,authors.user_title),
                     user_level=COALESCE(excluded.user_level,authors.user_level),
                     raw_sha=excluded.raw_sha, last_seen_at=excluded.last_seen_at""",
                (
                    host,
                    author.author_id,
                    author.name,
                    author.url,
                    author.avatar,
                    author.user_title,
                    author.user_level,
                    raw_sha,
                    now,
                ),
            )

    def author_name(self, host: str, author_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT name FROM authors WHERE host=? AND author_id=?", (host, author_id)
        ).fetchone()
        return row["name"] if row else None

    def upsert_category_authoritative(
        self, host: str, node: CategoryNode, raw_sha: str, now: str
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO categories(
                     host,category_id,parent_id,title,url,is_container,visible_topics_count,
                     description,raw_sha,first_seen_at,last_seen_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(host,category_id) DO UPDATE SET
                     parent_id=excluded.parent_id,
                     title=excluded.title,
                     url=COALESCE(excluded.url,categories.url),
                     is_container=excluded.is_container,
                     visible_topics_count=excluded.visible_topics_count,
                     description=COALESCE(excluded.description,categories.description),
                     raw_sha=excluded.raw_sha,
                     last_seen_at=excluded.last_seen_at""",
                (
                    host,
                    node.category_id,
                    node.parent_id,
                    node.title,
                    node.url,
                    int(node.is_container),
                    node.visible_topics_count,
                    node.description,
                    raw_sha,
                    now,
                    now,
                ),
            )

    def ensure_category_from_sitemap(
        self, host: str, category_id: str, title: str, url: str, raw_sha: str, now: str
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO categories(
                     host,category_id,parent_id,title,url,is_container,visible_topics_count,
                     description,raw_sha,first_seen_at,last_seen_at)
                   VALUES(?,?,NULL,?,?,0,NULL,NULL,?,?,?)
                   ON CONFLICT(host,category_id) DO UPDATE SET
                     last_seen_at=excluded.last_seen_at,
                     url=COALESCE(categories.url,excluded.url)""",
                (host, category_id, title, url, raw_sha, now, now),
            )

    def update_category_after_page(
        self,
        host: str,
        category_id: str,
        *,
        title: Optional[str],
        backfill_done: Optional[bool],
        next_page: Optional[int],
        last_scan_at: Optional[str],
        last_full_scan_at: Optional[str],
    ) -> None:
        fields = []
        values: List[Any] = []
        if title:
            fields.append("title=?")
            values.append(title)
        if backfill_done is not None:
            fields.append("backfill_done=?")
            values.append(1 if backfill_done else 0)
        if next_page is not None:
            fields.append("next_page=?")
            values.append(next_page)
        if last_scan_at is not None:
            fields.append("last_scan_at=?")
            values.append(last_scan_at)
        if last_full_scan_at is not None:
            fields.append("last_full_scan_at=?")
            values.append(last_full_scan_at)
        if not fields:
            return
        values.extend([host, category_id])
        with self.tx() as conn:
            conn.execute(
                f"UPDATE categories SET {', '.join(fields)} WHERE host=? AND category_id=?",
                values,
            )

    def categories(self, host: str) -> List[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM categories WHERE host=? ORDER BY category_id", (host,)
            )
        )

    def category_count(self, host: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM categories WHERE host=?", (host,)
        ).fetchone()
        return int(row["n"])

    def get_topic(self, host: str, public_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM topics WHERE host=? AND public_id=?", (host, public_id)
        ).fetchone()

    def upsert_topic(
        self, host: str, topic: TopicRecord, raw_sha: str, capture_seq: int, now: str
    ) -> Tuple[Optional[sqlite3.Row], bool]:
        existing = self.get_topic(host, topic.public_id)
        changed = True
        if existing is not None:
            changed = any(
                (
                    existing["last_activity_at"] != topic.last_activity_at,
                    int(existing["reply_count"]) != topic.reply_count,
                    existing["title"] != topic.title,
                    existing["content"] != topic.content,
                    existing["category_id"] != topic.category_id,
                )
            )
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO topics(
                     host,public_id,internal_id,category_id,category_name,category_url,title,
                     content,content_type,author_id,author_name,author_url,published_at,
                     last_activity_at,reply_count,url,sticky,first_seen_at,last_seen_at,
                     raw_sha,last_capture_seq)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(host,public_id) DO UPDATE SET
                     internal_id=excluded.internal_id, category_id=excluded.category_id,
                     category_name=excluded.category_name, category_url=excluded.category_url,
                     title=excluded.title, content=excluded.content,
                     content_type=excluded.content_type, author_id=excluded.author_id,
                     author_name=COALESCE(excluded.author_name,topics.author_name),
                     author_url=COALESCE(excluded.author_url,topics.author_url),
                     published_at=excluded.published_at,
                     last_activity_at=excluded.last_activity_at,
                     reply_count=excluded.reply_count, url=excluded.url, sticky=excluded.sticky,
                     last_seen_at=excluded.last_seen_at, raw_sha=excluded.raw_sha,
                     last_capture_seq=excluded.last_capture_seq""",
                (
                    host,
                    topic.public_id,
                    topic.internal_id,
                    topic.category_id,
                    topic.category_name,
                    topic.category_url,
                    topic.title,
                    topic.content,
                    topic.content_type,
                    topic.author_id,
                    topic.author_name,
                    topic.author_url,
                    topic.published_at,
                    topic.last_activity_at,
                    topic.reply_count,
                    topic.url,
                    int(topic.sticky),
                    now,
                    now,
                    raw_sha,
                    capture_seq,
                ),
            )
        return existing, changed

    def set_topic_reply_state(
        self, host: str, public_id: str, state: str, offset: int, total: Optional[int] = None
    ) -> None:
        with self.tx() as conn:
            if total is None:
                conn.execute(
                    "UPDATE topics SET replies_state=?, replies_offset=? WHERE host=? AND public_id=?",
                    (state, offset, host, public_id),
                )
            else:
                conn.execute(
                    "UPDATE topics SET replies_state=?, replies_offset=?, replies_total=? WHERE host=? AND public_id=?",
                    (state, offset, total, host, public_id),
                )

    def update_topic_reply_counts(self, host: str, public_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM replies WHERE host=? AND topic_public_id=?",
            (host, public_id),
        ).fetchone()
        count = int(row["n"])
        with self.tx() as conn:
            conn.execute(
                "UPDATE topics SET replies_captured=? WHERE host=? AND public_id=?",
                (count, host, public_id),
            )
        return count

    def delete_topic_and_replies(self, host: str, public_id: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM replies WHERE host=? AND topic_public_id=?", (host, public_id))
            conn.execute("DELETE FROM topics WHERE host=? AND public_id=?", (host, public_id))

    def upsert_reply(
        self,
        host: str,
        topic_public_id: str,
        reply: ReplyRecord,
        author_name: Optional[str],
        author_url: Optional[str],
        raw_sha: str,
        capture_seq: int,
        now: str,
        children_missing: int = 0,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO replies(
                     host,topic_public_id,reply_id,public_reply_id,parent_reply_id,author_id,
                     author_name,author_url,content,replied_at,url,is_best_answer,highlighted,
                     attachments_json,position,children_expected,children_missing,
                     first_seen_at,last_seen_at,raw_sha,last_capture_seq)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(host,topic_public_id,reply_id) DO UPDATE SET
                     public_reply_id=excluded.public_reply_id,
                     parent_reply_id=excluded.parent_reply_id,
                     author_id=excluded.author_id,
                     author_name=COALESCE(excluded.author_name,replies.author_name),
                     author_url=COALESCE(excluded.author_url,replies.author_url),
                     content=excluded.content, replied_at=excluded.replied_at,
                     url=excluded.url,
                     is_best_answer=excluded.is_best_answer,
                     highlighted=excluded.highlighted,
                     attachments_json=excluded.attachments_json,
                     position=excluded.position,
                     children_expected=excluded.children_expected,
                     children_missing=excluded.children_missing,
                     last_seen_at=excluded.last_seen_at,
                     raw_sha=excluded.raw_sha, last_capture_seq=excluded.last_capture_seq""",
                (
                    host,
                    topic_public_id,
                    reply.reply_id,
                    reply.public_reply_id,
                    reply.parent_reply_id,
                    reply.author_id,
                    author_name,
                    author_url,
                    reply.content,
                    reply.replied_at,
                    reply.url,
                    int(reply.is_best_answer),
                    int(reply.highlighted),
                    util.canonical_json(reply.attachments),
                    reply.position,
                    reply.children_expected,
                    children_missing,
                    now,
                    now,
                    raw_sha,
                    capture_seq,
                ),
            )

    def insert_exclusion(self, host: str, public_id: str, reason: str, now: str) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO excluded_topics(host,public_id,reason,first_seen_at,last_seen_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(host,public_id) DO UPDATE SET
                     reason=excluded.reason, last_seen_at=excluded.last_seen_at""",
                (host, public_id, reason, now, now),
            )

    # ---- audit ---------------------------------------------------------
    def add_denial(
        self, host: str, url: str, status: int, reason: str, snippet: str, now: str
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO denials(host,url,status,reason,snippet,detected_at)
                   VALUES(?,?,?,?,?,?)""",
                (host, url, status, reason, snippet, now),
            )

    def add_protocol_error(
        self, host: str, url: str, kind: str, message: str, capture_sha: Optional[str], now: str
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO protocol_errors(host,url,kind,message,capture_sha,detected_at)
                   VALUES(?,?,?,?,?,?)""",
                (host, url, kind, message, capture_sha, now),
            )

    def add_note(self, host: str, key: str, note: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO notes(host,key,note,created_at) VALUES(?,?,?,?)",
                (host, key, note, util.now_iso()),
            )

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # ---- rebuild -------------------------------------------------------
    def clear_projection(self) -> None:
        with self.tx() as conn:
            for table in ("categories", "topics", "replies", "authors", "excluded_topics", "tasks", "notes"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("UPDATE hosts SET categories_state='pending', sitemap_state='pending'")

    def counts(self) -> Dict[str, int]:
        def one(sql: str) -> int:
            row = self.conn.execute(sql).fetchone()
            return int(row[0])

        return {
            "topics": one("SELECT COUNT(*) FROM topics"),
            "replies": one("SELECT COUNT(*) FROM replies"),
            "categories": one("SELECT COUNT(*) FROM categories"),
            "captures": one("SELECT COUNT(*) FROM captures"),
            "authors": one("SELECT COUNT(*) FROM authors"),
            "denials": one("SELECT COUNT(*) FROM denials"),
            "protocol_errors": one("SELECT COUNT(*) FROM protocol_errors"),
            "excluded_topics": one("SELECT COUNT(*) FROM excluded_topics"),
            "pending_tasks": one("SELECT COUNT(*) FROM tasks WHERE state='pending'"),
            "blocked_tasks": one("SELECT COUNT(*) FROM tasks WHERE state='blocked'"),
        }
