# ADR-0032 — Adjusted-read coverage basis for vendor-sourced slices: a named weaker stamp, no invented clock

**Status:** **Accepted (2026-11-18).** Accepted does not mean implemented: nothing here is
implemented yet, and this record authorizes no ingest, no feature run and no R01/R02 run.

**Recorded:** 2026-11-18

**Accepted:** 2026-11-18 by the owner ("accept ADR 0032"), who asked for the implementation to
be picked up on a new thread. Acceptance authorizes the implementation stage — decisions 1-9
including their tests and mutation proofs (decision 8) — and nothing else: every *run* against
`data/lake_real/` keeps needing its own scoped authorization (ADR-0030 decision 10), and
decision 10's exclusions stand unchanged.

**Extends:** the coverage vocabulary of [ADR-0015](0015-point-in-time-adjustment-and-predictor-availability.md).
No ADR-0015 decision is relaxed: the strict, source-clock basis stays the default and the only
point-in-time basis. **Amends the consequence of**
[ADR-0030](0030-second-real-source-lake-root.md) **decision 5** ("`03_gold/features` stays
absent in the real root because no source-issued action proof exists"): under this record a
Gold feature table may exist in the real root only when stamped `vendor_events_no_clock` and
produced by an explicitly opted-in run. ADR-0030's containment, per-root isolation and
"not cataloged" rules are untouched. Carries forward the residual limit named in
[ADR-0029](0029-split-treatment-ledger-and-raw-price-basis.md) Part I and the 2026-11-17
implementation note ("there is still no source-issued action clock for yfinance").

**Implementation note 2026-11-19 — decisions 1-9 shipped, and nothing has run.** The
contract now has `VendorEventCoverageProof` (no clock field at all) beside the unchanged
strict `ActionCoverageProof` (which gains `coverage_basis: Literal["source_clock"]`),
`ACTION_COVERAGE_SCHEMA_VERSION` 2 -> 3, `AVAILABILITY_SCHEMA_VERSION` 4 -> 5,
`FEATURE_SCHEMA_VERSION` 7 -> 8, and `action_coverage_basis` in the coverage and feature
column sets. `with_price_adjustment(..., coverage_basis="source_clock")` keeps the strict
default; one read admits one basis; mixed bases and mixed demo/real roots refuse; weak output
carries null clocks and null age with `action_coverage_status = "vendor_events_complete"`.
The clockless fetch seam issues one weak proof per fetched ticker over the vendor list window
(recorded before the watermark filter), ingestion persists it with typed null clocks, the
coverage schema and the feature writer route on the stamp, and the strict validators plus the
ML loaders refuse the weak basis. Legacy v2/v4/v7 rows still read as `source_clock`. The
catalog was regenerated (`data/catalog.jsonl` now `537bf5fb...`) and the demo lake's numbers
are unchanged; the version stamps move by design. The landed slice was read only: its three
scale steps (0.2585 / 0.2478 / 0.1007) and the NVDA 2024-06-10 seam were reproduced with the
weak basis. **Not done, still gated:** any ingest, any feature run, any R01/R02 run, and any
write under `data/lake_real/`.

## Context

`with_price_adjustment` (`storage/lake_reader.py:141`, ADR-0015) turns stored raw prices into
a split-only-adjusted series and stamps `action_coverage_*` proof columns on every output row.
It requires an `ActionCoverageProof` (`core/availability.py`) with a source-issued
`source_available_at` and `source_valid_until`, and it fails closed on: a missing/empty proof
set, legacy ticker-only price frames, unknown event clocks, incomplete or expired coverage,
conflicting action revisions, and unproven-empty action data.

yfinance publishes no action clock. Verified 2026-11-18 on the landed slice (`data/lake_real/`,
built under ADR-0028/0029/0030): all **703** Silver action rows carry a null
`source_available_at` — the fetch seam writes `pl.lit(None)` for that column
(`sources/base.py:545`) — and `02_silver/corporate_action_coverage/` **does not exist at all**
under `data/lake_real/` (`ingest_corporate_actions` writes proofs only when the fetcher
supplies `action_coverage_proofs`, `ingestion/corporate_actions.py:218,249-256`; yfinance
supplies none, so `coverage_written=0` is expected). Every adjusted read on real rows therefore
fails closed, and all nine call sites that depend on it are blocked:
`features/engineering.py:432,466`, `features/labels.py:529`, `signals/tracking.py:643`,
`signals/generators/backtest.py:59`, `backtesting/engine.py:382,391`,
`backtesting/data_loader.py:581,585`. That blocks the R01/R02 harness stages (frozen universe,
features, backtest — spec `docs/plans/20261102-01-portfolio-and-research-implementation-spec.md`
§7) on the landed real data.

What is new since ADR-0029 Part I shipped: prices in a real-source root are **already
reconstructed to raw, split-free basis at the write boundary**
(`ingestion/price_reconstruction.py`, `reconstruct_vendor_prices`), and that transform refuses
on `action_store_missing`, `action_scope_incomplete`, `action_invalid` and
`action_window_uncovered`. Its correctness was verified on landed rows: re-applying the stored
split ratios reproduces the vendor's OHLC **and** volume at 0.0 relative error (ADR-0029
implementation note 2026-11-17; `docs/handoffs/20261117-01-post-d01-landing.md` §2). So the
lake already holds the vendor's own split events next to raw prices — what is missing is only
the *publication clock* those events will never have.

