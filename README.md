# Sonos Community Archive

An **anonymous, incremental, public archive** of the Sonos community forums:

- `https://en.community.sonos.com` (English)
- `https://de.community.sonos.com` (German)
- `https://nl.community.sonos.com` (Dutch)

It captures every publicly visible category, topic, reply and event through
the forum's own public JSON API plus the public topic sitemaps, stores the raw
responses verbatim (content-addressed, gzip-compressed), and builds a
queryable SQLite index plus readable JSONL exports from those captures. It
runs on GitHub Actions and publishes **immutable snapshot releases**.

Python 3 standard library only. No third-party dependencies.

## What it does (and doesn't)

- **Anonymous only.** No login, no credentials, no private or account-only
  data. The crawler uses a fresh anonymous cookie jar plus the site's own
  public CSRF token, exactly like an unauthenticated browser visit. There is
  no log filtering: everything it fetches is public and stored verbatim.
- **No exclusions.** Every public topic on the three forums is in scope.
- **No media/attachment bytes.** Attachment *metadata* inside JSON responses
  (filenames, URLs) is preserved; the files themselves are never downloaded.
- **Denials are recorded, not worked around.** If the site answers
  401/403/407/429/451 or an anti-bot challenge, the response is logged in the
  `denials` table, the task rotates to the back of the queue (up to 3 attempts
  per run) and everything else keeps going. The next run retries it. If the
  site wants this archive gone, a persistent 403 wall is the answer and the
  corpus so far stays intact.
- **No robots.txt ceremony.** Requests go out as fast as the connection
  allows. If the platform objects, it can deny access; denials are recorded.
- **Honest coverage.** `coverage.json` states exactly what is complete,
  pending, denied or missing. `is_complete_mirror` is only ever `true` when
  every leaf category is fully listed, zero tasks are pending/denied and no
  nested replies are known missing.

## Architecture

```
config.json            hosts, user-agent, timeouts
sonos_archive/
  errors.py            typed failures
  forum.py             urllib transport, anonymous session + CSRF, denial
                       detection, FixtureTransport for offline replay
  store.py             SQLite index + content-addressed raw blobs (WAL,
                       every record committed immediately)
  crawl.py             task-queue crawler: tree -> sitemap -> standalone ->
                       listing -> replies; resumable continuations
  publish.py           snapshot build/verify + LocalRemote + GithubRemote
                       (draft release -> upload assets -> publish)
  cli.py               `python3 -m sonos_archive` entry point
tests/test_archive.py  offline suite against a scripted transport
```

### How a run works

1. **Restore for crawling.** The newest published `snapshot-NNNNNN`
   release's `state.tar.gz` and incremental `raw.tar.gz` are downloaded
   and checksum-verified. The crawler only needs the latest index and its
   newly captured blobs; older raw bodies stay in immutable ancestor releases.
   The separate `restore` command hydrates the **entire** raw lineage.
2. **Seed** (first run per host): category tree, `sitemap.xml`, and the two
   standalone feeds (`/productUpdates/topics`, `/events/events`).
3. **Crawl.** A SQLite task queue drives the run: the tree yields leaf
   categories → each gets a `listing` task that paginates
   `contentNavigation/topics` → every topic yields a `replies` task that
   paginates `/topic/<id>/replies`. The topics sitemaps seed ~100k topic ids
   directly. Tasks carry their own cursor (page/offset), so a kill mid-task
   resumes exactly where it stopped. Denied tasks retry up to 3× per run and
   are revived next run; failed tasks are dead-lettered, never blocking the
   queue.
4. **Publish.** Every `--checkpoint-seconds` (default 240) and at the end of
   a run, a snapshot is staged (`state.tar.gz` = index + exports + coverage;
   `raw.tar.gz` = blobs added since the last snapshot; `manifest.json` +
   `manifest.sha256`) and published. On GitHub it's a draft release, assets
   uploaded and size-checked, then the draft flag clears. Published
   snapshots are never modified or deleted; each manifest links to its
   parent (`previous`).

### Data layout

Working root (per run, rebuilt from snapshots):

```
index.sqlite            index + resumable state
raw/sha256/ab/<sha>.gz  raw response bodies, content-addressed
```

Snapshot release `snapshot-NNNNNN` assets:

```
state.tar.gz    index.sqlite + topics.jsonl + replies.jsonl + events.jsonl
                + coverage.json
raw.tar.gz      raw blobs added since the previous snapshot
manifest.json   format, sequence, lineage, exact capture cursor, asset hashes,
                counts and coverage, all from one frozen SQLite backup
manifest.sha256 checksum of manifest.json
```

SQLite tables: `hosts`, `tasks` (durable queue with per-task cursors),
`captures` (provenance spine: method, url, request body, status, blob sha),
`categories`, `topics` (incl. `replies_state`/`replies_offset` resume
cursors and `replies_missing` accounting), `replies` (nested via
`parent_reply_id`/`depth`), `events`, `denials`, `meta`.

## Usage

```
python3 -m sonos_archive capture [--host en.community.sonos.com] \
    [--remote auto|local|github] [--archive-dir archive] \
    [--checkpoint-seconds 240] [--no-publish] [--fixtures file.json]

python3 -m sonos_archive status   [--remote ...] [--archive-dir ...]
python3 -m sonos_archive restore  [--remote ...] [--archive-dir ...] --out new-dir
python3 -m sonos_archive verify   [--archive-dir ...] [--snapshot-dir dir]
python3 -m sonos_archive export   [--snapshot-dir dir | --archive-root dir] --out dir
```

`--remote auto` picks GitHub Releases when `GITHUB_TOKEN` and
`GITHUB_REPOSITORY` are set (as on Actions), else the local filesystem.

## Known limits (honest)

- **Nested replies require explicit pagination.** The initial topic response
  embeds just one child per parent. The public forum frontend fetches the
  remaining children via the same replies endpoint with
  `parentId: {"id": <internal reply id>, "contentType": <topic type>}`.
  The crawler now follows each parent independently, preserves pagination
  offsets in the durable task queue, and reconciles direct-child counts.
  When the backend reassigns internal reply IDs, it resolves them through
  stable `publicReplyId` values and preserves alias provenance. Any
  unresolved children remain visible in `missing_nested_replies`.
- A topic whose replies endpoint 404s is marked `replies_state = "gone"`.
- Denied and dead-lettered tasks are retried next run without resetting
  pagination cursors. A persistent denial remains visible in coverage.
- GitHub Actions capture jobs report failure for denials or task errors,
  **after** saving checkpoints. A green CI run only validates the offline tests.
- Full historical restore verifies the manifest and raw archive of every
  ancestor, then checks that every indexed capture's SHA has a corresponding
  raw blob. Existing historical gaps cannot be reconstructed if no prior
  release contains the original response. The command reports such gaps
  rather than silently claiming a complete archive.
- GitHub's scheduler is not a realtime SLA; runs may be delayed or dropped.
  Snapshots are the durable state, so a missed run only delays capture.

## Tests

```
python3 -m unittest -q
```

Offline only. A scripted `FixtureTransport` replays canned responses. Tests
cover the full crawl flow, denial retry/yield behaviour, schema fail-closed
dead-lettering, resume across runs, snapshot publish/restore round-trip,
incremental raw tars, tamper detection, immutability, and the events feed
shape.
