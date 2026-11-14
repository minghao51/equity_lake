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
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import polars as pl
import structlog

from equity_lake.backtesting.arena import (
    BENCHMARK_MISSING_MARK_POLICY,
    BENCHMARK_POLICY,
    ArenaOutcome,
    ArenaRun,
    _sanitize_failure_reason,
)
from equity_lake.backtesting.metrics import DEFAULT_RISK_FREE_RATE, equity_curve_metrics
from equity_lake.backtesting.result import BacktestResult
from equity_lake.findings import FindingCard
from equity_lake.findings.writer import refuse_non_finite_json

if TYPE_CHECKING:  # pragma: no cover - typing only (evidence imports this module)
    from equity_lake.backtesting.attempts import AttemptStatus, FinalisedAttempt, PairOutcome
    from equity_lake.backtesting.evidence import CostApplication, FinalisedRun, UniverseBasis

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


# Dated-run JSON artifact names. ``evidence.py`` owns the canonical constants, but it
# imports this module, so the dated writer mirrors the two names it serializes.
_DATED_METRICS_ARTIFACT = "metrics.json"
_DATED_TRADES_ARTIFACT = "trades.json"


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

    This is the **legacy**, undated layout kept byte-for-byte for the pre-E01
    directory shape (ADR-0025 Decision 6/8): the positional ``t`` column stays and
    ``equity_dates`` is deliberately not merged into ``metrics.json``. Dated
    evidence inside an immutable run directory is written by
    :func:`write_dated_backtest_report`.

    **No command uses this function.** It is the frozen-legacy fixture/legacy
    helper: it exists for tests and for reading or reproducing the pre-E01 layout,
    never for production output. Every shipped writer path publishes immutable run
    and attempt evidence instead — ``equity arena run`` through
    :func:`write_arena_artifacts` and ``equity report backtest`` through
    :func:`equity_lake.backtesting.evidence.finalise_run` plus
    :func:`equity_lake.backtesting.attempts.write_attempt_record` — so no command
    creates or overwrites a ``<strategy>__<regime>/`` directory (ADR-0025
    Decision 8).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    equity = result.equity_curve
    drawdown = drawdown_series(equity)
    pl.DataFrame({"t": range(equity.len()), "equity": equity}).write_parquet(out_dir / "equity.parquet")
    pl.DataFrame({"t": range(drawdown.len()), "drawdown": drawdown}).write_parquet(out_dir / "drawdown.parquet")
    legacy_fields = {key: value for key, value in result.to_dict().items() if key != "equity_dates"}
    meta = {
        "strategy": strategy or result.strategy_name,
        "cost_regime": cost_regime,
        "risk_free_rate": DEFAULT_RISK_FREE_RATE,
        "warnings": list(result.warnings),
        **legacy_fields,
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


def dump_json_artifact_strictly(artifact: str, payload: Any, *, indent: int | None = 2) -> str:
    """Serialize a dated-run JSON artifact with ``allow_nan=False`` and a named refusal.

    Every JSON artifact the dated writer produces goes through here, so no artifact
    can ever contain a literal ``NaN``/``Infinity``/``-Infinity`` token: a non-finite
    value is refused by :func:`refuse_non_finite_json` (naming the entry and field)
    before serialization, and ``allow_nan=False`` is the belt-and-braces backstop for
    a value the walk cannot see.

    Raises:
        ValueError: on a non-finite number in the payload, or on any value that
            cannot be serialized without a non-finite JSON token.
    """
    refuse_non_finite_json(artifact, payload)
    try:
        return json.dumps(payload, indent=indent, default=str, allow_nan=False)
    except ValueError as exc:
        raise ValueError(f"{artifact} cannot be serialized as JSON without a non-finite token: {exc}") from exc


#: The only trade fields a *still-open* position leaves unmeasured. The backend
#: report encodes an open position's missing entry price and realised pnl as ``NaN``
#: rather than ``None``; those two fields (plus the exit price) are unavailable, so
#: they are written as ``null`` — the same honest encoding an open position gets from
#: an explicit ``None``. Every other non-finite trade value refuses the run.
_OPEN_POSITION_FIELDS = frozenset({"entry_price", "exit_price", "pnl"})


def _is_open_position_row(row: dict[str, Any]) -> bool:
    """Whether a trade row is the backend's still-open record.

    Narrow on purpose: both lifecycle dates must be *present and null*. A row that
    simply omits ``entry_date``/``exit_date`` (e.g. a hand-built
    ``trades=[{"price": NaN}]``) is not an open position, so its non-finite values
    still refuse the run.
    """
    return "entry_date" in row and "exit_date" in row and row["entry_date"] is None and row["exit_date"] is None


def trades_payload(trades: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trade rows for the dated ``trades.json``, refusing every non-finite measurement.

    A non-finite price, fee or quantity is a data defect, not an unavailable
    measurement: the run is refused (``ValueError`` naming the entry and field)
    rather than the value being sanitised to ``null`` (ADR-0025; F03 precedent).

    The one exemption is the backend's still-open position record, which leaves
    ``entry_price``/``exit_price``/``pnl`` unmeasured and encodes them as ``NaN``
    instead of ``None``: an open position genuinely has no realised pnl or exit
    price, so those three outcome fields are written as ``null`` (the contract's own
    encoding for an open position, per the ``None``-stays-``null`` rule). The
    exemption is limited to those three fields on a row whose ``entry_date`` and
    ``exit_date`` are both present-and-null; any other non-finite value, including a
    ``price`` on such a row, still refuses the run.

    Raises:
        ValueError: naming the artifact, entry and field, e.g.
            ``trades.json entry 3 field 'price' is non-finite``.
    """
    payload: list[dict[str, Any]] = []
    for row in trades:
        copy = dict(row)
        if _is_open_position_row(row):
            for field in _OPEN_POSITION_FIELDS:
                value = copy.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                    copy[field] = None
        payload.append(copy)
    return payload


def json_safe_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Copy a metrics mapping with every non-finite number replaced by ``None``.

    The evidence rule is that an unavailable number is *recorded as unavailable*,
    never persisted (ADR-0025; F03 precedent: never persist NaN as measured
    evidence). ``json.dumps(..., allow_nan=False)`` refuses ``NaN``/``Infinity``/
    ``-Infinity``, so a non-finite metric is replaced by ``null`` here while its key
    is kept, and the run manifest records its availability as ``non_finite``
    (:func:`equity_lake.backtesting.evidence.metric_availability_from_result`
    classifies the same result; finalisation cross-checks the two). A non-finite
    metric is never coerced to ``0`` and its key is never dropped silently.
    """
    safe: dict[str, Any] = {}
    for key, value in metrics.items():
        if value is not None and not isinstance(value, (str, bool)):
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                safe[key] = value
                continue
            if not math.isfinite(numeric):
                safe[key] = None
                continue
        safe[key] = value
    return safe


def write_dated_backtest_report(
    result: BacktestResult,
    out_dir: Path,
    *,
    strategy: str | None = None,
    cost_regime: str | None = None,
) -> dict[str, Path]:
    """Write the **dated** artifacts of one run under ``out_dir`` (E01a).

    ``equity.parquet`` carries the backend's real session dates as ``(date,
    equity)`` and ``drawdown.parquet`` as ``(date, drawdown)``; the dates come
    from :attr:`BacktestResult.equity_dates` and are never derived from the series
    length (ADR-0025 Decision 6). ``metrics.json`` and ``trades.json`` keep the
    legacy payload shape so existing readers work unchanged.

    Every JSON artifact is serialized with ``allow_nan=False``
    (:func:`dump_json_artifact_strictly`): a non-finite metric value is first
    replaced by ``null`` (:func:`json_safe_metrics`) and recorded as ``non_finite``
    in the run manifest — an unavailable metric is never published as a literal
    ``NaN`` and never coerced to ``0`` — while a non-finite value anywhere else in
    ``metrics.json`` or in ``trades.json`` **refuses the run**, because a non-finite
    price/fee/quantity is a data defect, not an unavailable measurement. ``null``
    trade fields stay ``null`` (an open position has no realised pnl/exit price).

    Raises:
        ValueError: when the result carries no backend dates. An undated curve is
            not written into a dated run directory — legacy undated evidence stays
            legacy, it is never silently repaired or given invented sessions. Also
            when a non-metric numeric field of the payload is non-finite, or when a
            trade carries a non-finite value (naming the artifact, entry and field):
            a dated run never carries a non-finite JSON token.
    """
    dates = result.equity_dates
    if dates is None:
        raise ValueError("dated evidence unavailable: BacktestResult.equity_dates is None, so no (date, value) artifact can be written")
    if len(dates) != result.equity_curve.len():
        raise ValueError(f"dated evidence misaligned: {len(dates)} dates for {result.equity_curve.len()} equity points")

    equity = result.equity_curve
    drawdown = drawdown_series(equity)
    legacy_fields = {key: value for key, value in result.to_dict().items() if key != "equity_dates"}
    metrics = legacy_fields.get("metrics")
    if isinstance(metrics, dict):
        legacy_fields["metrics"] = json_safe_metrics(metrics)
    meta = {
        "strategy": strategy or result.strategy_name,
        "cost_regime": cost_regime,
        "risk_free_rate": DEFAULT_RISK_FREE_RATE,
        "warnings": list(result.warnings),
        **legacy_fields,
    }
    # Both JSON payloads are serialized (and therefore refused) before anything is
    # written, so a defective trade refuses the run without leaving a half directory.
    # A metric is already null here; anything left that is non-finite is a non-metric
    # numeric field such as final_cash, which is refused with its name and location.
    metrics_text = dump_json_artifact_strictly(_DATED_METRICS_ARTIFACT, meta)
    # Trades are *measurements*, so a non-finite price/fee/quantity is a defect that
    # refuses the run instead of being sanitised to null. The single exemption is the
    # backend's still-open position record, whose unmeasured entry_price/pnl are
    # written as null (see :func:`trades_payload`).
    trades_text = dump_json_artifact_strictly(_DATED_TRADES_ARTIFACT, trades_payload(result.trades))

    out_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": dates, "equity": equity}).with_columns(pl.col("date").cast(pl.Date)).write_parquet(out_dir / "equity.parquet")
    pl.DataFrame({"date": dates, "drawdown": drawdown}).with_columns(pl.col("date").cast(pl.Date)).write_parquet(out_dir / "drawdown.parquet")
    (out_dir / "metrics.json").write_text(metrics_text, encoding="utf-8")
    (out_dir / "trades.json").write_text(trades_text, encoding="utf-8")
    logger.info("dated_backtest_report_written", out_dir=str(out_dir), strategy=strategy, cost_regime=cost_regime)
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

    ``evidence_refs`` are **unbound**: they use the frozen legacy vocabulary
    (``<strategy>__<regime>/`` and ``benchmark__equity.parquet``), which names a
    mutable legacy location rather than this invocation's immutable evidence. The
    arena writer binds them with
    :func:`equity_lake.backtesting.attempts.bind_cards_to_evidence` before persisting
    a card (ADR-0025 Decision 8); a caller that persists a card directly must bind it
    the same way, or the card points at whatever a different run last wrote there.
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


#: Reason recorded (and logged) when a completed run cannot be finalised as dated
#: evidence. A run never gets an invented calendar: undated stays undated.
UNDATED_RUN_EVIDENCE_SKIP_REASON = (
    "undated: the backend report carried no usable date column, so the run has no backend sessions to record; "
    "no run directory is created and no calendar is invented from the curve length (ADR-0025 Decision 6)"
)

#: Reason recorded (and logged) when a run cannot be finalised because the fee/tax
#: pair its engine was actually configured with was never recorded on the run
#: (``ArenaRun.applied_costs is None`` — a legacy or hand-built outcome). The writer
#: refuses to re-resolve :data:`MARKET_COST_DEFAULTS` in that case: a manifest that
#: guesses the cost application is worse than one that admits it is unknown.
APPLIED_COSTS_NOT_RECORDED_SKIP_REASON = (
    "applied costs not recorded: the run does not carry the fee/tax pair its engine was actually configured with, "
    "and the writer never guesses a market default for it; no run directory is created and no manifest claims a "
    "cost application (E01a fix round 3)"
)


@dataclass
class ArenaEvidenceSummary:
    """Immutable-evidence outcome of one arena artifact write.

    ``finalized`` holds the published :class:`FinalisedRun` records and ``attempt``
    the one :class:`FinalisedAttempt` this invocation published (``None`` only when
    the attempt record itself could not be written). ``skipped_undated`` maps a
    ``<strategy>__<regime>`` slug to the reason its run was **not** finalised because
    the backend curve carried no dates (see
    :data:`UNDATED_RUN_EVIDENCE_SKIP_REASON`), and ``skipped_unrecorded_costs`` does
    the same for a run that did not record the costs its engine applied (see
    :data:`APPLIED_COSTS_NOT_RECORDED_SKIP_REASON`). Both are the same skip
    mechanism: an explicit per-slug reason in this summary, a structlog skip event,
    a count in the ``arena_run_evidence_written`` event, and no run directory — a
    skip is never silent and never fails the other runs.
    """

    finalized: list[FinalisedRun] = field(default_factory=list)
    skipped_undated: dict[str, str] = field(default_factory=dict)
    skipped_unrecorded_costs: dict[str, str] = field(default_factory=dict)
    attempt: FinalisedAttempt | None = None


@dataclass
class _RunEvidenceWrite:
    """One invocation's finalized runs plus the failures and skips it recorded.

    Internal shape between finalisation and attempt publication: the public
    :class:`ArenaEvidenceSummary` stays the caller-facing view, while
    ``finalized_by_pair``/``failure_details`` let the attempt record classify every
    requested pair exactly once.
    """

    summary: ArenaEvidenceSummary = field(default_factory=ArenaEvidenceSummary)
    finalized_by_pair: dict[tuple[str, str], FinalisedRun] = field(default_factory=dict)
    failure_details: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    failures: list[BaseException] = field(default_factory=list)


def _recorded_costs(run: ArenaRun) -> dict[str, float] | None:
    """The fee/tax pair the run's engine was configured with, or ``None`` if unrecorded.

    A record that does not carry both ratios is treated exactly like no record at
    all: the writer must not fill the gap with a default. A recorded but non-finite
    ratio is *not* silently dropped here — :class:`CostApplication` refuses it.
    """
    costs = run.applied_costs
    if costs is None or not {"fee_ratio", "tax_ratio"} <= set(costs):
        return None
    return costs


def _requested_pairs(outcome: ArenaOutcome) -> list[tuple[str, str]]:
    """Every requested pair, de-duplicated in first-occurrence order.

    ``run_arena`` records the request; a legacy library outcome without it falls back
    to the pairs that actually ran or failed, so existing fixtures keep a coherent
    recorded scope.
    """
    recorded = list(outcome.requested) or [(run.strategy, run.cost_regime) for run in outcome.runs] + [
        (failure.strategy, failure.cost_regime) for failure in outcome.failed
    ]
    return list(dict.fromkeys(recorded))


def _requested_runs(outcome: ArenaOutcome, pairs: Sequence[tuple[str, str]]) -> list[ArenaRun]:
    """The completed runs whose pair was requested and not recorded as a failure.

    A pair that the outcome records as failed publishes no run evidence, and that
    includes the contradictory case where the same pair is recorded both completed and
    failed: a self-contradicting pair must never become the "latest success", because a
    failed attempt leaving misleading healthy evidence is exactly what ADR-0025
    Decision 5 forbids. Nothing outside the request is published either.
    """
    requested = set(pairs)
    recorded_failures = {(failure.strategy, failure.cost_regime) for failure in outcome.failed}
    return [run for run in outcome.runs if (run.strategy, run.cost_regime) in requested and (run.strategy, run.cost_regime) not in recorded_failures]


def _loaded_canonical_universe(outcome: ArenaOutcome) -> list[str] | None:
    """Canonical display keys of the shared load, or ``None`` when it cannot prove them.

    The arena loaded **one** frame for every run, so its identity columns are what the
    invocation actually consulted: they are parsed through the identity model into
    display keys (``market/MIC/percent-quoted-symbol``). A ticker-only fixture, a
    malformed identity or an empty frame returns ``None``, and the caller records the
    run's own bare tickers with the ``ticker_only`` basis instead.

    A missing MIC is **never** filled and a venue is never guessed (ADR-0016
    Decision 1/2), which is why this is a parse of what was loaded rather than a
    reconstruction: an unparseable frame is disclosed as ``ticker_only``.

    Falsified by: claiming ``canonical`` for a ticker-only fixture, filling a blank
    MIC, or resolving a ticker to a venue from configuration.
    """
    from equity_lake.core.instruments import (  # noqa: PLC0415 - evidence imports this module
        InstrumentIdentityError,
        InstrumentKey,
        identity_key_values,
    )

    try:
        keys = identity_key_values(outcome.data)
    except (InstrumentIdentityError, ValueError):
        return None
    if not keys:
        return None
    return sorted({InstrumentKey(market=market, mic=mic, symbol=symbol).display_key for market, mic, symbol in keys}) or None


def _finalise_arena_run_evidence(outcome: ArenaOutcome, *, root: Path) -> _RunEvidenceWrite:
    """Finalise one immutable run per requested completed :class:`ArenaRun` under ``root/runs``.

    Each run's scope is its own: that strategy, that cost regime, the run's window
    and universe, its initial cash, the strategy parameters the run was built with
    (``run.strategy_params``, captured from the instance that actually ran — never
    re-instantiated from the registry here), and the fees/taxes that regime actually
    applied — read from ``run.applied_costs``, the pair the arena recorded when it
    built the engine. The writer never re-resolves :data:`MARKET_COST_DEFAULTS`, so
    an override or a per-market default can never be recorded wrongly. Each run also
    records a bounded, non-secret ``config_digest`` of the configuration actually in
    force on this path (markets plus the ``market_costs`` override used).

    Runs that cannot be finalised are skipped and reported: an undated run, and a run
    with no recorded applied costs (never guessed). A raising ``finalise_run`` is
    collected per pair so the remaining runs still finalise; the caller publishes the
    attempt record for what did finalise and only then re-raises, so a write failure
    is durable evidence rather than a lost one.
    """
    from equity_lake.backtesting.evidence import (  # noqa: PLC0415 - evidence imports this module
        CodeIdentity,
        CostApplication,
        ExecutionTiming,
        InputSnapshot,
        PriceOriginSummary,
        RunScope,
        digest_non_secret_config,
        finalise_run,
        probe_code_identity,
    )

    evidence = _RunEvidenceWrite()
    summary = evidence.summary
    requested_runs = _requested_runs(outcome, _requested_pairs(outcome))
    if not requested_runs:
        return evidence

    # The canonical universe the shared load can prove (``None`` when it cannot), plus
    # the read-seam snapshots that load recorded. The conversions are plain-mapping ->
    # ``InputSnapshot`` here, in the writer, so ``arena``/``data_loader`` keep no
    # import of this module.
    loaded_universe = _loaded_canonical_universe(outcome)
    input_snapshots = [
        InputSnapshot(name=str(snapshot["name"]), sha256=str(snapshot["sha256"]), source=str(snapshot["source"]), version=snapshot.get("version"))
        for snapshot in outcome.input_snapshots
    ]
    # The read seam's whole-load origin classification, validated here rather than trusted:
    # each summary must bind to one of the snapshots above, and the manifest re-derives
    # `data_provenance` from them (ADR-0027 §4.5). Empty for a preloaded frame, which then
    # derives `unknown`.
    price_origin_summaries = [PriceOriginSummary.model_validate(entry) for entry in outcome.price_origin_summaries]

    # The configuration actually in force in this path: the scoped markets and the
    # market-cost override the arena really used ({} when it used the defaults).
    # Secret-named keys are stripped before hashing and the config itself is never stored.
    config_digest = digest_non_secret_config({"markets": list(outcome.markets), "market_costs": dict(outcome.market_costs or {})})

    # Probed lazily and once per write: code identity never changes a run ID, and an
    # invocation with no datable run has nothing to record it in.
    code: CodeIdentity | None = None
    for run in requested_runs:
        slug = _run_slug(run)
        pair = (run.strategy, run.cost_regime)
        if run.result.equity_dates is None:
            summary.skipped_undated[slug] = UNDATED_RUN_EVIDENCE_SKIP_REASON
            logger.warning(
                "arena_run_evidence_skipped_undated",
                strategy=run.strategy,
                cost_regime=run.cost_regime,
                slug=slug,
                reason=UNDATED_RUN_EVIDENCE_SKIP_REASON,
                evidence_root=str(root),
            )
            continue
        applied = _recorded_costs(run)
        if applied is None:
            summary.skipped_unrecorded_costs[slug] = APPLIED_COSTS_NOT_RECORDED_SKIP_REASON
            logger.warning(
                "arena_run_evidence_skipped_costs_not_recorded",
                strategy=run.strategy,
                cost_regime=run.cost_regime,
                slug=slug,
                reason=APPLIED_COSTS_NOT_RECORDED_SKIP_REASON,
                evidence_root=str(root),
            )
            continue
        try:
            if code is None:
                code = probe_code_identity()
            # Universe: the parseable identity of the load the arena read, else this
            # run's own tickers with the basis that claims no venue at all (E01c).
            universe: list[str]
            universe_basis: UniverseBasis
            if loaded_universe is not None:
                universe, universe_basis = list(loaded_universe), "canonical"
            else:
                universe = [str(ticker) for ticker in run.result.tickers if str(ticker).strip()]
                universe_basis = "ticker_only"
            scope = RunScope(
                universe=universe,
                start_date=run.result.start_date,
                end_date=run.result.end_date,
                strategies=[run.strategy],
                # The cost-regime identity is the regime name *plus* the fees and taxes
                # this run actually applied, so a differently-costed invocation of the
                # same regime is a different scope (and a different run ID). The values
                # come from the run itself, never from a writer-side re-resolution.
                costs=[
                    CostApplication(
                        regime=run.cost_regime,
                        fee_ratio=float(applied["fee_ratio"]),
                        tax_ratio=float(applied["tax_ratio"]),
                    )
                ],
                initial_cash=float(run.result.initial_cash),
                # Captured from the instance that ran; an empty mapping means
                # "none recorded", never a registry-default guess (E01b.1).
                parameters=dict(run.strategy_params),
            )
            finalised = finalise_run(
                run.result,
                scope=scope,
                # Derived by parsing the loaded frame, recorded as a parsed fact (never
                # a guess); an explicit claim over an unparseable universe is refused.
                universe_basis=universe_basis,
                # The engine trades at the session close with its default daily
                # resample (arena runs pass no resample override).
                execution=ExecutionTiming(
                    trade_at_price="close",
                    resample="D",
                    note="decision at the session close of each session, evaluated daily (engine default resample)",
                ),
                config_digest=config_digest,
                code=code,
                # Read-seam snapshots of the shared load, plus the row-bound origin
                # summaries that load classified. `data_provenance` is not passed: the
                # writer derives it from these summaries, never from a literal.
                input_snapshots=input_snapshots,
                price_origin_summaries=price_origin_summaries,
                root=root,
            )
        except Exception as exc:  # noqa: BLE001 - one run's evidence failure must not stop the others
            evidence.failures.append(exc)
            evidence.failure_details[pair] = (type(exc).__name__, _sanitize_failure_reason(exc) or type(exc).__name__)
            logger.error(
                "arena_run_evidence_failed",
                strategy=run.strategy,
                cost_regime=run.cost_regime,
                slug=slug,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            continue
        summary.finalized.append(finalised)
        evidence.finalized_by_pair[pair] = finalised
        logger.info(
            "arena_run_evidence_finalized",
            slug=slug,
            run_dir=str(finalised.directory),
            run_id=finalised.manifest.run_id,
        )

    logger.info(
        "arena_run_evidence_written",
        root=str(root),
        runs=len(requested_runs),
        finalized=len(summary.finalized),
        skipped_undated=len(summary.skipped_undated),
        skipped_unrecorded_costs=len(summary.skipped_unrecorded_costs),
        failed=len(evidence.failures),
        universe_basis="canonical" if loaded_universe is not None else "ticker_only",
        input_snapshots=len(input_snapshots),
    )
    return evidence


def _attempt_outcomes(outcome: ArenaOutcome, evidence: _RunEvidenceWrite) -> list[PairOutcome]:
    """Classify every requested pair exactly once, in request order (Decision 5)."""
    from equity_lake.backtesting.attempts import PairOutcome  # noqa: PLC0415 - attempts imports evidence

    completed_runs = {(run.strategy, run.cost_regime) for run in outcome.runs}
    arena_failures = {(failure.strategy, failure.cost_regime): failure for failure in outcome.failed}
    outcomes: list[PairOutcome] = []
    for strategy, cost_regime in _requested_pairs(outcome):
        pair = (strategy, cost_regime)
        slug = f"{strategy}__{cost_regime}"
        completed = pair in completed_runs
        recorded_failure = arena_failures.get(pair)
        evidence_failure = evidence.failure_details.get(pair)
        if completed and recorded_failure is not None:
            # A pair cannot be both a completed run and a recorded failure: name the
            # contradiction instead of silently picking a side.
            outcomes.append(PairOutcome(strategy=strategy, cost_regime=cost_regime, outcome="contradictory"))
            continue
        if evidence_failure is not None:
            outcomes.append(
                PairOutcome(
                    strategy=strategy,
                    cost_regime=cost_regime,
                    outcome="failed",
                    error_type=evidence_failure[0],
                    reason=evidence_failure[1],
                )
            )
            continue
        if recorded_failure is not None:
            outcomes.append(
                PairOutcome(
                    strategy=strategy,
                    cost_regime=cost_regime,
                    outcome="failed",
                    error_type=recorded_failure.error_type,
                    reason=recorded_failure.reason,
                )
            )
            continue
        if completed:
            finalised = evidence.finalized_by_pair.get(pair)
            if finalised is not None:
                run = next(run for run in outcome.runs if (run.strategy, run.cost_regime) == pair)
                outcomes.append(
                    PairOutcome(
                        strategy=strategy,
                        cost_regime=cost_regime,
                        outcome="completed",
                        run_id=finalised.manifest.run_id,
                        run_dir=finalised.directory.name,
                        evaluation_status=run.evaluation_status,
                    )
                )
            elif slug in evidence.summary.skipped_undated:
                outcomes.append(PairOutcome(strategy=strategy, cost_regime=cost_regime, outcome="unevidenced", reason="undated_curve"))
            else:
                outcomes.append(PairOutcome(strategy=strategy, cost_regime=cost_regime, outcome="unevidenced", reason="applied_costs_not_recorded"))
            continue
        outcomes.append(PairOutcome(strategy=strategy, cost_regime=cost_regime, outcome="missing"))
    return outcomes


def _attempt_costs(runs: Sequence[ArenaRun]) -> list[CostApplication]:
    """One applied cost application per regime whose fees/taxes the runs recorded."""
    from equity_lake.backtesting.evidence import CostApplication  # noqa: PLC0415 - evidence imports this module

    costs: dict[str, CostApplication] = {}
    for run in runs:
        applied = _recorded_costs(run)
        if applied is None or run.cost_regime in costs:
            continue
        costs[run.cost_regime] = CostApplication(
            regime=run.cost_regime,
            fee_ratio=float(applied["fee_ratio"]),
            tax_ratio=float(applied["tax_ratio"]),
        )
    return [costs[regime] for regime in sorted(costs)]


def _attempt_universe(outcome: ArenaOutcome) -> list[str]:
    """The instruments this invocation was scoped to (never empty).

    Canonical ``(market, MIC, symbol)`` universe identity is deferred to E01c, so
    this is the requested ticker set as the arena recorded it.
    """
    tickers = {str(ticker).strip() for run in outcome.runs for ticker in run.result.tickers if str(ticker).strip()}
    if not tickers and "ticker" in outcome.data.columns:
        tickers = {str(value).strip() for value in outcome.data["ticker"].to_list() if value is not None and str(value).strip()}
    return sorted(tickers) or ["unspecified"]


def _attempt_window(outcome: ArenaOutcome) -> tuple[date, date]:
    """The exact date window the runs actually used."""
    if outcome.runs:
        return min(run.result.start_date for run in outcome.runs), max(run.result.end_date for run in outcome.runs)
    if "date" in outcome.data.columns and not outcome.data.is_empty():
        dates = outcome.data["date"]
        return cast("date", dates.min()), cast("date", dates.max())
    today = date.today()
    return today, today


def _publish_attempt_record(
    outcome: ArenaOutcome,
    evidence: _RunEvidenceWrite,
    *,
    cards: Sequence[FindingCard],
    root: Path,
) -> tuple[list[FindingCard], FinalisedAttempt]:
    """Publish the one immutable attempt directory this invocation owns.

    The attempt owns the three arena comparison cards and the benchmark copy, one
    ``PairOutcome`` per requested pair, the applied costs whose fees/taxes are known,
    and the run evidence that did finalise. It records a ``write_failed`` status when
    a run-evidence write raised, naming each such write in ``run_write_failures``, so
    the failure is durable before the exception reaches the caller (ADR-0025
    Decision 5).

    The cards are built with **unbound** legacy-vocabulary refs, so they are bound to
    this invocation's evidence (``run:<run_id>``, ``attempt:<attempt_id>/...``,
    ``unevidenced:<slug>``, ``unevidenced:benchmark``) here, at the one choke point
    every persisted arena card passes through (ADR-0025 Decision 8). Only the requested
    pairs' slugs are eligible for binding, and the benchmark ref is bound to this
    attempt's copy **only** when this attempt really writes
    ``benchmark__equity.parquet``: an empty curve writes no copy, so the token then
    becomes ``unevidenced:benchmark`` rather than a path that does not exist.

    Returns:
        The **bound** cards that were persisted, plus the published attempt. Returning
        the cards actually written keeps a caller's round-trip check meaningful: what
        it holds is byte-for-byte what the attempt directory holds.
    """
    from equity_lake.backtesting.attempts import (  # noqa: PLC0415 - attempts imports evidence
        PairRequest,
        RunWriteFailure,
        attempt_id_for,
        bind_cards_to_evidence,
        write_attempt_record,
    )

    pairs = _requested_pairs(outcome)
    requested_runs = _requested_runs(outcome, pairs)
    outcomes = _attempt_outcomes(outcome, evidence)
    completed = sum(1 for pair_outcome in outcomes if pair_outcome.outcome == "completed")
    if evidence.failures:
        status: AttemptStatus = "write_failed"
    elif completed and completed == len(pairs):
        status = "complete"
    elif completed:
        status = "partial"
    else:
        status = "zero_completion"
    notes: list[str] = []
    if not pairs:
        notes.append("no pair was requested, so this invocation attempted no run and has no run evidence to point at")
    start_date, end_date = _attempt_window(outcome)
    universe = _attempt_universe(outcome)
    costs = _attempt_costs(requested_runs)
    pair_requests = [PairRequest(strategy=strategy, cost_regime=cost_regime) for strategy, cost_regime in pairs]
    attempt_id = attempt_id_for(
        requested=pair_requests,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        initial_cash=outcome.initial_cash,
        markets=list(outcome.markets),
        costs=costs,
    )
    # The single statement of whether this attempt writes ``benchmark__equity.parquet``:
    # the binder below is told the same fact the writer is, so a card can never name a
    # benchmark copy the attempt omits.
    benchmark = outcome.benchmark if not outcome.benchmark.is_empty() else None
    bound_cards = bind_cards_to_evidence(
        cards,
        runs_by_slug={f"{strategy}__{cost_regime}": run.manifest.run_id for (strategy, cost_regime), run in evidence.finalized_by_pair.items()},
        requested_slugs={f"{strategy}__{cost_regime}" for strategy, cost_regime in pairs},
        attempt_id=attempt_id,
        benchmark_written=benchmark is not None,
    )
    run_write_failures = [
        RunWriteFailure(strategy=strategy, cost_regime=cost_regime, error_type=error_type, reason=reason)
        for (strategy, cost_regime), (error_type, reason) in evidence.failure_details.items()
    ]
    summary_facts: dict[str, Any] = {
        "requested": len(pairs),
        "completed": completed,
        "failed": sum(1 for pair_outcome in outcomes if pair_outcome.outcome == "failed"),
        "missing": sum(1 for pair_outcome in outcomes if pair_outcome.outcome == "missing"),
        "contradictory": sum(1 for pair_outcome in outcomes if pair_outcome.outcome == "contradictory"),
        "unevidenced": sum(1 for pair_outcome in outcomes if pair_outcome.outcome == "unevidenced"),
        "skipped_undated": sorted(evidence.summary.skipped_undated),
        "skipped_unrecorded_costs": sorted(evidence.summary.skipped_unrecorded_costs),
    }
    return bound_cards, write_attempt_record(
        root,
        requested=pair_requests,
        outcomes=outcomes,
        run_write_failures=run_write_failures,
        start_date=start_date,
        end_date=end_date,
        universe=universe,
        initial_cash=outcome.initial_cash,
        markets=list(outcome.markets),
        costs=costs,
        benchmark=benchmark,
        cards=bound_cards,
        runs=evidence.summary.finalized,
        summary_facts=summary_facts,
        status=status,
        notes=notes,
    )


def write_arena_artifacts(
    outcome: ArenaOutcome,
    *,
    base: Path | None = None,
    run_date: date | None = None,
    scope: dict[str, Any] | None = None,
) -> list[FindingCard]:
    """Write the immutable run/attempt evidence and return the three Phase 1 FindingCards.

    Each requested completed pair is finalised as one immutable dated run under
    ``<base>/runs/``, and the invocation publishes one immutable attempt directory
    under ``<base>/attempts/`` that owns the three comparison cards and the benchmark
    copy (ADR-0025 Decisions 2, 3 and 8). ``base`` roots the layout, so tests can
    point at a temporary directory; it defaults to ``FINDINGS_DIR``.

    **No legacy path is written**: no ``<strategy>__<regime>/`` directory, no flat
    ``benchmark__equity.parquet`` and no flat ``<card-id>.json`` card. Legacy files
    written before E01 stay frozen, readable and untouched.

    Returns the cards it actually persisted: the evidence refs are bound to this
    invocation's runs and attempt directory (``run:<run_id>``,
    ``attempt:<attempt_id>/benchmark__equity.parquet``,
    ``unevidenced:<slug>``, ``unevidenced:benchmark``), which
    is exactly what the attempt directory holds on disk. Every existing caller is
    unaffected. The evidence
    outcome (which runs were finalised, which were skipped and why, and the attempt
    directory) is returned by :func:`write_arena_artifacts_with_evidence`, which this
    function delegates to — the extra mechanism is a second return value on that
    sibling rather than a changed return type here.
    """
    cards, _ = write_arena_artifacts_with_evidence(outcome, base=base, run_date=run_date, scope=scope)
    return cards


def write_arena_artifacts_with_evidence(
    outcome: ArenaOutcome,
    *,
    base: Path | None = None,
    run_date: date | None = None,
    scope: dict[str, Any] | None = None,
) -> tuple[list[FindingCard], ArenaEvidenceSummary]:
    """Write the arena evidence: one immutable run per completed pair, one attempt record.

    Writes, in this order: one immutable dated run per requested completed pair under
    ``<base>/runs/``, then one attempt directory under ``<base>/attempts/`` that owns
    the three comparison cards and the benchmark copy (ADR-0025 Decision 2/3/8). No
    legacy path is written.

    Returns:
        The cards this invocation **persisted** — evidence refs already bound to its
        runs and attempt directory, so each returned card equals the card reloaded
        from the attempt directory field-for-field — plus the
        :class:`ArenaEvidenceSummary`, whose ``attempt``
        is the published attempt record. A run whose backend curve carries no dates
        is skipped and reported in ``skipped_undated`` (no fabricated calendar, no
        manifest claiming dates), and a run that does not record the costs its engine
        applied is skipped and reported in ``skipped_unrecorded_costs`` (no guessed
        default cost application); neither ever stops the other runs.

    Raises:
        Exception: the first run-evidence write failure, re-raised **after** the
            attempt record recording it as ``write_failed`` has been published, so the
            failure is durable and the caller still sees it. A partially written
            temporary directory never becomes a run or an attempt.
    """
    from equity_lake.core.paths import FINDINGS_DIR  # noqa: PLC0415 - keep the CLI import seam lazy

    root = base or FINDINGS_DIR
    root.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today()

    # Immutable run evidence first: one dated run per requested completed pair.
    evidence = _finalise_arena_run_evidence(outcome, root=root)
    unbound_cards = build_finding_cards(outcome, run_date=run_date, scope=scope)

    # The cards the attempt actually persisted (evidence refs bound to this
    # invocation's runs/attempt) are what this function returns, so a caller's
    # round-trip check compares against the persisted bytes rather than a
    # pre-binding copy.
    cards = unbound_cards
    attempt: FinalisedAttempt | None = None
    try:
        cards, attempt = _publish_attempt_record(outcome, evidence, cards=unbound_cards, root=root)
    except Exception:  # noqa: BLE001 - never mask a run-evidence failure with a record failure
        if not evidence.failures:
            raise
        logger.error(
            "arena_attempt_record_failed",
            root=str(root),
            error="the attempt record could not be published; the run-evidence failure below is the reported one",
        )
        raise evidence.failures[0] from None
    evidence.summary.attempt = attempt
    logger.info(
        "arena_artifacts_written",
        root=str(root),
        runs=len(evidence.summary.finalized),
        cards=len(cards),
        attempt_dir=str(attempt.directory),
        attempt_status=attempt.record.status,
    )

    if evidence.failures:
        first = evidence.failures[0]
        first.add_note(
            f"arena run evidence: {len(evidence.summary.finalized)} run(s) finalized before {len(evidence.failures)} failed; "
            f"attempt {attempt.directory.name} was published with status '{attempt.record.status}' and no legacy path was written"
        )
        raise first
    return cards, evidence.summary


__all__ = [
    "APPLIED_COSTS_NOT_RECORDED_SKIP_REASON",
    "UNDATED_RUN_EVIDENCE_SKIP_REASON",
    "ArenaEvidenceSummary",
    "build_finding_cards",
    "drawdown_series",
    "write_arena_artifacts",
    "write_arena_artifacts_with_evidence",
    "dump_json_artifact_strictly",
    "json_safe_metrics",
    "refuse_non_finite_json",
    "trades_payload",
    "write_backtest_report",
    "write_dated_backtest_report",
]
