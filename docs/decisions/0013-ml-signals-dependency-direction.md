# ADR-0013: One-Way Dependency — `signals` May Import `ml`, Never the Reverse

**Status:** Accepted decision; implementation and offline-contract review accepted 2026-10-18. Operational readiness is not implied.
**Recorded:** 2026-09-10; owner acceptance recorded 2026-10-17.
**Deciders:** Repository owner
**Related:** ADR-0002 (import boundary tests), ADR-0012 (boundary coverage
extension)

## Context

`ml` and `signals` currently import each other in opposite directions with
asymmetric visibility:

- `signals/generators/ml.py:9` imports `PriceForecaster` at module level
  (signals → ml, hard edge). The generator **predicts on demand**, calling
  `forecaster.predict(ticker, date)` per ticker per scan — freshness does not
  depend on the pipeline's ML stage having run.
- `ml/forecasting.py:119-129` (`_load_candidate_strategies`) imports
  `equity_lake.signals.config.load_signal_config` lazily inside
  `contextlib.suppress(Exception)` (ml → signals, silent edge). Any failure —
  bad YAML, typo, import error — silently yields `DEFAULT_BACKTEST_STRATEGY`,
  so training can run with different candidate labels than configured with no
  log line.

The cycle is invisible to `LAYER_BOUNDARIES` (neither package is constrained,
and the edge is lazy). It is unlearnable from the code and the suppress is a
real debugging trap.

## Options Considered

- **Decouple fully via storage:** `MLPredictionSignalGenerator` reads
  `04_platinum/predictions` (the `api/deps.py` pattern) instead of importing
  the forecaster. Rejected for now: Platinum is written only by the pipeline's
  ML stage (`pipeline.py` → `run_prediction_job`), so `equity signal scan`
  would go stale/empty on any day or watchlist the pipeline did not cover —
  a user-facing semantic regression to buy architectural purity.
- **Injected `PredictionSource` protocol** (Platinum default, forecaster-backed
  `--on-demand` flag). Rejected: a new abstraction against the
  smallest-architecture rule; the problem is the cycle, not the source.
- **One-way direction with explicit injection (chosen).**

## Decision

1. The dependency direction is **signals → ml only**. `LAYER_BOUNDARIES`
   gains `"ml": {"cli", "dashboard", "signals"}` once the code complies.
2. `PriceForecaster` stops importing `signals.config`. Candidate strategies
   are resolved, in order: (a) explicit `candidate_strategies` in the
   caller-supplied `ml_config` (existing behavior), (b) an explicit
   constructor parameter `candidate_strategies=...`, (c)
   `DEFAULT_BACKTEST_STRATEGY`. No silent fallback: when neither (a) nor (b)
   is provided, that fact is logged once at info.
3. Callers that today relied on the implicit signals.yaml pickup
   (`pipeline.run_prediction_job`, the `equity forecast`/`ml train` CLI path)
   load the signal config themselves and pass it explicitly. Root
   `eod_pipeline` and `cli` may import `signals` freely; only `ml` is
   forbidden from doing so.
4. `signals/generators/ml.py` keeps its module-level `PriceForecaster` import
   and its on-demand `predict()` semantics — unchanged.

## Consequences

- The silent `suppress(Exception)` disappears; misconfiguration now surfaces
  at the call site that chose not to pass strategies.
- Behavior preservation: callers wired in the same change reproduce the old
  effective strategies (signals.yaml when present, else the default). Tests
  must pin this.
- `equity signal scan` keeps on-demand freshness; no new abstraction; the
  Platinum-decoupled source remains available as a future ADR if scan cost or
  freshness requirements change.
- Enforcement: adding `"signals"` to `ml`'s forbidden set makes regression of
  this decision a CI failure, not a code-review hope.

## Owner acceptance and implementation status

The owner approved this decision with: **“Approve 1-9 and C10 implementation”.**
The accepted contract is the one-way `signals` → `ml` dependency with
caller-resolved, explicit candidate-strategy injection, import-boundary
enforcement, and the existing on-demand prediction behavior retained. Source
followed this direction at the 2026-10-17 status snapshot, when independent
implementation review remained in progress. See the 2026-10-18
implementation/offline-contract acceptance supplement below; neither policy nor
implementation acceptance implies live operational readiness.

## Implementation/offline-contract acceptance supplement — 2026-10-18

The one-way `signals` → `ml` implementation, explicit candidate-strategy
injection, import-boundary enforcement, and retained on-demand prediction
contract were independently reviewed and accepted with the C10 offline gates.
The final reviewer returned no C10 must-fix. This acceptance is limited to
implementation and offline contracts; it makes no alpha, live-provider, or
production-readiness claim and does not authorize operational activity.
