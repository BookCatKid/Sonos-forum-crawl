# Implementation notes

## Design choices

- **One lineage, not two.** The initial tree contained two half-finished,
  incompatible implementations (a constants/`rules.py` design and a
  `config.json`/strict-schema design). I kept the latter — externalised scope
  rules, strict dataclass validators, task-queue crawler — deleted the
  orphaned modules, and rewrote `export.py`, `snapshot.py` and `cli.py`
  against it.
- **SQLite is both index and checkpoint.** Every projection write commits
  immediately (WAL + `synchronous=FULL`), so a cancelled run keeps everything
  it captured. Resume state lives in the `tasks` table (kind/key/state/cursor)
  plus per-topic `replies_state`/`replies_offset` — a reply crawl resumes at
  its exact page offset.
- **Raw bytes are immutable and content-addressed.** The wire response body is
  stored under `raw/sha256/<2>/<sha>.gz`; if the server sent gzip the wire
  bytes are kept verbatim and a `decoded_sha256` is recorded, otherwise the
  body is deterministically re-gzipped (`mtime=0`). The index never has to be
  trusted: every projection is reproducible from captures.
- **Publishing is all-or-nothing.** GitHub publish creates a *draft* release,
  uploads every asset with digest checks, uploads the manifest last, then
  flips `draft:false`. Draft deletion is only ever attempted after confirming
  the release is still a draft; a published release is never edited or
  deleted. Lineage (`previous.sequence` + manifest hash) is re-checked at
  publish time against the real latest release — a concurrent run fails
  cleanly instead of forking the chain.
- **State transport = GitHub Releases.** The repo only holds code/config.
  `state.tar.gz` carries the SQLite index + exports; `raw.tar.gz` carries only
  the new blobs since the previous snapshot, keyed by monotonically
  increasing `captures.seq` recorded in the manifest.
- **Fail closed on unknown shapes.** Strict validators (`schema.py`) raise
  `SchemaError` before anything is indexed; the offending capture is stored as
  evidence, a `protocol_errors` row + host stop are persisted, and the run
  exits 4. A deleted topic (replies 404) is marked `gone`, not fatal; HTTP 5xx
  is transient (task stays pending), not a stop.

## Verified live (anonymous, >1s between requests)

- GET `https://{en,de,nl}.community.sonos.com/robots.txt` — parsed; our API
  paths are allowed; no `Crawl-delay`.
- GET `/` — anonymous session cookie + `csrftoken` hidden input extracted.
- POST `/categoryV2/getAccessibleCategoriesTree` `{"type":[0,1]}` — category
  trees captured (EN 35 nodes, DE 19, NL 18).
- GET `/sitemap.xml` -> `sitemapindex` -> GET `/sitemap-structure.xml` —
  category URL seeds (EN 41, DE 23, NL 23).
- POST `/contentNavigation/topics` — category topic listing works as
  documented (`topics`, `total`, `authors`; `topicUrl.destination`).
- POST `/topic/<publicId>/replies` — **schema differs from the brief's note**:
  `children` is `{"result": [...], "metadata": {...}}` or `null`, not a list.
  Nested containers have their own `metadata.totalCount`/`hasMore`/
  `depthLevel`. Request-side `"depthLevel": 10` unlocks deeper levels; each
  level still returns at most ~1 child regardless of `pageSize`/`limit`/
  `parentReplyId` guesses, so deep threads are partially truncated by the API.
  The truncation is counted as `missing_nested_replies` and prevents
  `is_complete_mirror` — it is reported, never hidden.
- **False-positive caught and fixed:** the real home page contains the string
  `"RateLimiter-HideCaptcha"` in an embedded JSON, which tripped a broad
  `captcha` marker on the first smoke run. Markers are now specific
  challenge-page signatures only.
- Bounded smoke captures: EN full path (robots → CSRF → categories →
  sitemap → topics → replies; 6 topics / 36 replies / 10 captures,
  snapshot verified); DE + NL discovery path (robots → CSRF → categories →
  sitemap). Three snapshots published sequentially to the local remote with
  correct lineage; `verify` passes, exports contain real records with
  provenance.

## Not verified / limitations

- **No GitHub Releases round-trip against a real repository** — `GithubRemote`
  is exercised offline against an in-memory fake implementing the releases
  API (create/list/tag-lookup/asset upload/draft publish/delete). The REST
  calls follow the documented API but a live publish has not run here.
- **Nested replies are partially truncated by the API** (see above). No public
  endpoint was found to page a reply's children; the gap is accounted for in
  `children_expected`/`children_missing` and coverage.
- **DE/NL topic/reply capture** not exercised live (discovery only); the API
  surface is identical but content schemas could differ slightly.
- Attachment *bytes* are never downloaded (by design); only JSON metadata is
  kept.
- Refresh scans inspect page 1 of each category; changes deeper in a
  category's listing are picked up by the daily full rescan
  (`crawl.full_rescan_seconds`).
- Single-writer assumption for publishing is enforced via the Actions
  concurrency group + publish-time lineage re-check.
