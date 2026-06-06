"""
Portfolio management for backtesting.

This module provides portfolio state tracking, value calculation,
and performance measurement.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import structlog

from equity_lake.domain.backtesting.execution.broker import Broker, Execution

logger = structlog.get_logger(__name__)


@dataclass
class Position:
    """
    Position details.

    Attributes:
        ticker: Stock symbol
        shares: Number of shares (can be negative for short positions)
        avg_cost: Average cost per share
        current_price: Current market price
        market_value: Current market value
        unrealized_pnl: Unrealized profit/loss
        total_cost: Total cost basis
    """

    ticker: str
    shares: float
    avg_cost: float
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    total_cost: float = 0.0

    def update(self, price: float) -> None:
        """Update position with current price."""
        self.current_price = price
        self.market_value = self.shares * price
        self.total_cost = abs(self.shares * self.avg_cost)

        if self.shares > 0:
            self.unrealized_pnl = (price - self.avg_cost) * self.shares
        else:  # Short position
            self.unrealized_pnl = (self.avg_cost - price) * abs(self.shares)


@dataclass
class PortfolioSnapshot:
    """
    Portfolio state at a point in time.

    Attributes:
        date: Snapshot date
        cash: Cash balance
        positions: Dictionary of positions
        total_value: Total portfolio value
        daily_pnl: Daily profit/loss
        returns: Cumulative returns
    """

    date: date
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0
    daily_pnl: float = 0.0
    returns: float = 0.0


class Portfolio:
    """
    Portfolio state manager.

    Tracks portfolio value, positions, and performance over time.

    Attributes:
        initial_cash: Initial capital
        cash: Current cash balance
        positions: Current positions (ticker -> Position)
        snapshots: Historical portfolio snapshots
        executions: Trade execution history

    Example:
        >>> portfolio = Portfolio(initial_cash=100_000)
        >>>
        >>> # Update with prices
        >>> portfolio.update(
        ...     date=date(2024, 1, 1),
        ...     prices={"AAPL": 150.0, "MSFT": 300.0}
        ... )
        >>>
        >>> # Get total value
        >>> total_value = portfolio.get_total_value()
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        broker: Broker | None = None,
    ):
        """
        Initialize portfolio.

        Args:
            initial_cash: Starting capital
            broker: Optional broker instance for trade tracking
        """
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.snapshots: list[PortfolioSnapshot] = []
        self.executions: list[Execution] = []
        self.broker = broker

        logger.info(
            "Portfolio initialized",
            initial_cash=initial_cash,
        )

    def update(
        self,
        date: date,
        prices: dict[str, float],
    ) -> PortfolioSnapshot:
        """
        Update portfolio with current prices.

        Args:
            date: Current date
            prices: Dictionary of current prices (ticker -> price)

        Returns:
            PortfolioSnapshot representing current state
        """
        # Update positions
        for ticker, position in self.positions.items():
            if ticker in prices:
                position.update(prices[ticker])

        # Calculate total value
        total_value = self.get_total_value(prices)

        # Calculate daily PnL
        daily_pnl = 0.0
        if self.snapshots:
            prev_value = self.snapshots[-1].total_value
            daily_pnl = total_value - prev_value

        # Calculate returns
        returns = (total_value / self.initial_cash) - 1

        # Create snapshot
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
        """
        Add a trade execution to the portfolio.

        Args:
            execution: Execution object
        """
        self.executions.append(execution)

        # Update positions
        if execution.side.value == "BUY":
            if execution.ticker in self.positions:
                # Update existing position
                pos = self.positions[execution.ticker]
                total_shares = pos.shares + execution.quantity
                total_cost = (pos.shares * pos.avg_cost) + (execution.quantity * execution.price)
                pos.avg_cost = total_cost / total_shares if total_shares > 0 else 0
                pos.shares = total_shares
            else:
                # New position
                self.positions[execution.ticker] = Position(
                    ticker=execution.ticker,
                    shares=execution.quantity,
                    avg_cost=execution.price,
                )

            # Deduct cash (includes commission)
            self.cash -= (execution.quantity * execution.price) + execution.commission

        else:  # SELL
            if execution.ticker in self.positions:
                pos = self.positions[execution.ticker]
                pos.shares -= abs(execution.quantity)

                # Remove position if zero
                if pos.shares == 0:
                    del self.positions[execution.ticker]

            # Add cash (proceeds minus commission)
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
        """
        Calculate total portfolio value.

        Args:
            prices: Optional current prices (uses position.last_price if None)

        Returns:
            Total portfolio value (cash + positions)
        """
        total_value = self.cash

        for ticker, position in self.positions.items():
            price = prices.get(ticker, position.current_price) if prices else position.current_price
            total_value += position.shares * price

        return total_value

    def get_position(self, ticker: str) -> Position | None:
        """
        Get position for a ticker.

        Args:
            ticker: Stock symbol

        Returns:
            Position object or None if no position
        """
        return self.positions.get(ticker)

    def get_positions(self) -> dict[str, Position]:
        """Get all positions."""
        return dict(self.positions)

    def get_equity_curve(self) -> pd.Series:
        """
        Get equity curve (portfolio value over time).

        Returns:
            Series with dates as index and portfolio values
        """
        if not self.snapshots:
            return pd.Series(dtype=float)

        dates = [s.date for s in self.snapshots]
        values = [s.total_value for s in self.snapshots]

        return pd.Series(values, index=dates)

    def get_returns(self) -> pd.Series:
        """
        Get daily returns.

        Returns:
            Series with dates as index and daily returns
        """
        equity_curve = self.get_equity_curve()

        if equity_curve.empty:
            return pd.Series(dtype=float)

        return equity_curve.pct_change().dropna()

    def get_summary(self) -> dict[str, float | int]:
        """
        Get portfolio summary.

        Returns:
            Dictionary with portfolio statistics
        """
        if not self.snapshots:
            return {
                "initial_cash": self.initial_cash,
                "current_cash": self.cash,
                "total_value": self.cash,
                "num_positions": len(self.positions),
                "num_trades": len(self.executions),
            }

        latest = self.snapshots[-1]

        # Calculate additional metrics
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

        if not returns.empty:
            summary["volatility"] = returns.std() * np.sqrt(252)  # Annualized
            summary["max_drawdown"] = self._calculate_max_drawdown()
            summary["sharpe_ratio"] = self._calculate_sharpe_ratio(returns)

        return summary

    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown."""
        equity_curve = self.get_equity_curve()

        if equity_curve.empty:
            return 0.0

        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax

        return float(drawdown.min())

    def _calculate_sharpe_ratio(self, returns: pd.Series) -> float:
        """Calculate Sharpe ratio (assuming 4% risk-free rate)."""
        if returns.empty:
            return 0.0

        risk_free_rate = 0.04
        annual_return = returns.mean() * 252
        annual_vol = returns.std() * np.sqrt(252)

        if annual_vol == 0:
            return 0.0

        return float((annual_return - risk_free_rate) / annual_vol)

    def reset(self) -> None:
        """Reset portfolio to initial state."""
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
