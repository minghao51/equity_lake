# ADR-0018: Private Watchlist Events and Delivery

**Status:** Accepted decision; implementation and offline-contract review accepted 2026-10-18. Operational readiness is not implied.
**Recorded:** 2026-10-17
**Deciders:** Repository owner
**Related:** ADR-0006 (auxiliary data paths), ADR-0016 (canonical instrument
identity), ADR-0017 (bounded replay), C07–C10 and R12 in the
[repository review](../finding/20261016-repository-review-and-equity-alerts.md)

## Context

The existing read-only tracking preview is not a managed watchlist or durable
opportunity-delivery workflow. This decision establishes the approved private
watchlist, signal-state, event, delivery, and local-view contract. It does not
activate a scheduler or webhook, authorize live sends, or make a signal an
order or a validated investment recommendation.

## Decision

### Private watchlist and deterministic rule

1. Provide local, private YAML watchlist CRUD for groups, tags, notes, and
   optional price zones. Adding an instrument starts **watching**; held is
   false unless explicitly set. Watchlist changes do not trigger automatic
   data/history backfill.
2. The initial deterministic price rule uses split-adjusted closes and SMA20
   deviation thresholds fixed at BUY `+2%` and SELL `-1%`. Require the exact
   latest completed instrument session; if it is absent or stale, report
   unavailable and do not evaluate or re-arm. SMA20 requires 20 valid bars.
   These are candidate signals, not orders or calibrated probabilities.
3. ML opportunity inference is disabled by default. For ML candidates only,
   use a stored optimized threshold first and the configured fallback second.
   If explicitly enabled, it may run only with a known, trusted model no older
   than seven calendar days and compatible schema, feature names/dtypes, composite identity,
   adjustment/missingness contract, and candidate configuration. Unknown or
   incompatible provenance fails closed. Do not automatically retrain.
4. Emit an initial actionable condition and a changed actionable condition;
   suppress repeated unchanged states. Only a successfully evaluated neutral
   state re-arms a condition. Stale data, an error, or a closed session never
   re-arms it. Cooldown is configurable and defaults to zero; there is no
   two-session confirmation requirement.
5. Removing an instrument retains its history and cancels its pending
   deliveries. A watch-only SELL is downside caution, not a short position. If
   explicitly marked held, SELL is an exit candidate, never an order.

### Durable events and delivery

6. Keep opportunity delivery separate from health webhooks. Provide a generic
   opportunity webhook that is disabled by default and a durable local event /
   outbox lifecycle with `pending`, `sent`, `failed`, and `expired` states.
   Retrying an ambiguous send is allowed but is not exactly-once delivery.
7. Schedule support targets daily readiness-first processing at 17:30 in
   `America/New_York`, with DST-aware scheduling. Quiet hours are 21:00–08:00
   in that zone; events deferred during quiet hours require delayed
   revalidation before delivery. Emit stale-run warnings independently from
   opportunity notifications. Scheduler support does not enable or activate
   any schedule or channel.

### Private views and artifact boundary

8. Provide loopback-only Streamlit and private local API views for readiness,
   explanations, conflicting candidates, history, and delivery state. Do not
   expose watchlist notes, holdings, or credentials in public exports or
   default notifications. A loopback bind is not authentication; no public
   export or unauthenticated network exposure is authorized.
9. Persist operational watchlist, event, and delivery artifacts outside the
   cataloged lake under auxiliary `data/<name>/` paths, with Pydantic models at
   write boundaries. They are not catalog datasets and are not pointblank-
   validated lake tables.

## Implementation status and compatibility notes

The `equity tracking` CLI now manages private YAML watchlists and a local
SQLite event/outbox outside the cataloged lake. `equity signal scan` reads the
canonical private watchlist by default; an explicit legacy ticker-only list
requires `--market`, and `--mic` is required when the active source mapping is
ambiguous. Adding a watchlist item does not fetch or backfill history. The
readiness-first daily target (17:30 `America/New_York`) is a schedule plan, not
an active scheduler.

The deterministic price rule uses split-adjusted closes, SMA20 and fixed BUY
`+2%` / SELL `-1%` deviation thresholds; it requires the exact latest
completed instrument session and 20 valid bars. A source-coverage proof is
required for adjusted prices, so the currently unverified native action-source
path leaves this rule unavailable until verified proof is available. External
sentiment enrichment and unsupported non-US enrichment remain unavailable,
not neutral. Optional ML remains disabled by default and requires trusted,
compatible provenance with an inclusive maximum age of seven calendar days;
there is no automatic retraining.

Opportunity delivery is separate from health webhooks. It is disabled unless
an invocation opts in with `--enable-live`; only then is the raw
`EQUITY_OPPORTUNITY_WEBHOOK_URL` read at the sending seam. Durable local outbox
states and revalidation do not provide exactly-once delivery. The loopback-only
private API/UI are not authenticated; forwarded/proxy exposure is rejected,
and private notes, holdings and credentials are excluded from public/static
outputs and default webhook payloads.

At the 2026-10-17 status snapshot, implementation was under independent
review and not yet implementation-accepted. The 2026-10-18 supplement below
supersedes that review status. This acceptance does not activate the scheduler
or authorize source fetches, paid work, live sends, production
migration/repair, deployment, or broker execution.

## Consequences and acceptance evidence

- Deterministic synthetic tests must cover CRUD and privacy boundaries,
  no-backfill behavior, exact-session freshness, 20-valid-bar readiness,
  split-adjusted calculations, threshold crossings, duplicate suppression,
  neutral-only re-arming, stale/error/closed-session behavior, cooldown,
  removal/history/pending cancellation, watch-only versus held SELL semantics,
  outbox states and retries, quiet-hour revalidation, independent stale-run
  warnings, disabled defaults, and loopback-only views.
- The C10 acceptance evidence for the integrated add-to-watchlist → readiness
  → scan → event → mocked delivery → private-view journey included
  repeat/change conditions, failures, stale data, retries, and session edges;
  see the 2026-10-18 implementation/offline-contract acceptance supplement.
- This accepted product contract does not authorize live notifications,
  financial-provider calls, paid processing, deployment, scheduler activation,
  broker execution, or production-data migration or repair. Each such operation
  requires separate owner authorization.

## Implementation/offline-contract acceptance supplement — 2026-10-18

Private watchlist/readiness, deterministic candidate, event/outbox, retry and
revalidation, and private API/UI projection contracts were independently
reviewed on synthetic fixtures and the C10 journey, then accepted with the
offline gates. The final reviewer returned no C10 must-fix. Delivery was
mocked; no live webhook was sent, and the scheduler remains inactive. The native
action-source proof limitation still makes adjusted price evaluation
unavailable until qualifying proof exists. Loopback remains unauthenticated.
This status is not operational readiness or authorization for live delivery,
source fetching, paid processing, migration, repair, deployment, or broker
execution.
