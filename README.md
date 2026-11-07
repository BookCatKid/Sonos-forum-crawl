# Sonos Community Archive

An **anonymous, incremental, public archive** of the Sonos community forums:

- `https://en.community.sonos.com` (English)
- `https://de.community.sonos.com` (German)
- `https://nl.community.sonos.com` (Dutch)

It captures every publicly visible category, topic and reply through the
forum's own public JSON API, stores the raw responses verbatim
(content-addressed, gzip-compressed), and builds a queryable SQLite index plus
readable JSONL exports from those captures. It runs unattended on GitHub
Actions and publishes **immutable snapshot releases** via GitHub Releases.

Python 3 standard library only. No third-party dependencies.

## Scope & ethics (read this first)

- **Anonymous only.** No login, no credentials, no private or account-only
  data, no session reuse from a real person. The crawler uses a fresh
  anonymous cookie jar plus the site's own public CSRF token, exactly like an
  unauthenticated browser visit.
- **No access-control workarounds.** If the site denies access
  (HTTP 401/403/407/429/451 or an anti-bot challenge page), the denial is
  recorded, a stop state is persisted for that host, and the run exits with
  status 3 — cleanly, without retrying around it. A stopped host is never
  contacted again until an operator clears the stop with `--reset-host`.
- **No media/attachment bytes.** Attachment *metadata* returned in JSON
  (filenames, URLs) is preserved; the files themselves are never downloaded.
- **Politeness.** `robots.txt` is fetched, parsed (including `Crawl-delay`)
  and obeyed per request. Requests are spaced at least 1 second apart per
  host. A descriptive User-Agent identifies the archive; set a real contact
  URL in `config.json` → `project.contact` / `project.user_agent` before
  running this in production.
- **Explicit do-not-crawl list** (in `config.json`):
  - EN topic IDs `6935006, 4479975, 5219128, 5487455, 5936784, 6014182` are
    never fetched for detail/replies and are excluded from index and exports
    (matched on both the public id and the internal id).
  - `https://en.community.sonos.com/ideas/topics?page=1` is on the forbidden
    route list and is never requested (in fact *no* query strings are allowed).
- **Scope allow-list.** Only HTTPS, only the three hosts, and only these
  routes: `GET /`, `GET /robots.txt`, `GET /sitemap.xml`,
  `GET /sitemap-structure.xml`, `POST /categoryV2/getAccessibleCategoriesTree`,
  `POST /contentNavigation/topics`, `POST /topic/<publicId>/replies`.
  Everything else raises a scope error before a request is even attempted.
- **Never fabricate success.** Missing data is recorded as missing, never as
  empty. `coverage.json` says exactly what is complete, partial, denied or
  pending.

## Architecture

```
config.json      all scope / politeness / do-not-crawl / budget rules
sonos_archive/
  config.py      config.json -> validated Config dataclass
  errors.py      typed errors with stable exit codes
  scope.py       request allow-list + do-not-crawl enforcement
  robots.py      small strict robots.txt parser (rules + crawl-delay)
  net.py         urllib transport, anonymous session, CSRF, denial detection,
                 FixtureTransport for fully-offline replay
  schema.py      strict fail-closed validators for API response shapes
  store.py       SQLite index + content-addressed raw blob store (WAL,
                   committed per record -> a killed run loses nothing committed)
  crawler.py     bounded resumable task-queue crawler (categories -> sitemap
                   -> topic backfill/refresh -> replies)
  export.py      topics.jsonl / replies.jsonl / raw_provenance.jsonl /
                 coverage.json derived from the index
  snapshot.py    immutable snapshot build + verify + LocalRemote +
                 GithubRemote (draft release -> upload -> publish)
  cli.py         `python3 -m sonos_archive` entry point
```

### How a run works

1. **Restore.** The latest published snapshot's `state.tar.gz` is downloaded
   and its embedded `index.sqlite` is extracted into a temp archive root.
2. **Gate.** Per host: cached robots rules (or fetch `robots.txt`), crawl-delay
   applied to the pacing gate, persisted stop state respected.
3. **Crawl.** A priority task queue (SQLite `tasks` table) drives: category
   tree -> sitemap structure seeds -> per-category topic backfill -> per-topic
   replies -> periodic page-1 refreshes and daily full rescans. Each request is
   scope-checked, robots-checked, paced (>1s/host), then the raw response is
   stored verbatim *before* its projection into the index is attempted.
