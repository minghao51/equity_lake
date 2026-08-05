"""Strategy arena — run N strategies x cost regimes on a single shared data load.

Produces :class:`BacktestResult` runs plus an equal-weight buy-and-hold benchmark,
the inputs for the Phase 1 FindingCards (strategy / cost / benchmark axes). Reuses
:class:`VectorBacktestEngine` with ``preloaded_data`` so data is loaded once and each
run only re-evaluates the engine.

The meta-labeled ensemble is intentionally **not** here — it requires a trained
v2 model (Phase 2). This module is structured so a ``meta_label`` strategy can be
added to :data:`STRATEGY_REGISTRY` later without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl
import structlog

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.engine import DEFAULT_FEE_RATIO, DEFAULT_TAX_RATIO, VectorBacktestEngine
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy import (
    BaseStrategy,
    BBMeanReversionStrategy,
    CrossSectionalMomentumStrategy,
    SMACrossoverStrategy,
)

logger = structlog.get_logger(__name__)

# Cost regimes — per-leg fee_ratio + tax_ratio. "realistic" mirrors the engine defaults.
COST_REGIMES: dict[str, dict[str, float]] = {
    "zero": {"fee_ratio": 0.0, "tax_ratio": 0.0},
    "realistic": {"fee_ratio": DEFAULT_FEE_RATIO, "tax_ratio": DEFAULT_TAX_RATIO},
    "high": {"fee_ratio": 0.005, "tax_ratio": DEFAULT_TAX_RATIO},
}

# Strategy registry — name -> zero-arg factory (strategies carry their own defaults).
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "momentum": CrossSectionalMomentumStrategy,
    "mean_reversion": BBMeanReversionStrategy,
    "trend_following": SMACrossoverStrategy,
}


@dataclass
class ArenaRun:
    """One backtest in the arena: a single strategy under one cost regime."""

    strategy: str
    cost_regime: str
    result: BacktestResult


@dataclass
class ArenaOutcome:
    """All arena runs plus the shared data, benchmark, and starting capital."""

    runs: list[ArenaRun]
    data: pl.DataFrame
    benchmark: pl.DataFrame  # columns: [date, equity]
    initial_cash: float


def equal_weight_buyhold(data: pl.DataFrame, initial_cash: float = 100_000.0) -> pl.DataFrame:
    """Equal-weight buy-and-hold equity curve from long-format OHLCV data.

    Returns a frame with columns ``[date, equity]`` scaled to ``initial_cash``.
    Used as the benchmark for the "vs benchmark" FindingCard axis. Robust to a
    single ticker or missing returns (nulls dropped before averaging).
    """
    if data.is_empty() or "close" not in data.columns:
        return pl.DataFrame(schema={"date": pl.Date, "equity": pl.Float64})

    daily = (
        data.select("date", "ticker", "close")
        .sort("date")
        .with_columns(pl.col("close").pct_change().over("ticker").alias("ret"))
        .group_by("date")
        .agg(pl.col("ret").drop_nulls().mean().alias("ew_ret"))
        .sort("date")
        .with_columns((1.0 + pl.col("ew_ret").fill_null(0.0)).cum_prod().alias("growth"))
    )
    return daily.select(
        pl.col("date"),
        (pl.col("growth") * initial_cash).alias("equity"),
    )


def run_arena(
    tickers: list[str],
    start_date: date,
    end_date: date,
    *,
    markets: tuple[str, ...] = ("us",),
    initial_cash: float = 100_000.0,
    strategies: list[str] | None = None,
    cost_regimes: list[str] | None = None,
    preloaded_data: pl.DataFrame | None = None,
) -> ArenaOutcome:
    """Run strategies x cost regimes on one shared data load.

    Args:
        tickers: Tickers to backtest.
        start_date/end_date: Backtest window.
        markets: Market codes forwarded to :class:`BacktestDataLoader`.
        initial_cash: Starting capital per run (and the benchmark scale).
        strategies: Subset of :data:`STRATEGY_REGISTRY` keys; default all.
        cost_regimes: Subset of :data:`COST_REGIMES` keys; default all.
        preloaded_data: Pre-loaded long OHLCV frame; skips the loader if given.

    Returns:
        :class:`ArenaOutcome` with one :class:`ArenaRun` per (strategy, regime)
        that completed. A run that raises is logged and skipped rather than
        aborting the whole arena.
    """
    data = (
        preloaded_data
        if preloaded_data is not None
        else BacktestDataLoader().load(tickers=tickers, start_date=start_date, end_date=end_date, markets=list(markets))
    )
    if data.is_empty():
        raise ValueError("Arena has no data to backtest (empty load).")

    strat_names = strategies or list(STRATEGY_REGISTRY)
    regime_names = cost_regimes or list(COST_REGIMES)

    unknown_strats = set(strat_names) - set(STRATEGY_REGISTRY)
    if unknown_strats:
        raise ValueError(f"Unknown strategies: {sorted(unknown_strats)}. Available: {list(STRATEGY_REGISTRY)}")
    unknown_regimes = set(regime_names) - set(COST_REGIMES)
    if unknown_regimes:
        raise ValueError(f"Unknown cost regimes: {sorted(unknown_regimes)}. Available: {list(COST_REGIMES)}")

    runs: list[ArenaRun] = []
    for strat_name in strat_names:
        strategy_cls = STRATEGY_REGISTRY[strat_name]
        for regime_name in regime_names:
            config = dict(COST_REGIMES[regime_name])
            engine = VectorBacktestEngine(
                strategy=strategy_cls(),
                tickers=tickers,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                markets=list(markets),
                config=config,
                preloaded_data=data,
            )
            try:
                result = engine.run()
            except Exception as exc:  # noqa: BLE001 — one failing run must not kill the arena
                logger.warning("arena_run_failed", strategy=strat_name, cost_regime=regime_name, error=str(exc))
                continue
            runs.append(ArenaRun(strategy=strat_name, cost_regime=regime_name, result=result))
            logger.info(
                "arena_run_completed",
                strategy=strat_name,
                cost_regime=regime_name,
                sharpe=round(result.sharpe_ratio, 3),
                total_return=round(result.total_return, 4),
            )

    benchmark = equal_weight_buyhold(data, initial_cash=initial_cash)
    return ArenaOutcome(runs=runs, data=data, benchmark=benchmark, initial_cash=initial_cash)


__all__ = [
    "COST_REGIMES",
    "STRATEGY_REGISTRY",
    "ArenaOutcome",
    "ArenaRun",
    "equal_weight_buyhold",
    "run_arena",
]
