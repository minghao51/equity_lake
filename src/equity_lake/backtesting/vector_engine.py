"""
Vectorized backtesting engine using VectorBT.

Provides a 10-100x speedup over the loop-based engine by leveraging
vectorbt's portfolio simulation framework.

Usage:
    from equity_lake.backtesting.vector_engine import VectorBacktestEngine
    from equity_lake.backtesting.strategy import SMACrossoverStrategy
    from datetime import date

    engine = VectorBacktestEngine(
        strategy=SMACrossoverStrategy(params={"fast_period": 10, "slow_period": 30}),
        tickers=["AAPL", "MSFT"],
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
        initial_cash=100_000,
    )
    result = engine.run()
    print(result.summary())
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.engine import BacktestEngine, BacktestResult
from equity_lake.backtesting.strategy.base import BaseStrategy
from equity_lake.backtesting.utils import extract_field_from_maybe_multiindex

logger = structlog.get_logger(__name__)

# Check for vectorbt availability at module level
try:
    import vectorbt as vbt

    VECTORBT_AVAILABLE = True
except ImportError:
    VECTORBT_AVAILABLE = False

# Default configuration constants
DEFAULT_FIXED_FEES = 0.0
DEFAULT_SLIPPAGE = 0.001
DEFAULT_FREQ = "1D"


class VectorBacktestEngine:
    """
    Vectorized backtesting engine using VectorBT.

    This engine leverages vectorbt's portfolio simulation framework for
    fast, vectorized backtesting. It uses the same strategy interface
    as the loop-based engine but executes trades using vectorbt's
    Portfolio.from_signals() API.

    Key advantages:
    - 10-100x faster than loop-based execution
    - Built-in transaction cost modeling
    - Automatic portfolio metrics (Sharpe, Sortino, max drawdown, etc.)
    - Easy parameter optimization via vectorbt's Portfolios API

    Attributes:
        strategy: Trading strategy instance
        tickers: List of ticker symbols
        start_date: Backtest start date
        end_date: Backtest end date
        initial_cash: Starting capital
        markets: Markets to query
        config: Additional configuration

    Example:
        >>> strategy = SMACrossoverStrategy(params={"fast_period": 10, "slow_period": 30})
        >>> engine = VectorBacktestEngine(
        ...     strategy=strategy,
        ...     tickers=["AAPL", "MSFT"],
        ...     start_date=date(2020, 1, 1),
        ...     end_date=date(2024, 12, 31),
        ...     initial_cash=100_000,
        ... )
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
        Initialize the vectorized backtesting engine.

        Args:
            strategy: Trading strategy instance
            tickers: List of ticker symbols to backtest
            start_date: Backtest start date
            end_date: Backtest end date
            initial_cash: Starting capital (default: 100,000)
            markets: Markets to query (default: all available)
            config: Additional configuration options:
                - fixed_fees: Fixed transaction cost per trade (default: 0)
                - slippage: Slippage as fraction (default: 0.001)
                - freq: Data frequency for vectorbt (default: '1D')
        """
        self.strategy = strategy
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.markets = markets or ["us", "cn", "hk_sg"]
        self.config = config or {}

        # Transaction cost defaults
        self.fixed_fees = self.config.get("fixed_fees", DEFAULT_FIXED_FEES)
        self.slippage = self.config.get("slippage", DEFAULT_SLIPPAGE)
        self.freq = self.config.get("freq", DEFAULT_FREQ)

        self.data_loader = BacktestDataLoader()

        # Results (populated after run())
        self._portfolio: Any = None
        self._signals: pd.DataFrame | None = None
        self.metrics: dict[str, float] = {}
        self._portfolio_value: pd.Series | None = None

        logger.info(
            "VectorBacktestEngine initialized",
            strategy=strategy.name,
            tickers=len(tickers),
            start_date=str(start_date),
            end_date=str(end_date),
            initial_cash=initial_cash,
        )

    def run(self) -> BacktestResult:
        """
        Run the vectorized backtest.

        Returns:
            BacktestResult object with performance metrics and trade details

        Raises:
            ValueError: If data loading fails or strategy is invalid
            ImportError: If vectorbt is not installed
        """
        if not VECTORBT_AVAILABLE:
            raise ImportError("vectorbt is required for VectorBacktestEngine. Install it with: uv sync --extra backtesting")

        logger.info("Starting vectorized backtest", strategy=self.strategy.name)

        # Load data
        data = self._load_data()
        if data.empty:
            raise ValueError("No data available for backtest")

        # Initialize strategy and generate signals
        self.strategy.initialize(data)
        signals = self.strategy.generate_signals(data)
        self._signals = signals

        # Extract close prices for vectorbt
        close_prices = self._extract_prices(data, "close")

        # Build entry/exit signals for vectorbt
        entries, exits = self._build_signals(signals, data)

        # Run vectorbt portfolio simulation
        per_ticker_cash = self.initial_cash / max(len(close_prices.columns), 1)
        self._portfolio = vbt.Portfolio.from_signals(
            close=close_prices,
            entries=entries,
            exits=exits,
            init_cash=per_ticker_cash,
            fees=self.fixed_fees,
            slippage=self.slippage,
            freq=self.freq,
        )
        self._portfolio_value = self._portfolio.value()
        if isinstance(self._portfolio_value, pd.DataFrame):
            self._portfolio_value = self._portfolio_value.sum(axis=1)

        # Compute metrics and build result
        self._compute_metrics()

        self.strategy.finalize()

        trades = self._extract_trades()

        result = BacktestResult(
            strategy_name=self.strategy.name,
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_cash=self.initial_cash,
            final_cash=float(self._portfolio_value.iloc[-1]),
            equity_curve=self._portfolio_value,
            trades=trades,
            metrics=self.metrics,
        )

        logger.info(
            "Vectorized backtest completed",
            strategy=self.strategy.name,
            total_return=result.total_return,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            num_trades=len(trades),
        )

        return result

    def _load_data(self) -> pd.DataFrame:
        """Load data for backtesting."""
        return self.data_loader.load(
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            markets=self.markets,
            wide_format=True,
        )

    def _extract_prices(self, data: pd.DataFrame, field: str = "close") -> pd.DataFrame:
        """Extract price matrix from wide-format data."""
        prices = extract_field_from_maybe_multiindex(data, field)
        # Ensure we have a DataFrame with tickers as columns
        if isinstance(prices, pd.Series):
            prices = prices.to_frame()
        return prices

    def _build_signals(
        self,
        signals: pd.DataFrame,
        data: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Build entry/exit signal matrices for vectorbt.

        Converts strategy signals into boolean matrices matching the
        price matrix shape expected by vectorbt.

        Args:
            signals: Strategy signal DataFrame
            data: Original price data

        Returns:
            Tuple of (entries, exits) boolean DataFrames
        """
        # Get the list of tickers from the price data
        close_prices = self._extract_prices(data, "close")
        ticker_cols = list(close_prices.columns)

        # Initialize boolean matrices
        entries = pd.DataFrame(False, index=close_prices.index, columns=ticker_cols)
        exits = pd.DataFrame(False, index=close_prices.index, columns=ticker_cols)

        engine = BacktestEngine(
            strategy=self.strategy,
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_cash=self.initial_cash,
            markets=self.markets,
            preloaded_data=data,
        )
        entries, exits = engine._extract_signal_matrices(signals, ticker_cols)

        return entries.fillna(False), exits.fillna(False)

    def _compute_metrics(self) -> None:
        """Compute performance metrics from the vectorbt portfolio."""
        if self._portfolio is None:
            return

        portfolio = self._portfolio

        # VectorBT provides these metrics directly
        if self._portfolio_value is None or self._portfolio_value.empty:
            return

        returns = self._portfolio_value.pct_change().dropna()
        total_return = (self._portfolio_value.iloc[-1] / self._portfolio_value.iloc[0]) - 1
        days = max((self._portfolio_value.index[-1] - self._portfolio_value.index[0]).days, 1)
        years = days / 365.25
        ann_return = ((self._portfolio_value.iloc[-1] / self._portfolio_value.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
        volatility = float(returns.std() * np.sqrt(252)) if not returns.empty else 0.0
        sharpe_ratio = (ann_return / volatility) if volatility > 0 else 0.0
        drawdown = (self._portfolio_value / self._portfolio_value.cummax()) - 1
        max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

        trade_count = portfolio.trades.count()
        num_trades = int(trade_count.sum()) if isinstance(trade_count, pd.Series) else int(trade_count)

        closed_trades = self._extract_trades()
        win_rate = len([trade for trade in closed_trades if trade.get("pnl", 0.0) > 0]) / len(closed_trades) if closed_trades else 0.0

        self.metrics = {
            "total_return": float(total_return) if not np.isnan(total_return) else 0.0,
            "cagr": float(ann_return) if not np.isnan(ann_return) else 0.0,
            "volatility": volatility,
            "sharpe_ratio": float(sharpe_ratio) if not np.isnan(sharpe_ratio) else 0.0,
            "max_drawdown": float(max_drawdown) if not np.isnan(max_drawdown) else 0.0,
            "win_rate": float(win_rate),
            "num_trades": num_trades,
        }

    def _extract_trades(self) -> list[dict[str, Any]]:
        """Extract trade records from the vectorbt portfolio."""
        if self._portfolio is None:
            return []

        trades = []
        try:
            records = self._portfolio.trades.records

            if records is not None and not records.empty:
                for _, record in records.iterrows():
                    trades.append(
                        {
                            "date": record.get("exit_idx", record.get("entry_idx", "")),
                            "ticker": (
                                self._portfolio.wrapper.columns[int(record.get("column"))]
                                if isinstance(record.get("column"), (int, np.integer))
                                else record.get("column", "")
                            ),
                            "action": "SELL",
                            "shares": record.get("size", 0),
                            "price": record.get("exit_price", 0),
                            "value": record.get("exit_value", 0),
                            "pnl": record.get("pnl", 0),
                        }
                    )
        except Exception as e:
            logger.warning("Could not extract trades records: %s", e)

        return trades

    def optimize(
        self,
        param_grid: dict[str, list[Any]],
        target: str = "sharpe_ratio",
    ) -> dict[str, Any]:
        """
        Run parameter optimization using vectorbt's Portfolios API.

        Args:
            param_grid: Dictionary of parameter names to value lists
            target: Metric to optimize (default: 'sharpe_ratio')

        Returns:
            Dictionary with best parameters and metrics

        Example:
            >>> result = engine.optimize({
            ...     "fast_period": [5, 10, 15],
            ...     "slow_period": [20, 30, 50],
            ... })
        """
        if not VECTORBT_AVAILABLE:
            raise ImportError("vectorbt is required for optimization. Install it with: uv sync --extra backtesting")

        logger.info(
            "Starting parameter optimization",
            strategy=self.strategy.name,
            param_combinations=np.prod([len(v) for v in param_grid.values()]),
        )

        data = self._load_data()
        if data.empty:
            raise ValueError("No data available for optimization")

        close_prices = self._extract_prices(data, "close")

        # Build parameter grids using vectorbt
        param_names = list(param_grid.keys())
        param_values = [param_grid[name] for name in param_names]

        # Create parameter mesh
        param_mesh = {}
        for name, values in zip(param_names, param_values, strict=True):
            param_mesh[name] = vbt.Parameters.from_product(values, names=[name], short_name=name)

        # For simplicity, we iterate over param combinations manually
        # A full vectorbt optimization would use vbt.Portfolio.from_signal_func
        best_sharpe = -np.inf
        best_params: dict[str, Any] = {}

        import itertools

        for combo in itertools.product(*param_values):
            params = dict(zip(param_names, combo, strict=True))

            # Create strategy with these params
            strategy_class = type(self.strategy)
            strategy = strategy_class(params=params)
            strategy.initialize(data)
            signals = strategy.generate_signals(data)

            entries, exits = self._build_signals(signals, data)

            try:
                portfolio = vbt.Portfolio.from_signals(
                    close=close_prices,
                    entries=entries,
                    exits=exits,
                    init_cash=self.initial_cash,
                    fees=self.fixed_fees,
                    slippage=self.slippage,
                    freq=self.freq,
                )

                sharpe = portfolio.sharpe_ratio()
                if not np.isnan(sharpe) and sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_params = params.copy()
            except Exception as e:
                logger.warning("Optimization failed for params %s: %s", params, e)

            strategy.finalize()

        result = {
            "best_params": best_params,
            "best_sharpe_ratio": best_sharpe if best_sharpe != -np.inf else None,
            "total_combinations": np.prod([len(v) for v in param_values]),
        }

        logger.info(
            "Optimization complete",
            best_params=best_params,
            best_sharpe=best_sharpe,
        )

        return result


__all__ = ["VectorBacktestEngine"]
