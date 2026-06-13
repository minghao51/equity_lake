from datetime import date
from typing import Any

import polars as pl


class BacktestResult:
    """Container for backtest results."""

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
    ):
        self.strategy_name = strategy_name
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.final_cash = final_cash
        self.equity_curve = equity_curve
        self.trades = trades
        self.metrics = metrics

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "tickers": self.tickers,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "initial_cash": self.initial_cash,
            "final_cash": self.final_cash,
            "metrics": self.metrics,
            "num_trades": len(self.trades),
        }


__all__ = ["BacktestResult"]
