# ADR-0026: Portfolio Projection Precision, Marks and Reconciliation

**Status:** Accepted (2026-11-07). The owner accepted this record alongside
ADR-0027 after approving the bounded fixture-only P02 plan. The P02 policy gate
is satisfied; implementation and independent acceptance are still outstanding.
No operational use or E01-6 implementation is authorized by this acceptance.

**Recorded:** 2026-11-07

**Deciders:** Repository owner

**Related:** [ADR-0024](0024-private-portfolio-ledger-and-accounting.md),
[ADR-0006](0006-auxiliary-data-paths.md),
[P02 specification §5.3](../plans/20261102-01-portfolio-and-research-implementation-spec.md#53-p02--projections-and-valuation-contract),
[current pickup](../handoffs/20261107-01-portfolio-projections-and-open-items.md),
[separate E01-6 policy](0027-row-bound-price-source-provenance.md).

## Context

ADR-0024 establishes average-cost accounting, unknown basis, stored raw marks,
reconciliation and four frozen CLI names. P01 already stores exact Decimal
ledger events, orders them by source sequence and opens read connections without
creating a database. Only `equity portfolio import` exists. There are no basis,
P&L, valuation or reconciliation projections yet.

P02 needs explicit rules for repeating average costs, as-of replay, session age
and statement tolerances before code. The accepted ledger schema also contains
net dividend cash but no dedicated gross-dividend or withholding fields. These
limits must not become guessed values or an implicit schema migration.

This is an additive amendment to ADR-0024 Decisions 4, 6, 8, 10 and 13,
not a replacement of that record. Decision 6's gross/withholding display is
narrowed for schema-v1 inputs as described below. ADR-0024 remains unchanged;
its split prohibition, private boundary and migration/backup gates remain in
force. Acceptance establishes this policy amendment, not implementation completion.

## Decision

These decisions bind the bounded fixture-only P02 implementation.

### 1. Bounded ownership and storage

P02 covers one invented account in `us_equity`, USD only. Reuse `LedgerEvent`,
`event_order_key` and `LedgerStore.load_events()`; derive balances and projections
in memory. Preserve ledger schema version 1 and its append-only/idempotent import
contract. No projection cache, schema alteration or hidden write is added.

Stored marks are explicit local JSON inputs, not a lake reader or quote service.
A version-1 envelope contains `schema_version`, `marks` and `valuation_blocks`.
Strict Pydantic models validate fixture objects before serialization and validate
files again on read; decimal values are serialized as strings, never floats.
Unknown schema versions and extra fields are rejected.

Only test fixture builders write these files in temporary directories, with
private directory/file modes `0700`/`0600`. There is no mark-import/write command,
default file creation or persistence inside a read command. Any future supported
private mark artifact belongs under `DATA_DIR / "portfolio"`, outside the lake
and catalog; its producer requires a separate approved plan. P02 neither creates
nor accesses a real private mark file or account.

### 2. Deterministic effective-date replay

- Require an explicit timezone-aware UTC valuation instant `T` (`--as-of`).
  Normalize aware offsets to UTC; do not use the current clock as a default.
- Preserve `(account, sequence, external_id)` ordering. Duplicate IDs or source
  sequences and multiple accounts fail visibly; file order and receipt order
  never break ties.
- Require nondecreasing `effective_at` in sequence order for the supplied account
  timeline; equal times are ordered by sequence. A temporal inversion is rejected
  at the projection boundary, not repaired by re-sorting or editing events.
- Replay events with `effective_at <= T`, including equality. Receipt/recording
  times remain provenance, not ordering or historical-knowledge cutoffs. Output
  is explicitly an effective-date reconstruction from the supplied ledger, not
  proof of what was known at `T`.
- Revalidate the replay invariants, including oversells and unsupported negative
  cash/margin states. A stored row that cannot support a valid projection is
  refused without modifying it or reopening P01 import implementation.
- Repair events apply their stored event-kind, cash and quantity effects once.
  `reverses_external_id` is an audit link, not an instruction to erase a target
  or synthesize its inverse. A richer reversal model needs another decision.

### 3. Precision and average-cost accounting

Use Decimal only, with an explicit local context of 256 significant digits;
never depend on the process-global context. Retain P01's input magnitude bounds.
Addition, subtraction and multiplication must be exact or fail visibly on
precision loss/overflow. Do not round source events, cash or quantities to fit.

For a known holding with quantity `Q` and total basis `B`:

- A BUY adds its quantity and `quantity * price + fees` to basis. Require its
  signed `cash_effect` to equal the negative of that total.
- A partial SELL of `q` removes `B * q / Q`, rounded directly to 38 fractional
  USD digits with `ROUND_HALF_EVEN`; remaining basis is the exact subtraction.
  Detect halfway cases correctly rather than introducing double rounding. The
  removed basis must remain between zero and `B`.
- A full liquidation removes exactly `B`, leaving zero basis and zero quantity;
  do not strand a rounding residual. Realized gain is the sale's net cash
  proceeds minus the removed basis. Require SELL cash to equal
  `quantity * price - fees`.
- Average cost is a derived display value, not a separately rounded accumulator.
  Money displays use cents with `ROUND_HALF_EVEN`; quantities display exactly.
  Neither display rounding nor reconciliation tolerances affect replay.

Sum signed `cash_effect` exactly once per event. Embedded BUY/SELL fees are
reported but never subtracted again. A standalone FEE contributes its cash
outflow once; its descriptive `fees` field, if supplied, must agree with the
outflow rather than adding a second expense. Fee categories and totals disclose
this distinction. Inconsistent stored monetary relationships fail projection,
never trigger a source-history rewrite.

Dividends contribute net cash income, not basis. Schema-v1 cannot establish a
separate gross amount or withholding: report those fields as unavailable, not
zero or `net + fees`. A nonzero dividend `fees` field without separately approved
semantics makes that breakdown unavailable; it is not assumed to be withholding.
Deposits/withdrawals are external flows, not profit. Total book gain combines
realized gain, unrealized gain and net dividend income less standalone fees;
purchase/sale fees are already included in gain and must not be deducted again.
No time-weighted, money-weighted, tax-lot or tax-return claim is made.

### 4. Unknown basis remains unavailable

An unknown opening basis makes the instrument's pooled basis and basis-dependent
P&L unavailable. Later purchases do not repair that pool. Cash, quantity, fees,
net income and correctly marked market value can remain known; missing basis
alone does not prevent NAV or allocation when all required marks are valid.

On liquidation, the closed position has zero quantity/basis, but any realized
gain involving the unknown pool remains unavailable. A subsequent position may
start with known basis; historical/aggregate P&L containing an unknown component
stays unavailable. Never substitute zero, a current mark or a known subtotal for
a complete gain. Keep availability/reason fields separate from numeric values.

### 5. Raw stored marks and venue-session age

Each mark carries exact `(market, MIC, symbol)`, `currency=USD`, a positive
Decimal `price`, venue `session`, aware UTC `as_of`, a non-secret source label,
`price_basis=unadjusted`, and explicit `provenance=synthetic|unknown`. Version 1
uses session-close marks: `as_of` must equal the exact venue's scheduled session
close, including early closes. It is a quote time, not a source-publication proof.

Select the latest mark with `as_of <= T` for the exact identity. Conflicting
same-identity/same-time marks block that instrument; identical duplicates may
collapse. Never choose another venue, adjusted price, future quote or older
competing quote to hide a conflict or bad latest candidate. A valid earlier mark
may be selected when newer marks are strictly in the future, but must pass the
same visible age rule. Structurally invalid input files fail validation without
partial acceptance; missing required marks remain per-instrument unavailability.

Age is the count of completed sessions for that exact MIC after the mark's
session and through `T`. No calendar-day, market-union or weekday approximation:
Friday's close through a closed weekend has age zero. The default maximum is
one session: age zero or one is valid, age two is stale. Expose the applied bound;
P02 may offer a stricter zero-session option, never a value above one. No new
Settings/environment namespace is needed for this bounded CLI option.

A `valuation_blocks` entry names an exact identity and the supported reason
`unsupported_quantity_adjustment`. A known affected position remains visible
with its recorded quantity explicitly uncertain, but cannot be valued. The
fixture supplies this knowledge; P02 does not discover unreported splits, query
actions or certify that real history is split-free. Split import/accounting
remains prohibited under P00-2.

For missing, stale, wrong-currency, adjusted, conflicting or quantity-uncertain
required marks, retain cash, recorded quantities and valid marked subtotal,
name the affected instruments, and withhold complete NAV, portfolio allocation
and valuation-dependent portfolio P&L. Do not divide by a marked-only subtotal.
Independently known realized gain/income may still be displayed with explicit
scope. Allocation includes cash and all open holdings and is unavailable when
NAV is zero. There is no network fallback or silent carry.

These book-mark flags are not the Bronze price-source envelope defined in
ADR-0027 and cannot upgrade research or forecast provenance.

### 6. Statement reconciliation

Use one canonical UTF-8 CSV, no broker mapping/plugin, with columns
`account,as_of,line_type,market,mic,symbol,currency,quantity,cash`.
Require one neutral account label, a common aware snapshot instant equal to `T`,
USD throughout and exactly one CASH row. CASH has blank identity/quantity;
POSITION has exact identity/quantity and blank cash. Quantities/cash are finite
nonnegative decimal text under the existing input bounds; zero is explicit.
Duplicate position identities, multiple cash rows, malformed fields and unsupported
currencies are invalid, not silently combined or converted.

Compare the union of nonzero ledger holdings and explicit statement positions.
A statement-only position compares with ledger quantity zero; an omitted ledger
holding or missing cash line is unavailable, not implicit statement zero. Missing
required cash makes the report unavailable; contradictory duplicate/malformed
rows reject the input. Snapshot-time disagreement is unavailable.

Compare unrounded absolute differences, inclusively:

| Line | Matched when |
| --- | --- |
| Quantity | `abs(statement - ledger) <= 0.00000001` share |
| Cash | `abs(statement - ledger) <= 0.01` USD |

Report `matched | mismatched | unavailable`, both quantities/amounts, signed
`statement - ledger` delta, applied tolerance and reason. Overall unavailable
takes precedence over mismatched, which takes precedence over matched; retain
all line outcomes. No price marks are needed for quantity/cash reconciliation.
Never insert balancing events, adjust marks or alter tolerances to force agreement.

### 7. Frozen local CLI and failure contract

Land the three commands with working projections, never stubs:

| Command | Required input and output |
| --- | --- |
| `equity portfolio positions` | `--as-of`, `--marks`; exact holdings, basis/status, mark/source/session/age, value, unrealized gain and blockers |
| `equity portfolio summary` | `--as-of`, `--marks`; cash/flows/fees/net income, gains, marked subtotal, complete NAV/allocation or explicit unavailability |
| `equity portfolio reconcile` | `--as-of`, `--statement`; line comparisons and overall reconciliation status |

All retain a neutral `--account` label (default `main`, not a broker account
number). Valuation commands may accept `--max-stale-sessions` only as zero or one.
Use existing native Typer wiring, docstrings, Annotated options and help scans.
Exit 0 only for complete requested results (or all reconciliation lines matched);
exit 1 for required unavailability, mismatch or invalid input. Partial reports
remain visible; a nonzero exit never means they were complete. Fields declared
unsupported, such as schema-v1 gross/withholding, are not requested prerequisites
for otherwise complete net-income reporting.

Read commands never create a missing DB, directory, journal/WAL/SHM, mark file,
checkpoint or cache; a missing ledger is unavailable, not an empty account.
Private values may appear in the explicitly requested local report, not logs,
telemetry, stderr exception details, public/API output or static exports.
Affected identities belong in the report, not value-free error messages.

### 8. Acceptance and implementation gate

The independent hand oracle from ADR-0024 remains mandatory:

| Step | Expected result |
| --- | --- |
| Open 10 shares, basis 1000, cash 1000 | Quantity 10, cash 1000, basis 1000 |
| Buy 5 at 120, fee 5 | Quantity 15, cash 395, basis 1605 |
| Sell 4 at 130, fee 2 | Quantity 11, cash 913, basis 1177, realized gain 90 |
| Receive net dividend 11; mark at 125 | Cash 924, value 1375, NAV 2299, unrealized gain 198, total gain 299 |

Test repeating/fractional costs, half-even boundaries, full liquidation/reopening,
unknown-basis sales, fees once, external flows, as-of equality/inversions,
file-order/restart invariance, zero NAV, mark conflicts/future/holiday/early-close
and age-one/age-two boundaries, split blocks and tolerance equality/just-over.
Preserve import idempotency, duplicate-conflict rollback and oversell regressions;
prove all read and existing dry-run paths leave filesystem and DB bytes unchanged.
Test redaction, CLI exits/help and package import boundaries.

The owner-approved plan and this accepted amendment are not implementation
acceptance. Before code, present the minimal file plan and stop for owner
approval of any deviation from this bounded contract.
Use one bounded writing worker, a separate adversarial reviewer with a formal
verdict, checkpoints outside the repo, coordinator mutation/restore proofs and
serialized tests. Apply spec §9 final gates to the implemented diff. No review,
mutation or product-test success is claimed for drafting this record.

## Consequences

P02 can be checked with a hand oracle without providers or a ledger migration.
Unavailable numbers remain distinguishable from zero; session age and statement
tolerance become reviewable rather than hidden defaults. Very small proportional
basis allocations have a documented rounding rule and full-close conservation.

The trade-off is deliberate refusal of ambiguous timelines/monetary relationships
that P01 could store, no gross/withholding certification from schema v1, and no
real split discovery. A fixture JSON mark input is not a production mark producer.

Rejected alternatives: binary floats; cents-rounded replay; receipt-time sorting;
unknown-basis substitution; adjusted research prices as book marks; partial NAV
presented as complete; omitted statement rows treated as zero; automatic repairs,
broker plugins, caches or SQLite evolution to make the first slice convenient.

No commit, private-account/broker access, provider/LLM call, real ingestion,
refit/training, migration/backfill/backup automation, deployment, W&B change or
freshness widening is authorized. P00-2/P00-4 and D01 remain gated. E01-6 requires
its own approved implementation plan in addition to accepted ADR-0027;
accepting both records does not join their implementation scopes.

## References

- `src/equity_lake/portfolio/models.py` — exact event fields, `event_order_key`,
  unknown basis, validation and quantity-only replay.
- `src/equity_lake/portfolio/store.py` — schema v1, Decimal text storage and
  read-only `load_events()` / `_connect(writable=False)`.
- `src/equity_lake/portfolio/csv_import.py` — frozen 18-column import and no-write preview.
- `src/equity_lake/core/calendar.py` — exact-MIC session helpers.
- `src/equity_lake/cli/commands/portfolio.py` and
  `tests/unit/test_portfolio_cli.py` — existing import-only surface and isolation proofs.
