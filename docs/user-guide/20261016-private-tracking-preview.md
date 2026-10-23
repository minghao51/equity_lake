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
- Watchlist and scan commands make no live market-provider calls and do not
  refresh or backfill when an item is added. External source fetching, paid
  processing, live webhook sends, production migration/repair, retraining, and
  deployment each require separate owner authorization. The workflow performs
  no automatic repair/backfill/retraining, scheduler activation, or production
  migration.
- Synthetic tests establish local behavior only, not signal performance or
  trading suitability.