### Premise checks (measured 2026-11-18, read-only)

1. **"The adjusted read is only needed for cross-split series" — false, twice over.**
   (a) Operationally: all nine call sites invoke `with_price_adjustment` unconditionally and
   it raises at the proof checks *before* any split-in-window analysis; there is no
   split-free bypass in the code, and adding one would be the very call-site change this
   alternative claims to avoid. (b) For the landed slice specifically: the 2024 window
   contains exactly one in-window split (NVDA 2024-06-10, stored ratio `0.1` `old/new`), and
   the raw basis **has** the seam — NVDA close 1208.88 on 2024-06-07 → 121.79 on 2024-06-10
   (`01_bronze/market_data/us_equity/`) — so the R01 window is cross-split and needs the
   adjusted read by construction. Even for split-free windows the transform is not always a
   no-op: rows dated before a later ex-date are multiplied by a non-1 constant.
2. "Nine call sites" — confirmed: exactly nine call sites in `src/` (listed above).
3. "Every action row has a null `source_available_at`" — confirmed 703/703 on the landed slice.
4. The landed slice is 1,260 price rows, 252 sessions 2024-01-02..2024-12-31, 5 tickers
   (AAPL, JPM, KO, MSFT, NVDA); `price_provenance` is null on every row (i.e. `unknown` under
   [ADR-0027](0027-row-bound-price-source-provenance.md) — unchanged by this record).

## Decision

**Decision 1 — A second, named, explicitly weaker coverage basis; opt-in only.** The coverage
vocabulary gains one basis: **`vendor_events_no_clock` — "vendor-event completeness, no source
clock"**. It is admitted only through an explicit parameter on `with_price_adjustment`
(`coverage_basis=`), it applies to exactly one read at a time, and the default remains
`source_clock`. No existing call site changes behaviour until it opts in explicitly. No clock
is synthesized anywhere in this design (see decision 6 and the non-goals).

**Decision 2 — What the basis proves.** For an instrument and window it proves exactly:
(a) the stored action events are the vendor's own published event list for that instrument,
fetched from the same source as the prices and stored in the target root's Silver actions;
(b) the stored price rows were reconstructed to raw basis against exactly those events at the
write boundary, with ADR-0029 Part I's four refusals in force (`price_reconstruction.py`); so
(c) the split factors the adjusted read applies are the exact reciprocals of the ingest
factors — the adjusted series is consistent with the vendor's own split semantics for the
covered window. The 2026-11-17 verification (0.0 relative error for OHLC and volume on landed
rows; exactly one scale step per in-window split) is the evidence shape this basis rests on.

**Decision 3 — What the basis does not prove.** It does **not** prove:
- **point-in-time correctness** — nothing is known about when each event became visible;
  `source_available_at` does not exist for these rows, and ex-date is not availability
  (ADR-0015 decision 4);
- **freshness or expiry** — there is no `source_valid_until`; validity ends at the store's
  per-ticker watermark, and a vendor correction or omission below that watermark is
  undetectable (D00; ADR-0029 decision 3 and Part I residual limit);
