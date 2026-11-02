# Product Readiness Audit — Portfolio, Data and Research

**Recorded:** 2026-11-02 UTC
**Status:** Findings reproduced/source-reviewed; remediation not implemented.
**Baseline:** `00dad39` plus the uncommitted, accepted ADR-0023 containment.
**Continuation:** [Implementation specification](../plans/20261102-01-portfolio-and-research-implementation-spec.md)
and [next-thread handoff](../handoffs/20261102-01-portfolio-and-research-pickup.md).

## Assessment and evidence limits

Equity Lake has a substantial local-first data/research foundation. It is not
an account ledger, and the reviewed evidence does not establish investment
performance or capital capacity. Canonical instrument identity, Delta storage,
write validation, label maturity, fail-closed availability and private tracking
are strengths to preserve, not reasons to certify ML skill.

Two parallel scout attempts returned `Subagent was aborted`, without reports.
Session/process inspection found no remaining worker children owned by that
session; unrelated processes were not terminated. The coordinator completed
this initial review directly. It is not an independent reviewer sign-off.

The audit read source/configuration and inspected synthetic research artifact
metadata, not private account/watchlist contents. Four in-memory probes
reproduced defects. The focused existing suite passed **78 tests, zero
failures/errors/skips, 193.913 seconds**:

```bash
uv run pytest tests/unit/test_arena.py tests/unit/test_backtesting_regressions.py \
  tests/unit/test_ml_validation.py tests/unit/test_private_tracking_api.py \
  tests/unit/test_private_tracking_events.py tests/unit/test_findings.py \
  -q --junitxml=/tmp/equity-product-audit-targeted.xml \
  > /tmp/equity-product-audit-targeted.log 2>&1
```

Those tests use temporary synthetic inputs/models. No live-provider calls,
production fits, lake mutations, delivery, deployment, browser/security audit,
or scaling/capital-capacity study was performed. Prior full gates of
1739 passed / 17 skipped belong to the ADR-0023 working tree, not this audit.

## Findings register

| ID | Priority/type | Finding and source evidence | Remediation stage |
| --- | --- | --- | --- |
| A01 | High / defect | `backtesting/arena.py::equal_weight_buyhold` compounds daily mean returns: rebalanced equal weights, not fixed-share buy-and-hold; grouping uses ticker rather than canonical identity. | F01 |
| A02 | High / defect | `backtesting/strategy/momentum.py::generate_weights` applies `shift` outside the identity window; allocations depend on row ordering and can borrow another instrument's history. | F02 |
| A03 | Medium / defect | `backtesting/report.py::build_finding_cards` divides cost degradation by zero-cost Sharpe; negative denominators invert the verdict and text invents low turnover. | F03 |
| A04 | Medium / defect | `arena.py::run_arena` skips failed runs; `cli/commands/arena.py::arena_run` returns success even with zero completed runs. | F04 |
| A05 | Product blocker / missing capability | `signals/tracking.py::TrackedInstrument` contains a held flag, not quantities, trades, cash, basis or broker reconciliation. Simulated backtest cash is not account state. | P00–P03 |
| A06 | Real-research blocker / accepted limitation | `sources/base.py::fetch_corporate_actions` emits null source availability; native coverage proof is absent. `ingestion/corporate_actions.py` supports US action ingestion only. Strict adjusted consumers correctly refuse unknown proof. | D00–D01 |
| A07 | Data limitation | Price normalization selects a known schema before natural-key upsert; Delta time travel is not every raw response/publication vintage. Some enrichment functions intentionally return unavailable fields. See `sources/base.py`, `core/schemas.py`, `ingestion/writers.py`, `features/dag/enrichments_04.py`. | D00–D01 |
| A08 | Research limitation | Comparison verdicts use point-estimate margins; validation/row-usability checks are not alpha or calibration gates. ML is disabled in `config/signals.yaml`; the arena has no ML strategy. | R00–R02 |
| A09 | Evidence limitation | `backtesting/report.py` writes curves indexed by `t` and overwrites strategy/regime paths; `findings/writer.py` overwrites stable card IDs. ML comparison cards omit backing OOS evidence refs and full snapshot/window manifests. | E00–E01 |
| A10 | Scale/economic limitation | `engine.py::_prepare_point_in_time_inputs` repeatedly rebuilds histories/strategy state. The engine uses one fee/tax pair per run and has no inspected FX, spread/impact or liquidity-capacity model. Default five-ticker arena cannot satisfy momentum's default ten-stock minimum. | F04, R00–R02 |
| A11 | Product/deployment limitation | Streamlit/static dashboard are primarily operational; Astro is a catalog. `pages.yml` and `catalog-deploy.yml` independently deploy standalone artifacts to the same Pages environment. Fresh CI checkout does not restore durable portfolio/lake state; tracking schedule is descriptive only. | P03, H00 |

