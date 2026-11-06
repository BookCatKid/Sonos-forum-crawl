# Sonos forum crawl

API-first public forum collector for the English, German and Dutch Sonos forums.
Preserves full topic/reply content and original JSON receipts. No text scanning,
media-byte downloads, sign-in, or access-control workarounds.

Status: offline review build. The empty PUBLIC repository was created at
https://github.com/BookCatKid/Sonos-forum-crawl on November 5, 2026. No source
pushed, schedule enabled, release uploaded or new mass collection started.
Historical mass capture and its exact generated discovery plan are unavailable.
Do not invent a plan or start from a failed restore.

## Offline checks

Python 3 standard library on Linux:

```sh
python3 -m unittest -q
```

127 offline tests pass, including parser/controller regressions, local checkpoint
restore, rate/scope gates, schema-only public payload projection, packed checkpoint
round trips and mocked GitHub release calls. No real GitHub upload/download has
been tested. Tests do not prove exhaustive capture or indefinite remote durability.

## Collection core

- `build_discovery_plan.py`: builds a plan from saved public menu/tree evidence.
- `audit_public_config.py`, `integrate_config.py`, `discover_groups.py`: public
  category/group/KB/curation discovery.
- `controller.py`: explicit prepare, bounded run-batch and reply-phase commands.
- `api_adapter.py`: SQLite tasks/projections plus immutable gzipped JSON receipts.
- `audit_api_capture.py`: receipt hashes, projection integrity, listing families.
- `checkpoint.py`: offline database/receipt snapshot and verified restore.

Normal category listings can cap at 10,000 topics. Verified content-type
partitions, independent sitemap reconciliation and explicit drift reports are
required. A count-consistent tree is not proof of exhaustive global capture.
The source builders are recovered, but the full historical generated discovery
plan must be recovered or regenerated and checked before a new launch.

The old controller is retained for regression review. `scope.json` records the
current required exclusions and user-requested one-second sequential baseline; the controller filters pending tasks and reply planning when this file is
present in the run root. `automated_batch.py` requires this file, verifies a remote baseline before any
collection, and persists a backup-failure latch before another batch can run.
Its transport is still a fake in tests, not a configured remote destination.

## Checkpoint format

Export acquires the controller root lock; it refuses concurrent collection:

```sh
python3 checkpoint.py export data/run checkpoints/generation-001
python3 checkpoint.py restore checkpoints/generation-001 data/restored
```

Export uses SQLite's backup API, checks database and receipt integrity, and
copies only the database, immutable JSON tree and explicit policy/plan files.
A SHA-256 manifest covers every exported file. Session/bootstrap caches and
cookies are excluded. Restore requires a new target, checks every hash and
receipt audit, and never overwrites an existing run. This proves local restore,
not remote storage durability. Remote upload/download verification is pending. `generations.py` tests the
immutable-generation commit protocol through an in-memory fake transport; this
is not a GitHub upload or proof of real remote durability.

## Automation design for review

The owner chose one PUBLIC repository and PUBLIC checkpoint releases for content
that is anonymously accessible. Keep corpus out of git and expiring Actions
artifacts. Public post text may contain personal information posted by its author;
this collector does not classify or rewrite text and cannot guarantee otherwise.

- `public_payload.py`: explicit field schema for topic/reply content, hierarchy,
  dates and minimal public attribution. Actual EN/DE/NL listings, replies,
  product updates and category trees were checked offline. Drops profile extras, viewer flags and
  engagement-user lists. Unknown fields/routes or scalar type changes stop publication. A listing page
  containing a carried excluded EN topic stops before storage, not silent omission. Nonempty
  attachment metadata requires schema review rather than silent loss.
- `public_store.py`: a public-safe ingest wrapper and checkpoint preflight. It
  rejects raw/nonminimal receipts, unbacked topic/reply projections, untracked
  receipts, unknown tables and unreviewed discovery-plan files. The review entry point wires it to
  controller ingestion. It is not a completed legacy-store migration.
- `packed_generation.py`: packs a verified checkpoint into 128MiB chunks, bounded
  to 990 chunks. Uploads, downloads and hashes each part, checks a full restored
  copy, then writes COMMITTED last. No generation or prior backup is overwritten.
- `github_release.py`: same-repository GITHUB_TOKEN transport, minimal release
  create/list/upload/download calls. No deletion or update method. Uses the asset
  API and does not forward the token to redirected anonymous download storage.
  Transport calls are mocked in tests, not a real release round trip.

The earlier `generations.py` is a storage-neutral protocol test, not the planned
per-receipt GitHub format. A large receipt set would exceed 1,000 assets/release;
packing avoids one asset per receipt. No rolling pointer is used: resume must name
an explicit committed generation. `attempt_guard.py` reserves the output release
before any forum reads. An unfinished remote generation blocks another capture
on a fresh runner; committed ancestry rejects a stale source. This is mocked
test evidence only and assumes repository-wide single-job concurrency. Partial releases never count as valid backups.

A proposed bounded job would restore an explicitly committed generation, validate
scope/stop state, collect sequentially at least one second apart (or a longer
robots delay), checkpoint, verify remote download/restore, then end. Backup failure
must prevent more collection. One job per repository/root, no parallel crawl.
Logs contain counts and gap summaries, not post bodies or session data. Secrets
are runtime-only. No account sign-in, hidden routes or access-control workarounds.

The review-only `review/crawl-workflow.yml` is outside `.github/workflows`, so it
cannot execute. The only executable workflow is manual short offline checks.
`public_job.py` shows a one-batch resume, not a full automatic crawl. It has no
bootstrap/init, phase advance, automatic generation choice or cron.
Before source push or launch:

