# Private Portfolio Ledger: Canonical CSV Import

The private portfolio ledger is a local, single-account book of ownership events.
It is separate from the medallion catalog, from the watchlist and tracking state
under `data/tracking/`, and from public signal history. It holds no orders, no
broker credentials and no verified broker statements.

This page documents the one command that exists today, `equity portfolio import`,
and the canonical CSV it accepts. Fixtures are invented in the tests; importing a
real private account is a separate owner decision (ADR-0024) and is not
authorized by this document.

> **Implementation status:** the ledger core and the canonical-CSV import were
> implemented and reviewed offline (P01). This page describes local behavior only;
> it is not operational readiness, and it certifies no tax treatment.

## Where the ledger lives

The ledger is one private SQLite database at `data/portfolio/portfolio.sqlite3`
(`DATA_DIR / "portfolio"`). It follows ADR-0006: it is an auxiliary artifact, so
it is never placed under `data/lake/`, never listed in `data/catalog.jsonl` and
never pointblank-validated. The writer creates the directory with `0700` and the
database with `0600` permissions.

`data/*` is git-ignored apart from `data/.gitkeep` and `data/catalog.jsonl`, so a
private ledger cannot be committed by default. Do not special-case it open, and
do not copy it into an export, a dashboard build, a notebook output or a public
page. There is no supported automated backup yet (open item P00-4).

## Canonical CSV: the only accepted input

`equity portfolio import` accepts exactly one format: the frozen canonical CSV.
There is no broker-export mapping and no plugin surface, so an export from another
tool has to be converted to these columns first. The header must be exactly these
18 columns, in this spelling:

| Column | Meaning |
| --- | --- |
| `account` | Neutral local account label, e.g. `main`. Never a real account number. |
| `external_source` | Short label of the source that produced the row. |
| `external_id` | Source event ID; the idempotency key together with `account`. |
| `event_type` | One of the seven event types below. |
| `effective_at` | Timezone-aware ISO-8601 time the event took effect (e.g. `2024-01-02T15:00:00Z`). |
| `received_at` | Timezone-aware ISO-8601 time the source recorded it. |
| `sequence` | Deterministic positive integer source sequence; file order never orders events. |
| `market` | Canonical market; must be `us_equity` when an instrument is named. |
| `mic` | Canonical MIC (venue), e.g. `XNAS`. |
| `symbol` | Exact instrument symbol at that venue. |
| `currency` | `USD` only; the ledger performs no FX conversion. |
| `cash_effect` | Signed cash movement in USD (negative is cash out). |
| `quantity` | Per-instrument quantity; positive only. |
| `price` | Per-share price; positive only when stated. |
| `fees` | Non-negative fee already reflected in `cash_effect`. |
| `basis` | Total book cost in USD, stated on `OPENING` positions only. |
| `basis_status` | `KNOWN` (default) or `UNKNOWN`. |
| `reverses_external_id` | External ID of the event a correction reverses; blank otherwise. |

Money, quantity, price, fees and basis are exact decimals: they are parsed from
decimal text, never from binary floats, and they are never rounded. Leave a column
blank when the event does not use it.

Identities are canonical `(market, mic, symbol)` triples. A symbol is never mapped
to a market or venue by guessing from ticker text (ADR-0016), so `market`, `mic`
and `symbol` are all required together, or all blank for cash-only events.

## The seven event types

- `OPENING` — the starting state: cash and/or positions. A position row states its
  total basis, or states `basis_status=UNKNOWN`.
- `BUY` — shares in, cash out; a purchase fee is part of the cash effect.
- `SELL` — shares out, cash in; a sale fee reduces the proceeds.
- `DEPOSIT` — external cash in. It is not investment profit.
- `WITHDRAWAL` — external cash out. It is not investment loss.
- `FEE` — a cash-only fee that is not already part of another event's cash effect.
- `CASH_DIVIDEND` — the positive net cash amount received; withholding is netted
  in the cash effect, and dividends are income, not basis.

There is deliberately no split, transfer-in, spin-off, merger or conversion type.

## Usage

Validate a file first, then commit it. Both commands run the same full validation.

```bash
# Validate and preview: full validation, proposed effects, no writes at all.
uv run equity portfolio import --file ledger.csv --dry-run

# Commit atomically; prints inserted/unchanged counts.
uv run equity portfolio import --file ledger.csv
```

Options:

