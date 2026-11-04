from datetime import date
from typing import Any

import polars as pl


class BacktestResult:
    """Container for backtest results.

    ``equity_curve`` stays a bare ``pl.Series`` (ADR-0025 Decision 7); dated
    evidence is the additive companion ``equity_dates``, populated only from the
    backend's own timestamps. ``None`` means *undated*, never "a contiguous
    calendar of ``len(equity_curve)`` sessions": a calendar is never rebuilt
    from the series length or reindexed (ADR-0025 Decision 6).
    """

    def __init__(
        self,
        strategy_name: str,
        tickers: list[str],
        start_date: date,
        end_date: date,
        initial_cash: float,
        final_cash: float,
        equity_curve: pl.Series,
        trades: list[dict[str, Any]],
        metrics: dict[str, float],
        warnings: list[str] | None = None,
        equity_dates: list[date] | None = None,
    ):
        if equity_dates is not None and len(equity_dates) != equity_curve.len():
            raise ValueError(
                "equity_dates must align one-to-one with equity_curve "
                f"(got {len(equity_dates)} dates for {equity_curve.len()} points); "
                "the curve is never reindexed to fit a calendar"
            )
        self.strategy_name = strategy_name
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.final_cash = final_cash
        self.equity_curve = equity_curve
        self.trades = trades
        self.metrics = metrics
        #: Human-readable runtime warnings (e.g. stats unavailable) — never stored in metrics.
        self.warnings: list[str] = warnings or []
        #: Backend session dates aligned with ``equity_curve``, or ``None`` when the
        #: backend supplied no timestamps (ADR-0025 Decision 6).
        self.equity_dates: list[date] | None = list(equity_dates) if equity_dates is not None else None

    @property
    def total_return(self) -> float:
        return self.metrics.get("total_return", 0)

    @property
    def sharpe_ratio(self) -> float:
        return self.metrics.get("sharpe_ratio", 0)

    @property
    def max_drawdown(self) -> float:
        return self.metrics.get("max_drawdown", 0)

    def summary(self) -> str:
        return f"""
Backtest Results: {self.strategy_name}
{"=" * 60}
Period: {self.start_date} to {self.end_date}
Initial Capital: ${self.initial_cash:,.2f}
Final Capital: ${self.final_cash:,.2f}

Performance:
  Total Return: {self.total_return:.2%}
  CAGR: {self.metrics.get("cagr", 0):.2%}
  Volatility: {self.metrics.get("volatility", 0):.2%}
  Sharpe Ratio: {self.sharpe_ratio:.2f}
  Max Drawdown: {self.max_drawdown:.2%}

Trading:
  Total Trades: {self.metrics.get("num_trades", 0)}
  Win Rate: {self.metrics.get("win_rate", 0):.1%}
{"=" * 60}
        """.strip()

    @property
    def has_dated_evidence(self) -> bool:
        """Whether the curve carries backend dates (never inferred from length)."""
        return self.equity_dates is not None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy_name": self.strategy_name,
            "tickers": self.tickers,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "initial_cash": self.initial_cash,
            "final_cash": self.final_cash,
            "metrics": self.metrics,
            "num_trades": len(self.trades),
            "warnings": list(self.warnings),
        }
        # Present only when the backend actually supplied timestamps: an absent
        # key means undated, so a consumer can never read a synthetic calendar.
        if self.equity_dates is not None:
            payload["equity_dates"] = [day.isoformat() for day in self.equity_dates]
        return payload


__all__ = ["BacktestResult"]
