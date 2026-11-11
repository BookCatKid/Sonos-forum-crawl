"""Durable state: SQLite index + content-addressed raw captures.

Every response body is gzipped to ``raw/sha256/ab/<sha>.gz`` before its
projection is attempted, and every index write commits immediately, so a
killed run loses nothing that was already captured. Raw blobs are never
rewritten or deleted.
"""

import gzip
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 2

PRIORITY = {"tree": 10, "groups": 15, "sitemap": 20, "sitemap_file": 25,
            "standalone": 30, "listing": 40, "replies": 50}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS hosts (
    host TEXT PRIMARY KEY,
    last_request_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    host TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    url TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '',
    offset INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    state TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (host, kind, key)
);
CREATE INDEX IF NOT EXISTS tasks_pending ON tasks(state, priority);

CREATE TABLE IF NOT EXISTS captures (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    sha TEXT NOT NULL,
    host TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    request TEXT,
    status INTEGER NOT NULL,
    kind TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS captures_sha ON captures(sha);

CREATE TABLE IF NOT EXISTS categories (
    host TEXT NOT NULL,
    category_id TEXT NOT NULL,
    parent_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    url TEXT,
    is_container INTEGER NOT NULL DEFAULT 0,
    visible_topics_count INTEGER,
    listed_total INTEGER,
    done INTEGER NOT NULL DEFAULT 0,
    refresh_after TEXT,
    PRIMARY KEY (host, category_id)
);

CREATE TABLE IF NOT EXISTS topics (
    host TEXT NOT NULL,
    public_id TEXT NOT NULL,
    internal_id TEXT,
    category_id TEXT,
    title TEXT,
    content_type TEXT,
    url TEXT,
    author TEXT,
    published_at TEXT,
    last_activity_at TEXT,
    reply_count INTEGER,
    raw_json TEXT,
    capture_seq INTEGER,
    replies_state TEXT NOT NULL DEFAULT 'pending',
    replies_offset INTEGER NOT NULL DEFAULT 0,
    replies_seen INTEGER NOT NULL DEFAULT 0,
    replies_missing INTEGER NOT NULL DEFAULT 0,
    replies_fetched_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (host, public_id)
);

CREATE TABLE IF NOT EXISTS replies (
    host TEXT NOT NULL,
    topic_public_id TEXT NOT NULL,
    reply_id TEXT NOT NULL,
    public_reply_id TEXT,
    parent_reply_id TEXT,
    author TEXT,
    replied_at TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT,
    capture_seq INTEGER,
    PRIMARY KEY (host, topic_public_id, reply_id)
);

CREATE TABLE IF NOT EXISTS denials (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER,
    reason TEXT NOT NULL,
    snippet TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    host TEXT NOT NULL,
    event_id TEXT NOT NULL,
    title TEXT,
    destination_url TEXT,
    start_at TEXT,
    end_at TEXT,
    created_at TEXT,
    raw_json TEXT,
    capture_seq INTEGER,
    PRIMARY KEY (host, event_id)
);
"""

def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_root = self.root / "raw" / "sha256"
        self.raw_root.mkdir(parents=True, exist_ok=True)
        index = self.root / "index.sqlite"
        self.db = sqlite3.connect(index)
        self.db.row_factory = sqlite3.Row
        existing = {r[0] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if existing:
            # A restored snapshot from an incompatible format is refused
            # outright; the caller wipes the root and starts fresh.
            try:
                row = self.db.execute(
                    "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            except sqlite3.Error:
                row = None
            if not row or int(row[0]) != SCHEMA_VERSION:
                self.db.close()
                raise ValueError("index.sqlite has an incompatible schema version")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        self.set_meta("schema_version", SCHEMA_VERSION)

    def close(self):
        self.db.close()

    # ---- meta / hosts ---------------------------------------------------
    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))
        self.db.commit()

    def host_row(self, host):
        self.db.execute("INSERT OR IGNORE INTO hosts(host) VALUES(?)", (host,))
        self.db.commit()
        return self.db.execute("SELECT * FROM hosts WHERE host=?", (host,)).fetchone()

    def update_host(self, host, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE hosts SET {cols} WHERE host=?", (*fields.values(), host))
        self.db.commit()

    def revive_failed(self, host):
        """Retry denied and dead tasks once in a new run, preserving cursors.

        Failures must not be permanently stranded in the archive. The crawler
        still caps denial retries within each run and dead-letters failed
        attempts until the *next* run, avoiding an infinite hot loop.
        """
        self.db.execute(
            "UPDATE tasks SET state='pending' WHERE host=? AND state IN ('denied','dead')",
            (host,))
        self.db.commit()

    # ---- tasks ----------------------------------------------------------
    def add_task(self, host, kind, key, url, payload=None, offset=0, priority=100, at=""):
        """Insert a pending task if absent; resurrect a done/dead one is NOT
        automatic (callers pass a fresh key or clear state explicitly)."""
        self.db.execute(
            "INSERT OR IGNORE INTO tasks(host,kind,key,url,payload,offset,priority,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (host, kind, key, url, payload or "", offset, priority, at))
        self.db.commit()

    def next_task(self, host):
        # updated_at ordering rotates denied tasks to the back of the queue so
        # a denial never starves unrelated work.
        return self.db.execute(
            "SELECT * FROM tasks WHERE host=? AND state='pending' "
            "ORDER BY priority,updated_at,rowid LIMIT 1",
            (host,)).fetchone()

    def pending_count(self, host=None):
        q = "SELECT COUNT(*) c FROM tasks WHERE state='pending'"
        if host:
            return self.db.execute(q + " AND host=?", (host,)).fetchone()["c"]
        return self.db.execute(q).fetchone()["c"]

    def set_task(self, host, kind, key, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE tasks SET {cols} WHERE host=? AND kind=? AND key=?",
                        (*fields.values(), host, kind, key))
        self.db.commit()

    def revive_task(self, host, kind, key, url, payload=None, offset=0, at=""):
        """Reset a task to pending with a fresh payload (used for refresh)."""
        self.db.execute(
            "INSERT INTO tasks(host,kind,key,url,payload,offset,priority,state,updated_at) "
            "VALUES(?,?,?,?,?,?,?, 'pending', ?) "
            "ON CONFLICT(host,kind,key) DO UPDATE SET url=excluded.url,"
            "payload=excluded.payload,offset=excluded.offset,state='pending',updated_at=excluded.updated_at",
            (host, kind, key, url, payload or "", offset, PRIORITY.get(kind, 100), at))
        self.db.commit()

    # ---- raw captures ---------------------------------------------------
    def put_blob(self, body):
        digest = hashlib.sha256(body).hexdigest()
        path = self.raw_root / digest[:2] / (digest + ".gz")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(body, mtime=0))
            tmp.rename(path)
        return digest

    def blob_path(self, sha):
        return self.raw_root / sha[:2] / (sha + ".gz")

    def read_blob(self, sha):
        return gzip.decompress(self.blob_path(sha).read_bytes())

    def capture(self, host, method, url, request, status, body, kind, at):
        """Persist one response verbatim. Headers are deliberately not stored:
        session-shaped headers (set-cookie) must never reach a snapshot."""
        sha = self.put_blob(body)
        cur = self.db.execute(
            "INSERT INTO captures(sha,host,method,url,request,status,kind,fetched_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (sha, host, method, url, request, status, kind, at))
        self.db.commit()
        return cur.lastrowid, sha

    def last_capture_seq(self):
        return self.db.execute("SELECT COALESCE(MAX(seq),0) m FROM captures").fetchone()["m"]

    def captures_after(self, seq):
        return list(self.db.execute(
            "SELECT sha FROM captures WHERE seq > ? GROUP BY sha", (seq,)))

    # ---- categories -----------------------------------------------------
    def upsert_category(self, host, node, at):
        cid = str(node["id"])
        row = self.db.execute(
            "SELECT done, refresh_after FROM categories WHERE host=? AND category_id=?",
            (host, cid)).fetchone()
        self.db.execute(
            "INSERT INTO categories(host,category_id,parent_id,title,url,is_container,"
            "visible_topics_count,refresh_after) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(host,category_id) DO UPDATE SET parent_id=excluded.parent_id,"
            "title=excluded.title,url=excluded.url,is_container=excluded.is_container,"
            "visible_topics_count=excluded.visible_topics_count",
            (host, cid, str(node.get("parentId") or "") or None, node.get("title") or "",
             node.get("url"), 1 if node.get("isContainer") else 0,
             node.get("visibleTopicsCount"),
             row["refresh_after"] if row else None))
        self.db.commit()
        return row

    def category(self, host, cid):
        return self.db.execute(
            "SELECT * FROM categories WHERE host=? AND category_id=?", (host, cid)).fetchone()

    def categories(self, host):
        return list(self.db.execute("SELECT * FROM categories WHERE host=?", (host,)))

    def set_category(self, host, cid, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE categories SET {cols} WHERE host=? AND category_id=?",
                        (*fields.values(), host, cid))
        self.db.commit()

    # ---- topics ---------------------------------------------------------
    def upsert_topic(self, host, public_id, fields, raw_json, capture_seq, at):
        public_id = str(public_id)
        row = self.db.execute(
            "SELECT public_id FROM topics WHERE host=? AND public_id=?",
            (host, public_id)).fetchone()
        cols = {k: v for k, v in fields.items() if v is not None}
        cols.update(raw_json=raw_json, capture_seq=capture_seq, last_seen_at=at)
        if row is None:
            cols.update(host=host, public_id=public_id, first_seen_at=at)
            names = ",".join(cols)
            self.db.execute(
                f"INSERT INTO topics({names}) VALUES({','.join('?' * len(cols))})",
                tuple(cols.values()))
        else:
            sets = ",".join(f"{k}=?" for k in cols)
            self.db.execute(f"UPDATE topics SET {sets} WHERE host=? AND public_id=?",
                            (*cols.values(), host, public_id))
        self.db.commit()

    def topic(self, host, public_id):
        return self.db.execute(
            "SELECT * FROM topics WHERE host=? AND public_id=?", (host, str(public_id))).fetchone()

    def set_topic_replies(self, host, public_id, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE topics SET {cols} WHERE host=? AND public_id=?",
                        (*fields.values(), host, str(public_id)))
        self.db.commit()

    def seed_topics(self, host, urls, at):
        """Bulk-insert sitemap topic stubs in one transaction. Returns the
        parsed (public_id, url) pairs."""
        rows = []
        for url in urls:
            m = re.search(r"-(\d{1,12})/?$", urlsplit(url).path)
            if m:
                rows.append((host, m.group(1), url, at, at))
        with self.db:
            self.db.executemany(
                "INSERT INTO topics(host,public_id,url,first_seen_at,last_seen_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(host,public_id) DO NOTHING", rows)
        return [(pid, url) for _h, pid, url, _a, _b in rows]

    def queue_replies_bulk(self, host, public_ids, at):
        """Enqueue one replies task per topic id; existing tasks (done, dead
        or pending) are left untouched."""
        url = "https://" + host + "/topic/%s/replies"
        payload = canon({"offset": 0, "pageSize": 20, "sort": "oldestFirst"})
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO tasks(host,kind,key,url,payload,offset,priority,updated_at) "
                "VALUES(?,?,?,?,?,0,50,?)",
                [(host, "replies", str(pid), url % pid, payload, at)
                 for pid in public_ids])

    def topic_counts(self, host):
        row = self.db.execute(
            "SELECT COUNT(*) total,"
            "SUM(replies_state='done') done,"
            "SUM(replies_state='gone') gone,"
            "SUM(replies_state='pending' OR replies_state='partial') pending,"
            "COALESCE(SUM(replies_missing),0) missing "
            "FROM topics WHERE host=?", (host,)).fetchone()
        return dict(row)

    # ---- replies --------------------------------------------------------
    def add_reply(self, host, topic_id, reply_id, public_reply_id, parent_id,
                  author, replied_at, depth, raw_json, capture_seq):
        self.db.execute(
            "INSERT OR REPLACE INTO replies VALUES(?,?,?,?,?,?,?,?,?,?)",
            (host, str(topic_id), str(reply_id), public_reply_id, parent_id,
             author, replied_at, depth, raw_json, capture_seq))
        self.db.commit()

    def add_denial(self, host, url, status, reason, snippet, at):
        self.db.execute("INSERT INTO denials VALUES(NULL,?,?,?,?,?,?)",
                        (host, url, status, reason, snippet, at))
        self.db.commit()

    @staticmethod
    def _scalar(v):
        return v if v is None or isinstance(v, (str, int, float)) else canon(v)

    def add_event(self, host, event_id, title, destination_url, start_at,
                  end_at, created_at, raw_json, capture_seq):
        self.db.execute(
            "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?)",
            (host, str(event_id), self._scalar(title),
             self._scalar(destination_url), self._scalar(start_at),
             self._scalar(end_at), self._scalar(created_at),
             raw_json, capture_seq))
        self.db.commit()

    # ---- export ---------------------------------------------------------
    def iter_topics(self):
        return self.db.execute("SELECT * FROM topics ORDER BY host, public_id")

    def iter_replies(self):
        return self.db.execute(
            "SELECT * FROM replies ORDER BY host, topic_public_id, replied_at, reply_id")

    def counts(self):
        def c(sql):
            return self.db.execute(sql).fetchone()[0]
        return {
            "captures": c("SELECT COUNT(*) FROM captures"),
            "topics": c("SELECT COUNT(*) FROM topics"),
            "replies": c("SELECT COUNT(*) FROM replies"),
            "categories": c("SELECT COUNT(*) FROM categories"),
            "denials": c("SELECT COUNT(*) FROM denials"),
            "events": c("SELECT COUNT(*) FROM events"),
            "pending_tasks": c("SELECT COUNT(*) FROM tasks WHERE state='pending'"),
        }
