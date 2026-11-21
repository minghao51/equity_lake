# Private Portfolio Ledger: Import, Projections and Reconciliation

The private portfolio ledger is a local, single-account book of ownership events.
It is separate from the medallion catalog, from the watchlist and tracking state
under `data/tracking/`, and from public signal history. It holds no orders, no
broker credentials and no verified broker statements.

This page documents `equity portfolio import` (the only write path, accepting the
canonical CSV), the three read-only reports `positions`, `summary` and
`reconcile`, the stored book-mark file those reports read, and the accounting,
staleness and tolerance rules behind them. Fixtures are invented in the tests;
importing a real private account is a separate owner decision (ADR-0024) and is
not authorized by this document.

> **Implementation status:** the ledger core, the canonical-CSV import (P01) and
the P02 projections, stored marks, reconciliation and read reports are implemented
and reviewed offline. This page describes local behavior only; it is not
operational readiness, and it certifies no tax treatment. Marks are explicit local
inputs: there is no mark producer, quote service or network fallback.

## Where the ledger lives

The ledger is one private SQLite database at `data/portfolio/portfolio.sqlite3`
(`DATA_DIR / "portfolio"`). It follows ADR-0006: it is an auxiliary artifact, so
it is never placed under `data/lake/`, never listed in `data/catalog.jsonl` and
never pointblank-validated. The writer creates the directory with `0700` and the
database with `0600` permissions.

`data/*` is git-ignored apart from `data/.gitkeep` and `data/catalog.jsonl`, so a
private ledger cannot be committed by default. Do not special-case it open, and
do not copy it into an export, a dashboard build, a notebook output or a public
page. Backup is **manual and verified** (P00-4, [ADR-0031](../decisions/0031-p00-4-ledger-backup-restore-migration-compaction.md)):
`equity portfolio migrate` writes a verified exact-file copy before it touches anything, and
`equity portfolio restore` / `equity portfolio compact` verify before they replace or rebuild.
There is no automated, scheduled or remote backup, and none is authorized. All three are
report-only by default.

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

`SPLIT` is the only quantity-adjusting type (ADR-0029); transfer-in, spin-off, merger
and conversion types are deliberately absent.

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

## Splits: the one quantity-adjusting event (ADR-0029)

> **Storage: schema v2 (2026-11-16).** A ledger from an older build is schema v1, whose SQLite
> `CHECK` rejected `SPLIT`; splitting is possible on v2 (ADR-0031). This build refuses to read
> or write a v1 ledger and names the upgrade, and the upgrade is never implicit:
>
> ```bash
> uv run equity portfolio migrate            # report only: verifies and writes nothing
> uv run equity portfolio migrate --apply    # verified .v1.bak backup, then the rebuild
> ```
>
> The upgrade first writes a verified exact-file copy (`portfolio.sqlite3.<utc>.v1.bak`, mode
> `0600`) beside the ledger, then rebuilds the table in one transaction and proves the record is
> unchanged (row count and record digest). It refuses an unsupported version, a rollback
> journal beside the file, an unexpected column order or a leftover rebuild table — always
> before the first write. There is no down-migration: restoring that verified backup is the
> reversal. A split import into a v1 ledger now fails with a message naming `equity portfolio
> migrate`, not with a misleading "could not be read … retry".
>
> **Restore and compaction (2026-11-17, ADR-0031 decisions 4-5).** Both are report-only by
> default and never automatic:
>
> ```bash
> uv run equity portfolio restore --from portfolio.sqlite3.20261116T101500Z.v2.bak   # plan only
> uv run equity portfolio restore --from <verified>.bak --apply                      # back up current, swap, re-verify
> uv run equity portfolio compact                                                    # plan only
> uv run equity portfolio compact --apply                                            # VACUUM INTO, verify, swap, re-verify
> ```
>
> A restore re-runs the five verification checks on the candidate, refuses a different account
> label (one private book holds one account), a rollback journal, the current file itself, or
> insufficient free space, then backs up the **current** file first — it may hold events the
> backup lacks, and the report tells you how many it rolls back and the newest booking instant
> it gives up. A compaction rebuilds through `VACUUM INTO` into a fresh file and is accepted
> only when the version, row count and `(account, external_id, content_digest)` digest all match
> the pre-compaction state; no event is deleted and `row_id` renumbering carries no meaning.

