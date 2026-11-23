# Private Canonical Watchlists and Opportunity Tracking

The private tracking workflow manages a local canonical-instrument watchlist,
observes stored data, evaluates deterministic session-based conditions, records
local events, and optionally delivers a bounded webhook outbox. It is separate
from the medallion catalog, public signal history, order execution, and the
static dashboard exporter.

It supports the five configured price markets: `us_equity`, `cn_ashare`,
`hk_sg_equity`, `jpx_equity`, and `krx_equity`. Each instrument is identified by
its canonical `(market, MIC, symbol)` tuple. A ticker spelling alone is never
used to guess a market or venue.

> **Implementation/offline-contract status:** ADR-0018 implementation and the
> C10 offline contracts were independently reviewed and accepted on 2026-10-18.
> This is not live operational readiness or authorization for source/provider
> calls, webhook sends, scheduler activation, migration, or deployment.

## Private watchlist

The default private YAML is `data/watchlists/watchlist.yaml`; delivery and scan
state is stored in the auxiliary SQLite database
`data/tracking/tracking.sqlite3`. These files are outside `data/lake/` and are
not part of the generated data catalog. Watchlist and event/outbox writes use
validated Pydantic models at their persistence boundaries. The application
creates private permissions for these local artifacts. Do not put credentials
in the watchlist. `equity signal scan` reads this canonical watchlist by default;
its explicitly supplied legacy ticker-only list requires `--market` and `--mic`
when needed to disambiguate. `equity tracking` owns the richer readiness,
event, and delivery lifecycle.

Use `equity tracking list`, `add`, `update`, and `remove` to manage membership.
`add` requires an exact active identity from `config/tickers.yaml`; pass `--mic`
when the symbol needs an explicit venue. For example:

```bash
# Replace SYMBOL with a value present in the selected ticker configuration.
uv run equity tracking add \
  --market us_equity --mic XNAS --symbol SYMBOL \
  --group core --tag momentum --notes "private note" --watch-only
uv run equity tracking list
uv run equity tracking update \
  --market us_equity --mic XNAS --symbol SYMBOL --held
uv run equity tracking remove \
  --market us_equity --mic XNAS --symbol SYMBOL
```

Groups, tags, notes, held/watch-only status, and explicitly entered low/high
price zones are private watchlist metadata. They do not affect public signal
history or webhook payloads. A `SELL` candidate for a watch-only instrument is
shown as a downside watch, not a short instruction; held status is displayed as
user-supplied context, not verified position data. Candidate actions are not
orders or recommendations.

The legacy `config/watchlist.yaml` is not loaded automatically by private
tracking. `equity tracking migrate-legacy` explicitly migrates current membership and
recognized group membership only; each symbol must resolve unambiguously against
the active ticker configuration. It does not infer historical signal identity
or rewrite old history.

## Readiness and scans

`equity tracking readiness` reports canonical watchlist readiness, stored
session coverage, previous scan outcomes, event state, and stale-run warnings.
`equity doctor` is a read-only observation over canonical instruments in the
configured markets, and `equity signal history --date YYYY-MM-DD --limit N`
reads existing signal history without migration or generator construction.

`equity tracking scan` processes only stored data. It checks the relevant venue
calendar and exact latest completed session, requires canonical identity, and
applies the split-adjusted SMA20 rule only with 20 valid session bars. BUY is
fixed at `+2%` deviation and SELL at `-1%`. Missing, stale,
ambiguous, incomplete, closed-session, or unreadable inputs are not interpreted
as a neutral/no-condition result and do not re-arm an alert. A complete
`no_condition` evaluation is required for re-arming. Conflicting candidates are
preserved rather than silently ranked.

