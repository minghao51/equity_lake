"""Shared construction path for the CLI backtest entry points.

``equity backtest`` (:mod:`equity_lake.cli.commands.analysis`) and
``equity report backtest`` (:mod:`equity_lake.cli.commands.arena`) both validate
a strategy name (and optionally a cost regime) against the registries in
:mod:`equity_lake.backtesting.arena` before constructing a
:class:`~equity_lake.backtesting.engine.VectorBacktestEngine`. That validation +
construction lives here so the two commands cannot drift apart.

Failures raise :class:`ValueError` with the operator-facing message; the CLI
renders it (``typer.secho`` + exit 1). No ``typer`` import in this layer.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, cast

from equity_lake.backtesting.arena import COST_REGIMES, STRATEGY_REGISTRY

if TYPE_CHECKING:
    from equity_lake.backtesting.engine import VectorBacktestEngine


def build_backtest_engine(
    *,
    strategy: str,
    tickers: list[str],
    start_date: date,
    end_date: date,
    initial_cash: float = 100_000.0,
    markets: list[str] | None = None,
    cost_regime: str | None = None,
) -> VectorBacktestEngine:
    """Validate the strategy/cost-regime names and build the backtest engine.

    Args:
        strategy: Key into :data:`~equity_lake.backtesting.arena.STRATEGY_REGISTRY`.
        tickers: Already-parsed ticker list.
        start_date: Backtest start.
        end_date: Backtest end.
        initial_cash: Starting capital.
        markets: Already-parsed market keys; ``None`` keeps the engine default.
        cost_regime: Key into :data:`~equity_lake.backtesting.arena.COST_REGIMES`;
            ``None`` keeps the engine's default fee/tax pair.

    Raises:
        ValueError: Unknown strategy or cost regime (message is CLI-ready).
    """
    if strategy not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {strategy}. Available: {', '.join(STRATEGY_REGISTRY)}")
    if cost_regime is not None and cost_regime not in COST_REGIMES:
        raise ValueError(f"Unknown cost regime: {cost_regime}. Available: {', '.join(COST_REGIMES)}")

    # Resolved through the package attribute (not a module-level import) so
    # callers and tests can substitute ``equity_lake.backtesting.VectorBacktestEngine``.
    from equity_lake import backtesting

    engine: Any = backtesting.VectorBacktestEngine(
        strategy=STRATEGY_REGISTRY[strategy](),
        tickers=tickers,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        markets=markets,
        config=dict(COST_REGIMES[cost_regime]) if cost_regime is not None else None,
    )
    return cast("VectorBacktestEngine", engine)


__all__ = ["build_backtest_engine"]