`SPLIT` is the only event kind that changes a quantity without moving cash. Its
`quantity` column holds the **share ratio** (new shares per old share) — not a share
count — because the ledger's stored columns are fixed and a dedicated `split_ratio`
column would need its own schema change plus an authorized migration run (the v1→v2
mechanism exists, P00-4/ADR-0031; no column has been added).

```text
main,fixture,evt-0002,SPLIT,2024-06-10T13:30:00Z,2024-06-10T13:30:00Z,2,us_equity,XNAS,NVDA,USD,0,10,,0,,KNOWN,
```

- **Replay:** quantity is multiplied by the ratio and the *total* basis stays unchanged, so
  the average cost per share divides by the same ratio. Cash, fees and realised gain are
  untouched, and an unknown opening basis stays unknown.
- **Validation:** the ratio must be positive and not `1`; `cash_effect` must be `0`; no
  price, fee or basis is allowed. A sale of post-split shares is legitimate.
- **No position:** a split for an instrument you do not hold is recorded and inert (broker
  exports carry full histories) — never an error.
- **Replacement:** a restated split is never an edit. Import a reversal (a second `SPLIT`
  whose ratio is the exact reciprocal, with `reverses_external_id` naming the original)
  followed by the corrected split. The product must be exactly `1`.
- **Marks:** a stored mark from *before* the split is refused for a post-split instant — the
  position renders `missing_mark` rather than valuing the new quantity at an old price.
  Replace the mark (or import a fresh one); the ledger never touches `marks.json` and never
  clears a `valuation_blocks` entry, which stays an explicit operator action.

Broker aliases (`STOCK_SPLIT`, `REVERSE_SPLIT`, `QUANTITY_ADJUSTMENT`, …) are still
rejected: a ratio convention is never guessed.

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
- **Split aliases and other quantity adjustments.** The bare `SPLIT` kind is accepted
  (see the split section below). `STOCK_SPLIT`, `REVERSE_SPLIT`, `BONUS_ISSUE`,
  `STOCK_DIVIDEND`, `SPIN_OFF`, `MERGER` and similar types are refused: a ratio
  convention is never guessed, and split discovery does not exist.
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

## Read-only reports: `positions`, `summary`, `reconcile`

Each report reads the stored ledger plus exactly one explicit local input file, and
never writes anything: no database, no directory, no journal/WAL/SHM companion, no
mark file, no checkpoint and no cache. A missing ledger is reported as unavailable
and is never created by a read — a non-existent book is not an empty account.

All three require an explicit timezone-aware `--as-of` (UTC `Z` or an offset).
There is no "now" default. Output is an **effective-date reconstruction** from the
supplied ledger: it says what the stored events imply for that instant, not what a
source had published or what you knew then. Events with `effective_at <= --as-of`
are replayed once each in `(account, sequence, external_id)` order; file order and
`received_at` never order events, and a temporal inversion in the stored sequence
is refused rather than repaired by re-sorting.

| Command | Required inputs | Reports |
| --- | --- | --- |
| `positions` | `--as-of`, `--marks` | Exact holdings, basis status, average cost, realized gain, the selected mark (price, source, provenance, session, age), value, unrealized gain and blockers |
| `summary` | `--as-of`, `--marks` | Cash, deposits/withdrawals, fees, net dividend income, realized gain, valid marked subtotal, and complete NAV/allocation/total gain or explicit unavailability |
| `reconcile` | `--as-of`, `--statement` | Line-by-line quantity and cash comparison; no marks needed |

Every command also takes the neutral `--account LABEL` (default `main`). The two
valuation commands accept `--max-stale-sessions` as `0` or `1` only — never above
one. Reports print private values only because you asked for them locally;
identities appear in the report, never in error text.