The adjustment input is strictly split-only, leaves raw Bronze unchanged, and
excludes cash dividends. It requires source-scoped complete coverage, a verified
source publication timestamp, and source-issued validity through the decision
session. The current native yfinance corporate-action path supplies no verified
publication timestamp, complete-coverage proof, or expiry, so adjusted results
remain unavailable until an independently verified proof is supplied. No
provider availability is inferred, no TTL is guessed, and no raw-price fallback
is used. Unsupported sentiment and non-US enrichment remain unavailable, not
neutral.

ML evaluation is disabled in the checked-in signal configuration. It remains
optional and should be enabled only with an explicit local configuration and
trusted compatible artifact no older than seven calendar days (inclusive).
Unknown model identity/schema/availability provenance remains unavailable; no
automatic retraining occurs. Tracking never downloads data, performs a backfill, writes a
cataloged table, or starts a scheduler. Use the dry-run flag to view a plan
without reading, inferring, persisting, or delivering:

```bash
uv run equity tracking scan --dry-run
uv run equity tracking readiness
uv run equity tracking scan --as-of 2026-10-16T17:30:00-04:00
uv run equity tracking inbox
uv run equity tracking digest
```

A non-dry scan persists its report, state transitions, and eligible events in
the local auxiliary database. It does not write signal history or a medallion
layer. Cooldown is configured with `equity tracking settings`; the default is
zero minutes. Removal retains history and expires pending deliveries for that
instrument.

## Delivery and schedule

Delivery is disabled unless the invocation explicitly includes
`--enable-live`. Do not use that opt-in unless external delivery is intended
and the endpoint is authorized. The normal local command reports disabled
status and pending counts without sending:

```bash
uv run equity tracking deliver
uv run equity tracking schedule
```

The schedule command describes a DST-aware, readiness-first plan targeting
17:30 `America/New_York`; it does not activate a scheduler. Quiet hours are
21:00–08:00 in that DST-aware zone. Delivery revalidates that an event is
current, respects quiet hours and cooldowns, retries at most three attempts,
records sanitized failure codes, and does not claim exactly-once receipt. A
crash or ambiguous remote response can lead to a retry after the remote side
accepted the prior request, so duplicate receipt cannot be ruled out.

### Paper shadow (offline rehearsal)

`equity tracking shadow` rehearses the delivery loop with a local in-memory sink.
It records accepted deliveries in memory only: it constructs no HTTP sender,
reads no webhook credential, opens no socket, enables no live channel, and
activates no scheduler. The report prints the sink type, each bounded round's
attempt/sent/failed/expired/pending counts, the delivered event IDs, any
duplicate event IDs, the not-exactly-once disclosure, and
`live_delivery_enabled: false` / `scheduler_activated: false`.

```bash
uv run equity tracking shadow --rounds 1
uv run equity tracking shadow --no-scan --as-of 2026-10-16T17:30:00-04:00
uv run equity tracking shadow --require-strict-coverage --lake-dir data/lake_real
```

Guard order is fixed:

1. readiness (`watchlist_unreadable`, `history_corrupt`) before any scan;
2. the optional live-scan strict-coverage gate (`--require-strict-coverage`);
3. the operator kill switch;
4. one bounded readiness-first scan of stored data (`--scan`, default on);
5. readiness re-checked, then the kill switch and quiet hours re-checked per
   round, while holding the same cross-process delivery lock the live seam uses
   — an overlapping delivery or shadow run is refused, never queued;
6. per event, delivery expiry, revalidation, then at most three bounded attempts.

A blocked report exits non-zero and echoes its actionable refusal to stderr; a
quiet-hours or kill-switch stop is a normal, non-sending outcome (`status:
quiet_hours` / `kill_switch`) and exits zero.

The paper shadow may rehearse on synthetic data. A live rule scan may not: it
requires strict source coverage (`coverage_basis=source_clock`). A
`vendor_events_no_clock` proof asserts vendor-event completeness only and carries
no source clock, so the live readiness gate refuses it — as it refuses a watched
identity with no stored proof at all — with a refusal naming the strict basis,
before any scan or send.

### Kill switch