4. **Publish (all-or-nothing).** A snapshot is staged fully
   (`state.tar.gz` = index + exports + coverage; `raw.tar.gz` = only the raw
   blobs added since the last snapshot; `manifest.json` = hashes, counts,
   coverage, lineage). On GitHub it is created as a **draft** release, all
   assets are uploaded and digest-checked, and only then is the draft
   published. Locally the staging dir is verified then atomically renamed.
   Nothing is ever rewritten or deleted once published; each manifest links to
   its parent (`previous`), forming an auditable chain.

A killed or cancelled run cannot corrupt state: every index write commits
immediately (WAL + FULL sync), and a half-finished publish leaves at most a
draft release or a `.staging-*` directory, both harmless and never mistaken
for a snapshot.

### Data layout

Working archive root (per run, restored/built from snapshots):

```
index.sqlite            SQLite index + resumable state (see tables below)
raw/sha256/ab/<sha>.gz  content-addressed raw response bodies (gzip)
```

Snapshot (published to GitHub Releases as tag `snapshot-NNNNNN`):

```
state.tar.gz            index.sqlite + topics.jsonl + replies.jsonl +
                        raw_provenance.jsonl + coverage.json
raw.tar.gz              raw/sha256/... blobs added since the previous snapshot
manifest.json           format, sequence, tag, lineage, asset hashes, counts,
                        coverage, scope declaration
manifest.sha256         checksum of manifest.json
```

SQLite tables: `hosts` (per-host state incl. persisted stop reasons),
`categories`, `topics` (incl. `replies_state`/`replies_offset` resume cursors),
`replies` (incl. `children_expected`/`children_missing` accounting),
`authors`, `captures` (one row per request: request+response metadata,
blob sha, kind, context — the provenance spine), `denials`,
`protocol_errors`, `excluded_topics`, `tasks`, `notes`, `meta`.

Exports (`topics.jsonl`, `replies.jsonl`) carry full metadata — ids, host,
category, title, author display name, timestamps, url, content type,
reply counts — plus a `provenance` object identifying the exact capture
(request method/url/body, status, blob path) that produced each record.

## Coverage (truthful)

This is a **partial incremental archive** unless
`coverage.is_complete_mirror` is `true` in `coverage.json` / the manifest —
which additionally requires zero pending/blocked tasks and zero known missing
nested replies. Known gaps:

- **Nested replies can be truncated by the API.** The replies endpoint returns
  at most one visible child per level (even with `depthLevel` raised). We
  record `children_expected` vs captured children per reply and count the
  shortfall as `missing_nested_replies` in coverage.
- A stopped host (denial/challenge/fail-closed schema error) freezes its
  coverage until an operator resets it.
- Topics on the do-not-crawl list are deliberately absent.
- Topics whose replies endpoint 404s are marked `replies_state = "gone"`.

## Usage

```
python3 -m sonos_archive capture [--host en.community.sonos.com] \
    [--max-requests 240] [--max-seconds 480] \
    [--remote auto|local|github] [--archive-dir archive] \
    [--reset-host en.community.sonos.com] [--no-publish] [--fixtures file.json]

python3 -m sonos_archive status   [--remote ...] [--archive-dir ...]
python3 -m sonos_archive verify   [--snapshot-dir dir] [--shallow]
python3 -m sonos_archive export   [--snapshot-dir dir | --archive-root dir] --out dir
```

`--remote auto` picks GitHub Releases when `GITHUB_TOKEN` and
`GITHUB_REPOSITORY` are set (as on Actions), else the local filesystem backend
(`archive/snapshots/`). `--fixtures` replays recorded responses with zero live
network — used by tests and for offline runs.

Exit codes: `0` clean/budget-reached · `2` config/scope · `3` denial persisted
(clean, non-zero) · `4` transient/schema/integrity · `5` publish.

## GitHub Actions

- `.github/workflows/capture.yml` — scheduled 3×/hour plus `workflow_dispatch`
  (`host`, `max_requests`, `max_seconds` inputs); `concurrency` group prevents
  overlapping runs; `contents: write` for release publishing. Exit 3 (denial)
  is reported as a notice, not a failure.
- `.github/workflows/ci.yml` — compile check + `python3 -m unittest -q`
  (offline only) on push/PR.

## Testing

```
python3 -m unittest -q        # from the repo root, fully offline
```

The suite covers: config/scope validation and the do-not-crawl list,
robots parsing/gating and crawl-delay, pacing, denial & challenge
stop-and-persist, raw capture + index projection, checkpoint resume (incl.
mid-topic reply cursors), killed-run durability, interrupted-publish
recovery, snapshot immutability and lineage, tamper detection, the GitHub
publish flow (via an in-memory fake API), malformed/unknown schema
fail-closed, and real recorded API fixtures.
