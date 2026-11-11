"""The resumable crawl.

A per-host SQLite task queue drives everything. Kinds, in priority order:

  tree         POST /categoryV2/getAccessibleCategoriesTree  -> categories + listing tasks
  sitemap      GET  /sitemap.xml                             -> sitemap_file tasks
  sitemap_file GET  <sitemap-topics-N.xml>                   -> topic-id seeds
  standalone   GET  /productUpdates/topics?page=N, /events/events?page=N
  listing      POST /contentNavigation/topics (category page)-> topics + replies tasks
  replies      POST /topic/<id>/replies                      -> replies (+ self-continuation)

Every response body is stored verbatim before any projection. Denials are
recorded and the task retries after other work (up to 3 attempts per run;
the next run revives it). Schema failures dead-letter the task and move on.
A run pops tasks until the queue drains; the next run picks up where this
one stopped.
"""

import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .errors import Denied, HttpError, NetworkError, ShapeError
from .forum import Session, now_iso
from .store import PRIORITY, canon

STANDALONE_PAGE_LIMIT = 200      # per standalone feed
TOPICS_PAGE_SIZE = 25
REPLIES_PAGE_SIZE = 20
MAX_CATEGORY_PAGES = 4000        # >100k topics guardrail


def iso_after(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


# A trailing -<digits> in a forum URL is the topic public id (Store.seed_topics).


class TopicPageParser(HTMLParser):
    """Extract the server-rendered topic object used by Sonos's actual UI.

    This follows the site's Content/TopicView/V2 component data-preact and
    data-props contract, rather than guessing from meta-description snippets.
    HTMLParser decodes the HTML-escaped JSON attribute correctly.
    """
    def __init__(self):
        super().__init__()
        self.topic = None

    def handle_starttag(self, tag, attrs):
        if self.topic is not None:
            return
        props = dict(attrs)
        if "Content/TopicView/V2/" not in props.get("data-preact", ""):
            return
        if not props.get("data-props"):
            return
        try:
            data = json.loads(props["data-props"])
        except (ValueError, TypeError) as exc:
            raise ShapeError("malformed TopicView data-props") from exc
        self.topic = data.get("topic")


class Report:
    def __init__(self, run_id):
        self.run_id = run_id
        self.started_at = now_iso()
        self.requests = 0
        self.tasks_done = 0
        self.denials = 0
        self.errors = []

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": now_iso(),
            "requests": self.requests,
            "tasks_done": self.tasks_done,
            "denials": self.denials,
            "errors": self.errors,
        }


