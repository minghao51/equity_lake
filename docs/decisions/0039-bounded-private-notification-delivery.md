# ADR-0039 — Activate live notifications only as a bounded private pilot

**Status:** Proposed, deferred to backlog by the owner (2026-11-27). Live delivery stays off.

**Recorded:** 2026-11-27

## Context

[ADR-0018](0018-private-watchlist-events-and-delivery.md) and residual stage RS-S01 define
private outbox delivery and an offline/paper-shadow contract. Neither authorizes real sends.
Live rule scans require strict source coverage; the clockless research path is not a substitute.

## Options

- **Keep paper shadow:** no external side effect; no evidence of real receipt.
- **One private endpoint on an owner-operated host:** tests the actual delivery loop and can
  be stopped without changing the public site.
- **Hosted or broad rollout:** expands secret-bearing infrastructure and recipients before
  the first endpoint or operator handoff has been proven.

## Decision (recommended)

Keep paper shadow unchanged. Do not work on activation until the owner reopens the decision.
If reopened, once the owner names a private endpoint/channel, a local scheduler
host, and a named kill-switch owner, and supplies strict coverage evidence for the specified
watchlist, propose a time-boxed pilot. Agree on sessions, expiry, send cap, recipients,
rollback window and who monitors it before activation. Use a local systemd timer or cron with
calendar-aware readiness; never a public Pages job or a secret-bearing GitHub job. Start
with scoped credentials stored off-repo and a fake-sink rehearsal on that host.

Prove venue-calendar and DST handling, no overlap, bounded retries, missed-session handling,
expiry/deduplication, removal support, quiet hours and revalidation. Stop on lost readiness
or kill switch. Test that disabling the timer and delivery gates prevents further sends;
an in-flight send may already have escaped. Unknown receipt can lead to duplicates:
do not claim exactly-once. Keep secrets, private events and payloads out of public logs.

## Consequences

The owner gets a narrow, reversible pilot rather than a service commitment. No live send,
scheduler installation, paid processing or model promotion is authorized by this draft.