- **completeness against reality** — only that the events used are the vendor's own list
  applied to the vendor's own prices;
- **dividend adjustment** — the contract stays split-only (ADR-0015 decision 1); `adj_close`
  stays untouched (ADR-0029 decision 4);
- **provenance** — no `real` claim, no digest-based relabeling; rows stay `unknown`
  (ADR-0027).

**Decision 4 — Refusals that stay in force.** Unchanged on both bases unless stated:
- **PIT research.** `validate_action_coverage_for_decision` and the strict mode of
  `validate_feature_action_coverage` (`core/availability.py`) never accept a
  `vendor_events_no_clock` proof or row; the per-session visibility check in
  `features/engineering.py:443` stays strict; `ml/feature_loader.py:139`, `ml/labeling.py:52`
  and `ml/forecasting.py:282,998` refuse vendor-stamped rows. A consumer may never claim an
  action was or was not known at a past decision date on this basis.
- **Unknown clocks on the strict basis.** The per-event `source_available_at` requirement in
  `with_price_adjustment` ("action source clock is unknown on {ex_date}") is untouched; the
  weak path performs no clock validation because the basis name says there is no clock — that
  is a documented absence, never a fabricated timestamp.
- **Mixed demo/real evidence and mixed bases.** One read admits one basis and one root's
  proofs; a frame whose coverage mixes bases, or real rows against the demo lake's
  `synthetic_demo_seed` proof (and vice versa), refuses. This is the D00/ADR-0025 failure
  mode and is refused identically under the weak basis.
- **Conflicting actions.** "conflicting action revisions" and "conflicting overlapping
  coverage proofs" in `with_price_adjustment` are unchanged; so are the unknown action type,
  invalid value, partial identity, and already-stamped-frame refusals.
- **Unproven empty action data on the strict basis** (ADR-0015 decision 2) is unchanged.
- **Write-boundary refusals** of ADR-0029 Part I remain the only gate that admits prices to a
  real root.

**Decision 5 — The stamp, and how consumers distinguish the bases in data.** The basis is a
named, versioned row stamp, never inferred from nulls or status strings:
- Coverage proofs gain `coverage_basis` (`source_clock` | `vendor_events_no_clock`) and a
  weak-proof model beside `ActionCoverageProof` (whose fields stay total: clocks always
  required). `ACTION_COVERAGE_SCHEMA_VERSION` moves **2 → 3** for proofs carrying the stamp;
  a v2 row implies `source_clock`.
