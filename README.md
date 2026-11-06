# Sonos forum crawl

Ongoing anonymous public topic/reply capture for the EN, DE and NL Sonos forums.
Full post text is preserved without text analysis. No sign-in, media downloads,
private-account data, access-control workarounds or spending.

## Operation

`capture.yml` starts at minute17and47each hour on the standard public GitHub
runner. Eligible runs also dispatch one automatic successor no sooner than30minutes
after their own start, skipping when another run is already queued/active.
Cron is a recovery bootstrap if a runner dies before chaining. All-stopped state
does not spin a chain. GitHub can delay/drop events; this is not a realtime SLA.
Each run restores the verified committed checkpoint chain, performs at most500
sequential tasks within20minutes, then uploads and independently restores/hashes
the new checkpoint before writing COMMITTED. The40minute job ceiling reserves
backup time. Concurrency serializes runs. Requests are at least1second apart,
or a longer robots delay. Release checkpoints are public, immutable to this
collector, and never overwritten/deleted. GitHub owners can delete releases.

Listing integrity is checked before automatically queuing eligible-host replies.
After replies finish, the next run refreshes known listings and reply roots.
Historical topic-ID unions and immutable receipts survive page movement. New
continuations follow observed API pagination. Changed snapshots are not inferred
deletions. Count drift and inventory gaps do not silently become completeness.

EN is currently stopped after an ordinary anonymous403on a sitemap-linked
category outside the anonymously discovered tree. No automatic reset, alternate
route or retry occurs. Unrelated DE/NL capture continues. Six explicit EN topic
exclusions and the prior denied Ideas route remain excluded by `scope.json`.
Unknown public schemas fail closed. Public image/video attachment reference metadata is retained after schema
validation; other nonempty attachment shapes still require review. No
attachment/media bytes are collected. Profile extras,
viewer flags, engagement-user lists and session data are omitted.

## Recovery

An immutable ATTEMPT reservation precedes forum reads. A killed hosted run can
leave partial assets. The next run verifies its exact GitHub workflow run has
terminated, fresh-restores its committed parent, and appends ABANDONED.json with
an explicit uncommitted capture-gap record. It never deletes that attempt or
pretends the lost in-flight reads were saved. The new committed lineage records
the gap. Active runs, unknown/manual partial attempts, bad ancestry, failed
parent restore, and malformed recovery markers still stop collection. Access
stops are never cleared by recovery. Expiring job logs are not the corpus.

## Current evidence and limits

The November6discovery checkpoint contains88,386distinct historical topic IDs
and3,658successful JSON receipts, before any reply collection. All receipt hashes
and projected identities were checked after fresh remote restore. EN sitemap IDs
are covered except explicit exclusions. DE/NL recent100-item feeds are covered,
but are not independent full inventories. Historical unions match maximum
observed API listing totals; snapshots collected over time are not simultaneous.
Older category-tree differences and inaccessible/outside-tree categories remain
gaps. The fixed discovery plan covers86nodes/73leaves, including public groups,
KB, curated content, updates and events. Accessible category trees are refreshed each cycle and newly evidenced public
leaves enqueue supported listing/curation contracts. The separate public-group index is refreshed too. New group pages must explicitly
report canSee/public and a supported listing contract before topics are queued.
Only minimal structural fields are retained, not raw HTML/session props. Never call this a full/up-to-date mirror while these gaps
or uncollected replies remain.

One genuine GitHub schedule event first ran November6at09:31PST after the09:17
schedule. This proves native triggering, not indefinite reliable cadence.

## Verification

```sh
python3 -m unittest -q
```

Tests cover parsing, stop/scope rules, public projection, capture-union refresh,
immutable checkpoints, restore failures, automatic phases/cycles and terminated
hosted-attempt recovery. Tests do not establish complete live coverage.

Public sources: the forums' robots files, anonymously accessible category/API
surfaces, https://en.community.sonos.com/sitemap.xml and DE/NL `/feed/topics`.
Scheduling caveats: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
