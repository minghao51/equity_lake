# ADR-0029 — Split treatment: private-ledger quantity adjustment, replacement semantics, and the D01 raw-price reconstruction basis

**Status:** Accepted (2026-11-14)

**Recorded:** 2026-11-14

**Accepted:** 2026-11-14 by the owner. This records the decision; implementation is a
separately authorized stage and nothing here is implemented. The D01 landing additionally
requires [ADR-0030](0030-second-real-source-lake-root.md) (the second lake root).

**Implementation note 2026-11-16:** Part II needed a schema migration after all. Decision 1's
"no migration" reasoning covered the *column set*; the SQLite `CHECK` on `event_type` is
part of the schema, so persisting `SPLIT` required the explicit v1→v2 rebuild defined in
[ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md) (Accepted). The
decision itself is unchanged: the ratio still travels in `quantity` and no column was added.

**Implementation accepted 2026-11-15:** Part II (the ledger `SPLIT` contract — ratio in
`quantity`, exact-reciprocal replacement, split-aware oversell check, pre-split-mark refusal)
is implemented, reviewed over three rounds and accepted by the owner (round 3 `PASS`; record
in spec §10). Part I (reconstruct raw prices at ingest) is **not** implemented and needs its
own authorization together with a scoped landing run.

**Implementation note 2026-11-17 — Part I shipped, and one D01 landing ran.** Part I lives at
the price write boundary (`ingestion/price_reconstruction.py`, called from the orchestrator's
`_write_market`), gated on a **real-source root** (`core/paths.is_real_source_root`): a
second root by definition carries vendor rows, and decision 8 forbids re-inflating the demo
root's rows, so the canonical root is never transformed. Operative rules recorded here
because they are narrower than "reconstruct everywhere":

- The factor is the product of the stored split ratios whose `ex_date` is **strictly after**
  the row's date (a split's own session is already post-split at the vendor), which is the
  exact reciprocal of `with_price_adjustment`'s factor — verified on landed rows to
  `0.0` relative error for OHLC **and** volume against a fresh vendor fetch.