- `--file PATH` — the canonical CSV to import. Required.
- `--dry-run` — validate fully, print the proposed effects and write nothing.
- `--account LABEL` — the neutral local account label every row must carry.
  Defaults to `main`. It is an identifier for your own book, never a broker
  account number, and it is not a `Settings` value or an `EQUITY_*` config key.
  A row whose `account` column differs from `--account` is rejected, because one
  ledger holds one account.

A committed batch prints `inserted N, unchanged N, rejected 0`. A re-import of a
file whose content is unchanged is a no-op: every event is reported as
`unchanged` and no row is duplicated. A batch is atomic, so a rejected row leaves
the stored ledger untouched.

## What `--dry-run` guarantees

`--dry-run` performs the same full validation as a commit and prints:

- the number of events proposed as new versus already stored identically;
- the event count per event type;
- the quantity delta per instrument;
- the cash delta of the file.

It writes nothing: no `data/portfolio` directory, no database file, no rollback
journal or checkpoint, no backup and no lake table. The preview reads the stored
ledger read-only when it already exists, and a missing ledger is never created by
a dry run. A dry run exits `0` for a valid file and `1` for an invalid one.

## What is deliberately rejected

Each rejection exits `1` and nothing is written:

- **Other markets or currencies.** Only `us_equity` and USD are supported; a
  `cn_ashare` row or a EUR amount is refused rather than approximated by FX.
- **Splits and quantity adjustments.** `SPLIT`, `STOCK_SPLIT`, `REVERSE_SPLIT`,
  `BONUS_ISSUE`, `STOCK_DIVIDEND`, `SPIN_OFF`, `MERGER` and similar types are not
  representable, because no split accounting is approved (ADR-0024 decision 9).
- **Oversells.** A `SELL` that would take a position negative in sequence order is
  refused. Selling a position down to exactly zero is allowed.
- **Changed content under one external ID.** Re-importing the same
  `(account, external_id)` with identical content is a no-op; with different
  content it is a conflict and the batch is rolled back. Corrections are explicit
  reversal/repair events, never an in-place rewrite (ADR-0024 decision 5).
- **Ambiguous ordering or duplicates.** A duplicate external ID inside one batch,
  or two events reusing one `(account, sequence)`, is refused: ordering is never
  inferred from file order.
- **Malformed rows.** A header that is not the frozen schema, a wrong field count,
  a file that is not UTF-8, a naive timestamp, a non-finite or malformed decimal,
  a negative quantity, a blank required column or a missing account label is
  rejected with the 1-based file line number (the header is line 1).
- **A missing or unreadable file**, and a ledger file that is corrupt, has an
  unsupported schema version or is not a SQLite database: refused, never
  recreated or overwritten.

Rejection messages name the row, the column or event type, the external ID and a
reason category. They never contain an amount, quantity, price, basis, instrument
symbol or account label.

## Privacy rules

- Private balances, holdings, notes and full account identifiers never appear in
  logs, telemetry, error text, commits, static exports, Pages, W&B or public/API
  projections (ADR-0024 decision 11).
- The import is a local command. Any future display surface for this ledger must
  be loopback-only; forwarded/proxy rejection from ADR-0018 stays in force, and
  multi-user or remote access is not authorized.
- Broker credentials and full account identifiers stay out of `Settings` and away
  from every `EQUITY_*` config key. `--account` takes a neutral label such as
  `main`, and the ledger never stores a credential.
- `data/portfolio` is private: do not commit it, export it, or point a dashboard
  build at it.

## Not implemented yet

- `equity portfolio positions`, `equity portfolio summary` and
  `equity portfolio reconcile` do not exist yet. Their names are frozen in
  ADR-0024 decision 13, but they read the P02 projections, so they are not
  registered, stubbed or hinted at early. Only `import` is available.
- No projections at all: quantities, cash, average cost, realized/unrealized P&L,
  dividends/income totals and NAV are not derived or displayed yet (P02).
- No valuation: no stored marks, no staleness bound, no missing/future/wrong-
  currency mark handling.
- No reconciliation against a statement, and no tax-lot or tax-return treatment.
- No split accounting, no FX or multi-currency totals, no short/margin events.
- No backup, restore, migration or compaction: nothing is backed up, migrated or
  deleted automatically (open item P00-4).
- No broker API, no automatic ingestion or backfill, and no scheduler.

Related records: [ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md)
(private portfolio ledger and book accounting), ADR-0006 (auxiliary data paths and
Pydantic write boundaries), ADR-0016 (canonical instrument identity) and ADR-0018
(loopback-only private surfaces).
