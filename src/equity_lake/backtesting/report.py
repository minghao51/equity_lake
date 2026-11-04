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
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import polars as pl
import structlog

from equity_lake.backtesting.arena import (
    BENCHMARK_MISSING_MARK_POLICY,
    BENCHMARK_POLICY,
    STRATEGY_REGISTRY,
    ArenaOutcome,
    ArenaRun,
)
from equity_lake.backtesting.metrics import DEFAULT_RISK_FREE_RATE, equity_curve_metrics
from equity_lake.backtesting.result import BacktestResult
from equity_lake.findings import FindingCard, write_finding_card

if TYPE_CHECKING:  # pragma: no cover - typing only (evidence imports this module)
    from equity_lake.backtesting.evidence import FinalisedRun

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


def _json_location(path: tuple[Any, ...]) -> str:
    """Render a JSON path as ``entry 3 field 'price'`` (an empty path is ``payload``)."""
    parts: list[str] = []
    fields: list[str] = []
    for node in path:
        if isinstance(node, int) and not isinstance(node, bool):
            if fields:
                parts.append(f"field {'.'.join(fields)!r}")
                fields = []
            parts.append(f"entry {node}")
        else:
            fields.append(str(node))
    if fields:
        parts.append(f"field {'.'.join(fields)!r}")
    return " ".join(parts) if parts else "payload"


