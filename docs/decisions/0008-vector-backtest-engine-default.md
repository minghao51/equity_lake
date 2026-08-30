# ADR-0008: `VectorBacktestEngine` as default backtesting engine

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled)

## Context

Backtesting needed vectorized execution over Polars frames rather than an
event-driven loop; `polars-backtest` supplies that and fits the Polars-first
engine decision (ADR-0003). It is an optional heavyweight dependency not
every environment installs.

## Decision

- `VectorBacktestEngine` (polars-backtest) is the default engine in
  `backtesting/engine.py`.
- The dependency ships as a `[dependency-groups]` entry: install with
  `uv sync --group backtesting` (`--group`, not `--extra`), and it is
  imported lazily per the optional-dependency rules.

## Consequences

- The default fast suite and non-backtesting environments stay lean.
- Running backtests requires the explicit group install; missing-dependency
  failures surface at the lazy import seam.
- Adding an alternative engine means a new optional dependency row in the
  change matrix and, if it changes the default, a superseding ADR.