Paths above are relative to `src/equity_lake/` unless otherwise stated. These
are new findings; R10/R12/R13 and other accepted closure decisions are not
reopened. In particular, the missing real-data proof is a documented boundary,
not permission to replace it with an ingestion timestamp or synthetic proof.

## Reproducible counterexamples

The following diagnostic prints the wrong baseline behaviours. It writes no
research artifacts, fetches no provider data and fits no model. Run from the
repository with `uv run python` using a temporary script or heredoc. The CLI
case mocks the outcome and artifact writer deliberately; it verifies exit-code
policy, not actual engine failure propagation. Regression tests must also cover
real run failure isolation and CLI wiring.

```python
from datetime import date
from unittest.mock import patch

import polars as pl
from typer.testing import CliRunner

from equity_lake.backtesting.arena import ArenaOutcome, ArenaRun, equal_weight_buyhold
from equity_lake.backtesting.report import build_finding_cards
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy.momentum import CrossSectionalMomentumStrategy
from equity_lake.cli.__main__ import app

days = [date(2024, 1, n) for n in (2, 3, 4)]
prices = pl.DataFrame({
    "ticker": ["A", "B"] * 3,
    "date": [day for day in days for _ in range(2)],
    "close": [100.0, 100.0, 200.0, 100.0, 100.0, 100.0],
})
benchmark = equal_weight_buyhold(prices, initial_cash=100.0)
print("A01 terminal benchmark:", benchmark["equity"][-1])


def result(sharpe):
    return BacktestResult(
        strategy_name="example", tickers=["A"],
        start_date=days[0], end_date=days[-1],
        initial_cash=100.0, final_cash=90.0,
        equity_curve=pl.Series("equity", [100.0, 95.0, 90.0]),
        trades=[], metrics={"sharpe_ratio": sharpe, "total_return": -0.1},
    )


outcome = ArenaOutcome(
    runs=[ArenaRun("example", "zero", result(-1.0)),
          ArenaRun("example", "realistic", result(-2.0))],
    data=prices, benchmark=benchmark, initial_cash=100.0,
)
card = next(card for card in build_finding_cards(outcome, run_date=days[-1])
            if card.id == "cost-regime")
print("A03 cost card:", card.verdict, card.conclusion)
empty = ArenaOutcome(runs=[], data=prices, benchmark=benchmark, initial_cash=100.0)
with patch("equity_lake.backtesting.arena.run_arena", return_value=empty), \
     patch("equity_lake.backtesting.report.write_arena_artifacts", return_value=[]):
    response = CliRunner().invoke(app, [
        "arena", "run", "--start-date", "2024-01-02", "--end-date", "2024-01-04",
    ])
print("A04 empty-run exit:", response.exit_code)
rows = [
    {"ticker": ticker, "date": date(2024, 1, i + 2), "close": price}
    for ticker, values in [
        ("A", [100.0, 110.0, 120.0, 130.0, 140.0]),
        ("B", [100.0, 90.0, 80.0, 70.0, 60.0]),
        ("C", [100.0, 101.0, 102.0, 103.0, 104.0]),
    ]
    for i, price in enumerate(values)
]
frame = pl.DataFrame(rows)
strategy = CrossSectionalMomentumStrategy({
    "lookback_days": 2, "skip_days": 1, "rebalance_days": 1,
    "min_stocks": 1, "top_pct": 0.3,
})
a = strategy.generate_weights(frame.sort(["ticker", "date"])).sort(["ticker", "date"])
b = strategy.generate_weights(frame.sort(["date", "ticker"])).sort(["ticker", "date"])
print("A02 order-invariant allocations:", a.equals(b))
```

Observed before remediation:

```text
A01 terminal benchmark: 112.5
A03 cost card: positive Costs barely move example Sharpe (-1.00 -> -2.00); turnover is low.
A04 empty-run exit: 0
A02 order-invariant allocations: False
```

Correct targets: fixed-share benchmark terminal **100.0**, adverse-cost verdict
**negative** without an unmeasured turnover claim, zero-completion exit **1**,
and order-invariant momentum weights with instrument-local warmup.

This probe's ticker-only fixtures do not authorize guessed identities in shared
storage. New regression coverage must add full identity, duplicate/missing
marks, interval boundaries and real engine/CLI integration where applicable.

## Continuation and authorization

Use the linked specification for exact proposed contracts, decision gates and
acceptance tests. Drafting does not approve new accounting/storage boundaries,
provider spend, production ingestion, scheduling, automatic refits or deployment.
The scratch attempt logs and extended evidence are local ignored files under
`.agent-progress/`; this tracked record preserves the essential evidence even
when that directory or `/tmp` is absent in another checkout.