```bash
uv run equity portfolio positions --as-of 2024-01-05T21:00:00Z --marks marks.json
uv run equity portfolio summary --as-of 2024-01-05T21:00:00Z --marks marks.json --max-stale-sessions 0
uv run equity portfolio reconcile --as-of 2024-01-05T21:00:00Z --statement statement.csv
```

### Exit codes

`0` means the requested result is complete (for `reconcile`: every line matched).
`1` means required unavailability, a mismatch, invalid input, a refused ledger or a
refused mark/statement file. Partial reports stay visible, and a nonzero exit never
means the report was complete.

## Stored book marks (version 1)

`--marks` names one local JSON file. It is an input, not a cache or a producer:
there is no mark-import command, and a read never creates it. The version-1
envelope has exactly three fields:

```json
{
  "schema_version": 1,
  "marks": [
    {
      "market": "us_equity",
      "mic": "XNAS",
      "symbol": "AAA",
      "currency": "USD",
      "price": "125",
      "session": "2024-01-05",
      "as_of": "2024-01-05T21:00:00+00:00",
      "source": "fixture",
      "price_basis": "unadjusted",
      "provenance": "synthetic"
    }
  ],
  "valuation_blocks": [
    {"market": "us_equity", "mic": "XNYS", "symbol": "BBB", "reason": "unsupported_quantity_adjustment"}
  ]
}
```

Rules the file must satisfy, or the whole file is rejected (never partly accepted):

- `schema_version` is exactly `1`; unknown versions and extra or missing fields are
  refused, so a future format fails loudly instead of being half-read.
- `price` is decimal **text**, never a JSON number or float.
- `currency` is `USD`, `price_basis` is `unadjusted` (research-adjusted prices are
  not book marks) and `price` is positive and exactly representable.
- `provenance` is explicitly `synthetic` or `unknown`. It is never inferred from a
  path or a filename, and a book mark cannot upgrade the provenance of research or
  forecast artifacts.
- `as_of` is timezone-aware and must equal the **exact MIC's scheduled close** for
  `session`, early closes and holidays included. A weekend, a holiday, a wrong
  close time or a naive timestamp is refused.
- `valuation_blocks` names at most one entry per instrument; the only supported
  reason is `unsupported_quantity_adjustment`. The block is *knowledge supplied by
the file*: P02 never discovers splits, queries corporate actions or certifies that
  history is split-free.

Mark selection uses the exact `(market, MIC, symbol)` identity: the latest mark with
`as_of <= --as-of` wins. A future mark is simply not eligible. Two marks for the same
identity at the same instant with different content block that instrument; two
purely identical duplicates collapse. A conflict is never hidden by falling back to
an older quote, another venue or an adjusted price.

### Session staleness

Age counts **completed sessions of that exact venue** after the mark's session and
through `--as-of`. A Friday close seen on Sunday is age zero; the same close seen
after Monday's close is age one; after Tuesday's close it is age two. Early closes
count as sessions (the close must already have happened), and a holiday week is not
a calendar-day count.

The default bound is **one** completed session, and `--max-stale-sessions 0` is the
only stricter option; a looser bound is refused. When a required mark is missing,
stale, conflicting or quantity-uncertain, cash, recorded quantities and the valid
marked subtotal remain visible, the affected instruments are named in the report,
and complete NAV, allocation and valuation-dependent P&L are withheld. A marked
partial subtotal is never presented as a total and there is no marked-only
denominator.

## Reconciliation

`--statement` accepts one canonical UTF-8 CSV — no broker export mapping and no
currency conversion:

```csv
account,as_of,line_type,market,mic,symbol,currency,quantity,cash
main,2024-01-05T21:00:00Z,CASH,,,,USD,,924
main,2024-01-05T21:00:00Z,POSITION,us_equity,XNAS,AAA,USD,11,
```

- Exactly one `CASH` row (identity and quantity blank, cash stated), any number of
  `POSITION` rows (exact identity and quantity, cash blank), USD throughout, one
  neutral account label and one common snapshot instant.
- Values are non-negative exact decimals; zero is explicit. A blank cell is not
  zero. Duplicate position identities, duplicate cash rows, mixed accounts, mixed
  instants, malformed values and unsupported currencies reject the file.
