"""Shared equity-curve performance metrics.

Single source of truth for the Sharpe / total-return / max-drawdown conventions
used by BOTH the vector backtest engine side and the report/FindingCard side, so
strategy metrics and benchmark metrics are always comparable.

Conventions (mirroring polars-backtest's ``Report.get_stats`` defaults):

- Risk-free rate ``rf`` is annual; the per-period (daily) deduction is
  ``(1 + rf) ** (1 / periods_per_year) - 1`` and is subtracted from every
  daily return before mean/std.
- Daily returns are first differences of the equity curve (``n - 1`` returns
  for ``n`` points), sample standard deviation (ddof=1), annualized by
  ``sqrt(periods_per_year)`` (252 trading days by default).
"""

from __future__ import annotations

import numpy as np
import polars as pl

#: Default annual risk-free rate — matches polars-backtest's ``get_stats`` default.
DEFAULT_RISK_FREE_RATE = 0.02
#: Default annualization factor for daily returns.
TRADING_DAYS_PER_YEAR = 252


def sharpe_ratio(
    returns: np.ndarray,
    *,
    rf: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Annualized Sharpe ratio of a per-period return series.

    Excess returns are ``returns - ((1 + rf) ** (1 / periods_per_year) - 1)``;
    uses sample std (ddof=1). Returns 0.0 when excess-return std is zero or the
    series has fewer than 2 observations.
    """
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 2:
        return 0.0
    rf_periodic = (1.0 + rf) ** (1.0 / periods_per_year) - 1.0
    excess = returns - rf_periodic
    std = float(np.std(excess, ddof=1))
    # Tolerance guards against catastrophic cancellation on ~constant series
    # (a mathematically constant excess return has std ~1e-18, not exactly 0).
    if std <= 1e-12:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(periods_per_year))


def equity_curve_metrics(
    equity: pl.Series | pl.DataFrame,
    initial_cash: float,
    *,
    rf: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> dict[str, float]:
    """Total return, annualized Sharpe, and max drawdown from an equity curve.

    Args:
        equity: Series of equity values, or a DataFrame with an ``equity`` column.
        initial_cash: Starting capital (the 1.0 point of the curve's scale).
        rf: Annual risk-free rate (default 2% — the engine convention).
        periods_per_year: Annualization factor (default 252).

    Returns:
        ``{"total_return", "sharpe_ratio", "max_drawdown"}`` — all 0.0 for a
        curve with fewer than 2 points.
    """
    series = equity["equity"] if isinstance(equity, pl.DataFrame) else equity
    if series.len() < 2:
        return {"total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0}
    values = np.asarray(series.to_numpy(), dtype=np.float64)
    total_return = float(values[-1] / initial_cash - 1.0) if initial_cash else 0.0
    rets = np.diff(values) / values[:-1]
    sharpe = sharpe_ratio(rets, rf=rf, periods_per_year=periods_per_year)
    running_max = np.maximum.accumulate(values)
    max_dd = float((values / running_max - 1.0).min())
    return {"total_return": total_return, "sharpe_ratio": sharpe, "max_drawdown": max_dd}


__all__ = [
    "DEFAULT_RISK_FREE_RATE",
    "TRADING_DAYS_PER_YEAR",
    "equity_curve_metrics",
    "sharpe_ratio",
]
