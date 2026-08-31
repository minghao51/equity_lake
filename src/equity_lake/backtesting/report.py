"""Serialize BacktestResults and build the Phase 1 FindingCards.

Per-run artifacts (equity/drawdown/metrics/trades) are written under
``data/findings/<run-slug>/`` and three evidence-backed :class:`FindingCard`
records are produced for the Phase 1 comparison axes: ``strategy``,
``cost``, and ``benchmark``. Verdicts are data-driven and honest — a defensible
negative is a valid (and strong) portfolio outcome.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import structlog

from equity_lake.backtesting.arena import ArenaOutcome, ArenaRun
from equity_lake.backtesting.metrics import DEFAULT_RISK_FREE_RATE, equity_curve_metrics
from equity_lake.backtesting.result import BacktestResult
from equity_lake.findings import FindingCard, write_finding_card

logger = structlog.get_logger(__name__)


def drawdown_series(equity: pl.Series) -> pl.Series:
    """Drawdown (fraction below running peak) for an equity-curve series."""
    if equity.len() == 0:
        return pl.Series("drawdown", [], dtype=pl.Float64)
    values = equity.to_numpy()
    running_max = np.maximum.accumulate(values)
    return pl.Series("drawdown", values / running_max - 1.0)


def write_backtest_report(
    result: BacktestResult,
    out_dir: Path,
    *,
    strategy: str | None = None,
    cost_regime: str | None = None,
) -> dict[str, Path]:
    """Write a single BacktestResult's artifacts under ``out_dir``.

    Emits ``equity.parquet``, ``drawdown.parquet``, ``metrics.json``, ``trades.json``.
    Returns a map of artifact name -> path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    equity = result.equity_curve
    drawdown = drawdown_series(equity)
    pl.DataFrame({"t": range(equity.len()), "equity": equity}).write_parquet(out_dir / "equity.parquet")
    pl.DataFrame({"t": range(drawdown.len()), "drawdown": drawdown}).write_parquet(out_dir / "drawdown.parquet")
    meta = {
        "strategy": strategy or result.strategy_name,
        "cost_regime": cost_regime,
        "risk_free_rate": DEFAULT_RISK_FREE_RATE,
        "warnings": list(result.warnings),
        **result.to_dict(),
    }
    (out_dir / "metrics.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    (out_dir / "trades.json").write_text(json.dumps(result.trades, indent=2, default=str), encoding="utf-8")
    logger.info("backtest_report_written", out_dir=str(out_dir), strategy=strategy, cost_regime=cost_regime)
    return {
        "equity": out_dir / "equity.parquet",
        "drawdown": out_dir / "drawdown.parquet",
        "metrics": out_dir / "metrics.json",
        "trades": out_dir / "trades.json",
    }


def _run_slug(run: ArenaRun) -> str:
    return f"{run.strategy}__{run.cost_regime}"


def _series_metrics(equity: pl.DataFrame | pl.Series, initial_cash: float) -> dict[str, float]:
    """Total return, annualized Sharpe, and max drawdown from an equity curve.

    Delegates to the shared :func:`equity_lake.backtesting.metrics.equity_curve_metrics`
    so benchmark metrics use exactly the engine's convention (rf=0.02 annual,
    252 trading days) and stay comparable with strategy Sharpe values.
    """
    return equity_curve_metrics(equity, initial_cash)


def _runs_for(outcome: ArenaOutcome, strategy: str | None = None, cost_regime: str | None = None) -> list[ArenaRun]:
    runs = outcome.runs
    if strategy is not None:
        runs = [r for r in runs if r.strategy == strategy]
    if cost_regime is not None:
        runs = [r for r in runs if r.cost_regime == cost_regime]
    return runs


def _verdict_strat_vs_bench(strat_sharpe: float, bench_sharpe: float) -> str:
    if strat_sharpe > bench_sharpe + 0.1:
        return "positive"
    if strat_sharpe < bench_sharpe - 0.1:
        return "negative"
    return "inconclusive"


def build_finding_cards(
    outcome: ArenaOutcome,
    *,
    run_date: date,
    scope: dict[str, Any] | None = None,
) -> list[FindingCard]:
    """Build the three Phase 1 FindingCards from an arena outcome.

    Cards: ``strategy-comparison`` (which strategy wins at realistic cost),
    ``cost-regime`` (how costs degrade Sharpe), ``vs-benchmark`` (each strategy vs
    equal-weight buy-and-hold). Returns only cards that could be computed from the
    available runs (e.g. an empty arena yields no cards).
    """
    scope = dict(scope or {})
    base_scope = {
        "tickers": len(outcome.data["ticker"].unique()) if not outcome.data.is_empty() else 0,
        # Sharpe convention shared with the engine (see backtesting/metrics.py).
        "sharpe_risk_free_rate": DEFAULT_RISK_FREE_RATE,
        # Honesty note: the benchmark pays no costs and holds from day one,
        # while strategies trade at the next close under their cost regime.
        "benchmark_asymmetry": (
            "equal-weight buy-and-hold benchmark pays zero costs and zero lag; "
            "strategies execute one bar after signal and pay the run's fee/tax regime"
        ),
        **scope,
    }
    bench = _series_metrics(outcome.benchmark, outcome.initial_cash)
    cards: list[FindingCard] = []

    # 1) strategy-comparison — realistic cost across strategies
    realistic = _runs_for(outcome, cost_regime="realistic")
    if realistic and bench:
        strat_sharpe = {r.strategy: float(r.result.sharpe_ratio) for r in realistic}
        best = max(strat_sharpe, key=strat_sharpe.get, default=None)  # type: ignore[arg-type]
        metrics: dict[str, float] = {
            **{f"{s}.sharpe": v for s, v in strat_sharpe.items()},
            "benchmark.sharpe": bench["sharpe_ratio"],
        }
        if best is not None:
            best_over = strat_sharpe[best] > bench["sharpe_ratio"] + 0.1
            verdict = "positive" if best_over else ("inconclusive" if any(v >= bench["sharpe_ratio"] for v in strat_sharpe.values()) else "negative")
            conclusion = (
                f"{best} leads at Sharpe {strat_sharpe[best]:.2f} vs benchmark {bench['sharpe_ratio']:.2f} (realistic costs)."
                if best_over
                else f"No strategy clears the benchmark by >0.1 Sharpe; best is {best} at {strat_sharpe[best]:.2f} vs {bench['sharpe_ratio']:.2f}."
            )
            cards.append(
                FindingCard(
                    id="strategy-comparison",
                    axis="strategy",
                    claim=f"Which of {sorted(strat_sharpe)} dominates at realistic cost?",
                    verdict=verdict,  # type: ignore[arg-type]
                    conclusion=conclusion,
                    metrics=metrics,
                    evidence_refs=[*[f"{s}__realistic/" for s in sorted(strat_sharpe)], "benchmark__equity.parquet"],
                    run_date=run_date,
                    scope=base_scope,
                )
            )

    # 2) cost-regime — Sharpe degradation zero -> realistic for a representative strategy
    rep_strategy = realistic[0].strategy if realistic else (outcome.runs[0].strategy if outcome.runs else None)
    if rep_strategy is not None:
        by_regime = {r.cost_regime: float(r.result.sharpe_ratio) for r in _runs_for(outcome, strategy=rep_strategy)}
        if "zero" in by_regime and "realistic" in by_regime:
            zero_s = by_regime["zero"]
            real_s = by_regime["realistic"]
            drop = (zero_s - real_s) / zero_s if zero_s != 0 else 0.0
            verdict = "negative" if drop > 0.3 else ("positive" if drop < 0.05 else "inconclusive")
            conclusion = (
                f"Realistic costs cut {rep_strategy} Sharpe from {zero_s:.2f} to {real_s:.2f} ({drop:.0%} drop)."
                if drop > 0.05
                else f"Costs barely move {rep_strategy} Sharpe ({zero_s:.2f} -> {real_s:.2f}); turnover is low."
            )
            cards.append(
                FindingCard(
                    id="cost-regime",
                    axis="cost",
                    claim="How do trading costs degrade risk-adjusted returns?",
                    verdict=verdict,  # type: ignore[arg-type]
                    conclusion=conclusion,
                    metrics={f"{k}.sharpe": v for k, v in by_regime.items()},
                    evidence_refs=[f"{rep_strategy}__zero/", f"{rep_strategy}__realistic/"],
                    run_date=run_date,
                    scope={**base_scope, "strategy": rep_strategy},
                )
            )

    # 3) vs-benchmark — each realistic strategy vs equal-weight buy-and-hold
    if realistic and bench:
        metrics_b: dict[str, float] = {
            "benchmark.sharpe": bench["sharpe_ratio"],
            "benchmark.total_return": bench["total_return"],
        }
        per_strat: dict[str, str] = {}
        for r in realistic:
            metrics_b[f"{r.strategy}.sharpe"] = float(r.result.sharpe_ratio)
            metrics_b[f"{r.strategy}.total_return"] = float(r.result.total_return)
            per_strat[r.strategy] = _verdict_strat_vs_bench(float(r.result.sharpe_ratio), bench["sharpe_ratio"])
        n_positive = sum(1 for v in per_strat.values() if v == "positive")
        n_negative = sum(1 for v in per_strat.values() if v == "negative")
        verdict = "positive" if n_positive > n_negative else ("negative" if n_negative > n_positive else "inconclusive")
        beats = [s for s, v in per_strat.items() if v == "positive"]
        conclusion = (
            f"{', '.join(sorted(beats))} beat(s) the equal-weight benchmark after costs."
            if beats
            else "No strategy beats the equal-weight benchmark after realistic costs."
        )
        cards.append(
            FindingCard(
                id="vs-benchmark",
                axis="benchmark",
                claim="Does any active strategy beat equal-weight buy-and-hold after costs?",
                verdict=verdict,  # type: ignore[arg-type]
                conclusion=conclusion,
                metrics=metrics_b,
                evidence_refs=[*[f"{r.strategy}__realistic/" for r in sorted(realistic, key=lambda r: r.strategy)], "benchmark__equity.parquet"],
                run_date=run_date,
                scope=base_scope,
            )
        )

    return cards


def write_arena_artifacts(
    outcome: ArenaOutcome,
    *,
    base: Path | None = None,
    run_date: date | None = None,
    scope: dict[str, Any] | None = None,
) -> list[FindingCard]:
    """Write per-run reports + the three Phase 1 FindingCards under ``base``.

    Each run's artifacts land under ``<base>/<strategy>__<regime>/``; the cards
    reference their relevant evidence directory. Returns the written cards.
    """
    from equity_lake.core.paths import FINDINGS_DIR

    root = base or FINDINGS_DIR
    root.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today()

    for run in outcome.runs:
        slug = _run_slug(run)
        write_backtest_report(run.result, root / slug, strategy=run.strategy, cost_regime=run.cost_regime)

    # benchmark artifact
    if not outcome.benchmark.is_empty():
        outcome.benchmark.write_parquet(root / "benchmark__equity.parquet")

    cards = build_finding_cards(outcome, run_date=run_date, scope=scope)
    for card in cards:
        write_finding_card(card, base=root)
    logger.info("arena_artifacts_written", root=str(root), runs=len(outcome.runs), cards=len(cards))
    return cards


__all__ = [
    "build_finding_cards",
    "drawdown_series",
    "write_arena_artifacts",
    "write_backtest_report",
]