- A statement without a cash line is accepted but reconciles as **unavailable**;
  contradictory duplicates are rejected outright.

Each line reports `matched`, `mismatched` or `unavailable`, both values, the signed
`statement - ledger` delta and the applied tolerance. Tolerances are inclusive and
apply to unrounded absolute differences: `0.00000001` share and `0.01` USD.

A position the statement states is compared with ledger quantity zero; a ledger
holding the statement omits is **unavailable**, not zero. A snapshot instant that
disagrees with `--as-of` makes every line unavailable with no delta. Overall state
takes the worst line: unavailable beats mismatched beats matched.

Reconciliation never mutates the ledger, inserts a balancing event, adjusts a mark
or widens a tolerance to force agreement, and it is not tax certification.

## Accounting rules behind the numbers

- **Average cost, exact decimals.** Quantities, money and basis are exact decimals
  with 256 significant digits of local precision; a computation that would lose
  exactness is refused instead of rounded. A partial sale removes
  `basis * sold / held` rounded once, half-even, to 38 fractional USD digits; a full
  liquidation consumes the exact residual, so no rounding crumb is stranded.
- **Fees are counted once.** A purchase/sale fee is already inside its cash effect
  and inside the resulting gain. A `FEE` event is one expense, and a descriptive
  `fees` field must agree with its outflow rather than adding a second charge.
- **Dividends are income, not basis.** Net dividend cash is known; **gross amount
  and withholding are unavailable**, not zero, because ledger schema v1 stores only
  the net cash effect. They are never derived from `net + fees`.
- **External flows are not profit.** Deposits and withdrawals are reported
  separately and never treated as investment return. Total book gain is realized
  gain plus unrealized gain plus net dividend income less standalone fees.
- **Unknown basis stays unavailable.** An `OPENING` position may state
  `basis_status=UNKNOWN`. Its pooled basis, its realized gain and every aggregate
  gain containing it stay unavailable — across liquidation and reopening too —
  while cash, quantities, fees, income and a correctly marked NAV remain usable.
  A later purchase never repairs the pool, and unknown basis is never zero.
- **Refusals instead of rewrites.** Oversells, negative cash/margin states,
  inconsistent `cash_effect` relationships, ambiguous ordering and temporal
  inversions are refused; stored history is never edited to make a projection work.
- **Display never feeds calculation.** Money is displayed in cents (half-even),
  quantities exactly, and allocation weights are rounded only for display.

## What a report must never claim

No time-weighted or money-weighted return, no tax-lot or tax-return correctness, no
multi-currency or FX total, no short/margin state, no split *discovery* (only imported
splits adjust), and no historical point-in-time proof. Marks are book marks for this private book only;
they are not the Bronze price-source provenance of ADR-0027.

## Not implemented yet

- No operational mark producer, quote service, broker plugin or network fallback:
  marks are supplied as the local JSON file above, and importing a real account or
  a real quote source remains separately gated.
- Split discovery: imported `SPLIT` events adjust quantity and average cost at replay,
  and replacement is an explicit reversal (ADR-0029). The ledger never scans the lake for
  splits and never clears a `valuation_blocks` entry on its own.
- No FX or multi-currency totals, no short/margin events.
- No automatic backup or compaction and no reset. Every ledger mechanism is explicit and
  report-only by default: `equity portfolio migrate` (verified backup before the rebuild),
  `equity portfolio restore` (verify candidate, back up the current file, atomic replace,
  re-verify) and `equity portfolio compact` (`VACUUM INTO`, record-preserving post-conditions) —
  all P00-4/ADR-0031, and nothing is ever backed up, migrated, compacted or deleted
  automatically.
- No private console or hosted view (P03), no broker API, no automatic ingestion or
  backfill, and no scheduler.

Related records: [ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md)
(private portfolio ledger and book accounting),
[ADR-0026](../decisions/0026-portfolio-projection-precision-marks-and-reconciliation.md)
(projection precision, marks and reconciliation), ADR-0006 (auxiliary data paths
and Pydantic write boundaries), ADR-0016 (canonical instrument identity) and
ADR-0018 (loopback-only private surfaces).
