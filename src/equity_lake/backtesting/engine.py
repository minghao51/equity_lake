"""
Core backtesting engine.

This module provides the main backtesting engine that orchestrates
strategy execution, portfolio management, and performance analysis.
"""

from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd
import structlog

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.strategy.base import BaseStrategy
from equity_lake.backtesting.utils import extract_field_from_maybe_multiindex

logger = structlog.get_logger(__name__)


class BacktestEngine:
    """
    Main backtesting engine.

    This engine orchestrates the entire backtesting workflow:
    1. Load data using BacktestDataLoader
    2. Initialize strategy with historical data
    3. Generate entry/exit signals
    4. Execute trades and track portfolio
    5. Compute performance metrics

    Attributes:
        strategy: Trading strategy instance
        data_loader: Data loader instance
        initial_cash: Starting capital
        config: Backtest configuration

    Example:
        >>> from equity_lake.backtesting import BacktestEngine
        >>> from equity_lake.backtesting.strategy import SMACrossoverStrategy
        >>>
        >>> strategy = SMACrossoverStrategy(params={
        ...     "fast_period": 10,
        ...     "slow_period": 30
        ... })
        >>>
        >>> engine = BacktestEngine(
        ...     strategy=strategy,
        ...     tickers=["AAPL", "MSFT"],
        ...     start_date=date(2020, 1, 1),
        ...     end_date=date(2024, 12, 31),
        ...     initial_cash=100_000
        ... )
        >>>
        >>> result = engine.run()
        >>> print(f"Total Return: {result.total_return:.2%}")
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        tickers: list[str],
        start_date: date,
        end_date: date,
        initial_cash: float = 100_000.0,
        markets: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize the backtesting engine.

        Args:
            strategy: Trading strategy instance
            tickers: List of ticker symbols to backtest
            start_date: Backtest start date
            end_date: Backtest end date
            initial_cash: Starting capital (default: 100,000)
            markets: Markets to query (default: all available)
            config: Additional backtest configuration
        """
        self.strategy = strategy
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.markets = markets or ["us", "cn", "hk_sg"]
        self.config = config or {}

        # Initialize data loader
        self.data_loader = BacktestDataLoader()

        # Portfolio state (will be set during run())
        self.cash: float | None = None
        self.positions: dict[str, float] = {}
        self.equity_curve: pd.Series | None = None
        self.trades: list[dict[str, Any]] = []

        # Performance metrics (will be computed after run())
        self.metrics: dict[str, float] = {}

        logger.info(
            "BacktestEngine initialized",
            strategy=strategy.name,
            tickers=len(tickers),
            start_date=str(start_date),
            end_date=str(end_date),
            initial_cash=initial_cash,
        )

    def run(self) -> "BacktestResult":
        """
        Run the backtest.

        Returns:
            BacktestResult object with performance metrics and trade details

        Raises:
            ValueError: If data loading fails or strategy is invalid

        Example:
            >>> result = engine.run()
            >>> print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
        """
        logger.info("Starting backtest", strategy=self.strategy.name)

        # Load data
        data = self._load_data()

        if data.empty:
            raise ValueError("No data available for backtest")

        # Initialize strategy
        logger.debug("Initializing strategy")
        self.strategy.initialize(data)

        # Generate signals
        logger.debug("Generating signals")
        signals = self.strategy.generate_signals(data)

        # Execute backtest
        logger.debug("Executing trades")
        self._execute_backtest(data, signals)

        # Compute metrics
        logger.debug("Computing metrics")
        self._compute_metrics()

        # Finalize strategy
        self.strategy.finalize()

        # Create result object
        result = BacktestResult(
            strategy_name=self.strategy.name,
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_cash=self.initial_cash,
            final_cash=cast(float, self.cash),
            equity_curve=self.equity_curve,
            trades=self.trades,
            metrics=self.metrics,
        )

        logger.info(
            "Backtest completed",
            strategy=self.strategy.name,
            total_return=result.total_return,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            num_trades=len(result.trades),
        )

        return result

    def _load_data(self) -> pd.DataFrame:
        """Load data for backtesting."""
        logger.debug(
            "Loading data",
            tickers=self.tickers,
            start_date=str(self.start_date),
            end_date=str(self.end_date),
            markets=self.markets,
        )

        data = self.data_loader.load(
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            markets=self.markets,
            wide_format=True,
        )

        if data.empty:
            logger.warning("No data loaded", tickers=self.tickers)

        return data

    def _execute_backtest(self, data: pd.DataFrame, signals: pd.DataFrame) -> None:
        """
        Execute backtest by simulating trades.

        This is a simplified execution model. For production use,
        consider using VectorBT's portfolio simulation.
        """
        # Initialize portfolio
        self.cash = self.initial_cash
        self.positions = {ticker: 0.0 for ticker in self.tickers}
        equity_values = []

        # Extract close prices
        close_prices = extract_field_from_maybe_multiindex(data, "close")

        # Iterate through dates
        for date_idx in data.index:
            # Update portfolio value
            portfolio_value = self.cash
            for ticker in self.tickers:
                if ticker in close_prices.columns:
                    price = close_prices.loc[date_idx, ticker]
                    if pd.notna(price):
                        portfolio_value += self.positions[ticker] * price

            equity_values.append(portfolio_value)

            # Process signals
            if date_idx in signals.index:
                row = signals.loc[date_idx]

                # Process entry signals
                if row.get("entry", False):
                    self._execute_entry(date_idx, close_prices)

                # Process exit signals
                if row.get("exit", False):
                    self._execute_exit(date_idx, close_prices)

        self.equity_curve = pd.Series(equity_values, index=data.index)

    def _execute_entry(self, date_idx: pd.Timestamp, prices: pd.DataFrame) -> None:
        """Execute entry signals."""
        # Equal-weight position sizing
        cash = self.cash
        if cash is None:
            return
        cash_per_stock = cash / len([t for t in self.tickers if t in prices.columns])

        for ticker in self.tickers:
            if ticker in prices.columns:
                price = prices.loc[date_idx, ticker]

                if pd.notna(price) and cash >= cash_per_stock:
                    # Buy shares
                    shares = int(cash_per_stock / price)
                    if shares > 0:
                        cost = shares * price
                        self.cash -= cost
                        self.positions[ticker] = self.positions.get(ticker, 0) + shares

                        self.trades.append(
                            {
                                "date": date_idx,
                                "ticker": ticker,
                                "action": "BUY",
                                "shares": shares,
                                "price": price,
                                "value": cost,
                            }
                        )

    def _execute_exit(self, date_idx: pd.Timestamp, prices: pd.DataFrame) -> None:
        """Execute exit signals."""
        for ticker in self.tickers:
            if ticker in prices.columns:
                price = prices.loc[date_idx, ticker]
                position = self.positions.get(ticker, 0)

                if position > 0 and pd.notna(price):
                    # Sell all shares
                    proceeds = position * price
                    self.cash += proceeds
                    self.positions[ticker] = 0

                    self.trades.append(
                        {
                            "date": date_idx,
                            "ticker": ticker,
                            "action": "SELL",
                            "shares": position,
                            "price": price,
                            "value": proceeds,
                        }
                    )

    def _compute_metrics(self) -> None:
        """Compute performance metrics."""
        if self.equity_curve is None or self.equity_curve.empty:
            return

        returns = self.equity_curve.pct_change().dropna()

        # Total return
        total_return = (self.equity_curve.iloc[-1] / self.equity_curve.iloc[0]) - 1

        # CAGR
        days = (self.equity_curve.index[-1] - self.equity_curve.index[0]).days
        years = days / 365.25
        cagr = (self.equity_curve.iloc[-1] / self.equity_curve.iloc[0]) ** (1 / years) - 1

        # Volatility (annualized)
        volatility = returns.std() * np.sqrt(252)

        # Sharpe ratio (assuming 4% risk-free rate)
        risk_free_rate = 0.04
        sharpe_ratio = (cagr - risk_free_rate) / volatility if volatility > 0 else 0

        # Max drawdown
        cummax = self.equity_curve.cummax()
        drawdown = (self.equity_curve - cummax) / cummax
        max_drawdown = drawdown.min()

        # Win rate
        sell_trades = [t for t in self.trades if t["action"] == "SELL"] if self.trades else []
        win_rate = (len(sell_trades) / len(self.trades) * 100) if sell_trades else 0

        self.metrics = {
            "total_return": total_return,
            "cagr": cagr,
            "volatility": volatility,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "num_trades": len(self.trades),
        }


class BacktestResult:
    """
    Container for backtest results.

    Attributes:
        strategy_name: Name of the strategy
        tickers: Tickers traded
        start_date: Backtest start date
        end_date: Backtest end date
        initial_cash: Starting capital
        final_cash: Ending capital
        equity_curve: Portfolio value over time
        trades: List of trades executed
        metrics: Performance metrics dictionary
    """

    def __init__(
        self,
        strategy_name: str,
        tickers: list[str],
        start_date: date,
        end_date: date,
        initial_cash: float,
        final_cash: float,
        equity_curve: pd.Series,
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
        """Total return as a decimal."""
        return self.metrics.get("total_return", 0)

    @property
    def sharpe_ratio(self) -> float:
        """Sharpe ratio."""
        return self.metrics.get("sharpe_ratio", 0)

    @property
    def max_drawdown(self) -> float:
        """Maximum drawdown as a decimal (negative value)."""
        return self.metrics.get("max_drawdown", 0)

    def summary(self) -> str:
        """Generate a summary of backtest results."""
        summary = f"""
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
        """
        return summary.strip()

    def to_dict(self) -> dict[str, Any]:
        """Convert results to dictionary."""
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


__all__ = [
    "BacktestEngine",
    "BacktestResult",
]