class Crawler:
    def __init__(self, store, config, transport_factory, *, run_id="manual",
                 log=print, after_task=None):
        self.store = store
        self.config = config
        self.transport_factory = transport_factory
        self.run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(run_id))[:80]
        self.log = log
        self.sessions = {}
        self.report = Report(self.run_id)
        self.after_task = after_task or (lambda: None)
        self._deny_attempts = {}
        self._stale_reply_ids_refreshed = set()

    # ---- request plumbing ------------------------------------------------
    def _capture(self, host, method, url, request, status, body, kind):
        self.report.requests += 1
        self.store.update_host(host, last_request_at=now_iso())
        seq, _sha = self.store.capture(host, method, url, request, status,
                                       body, kind, now_iso())
        return seq

    def session(self, host):
        s = self.sessions.get(host)
        if s is None:
            s = Session(host, self.transport_factory(host),
                        self.config["user_agent"],
                        capture=lambda m, u, req, st, b, k: self._capture(host, m, u, req, st, b, k))
            self.sessions[host] = s
        return s

    # ---- scope: https on the three forum hosts, that's it ----------------
    def check_url(self, url):
        p = urlsplit(url)
        if p.scheme != "https" or (p.hostname or "").lower() not in self.config["hosts"]:
            raise ShapeError(f"refusing out-of-scope URL {url}")

    # ---- task helpers ----------------------------------------------------
    def task(self, host, kind, key, url, payload=None, offset=0):
        try:
            self.check_url(url)
        except ShapeError:
            return  # silently skip seeds pointing off the three hosts
        self.store.add_task(host, kind, key, url,
                            payload=canon(payload) if payload else "",
                            offset=offset, priority=PRIORITY[kind], at=now_iso())

    def task_payload(self, row):
        return json.loads(row["payload"]) if row["payload"] else None

    # ---- seeding ---------------------------------------------------------
    def seed(self, host):
        """Runs at the top of every run: refresh the tree + standalone feeds,
        and re-fetch the topic sitemaps once a day."""
        self.store.host_row(host)
        base = "https://" + host
        self.store.revive_task(
            host, "tree", "tree",
            base + "/categoryV2/getAccessibleCategoriesTree",
            canon({"type": [0]}), at=now_iso())
        for path in ("/productUpdates/topics?page=1", "/events/events?page=1"):
            self.store.revive_task(host, "standalone", path, base + path,
                                   at=now_iso())
        # Public groups are categories too, but only listed on /groups (HTML),
        # not in the accessible category tree.
        self.store.revive_task(host, "groups", "groups", base + "/groups",
                               at=now_iso())
        try:
            last_sitemap = float(self.store.meta(f"sitemap_{host}", "0"))
        except ValueError:
            last_sitemap = 0.0
        if time.time() - last_sitemap >= 86400:
            self.store.revive_task(host, "sitemap", "sitemap",
                                   base + "/sitemap.xml", at=now_iso())
            self.store.set_meta(f"sitemap_{host}", str(int(time.time())))

    # ---- threaded reply recovery ----------------------------------------
    def _queue_nested(self, host, topic_id, node, content_type):
        """Queue an independent, resumable pagination cursor per parent reply.

        Verified against the forum's compiled frontend: it requests the same
        /topic/{publicId}/replies endpoint with a parentId object containing
        the *internal* reply id and the topic's contentType.
        """
        children = node.get("children") or {}
        if not isinstance(children, dict):
            return
        meta = children.get("metadata") or {}
        total = meta.get("totalCount")
        if not isinstance(total, int) or total <= 0:
            return
        parent_id = node.get("id")
        if parent_id is None:
            return
        existing = self.store.db.execute(
            "SELECT COUNT(*) FROM replies WHERE host=? AND topic_public_id=? "
            "AND parent_reply_id=?",
            (host, str(topic_id), str(parent_id))).fetchone()[0]
        if existing >= total:
            return
        if not content_type:
            msg = f"{host}: nested replies for topic {topic_id} lack contentType"
            if msg not in self.report.errors:
                self.report.errors.append(msg)
            return
        key = f"{topic_id}:{parent_id}"
        payload = {
            "offset": 0, "pageSize": REPLIES_PAGE_SIZE, "sort": "oldestFirst",
            "parentId": {"id": int(parent_id), "contentType": content_type},
        }
        url = f"https://{host}/topic/{topic_id}/replies"
        task = self.store.db.execute(
            "SELECT state FROM tasks WHERE host=? AND kind='nested' AND key=?",
            (host, key)).fetchone()
        if task is not None and task["state"] == "done":
            self.store.revive_task(host, "nested", key, url,
                                   canon(payload), offset=0, at=now_iso())
        elif task is None:
            self.task(host, "nested", key, url, payload)

    def seed_missing_topic_bodies(self, host):
        """Restore incomplete sitemap stubs without re-fetching known bodies."""
        public_ids = [r[0] for r in self.store.db.execute(
            "SELECT public_id FROM topics WHERE host=? "
            "AND raw_json IS NULL AND topic_body_state='pending'", (host,))]
        self.store.queue_topic_pages_bulk(host, public_ids, now_iso())

    def seed_nested_from_index(self, host):
        """Upgrade previously archived partial threads without recrawling them."""
        topics = self.store.db.execute(
            "SELECT public_id,content_type FROM topics "
            "WHERE host=? AND replies_missing>0", (host,)).fetchall()
        for topic in topics:
            pid = topic["public_id"]
            rows = self.store.db.execute(
                "SELECT raw_json FROM replies WHERE host=? AND topic_public_id=?",
                (host, pid)).fetchall()
            for row in rows:
                if row["raw_json"]:
                    self._queue_nested(host, pid, json.loads(row["raw_json"]),
                                       topic["content_type"])
            self._reconcile_replies(host, pid)

    def _save_reply_tree(self, host, topic_id, nodes, authors, seq,
                         content_type, parent_id=None, depth=0):
        """Store all embedded descendants and schedule incomplete child lists."""
        for node in nodes:
            rid = node.get("id")
            if rid is None:
                continue
            author = authors.get(str(node.get("authorId"))) or {}
            self.store.add_reply(
                host, topic_id, rid, node.get("publicReplyId"), parent_id,
                author.get("name") or str(node.get("authorId") or ""),
                node.get("repliedAt"), depth, canon(node), seq)
            children = node.get("children") or {}
            kids = children.get("result") if isinstance(children, dict) else children
            if kids:
                self._save_reply_tree(host, topic_id, kids, authors, seq,
                                      content_type, str(rid), depth + 1)
            self._queue_nested(host, topic_id, node, content_type)

    def _reconcile_replies(self, host, topic_id):
        """Count distinct archived replies and uncollected direct children.

        Historical inclusiveVisibleReplyCount is transitive and must not be
        summed over every ancestor (that double counts missing descendants).
        """
        rows = self.store.db.execute(
            "SELECT reply_id,parent_reply_id,raw_json FROM replies "
            "WHERE host=? AND topic_public_id=?", (host, str(topic_id))).fetchall()
        direct = {}
        for row in rows:
            parent = row["parent_reply_id"]
            direct[parent] = direct.get(parent, 0) + 1
        missing = 0
        for row in rows:
            node = json.loads(row["raw_json"]) if row["raw_json"] else {}
            children = node.get("children") or {}
            meta = children.get("metadata") if isinstance(children, dict) else {}
            total = (meta or {}).get("totalCount")
            if isinstance(total, int):
                missing += max(0, total - direct.get(row["reply_id"], 0))
        self.store.set_topic_replies(
            host, topic_id, replies_seen=len(rows), replies_missing=missing)
        return missing

    def _finalize_reply_task(self, host, kind, key):
        pid = key if kind == "replies" else key.split(":", 1)[0]
        topic = self.store.topic(host, pid)
        if not topic or topic["replies_state"] == "gone":
            return
        missing = self._reconcile_replies(host, pid)
        unfinished = self.store.db.execute(
            "SELECT COUNT(*) FROM tasks WHERE host=? AND state!='done' "
            "AND ((kind='replies' AND key=?) OR "
            "(kind='nested' AND key LIKE ?))",
            (host, pid, pid + ":%")).fetchone()[0]
        if unfinished or missing:
            self.store.set_topic_replies(host, pid, replies_state="partial")
        else:
            self.store.set_topic_replies(
                host, pid, replies_state="done", replies_fetched_at=now_iso())

    # ---- main loop -------------------------------------------------------
    DENY_CAP = 3  # per-task denial attempts within one run

    def run(self, hosts):
        for host in hosts:
            self.seed(host)
            self.seed_missing_topic_bodies(host)
            self.seed_nested_from_index(host)
            self.store.revive_failed(host)
        while True:
            progressed = False
            for host in hosts:
                row = self.store.next_task(host)
                if row is None:
                    continue
                progressed = True
                try:
                    self.execute(host, row)
                except Denied as d:
                    self.store.add_denial(host, d.url, d.status, d.reason,
                                          d.snippet, now_iso())
                    self.report.denials += 1
                    key = (host, row["kind"], row["key"])
                    self._deny_attempts[key] = self._deny_attempts.get(key, 0) + 1
                    if self._deny_attempts[key] >= self.DENY_CAP:
                        # Out of retries for this run; the next run revives it.
                        self.store.set_task(host, row["kind"], row["key"],
                                            state="denied", updated_at=now_iso())
                    else:
                        # Back of the queue: try other work first.
                        self.store.set_task(host, row["kind"], row["key"],
                                            updated_at=now_iso())
                    self.log(f"[{host}] denied {d.reason} at {d.url} "
                             f"(attempt {self._deny_attempts[key]})")
                except (ShapeError, NetworkError) as e:
                    # Unknown shapes and flaky transport dead-letter the task:
                    # never guess data into the archive, never block the host.
                    self.store.set_task(host, row["kind"], row["key"], state="dead")
                    self.report.errors.append(f"{host}: {e}")
                    self.log(f"[{host}] task {row['kind']}/{row['key']} dead: {e}")
                except HttpError as e:
                    # Non-denial HTTP error on a task: dead-letter it, keep going.
                    self.store.set_task(host, row["kind"], row["key"], state="dead")
                    self.report.errors.append(f"{host}: HTTP {e.status} {e.url}")
            if not progressed:
                for host in hosts:
                    dead = self.store.db.execute(
                        "SELECT COUNT(*) FROM tasks WHERE host=? AND state='dead'",
                        (host,)).fetchone()[0]
                    if dead:
                        self.report.errors.append(
                            f"{host}: {dead} unresolved dead crawl tasks")
                return

    def execute(self, host, row):
        kind = row["kind"]
        keep_pending = getattr(self, "do_" + kind)(host, row) is False
        if not keep_pending:
            self.store.set_task(host, kind, row["key"], state="done",
                                updated_at=now_iso())
        if kind in ("replies", "nested"):
            self._finalize_reply_task(host, kind, row["key"])
        self.report.tasks_done += 1
        self.after_task()

    # ---- handlers --------------------------------------------------------
    def do_tree(self, host, row):
        r = self.session(host).post("/categoryV2/getAccessibleCategoriesTree",
                                    self.task_payload(row), kind="tree")
        body = r.json()
        nodes = body.get("0")
        if not isinstance(nodes, list):
            raise ShapeError("category tree response missing top-level node list")

        def walk(items, parent=None):
            for node in items:
                if not isinstance(node, dict) or "id" not in node:
                    raise ShapeError("category node missing id")
                node = dict(node)
                if parent:
                    node["parentId"] = parent
                self.store.upsert_category(host, node, now_iso())
                if not node.get("isContainer"):
                    cid = str(node["id"])
                    cat = self.store.category(host, cid)
                    if not cat["done"] or self._due(cat["refresh_after"]):
                        self.store.revive_task(
                            host, "listing", cid,
                            "https://" + host + "/contentNavigation/topics",
                            canon(self._listing_body(cid, 1)), offset=1,
                            at=now_iso())
                walk(node.get("children") or [], node.get("id"))

        walk(nodes)

    def _due(self, refresh_after):
        return refresh_after is not None and now_iso() >= refresh_after

    @staticmethod
    def _listing_body(cid, page):
        return {
            "enableLanguageBasedCategoryFilter": False,
            "filters": {"category": [int(cid)], "contentType": [], "productArea": []},
            "pagination": {"page": page, "pageSize": TOPICS_PAGE_SIZE},
            "sort": {"lastActivityAt": "desc", "sticky": "desc"},
            "stripHtmlFromContent": False,
            "trimContentLength": 0,
        }

    GROUP_LINK_RE = re.compile(r'/groups/([a-z0-9-]+)-(\d{1,10})"')

    def do_groups(self, host, row):
        r = self.session(host).get("/groups", kind="groups", accept="text/html")
        links = self.GROUP_LINK_RE.findall(r.text())
        if not links:
            raise ShapeError("groups page contained no group links")
        for slug, gid in links:
            self.store.upsert_category(host, {
                "id": gid, "title": slug.replace("-", " "), "isContainer": False,
                "url": "https://" + host + "/groups/" + slug + "-" + gid,
            }, now_iso())
            cat = self.store.category(host, gid)
            if not cat["done"] or self._due(cat["refresh_after"]):
                self.store.revive_task(
                    host, "listing", gid,
                    "https://" + host + "/contentNavigation/topics",
                    canon(self._listing_body(gid, 1)), offset=1, at=now_iso())

    def do_sitemap(self, host, row):
        r = self.session(host).get("/sitemap.xml", kind="sitemap",
                                   accept="application/xml,text/xml,*/*")
        locs = re.findall(r"<loc>([^<]+)</loc>", r.text())
        if not locs:
            raise ShapeError("sitemap.xml contained no <loc> entries")
        for loc in locs:
            # Only the topics sitemaps carry topic URLs; structure sitemap
            # entries are category pages already covered by the tree.
            if re.search(r"sitemap-topics-\d+\.xml$", loc):
                try:
                    self.check_url(loc)
                except ShapeError:
                    continue
                self.store.revive_task(host, "sitemap_file", loc, loc,
                                       at=now_iso())

    def do_sitemap_file(self, host, row):
        r = self.session(host).get(urlsplit(row["url"]).path, kind="sitemap_file",
                                   accept="application/xml,text/xml,*/*")
        locs = re.findall(r"<loc>([^<]+)</loc>", r.text())
        seeds = self.store.seed_topics(host, locs, now_iso())
        self.store.queue_topic_pages_bulk(host, [pid for pid, _u in seeds], now_iso())
        self.store.queue_replies_bulk(host, [pid for pid, _u in seeds], now_iso())

    def do_topic(self, host, row):
        """Fetch opening-post content absent from sitemap-only topic stubs."""
        pid = row["key"]
        old = self.store.topic(host, pid)
        if old and old["raw_json"] is not None:
            return
        path = urlsplit(row["url"])
        if path.scheme != "https" or path.hostname != host:
            raise ShapeError(f"topic URL leaves intended forum: {row['url']}")
        try:
            response = self.session(host).get_following_redirects(
                path.path + ("?" + path.query if path.query else ""),
                kind="topic", accept="text/html")
        except HttpError as exc:
            if exc.status in (403, 404):
                # A genuine Sonos forbidden error screen and a missing URL
                # are unavailable publicly; do not conflate either with a
                # successfully archived first post or a transient denial.
                self.store.set_topic_replies(
                    host, pid,
                    topic_body_state="forbidden" if exc.status == 403 else "gone")
                return
            raise
        parser = TopicPageParser()
        parser.feed(response.text())
        topic = parser.topic
        if (not isinstance(topic, dict) or str(topic.get("publicId")) != pid
                or not isinstance(topic.get("title"), str)
                or not isinstance(topic.get("content"), str)):
            raise ShapeError(f"topic {pid} has no matching first-post data")
        self.store.upsert_topic(host, pid, {
            "internal_id": str(topic["id"]) if topic.get("id") is not None else None,
            "title": topic["title"],
            "content_type": topic.get("contentType"),
            "category_id": (str(topic["categoryId"])
                            if topic.get("categoryId") is not None else None),
            "published_at": topic.get("publishedAt"),
            "author": str(topic.get("authorId") or "") or None,
            "reply_count": topic.get("replyCount"),
            "url": topic.get("fullTopicUrl") or row["url"],
        }, canon(topic), self.store.last_capture_seq(), now_iso())

    def do_standalone(self, host, row):
        parts = urlsplit(row["url"])
        path = parts.path + ("?" + parts.query if parts.query else "")
        r = self.session(host).get(path, kind="standalone", accept="application/json")
        body = r.json()
        seq = self.store.last_capture_seq()
        page = int(re.search(r"page=(\d+)", path).group(1))
        if parts.path == "/events/events":
            events = (body.get("pastEvents") or []) + (body.get("upcomingEvents") or [])
            if not isinstance(body.get("pastEvents"), list) \
                    or not isinstance(body.get("upcomingEvents"), list):
                raise ShapeError("events feed missing pastEvents/upcomingEvents")
            for e in events:
                self.store.add_event(
                    host, e["id"], e.get("title"), e.get("destinationUrl"),
                    e.get("startAt"), e.get("endAt"), e.get("createdAt"),
                    canon(e), seq)
            total_pages = max(body.get("totalPageCountPast") or 1,
                              body.get("totalPageCountUpcoming") or 1)
        else:
            topics = body.get("topics")
            if not isinstance(topics, list):
                raise ShapeError("standalone feed missing topics list")
            for t in topics:
                self._record_topic(host, t, seq)
            total_pages = body.get("totalPageCount") or 1
        if page < total_pages:
            if page >= STANDALONE_PAGE_LIMIT:
                raise ShapeError(f"standalone feed exceeds {STANDALONE_PAGE_LIMIT} pages: {path}")
            next_path = re.sub(r"page=\d+", f"page={page + 1}", path)
            self.task(host, "standalone", next_path, "https://" + host + next_path)

    def do_listing(self, host, row):
        payload = self.task_payload(row)
        cid = row["key"]
        r = self.session(host).post("/contentNavigation/topics", payload, kind="listing")
        body = r.json()
        topics = body.get("topics")
        total = body.get("total")
        if not isinstance(topics, list) or not isinstance(total, int):
            raise ShapeError("listing response missing topics/total")
        seq = self.store.last_capture_seq()
        for t in topics:
            self._record_topic(host, t, seq)
        page = payload["pagination"]["page"]
        fetched = page * payload["pagination"]["pageSize"]
        self.store.set_category(host, cid, listed_total=total)
        if fetched < total:
            if page >= MAX_CATEGORY_PAGES:
                raise ShapeError(f"category {cid} listing exceeds {MAX_CATEGORY_PAGES} pages")
            # Same task, next page: stays pending and is popped again later.
            self.store.set_task(host, "listing", cid,
                                payload=canon(self._listing_body(cid, page + 1)),
                                offset=page + 1, updated_at=now_iso())
            return False
        self.store.set_category(
            host, cid, done=1,
            refresh_after=iso_after(self.config["refresh_listings_after_seconds"]))

    def _record_topic(self, host, t, seq):
        pid = t.get("publicId")
        if pid is None:
            return
        pid = str(pid)
        author = t.get("author") or {}
        url = t.get("topicUrl") or {}
        self.store.upsert_topic(host, pid, {
            "internal_id": str(t["id"]) if t.get("id") is not None else None,
            "category_id": str(t["categoryId"]) if t.get("categoryId") else None,
            "title": t.get("title"),
            "content_type": t.get("contentType"),
            "url": url.get("destination") if isinstance(url, dict) else t.get("categoryUrl"),
            "author": (author.get("name") if isinstance(author, dict) else None)
                      or str(t.get("authorId") or "") or None,
            "published_at": t.get("publishedAt"),
            "last_activity_at": t.get("lastActivityAt"),
            "reply_count": t.get("replyCount"),
        }, canon(t), seq, now_iso())
        self._queue_replies(host, pid, activity_at=t.get("lastActivityAt"))

    def _queue_replies(self, host, public_id, activity_at=None):
        t = self.store.topic(host, public_id)
        if t is None:
            return
        if t["replies_state"] == "gone":
            return
        if t["replies_state"] == "done":
            # Refresh replies only when the topic shows new activity since our
            # last capture.
            if not activity_at or not t["replies_fetched_at"] \
                    or activity_at <= t["replies_fetched_at"]:
                return
            self.store.revive_task(
                host, "replies", str(public_id),
                f"https://{host}/topic/{public_id}/replies",
                canon({"offset": 0, "pageSize": REPLIES_PAGE_SIZE,
                       "sort": "oldestFirst"}), offset=0, at=now_iso())
            return
        self.task(host, "replies", str(public_id),
                  f"https://{host}/topic/{public_id}/replies",
                  {"offset": 0, "pageSize": REPLIES_PAGE_SIZE, "sort": "oldestFirst"},
                  offset=0)

    def do_replies(self, host, row):
        pid = row["key"]
        payload = self.task_payload(row)
        try:
            response = self.session(host).post(
                f"/topic/{pid}/replies", payload, kind="replies")
        except HttpError as exc:
            if exc.status == 404:
                self.store.set_topic_replies(host, pid, replies_state="gone")
                return
            raise
        body = response.json()
        replies = body.get("replies")
        result = replies.get("result") if isinstance(replies, dict) else None
        if not isinstance(result, list):
            raise ShapeError("replies response missing result list")
        authors = body.get("authors") or {}
        topic = self.store.topic(host, pid)
        content_type = topic["content_type"] if topic else None
        self._save_reply_tree(host, pid, result, authors,
                              self.store.last_capture_seq(), content_type)

        meta = replies.get("metadata") or {}
        has_more = bool(meta.get("hasMore"))
        offset = payload.get("offset", 0)
        visible_total = body.get("topicReplyCount")
        declared_total = meta.get("totalCount")
        # Some migrated threads have an inflated metadata.totalCount that
        # includes unavailable historical replies. The API's topicReplyCount
        # is the count of *visible* replies (including nested children), so
        # there cannot be more top-level replies than that count. Only use it
        # as an upper bound when the metadata is demonstrably inconsistent.
        if (isinstance(visible_total, int) and isinstance(declared_total, int)
                and 0 <= visible_total < declared_total
                and offset + len(result) >= visible_total):
            has_more = False
        if has_more and not result:
            raise ShapeError(f"replies for {pid} claim more pages but returned zero rows")
        self.store.set_topic_replies(
            host, pid, replies_state="partial", replies_offset=offset + len(result))
        if has_more:
            self.store.set_task(
                host, "replies", pid,
                payload=canon(dict(payload, offset=offset + len(result))),
                offset=offset + len(result), updated_at=now_iso())
            return False

    def do_nested(self, host, row):
        """Fetch children exactly as the forum's ThreadedReplies frontend does."""
        pid, sep, parent = row["key"].partition(":")
        if not sep:
            raise ShapeError(f"invalid nested task key {row['key']}")
        payload = self.task_payload(row)
        parent_row = self.store.db.execute(
            "SELECT depth FROM replies WHERE host=? AND topic_public_id=? "
            "AND reply_id=?", (host, pid, parent)).fetchone()
        if parent_row is None:
            raise ShapeError(f"nested parent {parent} missing for topic {pid}")
        try:
            response = self.session(host).post(
                f"/topic/{pid}/replies", payload, kind="nested")
        except HttpError as exc:
            if exc.status != 404 or pid in self._stale_reply_ids_refreshed:
                raise
            # Public reply IDs survive a backend migration, but private
            # parent IDs can change. Refresh the root once and let
            # Store.add_reply migrate aliases, children and pending cursors.
            self._stale_reply_ids_refreshed.add(pid)
            self.store.revive_task(
                host, "replies", pid,
                f"https://{host}/topic/{pid}/replies",
                canon({"offset": 0, "pageSize": REPLIES_PAGE_SIZE,
                       "sort": "oldestFirst"}), offset=0, at=now_iso())
            self.store.set_task(
                host, "nested", row["key"], state="dead", updated_at=now_iso())
            self.log(f"[{host}] stale nested parent {parent} for {pid}; "
                     "refreshing root replies to resolve current internal ID")
            return False
        body = response.json()
        replies = body.get("replies")
        result = replies.get("result") if isinstance(replies, dict) else None
        if not isinstance(result, list):
            raise ShapeError("nested replies response missing result list")
        meta = replies.get("metadata") or {}
        if meta.get("hasMore") and not result:
            raise ShapeError(f"nested replies for {pid}/{parent} returned empty page")
        topic = self.store.topic(host, pid)
        content_type = topic["content_type"] if topic else None
        self._save_reply_tree(
            host, pid, result, body.get("authors") or {},
            self.store.last_capture_seq(), content_type,
            parent_id=parent, depth=parent_row["depth"] + 1)
        if meta.get("hasMore"):
            offset = payload.get("offset", 0) + len(result)
            self.store.set_task(
                host, "nested", row["key"],
                payload=canon(dict(payload, offset=offset)),
                offset=offset, updated_at=now_iso())
            return False