def refuse_non_finite_json(artifact: str, payload: Any, _path: tuple[Any, ...] = ()) -> None:
    """Refuse a JSON payload carrying a non-finite number, naming artifact and location.

    ``json.dumps(..., allow_nan=False)`` refuses the payload too, but its message
    names neither the artifact nor the offending entry/field, so a reviewer cannot
    tell *which* value is a defect. A non-finite price, fee or quantity is a data
    defect, not an unavailable measurement, so the run is **refused** rather than
    sanitised to ``null`` (ADR-0025; F03 precedent: an unavailable number is omitted,
    never persisted — and a measured-looking number is never invented).

    ``None`` fields pass: an open position legitimately has no realised pnl or exit
    price, and a JSON ``null`` is the honest encoding for it.

    Raises:
        ValueError: naming the artifact and the offending index/key, e.g.
            ``trades.json entry 3 field 'price' is non-finite``.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            refuse_non_finite_json(artifact, value, (*_path, str(key)))
        return
    if isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            refuse_non_finite_json(artifact, value, (*_path, index))
        return
    if payload is None or isinstance(payload, (bool, str, bytes)):
        return
    try:
        numeric = float(payload)
    except (TypeError, ValueError):
        return
    if not math.isfinite(numeric):
        raise ValueError(
            f"{artifact} {_json_location(_path)} is non-finite: a non-finite number is a data defect, not an "
            "unavailable measurement, so the run is refused rather than publishing NaN/Infinity as evidence "
            "(ADR-0025; F03 precedent)"
        )


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
    """Immutable-run evidence outcome of one arena artifact write.

    ``finalized`` holds the published :class:`FinalisedRun` records.
    ``skipped_undated`` maps a legacy ``<strategy>__<regime>`` slug to the reason its
    run was **not** finalised because the backend curve carried no dates (see
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


def _strategy_parameters(strategy: str) -> dict[str, Any]:
    """The parameters actually in force for ``strategy`` (empty when unknown).

    ``run_arena`` builds every strategy as ``STRATEGY_REGISTRY[name]()``, so the
    registry's default instance carries exactly the parameters that ran. An
    unregistered strategy records no parameters rather than invented ones.
    """
    factory = STRATEGY_REGISTRY.get(strategy)
    if factory is None:
        return {}
    return dict(factory().params)


def _finalise_arena_run_evidence(outcome: ArenaOutcome, *, root: Path) -> ArenaEvidenceSummary:
    """Finalise one immutable run per completed :class:`ArenaRun` under ``root/runs``.

    Each run's scope is its own: that strategy, that cost regime, the run's window
    and universe, its initial cash, that strategy's parameters, and the fees/taxes
    that regime actually applied — read from ``run.applied_costs``, the pair the
    arena recorded when it built the engine. The writer never re-resolves
    :data:`MARKET_COST_DEFAULTS`, so an override or a per-market default can never
    be recorded wrongly. Runs that cannot be finalised are skipped and reported: an
    undated run, and a run with no recorded applied costs (never guessed). A raising
    ``finalise_run`` is collected so the remaining runs still finalise, and is then
    re-raised so the write failure reaches the caller.
    """
    from equity_lake.backtesting.evidence import (  # noqa: PLC0415 - evidence imports this module
        CodeIdentity,
        CostApplication,
        ExecutionTiming,
        RunScope,
        finalise_run,
        probe_code_identity,
    )

    summary = ArenaEvidenceSummary()
    if not outcome.runs:
        return summary

    # Probed lazily and once per write: code identity never changes a run ID, and an
    # invocation with no datable run has nothing to record it in.
    code: CodeIdentity | None = None
    failures: list[BaseException] = []
    for run in outcome.runs:
        slug = _run_slug(run)
        if run.result.equity_dates is None:
            summary.skipped_undated[slug] = UNDATED_RUN_EVIDENCE_SKIP_REASON
            logger.warning(
                "arena_run_evidence_skipped_undated",
                strategy=run.strategy,
                cost_regime=run.cost_regime,
                slug=slug,
                reason=UNDATED_RUN_EVIDENCE_SKIP_REASON,
                legacy_artifacts=str(root / slug),
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
                legacy_artifacts=str(root / slug),
            )
            continue
        try:
            if code is None:
                code = probe_code_identity()
            scope = RunScope(
                universe=[str(ticker) for ticker in run.result.tickers if str(ticker).strip()],
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
                parameters=_strategy_parameters(run.strategy),
            )
            finalised = finalise_run(
                run.result,
                scope=scope,
                # The engine trades at the session close with its default daily
                # resample (arena runs pass no resample override).
                execution=ExecutionTiming(
                    trade_at_price="close",
                    resample="D",
                    note="decision at the session close of each session, evaluated daily (engine default resample)",
                ),
                code=code,
                root=root,
            )
        except Exception as exc:  # noqa: BLE001 - one run's evidence failure must not stop the others
            failures.append(exc)
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
        logger.info(
            "arena_run_evidence_finalized",
            slug=slug,
            run_dir=str(finalised.directory),
            run_id=finalised.manifest.run_id,
        )

    logger.info(
        "arena_run_evidence_written",
        root=str(root),
        runs=len(outcome.runs),
        finalized=len(summary.finalized),
        skipped_undated=len(summary.skipped_undated),
        skipped_unrecorded_costs=len(summary.skipped_unrecorded_costs),
        failed=len(failures),
    )
    if failures:
        # Every run was attempted first, so the other runs' evidence and the legacy
        # artifacts already written stay; the caller sees a write failure.
        first = failures[0]
        first.add_note(
            f"arena run evidence: {len(summary.finalized)} of {len(outcome.runs)} run(s) finalized before "
            f"{len(failures)} failed; legacy artifacts under {root} are untouched and no partial run directory was published"
        )
        raise first
    return summary


def write_arena_artifacts(
    outcome: ArenaOutcome,
    *,
    base: Path | None = None,
    run_date: date | None = None,
    scope: dict[str, Any] | None = None,
) -> list[FindingCard]:
    """Write per-run reports, immutable run evidence and the three Phase 1 FindingCards under ``base``.

    Each run's legacy artifacts land under ``<base>/<strategy>__<regime>/`` exactly
    as before, and each completed run is **additionally** finalised as one immutable
    dated run under ``<base>/runs/`` (ADR-0025 Decision 2). ``base`` roots both
    layouts, so tests can point at a temporary directory; it defaults to
    ``FINDINGS_DIR``.

    Returns the written cards, so every existing caller is unaffected. The
    run-evidence outcome (which runs were finalised, which were skipped and why) is
    returned by :func:`write_arena_artifacts_with_evidence`, which this function
    delegates to — the extra mechanism is a second return value on that sibling
    rather than a changed return type here.
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
    """Write the arena artifacts **and** one immutable run per completed pair.

    Writes, in this order: the legacy per-run reports under
    ``<base>/<strategy>__<regime>/`` (unchanged shape), the benchmark artifact, the
    FindingCards, and finally the immutable dated runs under ``<base>/runs/``.
    Ordering matters: evidence is written last, so a finalisation failure never
    costs the legacy artifacts or the cards.

    Returns:
        The written cards plus the :class:`ArenaEvidenceSummary`. A run whose
        backend curve carries no dates is skipped and reported in
        ``skipped_undated`` (no fabricated calendar, no manifest claiming dates), and
        a run that does not record the costs its engine applied is skipped and
        reported in ``skipped_unrecorded_costs`` (no guessed default cost
        application); neither ever stops the other runs.

    Raises:
        Exception: the first ``finalise_run`` failure, re-raised after every run has
            been attempted, with a note recording how many runs finalized. A
            partially written temporary directory never becomes a run.
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

    # Additional immutable run evidence: one dated run per completed pair.
    summary = _finalise_arena_run_evidence(outcome, root=root)
    return cards, summary


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