- Adjusted-read outputs and Gold feature rows gain `action_coverage_basis` with the same two
  values. `AVAILABILITY_SCHEMA_VERSION` moves **4 → 5** and `FEATURE_SCHEMA_VERSION`
  (`core/schemas.py:8`) moves **7 → 8**; rows stamped with the older versions imply
  `source_clock`. Weak rows carry null `action_source_available_at`,
  `action_source_valid_until` and `action_source_age_days`, and
  `action_coverage_status = "vendor_events_complete"` (its completeness claim is about the
  vendor's event list only). Readers distinguish the bases **by the stamp alone**.
- `PRICE_ADJUSTMENT_VERSION` stays `"split-only-v1"` (`core/availability.py:22`): the
  arithmetic is untouched; only proof admission changes.

**Decision 6 — Where the weak proof comes from (no clock synthesis).** The clockless fetch
seam (`sources/base.py`) may issue `vendor_events_no_clock` proofs recording, per fetched
ticker, the event window covered — the vendor's `actions` list as returned at fetch time. The
proof carries no clock field at all, so it cannot launder ingestion time into source time
(ADR-0015 decision 4; the 2026-11-17 handoff's "do not fix that by synthesizing a proof from
ingestion time"). The proof is a process assertion backed by the fetch and checkable against
the store's rows and watermark; the residual limit above the watermark (decision 3) is
reported, not promised away.

**Decision 7 — Existing behaviour for the demo lake is unchanged.** The canonical root keeps
producing `synthetic_demo_seed` source-clock proofs through the strict path; no demo row is
rewritten or relabelled; the strict default means no demo call site sees any difference in
results, stamps or refusals. Readers accept v2/v4/v7 rows as implicitly `source_clock` exactly
as today and accept v3/v5/v8 rows with their explicit stamp; strict consumers accept nothing
weaker than today. There is no default switch, no environment mode and no fallback between
bases or roots (ADR-0030 decision 2).

**Decision 8 — Change matrix (AGENTS.md rows), tests and mutation proofs.** When implemented:

| AGENTS.md row | Required work |
| --- | --- |
| Schema change | `core/availability.py` (weak proof model, `coverage_basis`, `ACTION_COVERAGE_SCHEMA_VERSION = 3`), `core/schemas.py` (`FEATURE_SCHEMA_VERSION = 8`, `AVAILABILITY_SCHEMA_VERSION = 5`, `CORPORATE_ACTION_COVERAGE_SCHEMA` + feature proof columns gain the stamp column), validators, catalog, reader compatibility (v2/v4/v7 = implicit `source_clock`), migration note (decision 8 below) |
| Storage change | `ingestion/corporate_actions.py` (weak-proof write for clockless sources; `corporate_action_coverage_table` shape), `storage/lake_reader.py` (`coverage_basis=` admission in `with_price_adjustment`; stamping), health/readiness untouched (real-root monitor keeps reporting Gold as `not_applicable` until an authorized run exists), idempotent re-ingest tests |
| Pipeline-stage change | failure contract of the feature/label stages: `ingestion/writers.py:302-327` routes on the basis (weak rows pass the weak contract, never the strict one), `features/engineering.py`, `features/labels.py`, `signals/tracking.py`, `signals/generators/backtest.py`, `backtesting/engine.py`, `backtesting/data_loader.py` thread the explicit opt-in; default at every call site stays strict |
| DAG feature change | Hamilton tags unchanged (no new node), but feature schema changes force catalog regeneration and feature tests |
| Boundary change | this ADR, accepted before any implementation |

Tests, with named falsifiers in the style of `tests/unit/test_price_reconstruction.py`, plus
the spec §10 convention of **one-rule-at-a-time mutation/restore evidence** for each new
refusal: strict default byte-identical on a demo fixture; weak proof round-trips and stamps;
PIT validators refuse weak proofs/rows (mutate the basis gate → named test must fail); mixed
bases and mixed roots refuse; conflicting actions still refuse; unknown clock on the strict
path still refuses; empty coverage on the strict path still refuses; v2/v4/v7 rows still read
as `source_clock`; a weak row is never emitted without its stamp (mutate the stamper).

**Decision 9 — Versions, catalog and migration note.** Yes, this changes both
`data/catalog.jsonl` and a feature-schema version, and neither is avoidable:
- The **feature-schema version bump is unavoidable under any design that persists weak rows**:
  `FEATURE_SCHEMA_VERSION` is what models and manifests bind
  (`FORECAST_PROVENANCE_COLUMNS.training/inference_feature_schema_version`, `core/schemas.py`),
  so a v7 row must mean exactly one contract. Writing weak rows as v7 would let a model or
  reader trained on strict v7 features consume non-PIT rows with no version signal — the
  silent reuse ADR-0015 decision 6 forbids. The same argument moves
  `AVAILABILITY_SCHEMA_VERSION` and `ACTION_COVERAGE_SCHEMA_VERSION`.
- **`data/catalog.jsonl` changes because the stamp is a column**, and the catalog is generated
  from declared column sets (`catalog/datasets.py`: `corporate_action_coverage`,
  `technical_features`) via `uv run equity catalog-generate`, which is the file's only writer
  (AGENTS.md). A named stamp must be unambiguous in row data; the rejected alternative —
  encoding the basis in `coverage_status` values and null clocks — would keep the catalog
  byte-identical at the cost of permanent ambiguity between a weak row and a legacy/corrupt
  one, and of one proof model with optional clocks weakening the strict path structurally.
  This repo has refused that trade before (ADR-0029's documented discomfort with the
  `quantity`-as-ratio overload; ADR-0023). The catalog change is the direct consequence of an
  unambiguous stamp, not a convenience.
- **Migration note: none.** Nullable additive columns via native Delta schema evolution
  (ADR-0017 decision 1); no persisted row is rewritten, re-versioned or relabelled; mixed
  version tables are read per row. The real root stays uncataloged (ADR-0030 decision 4); the
  catalog row changes describe the canonical table contracts only.
- Run manifests ([ADR-0025](0025-immutable-research-evidence-and-run-manifests.md) §4) keep
  binding to the exact table versions read and now record `action_coverage_basis` for any
  adjusted series they name; a manifest scope mixing bases refuses.

**Decision 10 — Not authorized here.** Explicitly:
- any ingest, landing or widening run against `data/lake_real/`, and any write to
  `data/lake/` or `data/lake_real/`;
- any R00/R01/R02 run (spec §7 gates — frozen universe, holdout, costs — remain theirs);
- any point-in-time claim, feature or label produced from vendor-stamped rows;
- source-clock synthesis from ingestion time, fetch time, or ex-date (ADR-0015 decision 4);
- split *discovery* beyond the vendor's own events (ADR-0029 decision 8);
- relabelling origin: rows stay `unknown`, no `real` claim (ADR-0027);
- any change to [ADR-0028](0028-d01-real-source-slice.md) decision 2 — local-only, no
  redistribution; the adjusted series inherit the vendor licence position;
- promotion of the real root into `data/lake/`, cataloging the real root, mixing roots, or
  copying demo proofs/rows anywhere (ADR-0030 decisions 2 and 4);
- total-return/dividend adjustment, `adj_close` consumption, model retraining or promotion;
- automatic fallback from strict to weak basis anywhere, including error paths.

## Consequences

- The real slice can produce split-adjusted research series — and only research series —
  without a clock ever being invented. R01/R02 lose "every adjusted read fails closed" as a
  blocker; their remaining gates (frozen universe/window/holdout/costs, E01 evidence, R00
  harness controls) are unchanged and still theirs.
- Every consumer that flows through the nine call sites must now state its basis explicitly.
  That is a readability gain with a code cost: the parameter threads through six modules, and
  a consumer that forgets to opt in still fails closed — the safe direction.
- The stamp makes the weaker evidence visible forever: a downstream reader, manifest or model
  can machine-refuse vendor-stamped rows, and a research report can name the basis it used.
- Two more version numbers move (availability 4→5, features 7→8) and the catalog is
  regenerated. The versions move in one direction and old rows keep reading as strict, so the
  cost is one-time; the alternative — version values encoding the basis — would collide with
  the next proof shape and is rejected in decision 5.
- The residual limit is unchanged and now applies to research series too: a vendor omission or
  correction below the action watermark is undetectable. Reports produced under this basis
  must say so (R01 already requires disclosing "unavailable coverage and uncertainty", spec §7).
- Still unsupported and unchanged: spin-offs, mergers, cash-in-lieu, dividend adjustment,
  multi-source action reconciliation, and any operational readiness claim.

## Alternatives considered

- **Document that reconstruction already gives raw prices, so the adjusted read is only
  needed for cross-split series; change nothing.** Rejected: false for the landed window
  (Premise check 1 — the 2024 slice contains the NVDA 2024-06-10 seam and the raw basis has
  it), and operationally inert — all nine call sites fail at the proof checks before any
  split-window analysis, so "documenting" would not unblock R01/R02 without the very
  call-site changes the option claims to avoid.
- **Refuse any change and keep R01/R02 blocked.** Rejected: it preserves a guarantee this
  data can never satisfy (a source clock yfinance does not publish) and blocks research the
  lake can evidence honestly. The strict basis keeps the guarantee where it matters (PIT);
  keeping it everywhere buys no safety, only unavailability. The alternative is still the
  right choice for any consumer that needs point-in-time action knowledge — those stay
  blocked by decision 4, deliberately.
- **Encode the basis in `coverage_status` values and nullable clocks (no new column).**
  Rejected: see decision 9 — it keeps `data/catalog.jsonl` byte-identical but makes a weak row
  indistinguishable from a corrupt or legacy one by data alone, and forces one proof model
  with optional clocks, weakening strict validation structurally.
- **Synthesize `source_available_at` from ingestion time or the ex-date.** Rejected without
  discussion: ADR-0015 decision 4 forbids it, the 2026-11-17 handoff names it as a trap, and
  it would launder absent evidence into a point-in-time claim.
