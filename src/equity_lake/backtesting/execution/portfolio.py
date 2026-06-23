from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import polars as pl
import structlog

from equity_lake.backtesting.execution.broker import Broker, Execution

logger = structlog.get_logger(__name__)


def _float_scalar(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Position:
    ticker: str
    shares: float
    avg_cost: float
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    total_cost: float = 0.0

    def update(self, price: float) -> None:
        self.current_price = price
        self.market_value = self.shares * price
        self.total_cost = abs(self.shares * self.avg_cost)

        if self.shares > 0:
            self.unrealized_pnl = (price - self.avg_cost) * self.shares
        else:
            self.unrealized_pnl = (self.avg_cost - price) * abs(self.shares)


@dataclass
class PortfolioSnapshot:
    date: date
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0
    daily_pnl: float = 0.0
    returns: float = 0.0


class Portfolio:
    def __init__(
        self,
        initial_cash: float = 100_000.0,
        broker: Broker | None = None,
    ):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.snapshots: list[PortfolioSnapshot] = []
        self.executions: list[Execution] = []
        self.broker = broker

        logger.info("Portfolio initialized", initial_cash=initial_cash)

    def update(
        self,
        date: date,
        prices: dict[str, float],
    ) -> PortfolioSnapshot:
        for ticker, position in self.positions.items():
            if ticker in prices:
                position.update(prices[ticker])

        total_value = self.get_total_value(prices)

        daily_pnl = 0.0
        if self.snapshots:
            prev_value = self.snapshots[-1].total_value
            daily_pnl = total_value - prev_value

        returns = (total_value / self.initial_cash) - 1

        snapshot = PortfolioSnapshot(
            date=date,
            cash=self.cash,
            positions=dict(self.positions),
            total_value=total_value,
            daily_pnl=daily_pnl,
            returns=returns,
        )

        self.snapshots.append(snapshot)
        return snapshot

    def add_execution(self, execution: Execution) -> None:
        self.executions.append(execution)

        if execution.side.value == "BUY":
            if execution.ticker in self.positions:
                pos = self.positions[execution.ticker]
                total_shares = pos.shares + execution.quantity
                total_cost = (pos.shares * pos.avg_cost) + (execution.quantity * execution.price)
                pos.avg_cost = total_cost / total_shares if total_shares > 0 else 0
                pos.shares = total_shares
            else:
                self.positions[execution.ticker] = Position(
                    ticker=execution.ticker,
                    shares=execution.quantity,
                    avg_cost=execution.price,
                )
            self.cash -= (execution.quantity * execution.price) + execution.commission
        else:
            if execution.ticker in self.positions:
                pos = self.positions[execution.ticker]
                pos.shares -= abs(execution.quantity)
                if pos.shares == 0:
                    del self.positions[execution.ticker]
            self.cash += (abs(execution.quantity) * execution.price) - execution.commission

        logger.debug(
            "Execution added to portfolio",
            ticker=execution.ticker,
            side=execution.side.value,
            quantity=execution.quantity,
            price=execution.price,
            cash=self.cash,
        )

    def get_total_value(
        self,
        prices: dict[str, float] | None = None,
    ) -> float:
        total_value = self.cash
        for ticker, position in self.positions.items():
            price = prices.get(ticker, position.current_price) if prices else position.current_price
            total_value += position.shares * price
        return total_value

    def get_position(self, ticker: str) -> Position | None:
        return self.positions.get(ticker)

    def get_positions(self) -> dict[str, Position]:
        return dict(self.positions)

    def get_equity_curve(self) -> pl.Series:
        if not self.snapshots:
            return pl.Series("equity", [], dtype=pl.Float64)
        return pl.Series("equity", [s.total_value for s in self.snapshots])

    def get_returns(self) -> pl.Series:
        equity_curve = self.get_equity_curve()
        if equity_curve.is_empty():
            return pl.Series("returns", [], dtype=pl.Float64)
        return equity_curve.pct_change().slice(1)

    def get_summary(self) -> dict[str, float | int]:
        if not self.snapshots:
            return {
                "initial_cash": self.initial_cash,
                "current_cash": self.cash,
                "total_value": self.cash,
                "num_positions": len(self.positions),
                "num_trades": len(self.executions),
            }

        latest = self.snapshots[-1]
        returns = self.get_returns()

        summary: dict[str, float | int] = {
            "initial_cash": self.initial_cash,
            "current_cash": self.cash,
            "total_value": latest.total_value,
            "total_return": latest.returns,
            "num_positions": len(self.positions),
            "num_trades": len(self.executions),
            "num_snapshots": len(self.snapshots),
        }

        if not returns.is_empty():
            summary["volatility"] = float(returns.std() * np.sqrt(252))
            summary["max_drawdown"] = self._calculate_max_drawdown()
            summary["sharpe_ratio"] = self._calculate_sharpe_ratio(returns)

        return summary

    def _calculate_max_drawdown(self) -> float:
        equity_curve = self.get_equity_curve()
        if equity_curve.is_empty():
            return 0.0
        cummax = equity_curve.cum_max()
        drawdown = (equity_curve - cummax) / cummax
        return _float_scalar(drawdown.min())

    def _calculate_sharpe_ratio(self, returns: pl.Series) -> float:
        if returns.is_empty():
            return 0.0
        risk_free_rate = 0.04
        annual_return = _float_scalar(returns.mean()) * 252
        annual_vol = _float_scalar(returns.std()) * float(np.sqrt(252)) if returns.len() > 1 else 0.0
        if annual_vol == 0:
            return 0.0
        return (annual_return - risk_free_rate) / annual_vol

    def reset(self) -> None:
        self.cash = self.initial_cash
        self.positions.clear()
        self.snapshots.clear()
        self.executions.clear()
        logger.info("Portfolio reset")


__all__ = [
    "Position",
    "PortfolioSnapshot",
    "Portfolio",
]