The operator emergency stop is a file marker beside the private ledger:
`data/tracking/DELIVERY_DISABLED` (with `--data-dir`, that root's
`tracking/DELIVERY_DISABLED`). While it exists, no send is attempted: the
`deliver` command reports `status: kill_switch` and the paper shadow refuses to
run a round, even when `--enable-live` is supplied. A marker that cannot be
inspected fails closed. Create or remove the file to stop or resume delivery; no
config change and no restart is needed. It is deliberately a local file, not an
environment variable, so the switch is visible next to the ledger it protects.

### Dedup expiry and removal

Delivery dedup keys are event IDs, and they expire. A pending event stays
deliverable only while its own stored session is at most five venue sessions
behind the latest completed session of its instrument; older pending rows are
marked `expired` with the value-free code `expired_out_of_window` instead of
being delivered late. A missed session is therefore retired, never
burst-delivered as if it were current. The window is derived from the event's
own session, so expiry needs no outbox column, no ledger migration and no data
rewrite.

An operator may remove one pending row instead of abandoning it:
`uv run equity tracking deliver --remove-event EVENT_ID`. That marks the row
`expired` with the value-free reason `removed_by_operator` and reports
`unsend_possible: false`. Removal and expiry only stop **future** sends. Un-sending
is impossible: a request a receiver already accepted cannot be recalled, and an
`expired` row is not evidence that the receiver never saw the event. Dedup keys
are not, and are never described as, exactly-once delivery.

Live activation remains outside this change. It still needs the operator's
private endpoint/channel and a scheduler host, and it stays owner-gated. The
intended scheduler shape is an external local cron or systemd job that invokes
the readiness-first scan and then delivery on this host; it is never a public Pages
job and never a secret-bearing GitHub Actions job.

Opportunity delivery is distinct from health monitoring/webhooks. It is disabled
unless the invocation includes `--enable-live`; only that explicit sending seam
reads the raw `EQUITY_OPPORTUNITY_WEBHOOK_URL` environment variable. The URL is
not a `Settings` value and is not logged or persisted. A loopback bind is not
authentication; forwarded/proxy requests are rejected and this service must not
be exposed through a proxy.

Webhook payloads contain only the event ID, canonical market/MIC/symbol,
session, source, action, rule version, and reason. They do not include private
notes, tags, groups, holdings, credentials. Delivery
is an optional notification mechanism, not an execution interface.

## Private API and dashboard

The private API endpoint is `GET /tracking/private`. It returns private
watchlist fields and local event state only to direct loopback clients. Requests
with proxy/forwarded headers or a non-loopback client/host are rejected. Do not
place it behind a reverse proxy or expose it beyond the local machine. The
public `/signals` projection uses an allowlist and represents legacy identity
as unknown rather than guessing from ticker text.

The Streamlit private tracking panel requires an effective loopback bind.
`equity dashboard serve` binds to `127.0.0.1`; do not proxy it, bind it to
`0.0.0.0`, publish it, or include private data in `equity dashboard build` or
static exports. A loopback bind is a local-access restriction, not
user-authentication.

## Limits

- This is a local watchlist, observation, candidate, and notification workflow;
  it does not place orders or verify account holdings.
- Readiness and stale-run warnings describe the stored local data; they do not
  guarantee completeness, future performance, or a calibrated probability of
  profit.
- The paper shadow is an offline rehearsal of the delivery loop, not operational
  readiness: it proves the guards behave, not that a channel, receiver, or
  scheduler has been authorized or exercised.
- Watchlist and scan commands make no live market-provider calls and do not
  refresh or backfill when an item is added. External source fetching, paid
  processing, live webhook sends, production migration/repair, retraining, and
  deployment each require separate owner authorization. The workflow performs
  no automatic repair/backfill/retraining, scheduler activation, or production
  migration.
- Synthetic tests establish local behavior only, not signal performance or
  trading suitability.