1. Review the exact source/workflow and approve the source push separately.
2. Regenerate and validate the discovery plan from actual public source evidence.
3. Finish public schemas for KB/curation/events, attachment metadata and
   discovery-plan export. State preflight validates exact columns and reviewed
   metadata codes; complete adversarial review is still required.
4. Review the new bounded public orchestration path and complete durable
   reservation/ancestry tests against real GitHub. Raw-store public publication
   remains forbidden. Finish initialization and phase/gap handling.
5. Perform a real authorized release upload/download/fresh restore test, plus
   interruption testing, before scheduling collection.
6. Review runner budget/cadence: standard public hosted runner only, explicit job
   timeout, no larger/private runner or paid product, no unapproved spending.
7. Approve the bounded crawl launch and automatic public checkpoint scope.

No GitHub Actions cache or expiring artifact is the sole corpus backup. No promise
of full coverage until independent inventories and continuation gaps reconcile.

## Fresh discovery and current review boundary

Finite anonymous discovery on November 5, 2026 regenerated 86 category/container
nodes, including 73 public leaf/group pages. All 73 configuration pages were
checked; 14 groups explicitly reported public/canSee, and the plan includes 66
extra KB/curation reads. Evidence records exact source URLs. No denied routes or
excluded resources were read, and no topic/reply continuations were traversed.
The 126-request finite grounding allowance is now exhausted, not a site denial.

`approved-discovery-plan.json` is a source-pinned public projection of that plan.
`public_plan.py` checks exact equality during restore; local paths are replaced
with public evidence URLs. `initialize_public.py` prepares this plan offline only,
never uploads or collects. Tests initialize, publish to a fake, and restore on a
fresh root. The word approved in the filename means schema-pinned for review,
not that the owner has authorized source push or collection.

The EN229131 tree reports 10,502 topics; first-page partition totals are article1,
conversation2961, question7534, sum10496. The six-topic discrepancy is an explicit
moving-count/type gap. Each partition is below10000 but no full union/completeness
claim follows. Exact observed source is the public EN category tree/list contract.

Public schemas now cover KB/curation, tree, product updates, and event bodies.
Event attendee lists, viewer flags, creator/profile extras and registration fields
are omitted. Nine actual saved public events pass projection. Unknown attachment
metadata still stops. Full shape changes fail rather than silently expand data.

The carried exclusions need explicit omitted-ID gap accounting if an excluded
topic occurs within a listing page. Current public ingestion fails on that page
rather than store excluded content or silently change receipt/count evidence.

Ready next decision: review the exact source and manual-only 15-request workflow,
then separately authorize initial source push and a synthetic public release
upload/download/fresh restore test. That test must pass before a real collection
launch. The review workflow is not active and has no cron. A full unattended crawl
still needs initialization/recovery/phase/gap orchestration and cadence review;
no immediate launch is being represented as ready.

## Reference implementations and platform limits

Inspected November 5, 2026:

- https://github.com/BookCatKid/geosweep
- https://github.com/adventure-labs-browser/labsweep

Their daily workflows use rolling release databases plus dated snapshots.
Neither their public audience nor their retry/restore fallback behavior is
inherited here. Geosweep's README says private, but live repository metadata
currently says public.

- https://docs.github.com/en/actions/reference/limits
  Hosted jobs have a six-hour ceiling. Bound the crawl well below it and reserve
  time for snapshot validation/upload. Limits and allowances can change.
- https://docs.github.com/en/actions/concepts/billing-and-usage
  Private hosted jobs consume included usage and may incur charges. Account
  allowance and spending settings have not been verified for this proposal.
- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
  Up to 1,000 assets per release, each under 2GiB; current docs state no total
  release-size or bandwidth limit. Releases remain deletable, not an indefinite
  preservation guarantee.
- https://docs.github.com/en/organizations/managing-organization-settings/configuring-the-retention-period-for-github-actions-artifacts-and-logs-in-your-organization
  Artifacts/logs expire; default retention is 90 days.

## Proposed next approval: first finite hosted batch

Not executed or pushed. The local `finite_launch.py` and
`review/first-batch-workflow.yml` propose one manual dispatch on the standard
ubuntu-latest runner, 25-minute job timeout, 15 API-task attempts maximum, no cron,
no second batch, no phase advance or replies. All three hosts can be bootstrapped,
so there are at most six additional robots/home requests (21 application requests
maximum, ordinary same-host redirects may add transport requests). Requests remain
sequential at least one second apart or a longer robots delay. The existing
8GB capture budget/2GB free-space stop stays in place.

Before forum reads: offline initialize the exact source-pinned plan, upload a
zero-content baseline, download/hash/restore-check it, reserve the new generation
remotely, and verify the reservation. Incomplete prior generations stop launch.
After the bounded reads: publish sanitized packed state, download and restore
again, then COMMITTED. Upload failure leaves an incomplete remote reservation
that blocks another run; no automatic retry or deletion. Hard termination can
lose post-baseline local work, but cannot silently resume the old generation.
Recovery requires an explicit review.

First15 queue positions: DE/EN/NL product-update and event first pages, then DE
categories223374/223383/223389/223391 and their curated widgets, then223393's first
page. Continuations append after those initial tasks, not an unlimited traversal.
Unknown public fields/attachments and carried excluded IDs stop before storage.
401/403/404 keep a resource gap; throttles/challenges/network/robots stop the host.
No alternate route or stop reset. No analytical text scanning or media downloads.

The next approval must cover the exact additional code push, activation of that
manual-only workflow and its single bounded real-read run with automatic public
checkpoint publication. It does not cover an ongoing crawl. The already verified
synthetic release remains a test, not real captured content or a crawl baseline.
