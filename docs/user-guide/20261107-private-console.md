# Private Console (P03)

The private console is a **loopback-only, display-only** Streamlit surface over locally
stored evidence. It is not an API, not an exporter, and not a trading tool, and a
loopback bind is **not** authentication (ADR-0018 §8).

```bash
# The app refuses every private page unless it is explicitly bound to loopback.
uv run streamlit run src/equity_lake/dashboard/streamlit_app.py --server.address 127.0.0.1

# One configured private path governs every private read.
export EQUITY_CONSOLE__PRIVATE_DIR=/absolute/path/to/private-root
uv run equity dashboard export        # static export: never contains a private page
```

## One configured path, explicit inputs

`EQUITY_CONSOLE__PRIVATE_DIR` (or `console.private_dir` in `config/settings.yaml`) is the
only private location the console reads, and it is never discovered:

| Read | Path under the configured root |
| --- | --- |
| Portfolio ledger (read-only) | `portfolio/portfolio.sqlite3` |
| Book marks | `marks.json` |
| Reconciliation statement | `statement.csv` |
| Private watchlist | `watchlist.yaml` |
| Tracking state | SQLite state written by `equity signal scan` |

The **as-of instant is always an explicit UTC input** (`2024-01-05T21:00:00Z`). The clock
is never used as a valuation time, and a timezone-naive or non-zero-offset timestamp is
refused rather than silently re-interpreted. Unset path, missing instant, missing ledger
or missing marks each render *unavailable* — never `0`, never a blank table.

## Pages and what each one will not do

| Page | Shows | Refuses to |
| --- | --- | --- |
| **Portfolio** | as-of NAV, cash, positions (quantity, basis status, mark age in exact-venue sessions, `synthetic`/`unknown` mark provenance), allocation, reconciliation | show a total while any position is unvalued; adjust the ledger to make numbers agree |
| **Data Readiness** | per-market availability, row/symbol counts, latest session, health alerts, per-instrument coverage for tracked watchlist instruments (price/feature status and sessions, ages, latest scan) | present a market-level union as per-instrument coverage; report `success` while any tracked instrument is unobserved |
| **Research Runs** | immutable runs: protocol, the manifest's derived price origin, finalisation age, last attempt | re-derive or upgrade a label; count an unvalidated directory as a run |
| **Signals** | established candidates with reason, session, source, price/deviation/threshold | show a candidate as an order or a calibrated probability; read "no candidate" from missing data |

Every panel renders exactly one state from the shared vocabulary — `info`, `success`,
`warning`, `error`, `unavailable`, `partial`, `refused`, `gated`. A `partial` panel shows
the real subtotal and says it is not a total; an `unavailable` panel names the missing
evidence.

## Per-instrument coverage (Data Readiness)

Instrument coverage is **tracked-instrument coverage**: it answers "does this watchlist
identity have price and feature rows as of the cut-off", not "is the market complete".
It is read through `signals.readiness.build_tracking_readiness` with the configured
private root bound for both the watchlist file and the tracking database.

- Rows are labelled `tracked watchlist instruments only, not market-wide coverage`.
- Coverage ages are **descriptive calendar days** relative to the report's own cut-off
date, which is printed on the page. They are not a valuation instant.
- An unconfigured path, unreadable watchlist/state or an empty watchlist renders
*unavailable* with the reason — never `0 instruments`, never an empty table.
- Any tracked identity without rows keeps the page from reporting success, so an
otherwise-successful page becomes *partial*: a healthy market summary is never allowed to
imply instrument completeness.
- Scan or signal history that could not be read makes those columns *unavailable* and
likewise keeps an otherwise-successful page from reporting success: an unreadable count is
never `0`, and an unreadable scan is never `no scan`.

## Privacy and display-only guarantees

- Private pages are **app-only**: no static export file, no navigation link, no Pages
  build, no public API projection, no W&B projection.
- A page never writes: no ledger event, no mark, no report, no cache file, no backfill, no
  model fit or inference. Rendering leaves the configured directory byte-identical, which
  is proven over a **populated** real ledger (real SQLite read, real marks, real statement),
  not only over an empty one. The comparison covers directories as well as file bytes, so
  a render that created an empty directory would fail it.
- The loopback check runs **before** any private import or read; a non-loopback bind
  renders `refused` and touches nothing.
- Research evidence (`data/findings/`) is local research evidence, not private account
  data: `data_provenance` there describes **price origin only** — never action, feature or
  prediction inputs, and never licensing, accuracy or point-in-time proof.

## Related

- [Private portfolio ledger](20261103-private-portfolio-ledger.md) — the CLI reports the
  Portfolio page reads.
- [Private tracking preview](20261016-private-tracking-preview.md) — watchlist, readiness
  and delivery state.
- [Backtesting guide](backtesting.md) — run manifests and price-origin provenance.
- ADR-0018 (private views and loopback), ADR-0024 (portfolio ledger), ADR-0027
  (row-bound price origin).