- Fail-closed reasons, all checked before any write: `action_store_missing` (no Silver
  actions in the target root), `action_scope_incomplete` (a ticker absent from the store is
  not "split-free"), `action_invalid` (a ratio that is not finite/positive/not-1 — reverse
  splits are > 1 in the `old/new` convention and stay valid), `action_window_uncovered`
  (the store's newest `ex_date` predates the price rows). There is still no source-issued
  action clock for yfinance, so the residual limit of decision 2 stands and is unchanged by
  this implementation: a vendor omission above the watermark is undetectable (decision 8),
  which is why the store's watermark is reported rather than promised away.
- `--tickers` was added to `equity backfill` (the plumbing already existed in
  `backfill_date_range`) so a landing can be scoped to an explicit list instead of the
  configured universe; an explicitly empty list is refused, never broadened.
- One defect was found while landing and fixed: the **serial** orchestrator branch dropped
  `lake_dir` when calling `_finalize_market`, so `backfill --lake-dir` wrote into the
  canonical root. The parallel branch always threaded it; the serial path now does too, with
  a regression test.

**Amends:** [ADR-0024](0024-private-portfolio-ledger-and-accounting.md) decision 9 (splits
are not approved) and carries the landing shape selected under
[ADR-0028](0028-d01-real-source-slice.md) decision 5. ADR-0024 remains authoritative
everywhere this record is silent.

**Owner input recorded 2026-11-14:** the D01 landing shape is **(a) reconstruct raw prices
at ingest** (ADR-0028 decision 5). This record does not authorize ingestion, a migration or
a lake write; those remain separate authorizations.

## Context

Two questions converged on the word "split", and they touch different storage:

1. **The D01 landing.** The probe in
   [the D01 record](../plans/20261114-01-d01-source-probe.md) showed that yfinance's OHLC
   **and** volume are split-back-adjusted at source, while
   [`with_price_adjustment`](../../src/equity_lake/storage/lake_reader.py) (ADR-0015)
   assumes stored prices are unadjusted and multiplies by the cumulative split ratio at
   nine call sites. Ingesting vendor rows as they arrive would adjust pre-split windows a
   second time — roughly 10× for NVDA's 10-for-1. Nothing in Bronze refuses it, because
   the `price_adjustment_version` guard only fires on frames already stamped as adjusted.
2. **The private ledger.** ADR-0024 decision 9 rejects split events on import
   ([`csv_import.py`](../../src/equity_lake/portfolio/csv_import.py) raises for
   quantity-adjusting kinds) and requires an affected position to carry a
   `valuation_block` with reason `unsupported_quantity_adjustment`. A held position across
   a real split therefore cannot be imported faithfully, and its valuation is blocked by
   design until an amendment exists.

ADR-0024's storage constraints bound the second answer: the ledger's event table has a
fixed column set, and migration was **not** authorized at the time (decision 3; P00-4 was
open — it is now [ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md), which
defines the explicit upgrade). A new `split_ratio` column would require a migration, so the
ratio must be carried by an existing field.

## Decision

### Part I — D01 price basis: reconstruct raw prices at ingest

**Decision 1 — Transform.** Before persistence, each fetched price row is re-inflated from the
vendor's own split events: OHLC is multiplied by the cumulative product of the stored
split ratios whose `ex_date` is **after** the row's date, and `volume` is divided by the
same factor. The stored ratio is the lake convention `old/new` (ADR-0011), so the factor
is its reciprocal product. Bronze therefore holds raw, split-free prices and the
existing `with_price_adjustment` contract is correct **without change**.
**Decision 2 — Inputs and refusal.** The transform consumes the fetched prices plus that
instrument's split actions for the window. If split actions cannot be fetched, read or
validated, the ingest **refuses** (fail closed) rather than writing vendor-basis prices
unlabelled. A missing action is indistinguishable from "no split" and must not be
treated as one.
**Decision 3 — Determinism and re-ingest.** The transform is a pure function of `(prices, actions)`.
Re-running the same ingest produces identical rows, so the keyed merge is idempotent.
Because the vendor can correct an action below a watermark (D00: undetectable), a
later action correction makes the reconstructed history **different for the same
dates**; rewriting it is an explicit, recorded lake operation, never an automatic
side effect of a later fetch.
**Decision 4 — `adj_close` is untouched.** It is stored as fetched (split- and dividend-adjusted) and
remains a cross-check column only (ADR-0011). No research path may consume it.
**Decision 5 — Origin labels do not move.** Reconstruction is not origin evidence: rows remain
`unknown` under ADR-0027. This record adds no `real` claim, no digest-based relabeling
and no retrospective envelope change.
**Decision 6 — No reader, schema, catalog or version change.** No `price_adjustment_version` is
stamped on Bronze; no column is added; the catalog row and validator set are unchanged.
The catalog's price description may note that real rows are reconstructed — text only.
**Decision 7 — Verification at the write boundary (tests, not promises).** (i) A split seam must be
absent after the transform: pre- and post-split closes around the ex-date show no ~ratio
discontinuity. (ii) Re-applying the same actions to the written rows reproduces the
vendor's `adj_close`/`close` relationship within a stated tolerance. (iii) A re-ingest
of the same window is byte-identical.
**Decision 8 — Not authorized here.** Any ingest, any write to `data/lake/`, the mixed-lake decision
(ADR-0028 decision 6: reset versus distinct real path, still open), a live re-inflation
of existing demo rows, and any split *discovery* beyond the vendor's own events.

### Part II — Private ledger: `SPLIT` as a first-class, quantity-adjusting event

**Decision 9 — New event kind, no new column.** `SPLIT` joins the closed set in ADR-0024 decision 4.
Its fields are: the canonical instrument, the effective instant, and
`quantity` = **new shares per old share** (a positive decimal, not `1`). `cash_effect`
is exactly `0`; `price`, `fees` and `basis` are null; `basis_status` is `KNOWN`
(irrelevant to this kind); the currency is USD. Carrying the ratio in `quantity` is a
deliberate, documented overload chosen over a real `split_ratio` column because that
column would require a migration (ADR-0024 decision 3, P00-4). The rejected alternative
is recorded here so it is not rediscovered as a surprise.
**Decision 10 — Import accepts it.** The current import rejection of quantity-adjusting kinds is
 removed for `SPLIT` only. `SPLIT` rows are imported through the same canonical CSV, the
 same idempotency contract (`external_id` + content digest) and the same atomic commit;
 a content change under an existing `external_id` still fails, so a replacement is never
 silent (see 13). The canonical column set is unchanged.
**Decision 11 — Replay semantics.** For an as-of instant at or after a split's effective time, the
 position's quantity is multiplied by the ratio and its **average cost per share** is
 divided by the ratio; **total basis is unchanged**, so realised and unrealised gains at
 the instant are unchanged. Cash is unaffected at every instant, and a split before the
 as-of point is invisible in totals. Events replay in the existing deterministic order
 (`effective_at`, `sequence`); an unknown basis stays unknown and is never derived from
 the split.
**Decision 12 — A split with no position is not an error.** Broker exports carry full histories, so a
 `SPLIT` for an instrument with no open position at that instant is recorded and reported
 as "no position affected", and changes no balance. A `SPLIT` naming an instrument that
 never appears in the ledger is rejected as malformed history.
**Decision 13 — Replacement split = reversal plus corrected event.** A restated split is never an
 edit. It is an explicit reversal: a second `SPLIT` whose `quantity` is the exact
 reciprocal of the original and whose `reverses_external_id` names it, followed by the
 corrected `SPLIT`. Validation requires the product of the reversal and the original to
 be exactly `1`; the existing correction contract (ADR-0024 decision 5) is otherwise
 unchanged. Replay applies both, so the net quantity is the corrected value.
**Decision 14 — Marks and splits interact, and the interaction fails closed.** A pre-split mark is
    **not** valid for a post-split quantity: for an as-of instant at or after a split, a
 mark whose session precedes the split's ex-date is refused, and the position renders
 `missing_mark` (never a value computed from a stale per-share price). This is the
 valuation-facing half of the amendment and is required for the numbers to mean what
 they say.
**Decision 15 — Valuation blocks are honoured, never edited.** `valuation_blocks` live in
 `marks.json`, are operator-authored and are never written or cleared by the ledger.
 Import reports which blocked instruments now have an imported split; removing the block
 stays an operator action, and `value_positions` keeps refusing a blocked position
 regardless of imported splits.
**Decision 16 — No migration, no schema version change, no catalog.** The ledger's stored column set
 is untouched and existing ledgers contain no `SPLIT` rows, so their replay is unchanged;
 `LEDGER_EVENT_SCHEMA_VERSION` stays `1`. The private ledger remains outside the lake and
 the catalog (ADR-0024 decision 3).
**Decision 17 — Cross-domain isolation is preserved.** The ledger's split ratio comes from the
 operator's broker history, never from the lake's Silver actions. The two parts of this
 record share a vocabulary, not a data dependency.

## Consequences

- The D01 landing has a defined basis: reconstructed raw prices, existing adjusted-read
  contract intact, no schema/catalog/reader change. Ingestion still needs (i) this record
  accepted, (ii) the mixed-lake decision, and (iii) its own scoped authorization.
- P00-2 stops being a blocker for split-affected *valuation* and becomes an implementable,
  bounded change: event kind plus import validation, replay, mark refusal, CLI reporting,
  tests, docs. No migration and no P00-4 dependency.
- The `quantity`-as-ratio overload is a permanent readability cost. It is accepted because
  the alternative is a private-ledger migration that P00-4 still gates; if P00-4 later
  authorizes migration, a dedicated `split_ratio` column should replace the overload and
  this record be superseded.
- Pre-split marks now produce `missing_mark` for post-split as-of instants. That is
  stricter than today's behaviour and will surface existing books with stale marks — the
  honest outcome, not a regression.
- Still unsupported and unchanged: spin-offs, mergers, transfers-in, fractional-share
  cash-in-lieu, tax lots, split discovery, and any automatic mark production.
- Change matrix when implemented: pipeline/CLI/storage-auxiliary rows, tests, user guide
  and `ARCHITECTURE.md`; no lake schema, no catalog row, no ADR-0024 rewrite.
