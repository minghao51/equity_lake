"""Serialize BacktestResults and build the Phase 1 FindingCards.

Per-run artifacts (equity/drawdown/metrics/trades) are written under
``data/findings/<run-slug>/`` and three evidence-backed :class:`FindingCard`
records are produced for the Phase 1 comparison axes: ``strategy``,
``cost``, and ``benchmark``. Verdicts are data-driven and honest — a defensible
negative is a valid (and strong) portfolio outcome.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl
import structlog

from equity_lake.backtesting.arena import BENCHMARK_MISSING_MARK_POLICY, BENCHMARK_POLICY, ArenaOutcome, ArenaRun
from equity_lake.backtesting.metrics import DEFAULT_RISK_FREE_RATE, equity_curve_metrics
from equity_lake.backtesting.result import BacktestResult
from equity_lake.findings import FindingCard, write_finding_card

logger = structlog.get_logger(__name__)

# Scope keys that disclose benchmark/Sharpe construction and the arena's
# completion/evaluation accounting. A caller-provided scope may add keys but can
# never replace these (see :func:`build_finding_cards`).
_MANDATORY_SCOPE_KEYS = (
    "benchmark_policy",
    "benchmark_missing_mark_policy",
    "benchmark_asymmetry",
    "sharpe_risk_free_rate",
    "requested_runs",
    "completed_runs",
    "failed_runs",
    "non_evaluable_runs",
)

# Numerical equality tolerance for the cost-regime Sharpe delta (F03). This is a
# floating-point comparison of two measured values, never a significance test.
_COST_DELTA_TOLERANCE = 1e-12


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


def _evaluated_runs(
    outcome: ArenaOutcome,
    strategy: str | None = None,
    cost_regime: str | None = None,
) -> list[ArenaRun]:
    """Runs admitted as card evidence: F04 only ``evaluated`` runs qualify.

    A completed computation is not the same as evaluable evidence: an
    ``ineligible`` run (universe below the strategy's ``min_stocks``) or a
    ``no_trades`` run must never become a measured Sharpe or a strategy verdict.
    """
    return [run for run in _runs_for(outcome, strategy=strategy, cost_regime=cost_regime) if run.evaluation_status == "evaluated"]


def _finite_metric(result: BacktestResult, key: str) -> float | None:
    """Return a metric that is *actually present and finite*, else ``None``.

    ``BacktestResult``'s convenience properties (``sharpe_ratio`` and friends)
    return ``0`` for a missing key, so a missing measurement would be read as a
    measured zero. Read the raw ``metrics`` dict instead and reject non-finite
    values so no NaN/inf can be persisted as evidence.
    """
    value = result.metrics.get(key)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


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

    ``scope`` adds caller metadata (e.g. ``synthetic``, ``universe``) to every card,
    but the four mandatory disclosures — ``benchmark_policy``,
    ``benchmark_missing_mark_policy``, ``benchmark_asymmetry`` and
    ``sharpe_risk_free_rate`` — are written last and cannot be replaced.
    """
    caller_scope = dict(scope or {})
    # Callers may add reproducibility keys (e.g. ``synthetic``, ``universe``) but the
    # four disclosures below are written last: they state how the benchmark was built
    # and how it is compared, so a caller must never be able to rewrite them.
    completed_runs = len(outcome.runs)
    # Legacy library fixtures construct ``ArenaOutcome`` without ``requested``; treat
    # every run they hold as a requested-and-completed pair so the scope stays coherent.
    requested_runs = len(outcome.requested) if outcome.requested else completed_runs
    non_evaluable_runs = sum(1 for run in outcome.runs if run.evaluation_status != "evaluated")
    base_scope = {
        "tickers": len(outcome.data["ticker"].unique()) if not outcome.data.is_empty() else 0,
        **caller_scope,
        # F04: which pairs were asked for, which completed, which failed, and how many
        # completed runs are not evaluable evidence (excluded from every card below).
        "requested_runs": requested_runs,
        "completed_runs": completed_runs,
        "failed_runs": len(outcome.failed),
        "non_evaluable_runs": non_evaluable_runs,
        # Sharpe convention shared with the engine (see backtesting/metrics.py).
        "sharpe_risk_free_rate": DEFAULT_RISK_FREE_RATE,
        # Honesty note: the benchmark pays no costs and holds from day one,
        # while strategies trade at the next close under their cost regime.
        "benchmark_asymmetry": (
            "equal-weight buy-and-hold benchmark pays zero costs and zero lag; "
            "strategies execute one bar after signal and pay the run's fee/tax regime"
        ),
        # F01: the benchmark is a fixed-share curve, not a daily-rebalanced one, and
        # requires a complete mark panel. Disclosed so a corrected baseline is never
        # read as evidence that a strategy now works.
        "benchmark_policy": BENCHMARK_POLICY,
        "benchmark_missing_mark_policy": BENCHMARK_MISSING_MARK_POLICY,
    }
    if caller_scope:
        overridden = sorted(key for key in caller_scope if key in _MANDATORY_SCOPE_KEYS and caller_scope[key] != base_scope[key])
        if overridden:
            logger.warning(
                "finding_card_scope_disclosure_override_rejected",
                keys=overridden,
                note="mandatory benchmark/Sharpe/completion disclosures keep their authoritative values",
            )
    bench = _series_metrics(outcome.benchmark, outcome.initial_cash)
    bench_sharpe = bench.get("sharpe_ratio")
    # A non-finite benchmark Sharpe is unavailable comparison data, never a measured
    # zero: omit it and keep the comparison inconclusive.
    if bench_sharpe is not None and not math.isfinite(bench_sharpe):
        bench_sharpe = None
    cards: list[FindingCard] = []
    # F04: every card verdict is one of the three card literals. Declaring the type
    # once keeps each card construction type-checked without per-site ignores.
    verdict: Literal["positive", "negative", "inconclusive"]

    # 1) strategy-comparison — realistic cost across strategies
    realistic = _evaluated_runs(outcome, cost_regime="realistic")
    if realistic and bench:
        strat_sharpe: dict[str, float] = {}
        unavailable: list[str] = []
        for run in realistic:
            sharpe = _finite_metric(run.result, "sharpe_ratio")
            if sharpe is None:
                unavailable.append(run.strategy)
            else:
                strat_sharpe[run.strategy] = sharpe
        metrics: dict[str, float] = {f"{s}.sharpe": v for s, v in sorted(strat_sharpe.items())}
        if bench_sharpe is not None:
            metrics["benchmark.sharpe"] = bench_sharpe
        if not strat_sharpe or bench_sharpe is None:
            # No measured strategy Sharpe (or no measured benchmark Sharpe): the
            # comparison is not evidence, so it can never be a positive verdict.
            missing_sides = []
            if not strat_sharpe:
                missing_sides.append(f"no finite sharpe_ratio metric was recorded for {sorted(unavailable)}")
            if bench_sharpe is None:
                missing_sides.append("the benchmark sharpe_ratio is missing or non-finite")
            conclusion = (
                f"Inconclusive: {' and '.join(missing_sides)}, so no strategy can be compared with the benchmark "
                "at realistic cost from these runs (unavailable metrics are omitted, never read as zero)."
            )
            verdict = "inconclusive"
            cards.append(
                FindingCard(
                    id="strategy-comparison",
                    axis="strategy",
                    claim=f"Which of {sorted(unavailable) or sorted(strat_sharpe)} dominates at realistic cost?",
                    verdict=verdict,
                    conclusion=conclusion,
                    metrics=metrics,
                    evidence_refs=[*[f"{s}__realistic/" for s in sorted({*strat_sharpe, *unavailable})], "benchmark__equity.parquet"],
                    run_date=run_date,
                    scope=base_scope,
                )
            )
        else:
            best = max(strat_sharpe, key=lambda name: strat_sharpe[name])
            best_over = strat_sharpe[best] > bench_sharpe + 0.1
            verdict = "positive" if best_over else ("inconclusive" if any(v >= bench_sharpe for v in strat_sharpe.values()) else "negative")
            omitted = f" Unavailable (non-finite/missing sharpe_ratio), omitted and never read as zero: {sorted(unavailable)}." if unavailable else ""
            conclusion = (
                f"{best} leads at Sharpe {strat_sharpe[best]:.2f} vs benchmark {bench_sharpe:.2f} (realistic costs)."
                if best_over
                else f"No strategy clears the benchmark by >0.1 Sharpe; best is {best} at {strat_sharpe[best]:.2f} vs {bench_sharpe:.2f}."
            ) + omitted
            cards.append(
                FindingCard(
                    id="strategy-comparison",
                    axis="strategy",
                    claim=f"Which of {sorted(strat_sharpe)} dominates at realistic cost?",
                    verdict=verdict,
                    conclusion=conclusion,
                    metrics=metrics,
                    evidence_refs=[*[f"{s}__realistic/" for s in sorted(strat_sharpe)], "benchmark__equity.parquet"],
                    run_date=run_date,
                    scope=base_scope,
                )
            )

    # 2) cost-regime — signed Sharpe change zero -> realistic for a representative strategy.
    # F03 (audit A03): compare measured values only. A missing/non-finite Sharpe is
    # *unknown*, not zero, and the old ratio-based rule inverted the verdict when the
    # zero-cost denominator was negative and invented a "turnover is low" reading.
    evaluated_any = _evaluated_runs(outcome)
    rep_strategy = realistic[0].strategy if realistic else (evaluated_any[0].strategy if evaluated_any else None)
    if rep_strategy is not None:
        # Group every run of the representative strategy by regime. A plain
        # ``{r.cost_regime: r}`` comprehension keeps whichever run happens to appear
        # last, so a duplicate run would silently make the measured comparison depend
        # on list order. Duplicates are kept as *ambiguous evidence* instead.
        runs_by_regime: dict[str, list[ArenaRun]] = {}
        for run in _evaluated_runs(outcome, strategy=rep_strategy):
            runs_by_regime.setdefault(run.cost_regime, []).append(run)
        if "zero" in runs_by_regime and "realistic" in runs_by_regime:
            duplicated = sorted(regime for regime, regime_runs in runs_by_regime.items() if len(regime_runs) > 1)
            by_regime: dict[str, float] = {}
            for regime, regime_runs in runs_by_regime.items():
                if len(regime_runs) > 1:
                    # No single measured value can be attributed to this regime, so its
                    # ``<regime>.sharpe`` key is omitted rather than picked arbitrarily.
                    continue
                sharpe = _finite_metric(regime_runs[0].result, "sharpe_ratio")
                if sharpe is not None:
                    by_regime[regime] = sharpe
            zero_s = by_regime.get("zero")
            real_s = by_regime.get("realistic")
            cost_metrics: dict[str, float] = {f"{regime}.sharpe": sharpe for regime, sharpe in by_regime.items()}
            missing = [label for label, value in (("zero-cost", zero_s), ("realistic-cost", real_s)) if value is None]
            if duplicated:
                counts = ", ".join(f"{regime} ({len(runs_by_regime[regime])} runs)" for regime in duplicated)
                verdict = "inconclusive"
                conclusion = (
                    f"Inconclusive: {rep_strategy} has more than one run for the duplicated cost regime(s) "
                    f"{counts}, so no single measured value can be attributed to that regime and the "
                    "zero-to-realistic cost comparison and its delta are not reported (using one run would "
                    "make the result depend on list order)."
                )
            elif zero_s is None or real_s is None:
                verdict = "inconclusive"
                conclusion = (
                    f"Inconclusive: no finite sharpe_ratio metric was recorded for the {' and '.join(missing)} "
                    f"run{'s' if len(missing) > 1 else ''}, so the cost-regime effect on {rep_strategy} "
                    "cannot be measured from these runs."
                )
            else:
                delta = real_s - zero_s
                if not math.isfinite(delta):
                    # Both values are individually finite, but their difference is not:
                    # the delta is unusable as evidence and must never be emitted (a
                    # non-finite float serializes as ``null`` and would make the card
                    # unreadable by ``load_finding_cards``).
                    verdict = "inconclusive"
                    conclusion = (
                        f"Inconclusive: {rep_strategy} Sharpe is {zero_s:.3g} at zero cost and {real_s:.3g} at "
                        "realistic cost, but their difference overflows double precision, so the cost-regime "
                        "change could not be measured finitely and no delta is reported."
                    )
                else:
                    cost_metrics["cost_regime.sharpe_delta"] = delta
                    if delta < -_COST_DELTA_TOLERANCE:
                        verdict = "negative"
                        conclusion = (
                            f"Realistic costs lowered {rep_strategy} Sharpe from {zero_s:.2f} at zero cost to "
                            f"{real_s:.2f} ({delta:+.2f} Sharpe points, measured on this run's cost regimes)."
                        )
                    elif delta > _COST_DELTA_TOLERANCE:
                        verdict = "positive"
                        conclusion = (
                            f"Observed difference only: {rep_strategy} Sharpe was {zero_s:.2f} at zero cost and "
                            f"{real_s:.2f} at realistic cost ({delta:+.2f} Sharpe points). A higher realistic-cost "
                            "Sharpe is an observed difference in this run, not evidence that costs or execution "
                            "causally improved the strategy."
                        )
                    else:
                        verdict = "inconclusive"
                        conclusion = (
                            f"Costs left {rep_strategy} Sharpe unchanged within {_COST_DELTA_TOLERANCE:g} "
                            f"({zero_s:.2f} at zero cost, {real_s:.2f} at realistic cost; "
                            f"signed change {delta:+.2f}): no measured cost effect."
                        )
            cards.append(
                FindingCard(
                    id="cost-regime",
                    axis="cost",
                    claim="How do trading costs degrade risk-adjusted returns?",
                    verdict=verdict,
                    conclusion=conclusion,
                    metrics=cost_metrics,
                    evidence_refs=[f"{rep_strategy}__zero/", f"{rep_strategy}__realistic/"],
                    run_date=run_date,
                    scope={**base_scope, "strategy": rep_strategy},
                )
            )

    # 3) vs-benchmark — each realistic strategy vs equal-weight buy-and-hold.
    # F04: only ``evaluated`` runs are evidence; F03's finite-metric discipline applies
    # here too, so an unavailable sharpe_ratio is omitted rather than read as a zero.
    if realistic and bench:
        metrics_b: dict[str, float] = {}
        if bench_sharpe is not None:
            metrics_b["benchmark.sharpe"] = bench_sharpe
        bench_total_return = bench.get("total_return")
        if bench_total_return is not None and math.isfinite(bench_total_return):
            metrics_b["benchmark.total_return"] = bench_total_return
        per_strat: dict[str, str] = {}
        unavailable_b: list[str] = []
        for r in realistic:
            sharpe = _finite_metric(r.result, "sharpe_ratio")
            total_return = _finite_metric(r.result, "total_return")
            if sharpe is None or bench_sharpe is None:
                unavailable_b.append(r.strategy)
                continue
            metrics_b[f"{r.strategy}.sharpe"] = sharpe
            if total_return is not None:
                metrics_b[f"{r.strategy}.total_return"] = total_return
            per_strat[r.strategy] = _verdict_strat_vs_bench(sharpe, bench_sharpe)
        n_positive = sum(1 for v in per_strat.values() if v == "positive")
        n_negative = sum(1 for v in per_strat.values() if v == "negative")
        verdict = "positive" if n_positive > n_negative else ("negative" if n_negative > n_positive else "inconclusive")
        beats = [s for s, v in per_strat.items() if v == "positive"]
        if not per_strat:
            verdict = "inconclusive"
            conclusion = (
                "Inconclusive: no finite sharpe_ratio metric was recorded for the realistic-cost run(s) of "
                f"{sorted(unavailable_b)}"
                + ("" if bench_sharpe is not None else " and the benchmark sharpe_ratio is unavailable")
                + ", so no strategy can be compared with the benchmark (unavailable metrics are omitted, never read as zero)."
            )
        else:
            conclusion = (
                f"{', '.join(sorted(beats))} beat(s) the equal-weight benchmark after costs."
                if beats
                else "No strategy beats the equal-weight benchmark after realistic costs."
            )
            if unavailable_b:
                conclusion += f" Omitted as unavailable (missing/non-finite sharpe_ratio), never read as zero: {sorted(unavailable_b)}."
        cards.append(
            FindingCard(
                id="vs-benchmark",
                axis="benchmark",
                claim="Does any active strategy beat equal-weight buy-and-hold after costs?",
                verdict=verdict,
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
