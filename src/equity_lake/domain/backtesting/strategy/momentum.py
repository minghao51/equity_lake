"""
Momentum-based trading strategies.

This module implements various momentum strategies including cross-sectional
momentum and time-series momentum.
"""

import pandas as pd
import structlog

from equity_lake.domain.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class CrossSectionalMomentumStrategy(BaseStrategy):
    """
    Cross-sectional momentum strategy.

    Ranks stocks by their past returns and goes long the top performers,
    short the bottom performers (if shorting is enabled).

    Parameters:
        lookback_days: Lookback period for return calculation (default: 252 = 1 year)
        skip_days: Days to skip between lookback and holding (default: 21 = 1 month)
        top_pct: Percentage of top stocks to long (default: 0.3 = 30%)
        bottom_pct: Percentage of bottom stocks to short (default: 0.3 = 30%)
        rebalance_days: Days between rebalancing (default: 21 = monthly)
        long_only: Only take long positions (default: True)
        volatility_target: Target volatility for position sizing (default: 0.15 = 15%)
        min_stocks: Minimum stocks required for strategy (default: 10)

    Example:
        >>> strategy = CrossSectionalMomentumStrategy(params={
        ...     "lookback_days": 252,
        ...     "top_pct": 0.3,
        ...     "rebalance_days": 21
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "lookback_days": 252,  # 1 year
            "skip_days": 21,  # 1 month
            "top_pct": 0.3,  # Top 30%
            "bottom_pct": 0.3,  # Bottom 30%
            "rebalance_days": 21,  # Monthly rebalancing
            "long_only": True,  # Long-only by default
            "volatility_target": 0.15,  # 15% vol target
            "min_stocks": 10,  # Minimum 10 stocks
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """
        Initialize momentum strategy.

        Pre-computes historical returns for ranking.
        """
        # Extract close prices
        close_df = data.xs("close", level="field", axis=1) if isinstance(data.columns, pd.MultiIndex) else data

        # Store close prices for signal generation
        self.indicators["close"] = close_df

        logger.info(
            "CrossSectionalMomentumStrategy initialized",
            num_tickers=len(close_df.columns),
            start_date=data.index.min(),
            end_date=data.index.max(),
        )

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate momentum signals.

        Returns:
            DataFrame with 'entry' and 'exit' columns for each ticker
        """
        close_df = self.indicators["close"]

        lookback = self.get_param("lookback_days")
        skip_days = self.get_param("skip_days")
        top_pct = self.get_param("top_pct")
        bottom_pct = self.get_param("bottom_pct")
        rebalance_days = self.get_param("rebalance_days")
        long_only = self.get_param("long_only")
        min_stocks = self.get_param("min_stocks")

        # Compute returns
        returns = close_df.pct_change(lookback).shift(skip_days)

        # Initialize signals DataFrame
        signals = pd.DataFrame(index=close_df.index, columns=close_df.columns)

        # Rebalance on schedule
        rebalance_dates = close_df.index[::rebalance_days]

        for rebalance_date in rebalance_dates:
            # Get available returns up to this date
            returns_slice = returns.loc[:rebalance_date]

            if returns_slice.empty:
                continue

            # Use latest available returns
            latest_returns = returns_slice.iloc[-1]

            # Remove NaN values
            latest_returns = latest_returns.dropna()

            # Check minimum stock count
            if len(latest_returns) < min_stocks:
                logger.warning(
                    "Insufficient stocks for momentum ranking",
                    date=str(rebalance_date),
                    num_stocks=len(latest_returns),
                    min_required=min_stocks,
                )
                continue

            # Rank stocks by returns
            ranked = latest_returns.rank(ascending=False)

            # Select top and bottom performers
            num_stocks = len(latest_returns)
            top_count = int(num_stocks * top_pct)
            bottom_count = int(num_stocks * bottom_pct)

            top_stocks = ranked.nsmallest(top_count).index.tolist()
            bottom_stocks = ranked.nlargest(bottom_count).index.tolist()

            # Generate signals (enter long positions)
            for ticker in close_df.columns:
                if ticker in top_stocks:
                    signals.loc[rebalance_date, ticker] = True
                elif ticker in bottom_stocks and not long_only:
                    signals.loc[rebalance_date, ticker] = False  # Short signal
                else:
                    signals.loc[rebalance_date, ticker] = None

        # Forward fill signals (hold positions between rebalances)
        signals = signals.ffill()

        entry_signals = signals.notna() & signals
        exit_signals = signals.notna() & ~signals
        return self.build_signal_frame(entry_signals, exit_signals)


class TimeSeriesMomentumStrategy(BaseStrategy):
    """
    Time-series momentum strategy (TSMOM).

    Goes long each asset individually if its past return > 0,
    goes short if past return < 0.

    Parameters:
        lookback_days: Lookback period for return calculation (default: 126 = 6 months)
        volatility_target: Target volatility for scaling (default: 0.15 = 15%)
        volatility_window: Window for volatility calculation (default: 20 days)

    Example:
        >>> strategy = TimeSeriesMomentumStrategy(params={
        ...     "lookback_days": 126,
        ...     "volatility_target": 0.15
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "lookback_days": 126,  # 6 months
            "volatility_target": 0.15,  # 15% vol target
            "volatility_window": 20,  # 20-day vol
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize time-series momentum strategy."""
        # Extract close prices
        close_df = data.xs("close", level="field", axis=1) if isinstance(data.columns, pd.MultiIndex) else data

        self.indicators["close"] = close_df

        logger.info(
            "TimeSeriesMomentumStrategy initialized",
            num_tickers=len(close_df.columns),
        )

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate time-series momentum signals.

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        close_df = self.indicators["close"]
        lookback = self.get_param("lookback_days")

        # Compute past returns
        past_returns = close_df.pct_change(lookback)

        # Generate entry signals (positive momentum)
        entry_signals = (past_returns > 0).astype(int).diff() == 1

        # Generate exit signals (momentum turns negative)
        exit_signals = (past_returns < 0).astype(int).diff() == 1

        return self.build_signal_frame(entry_signals, exit_signals)


__all__ = [
    "CrossSectionalMomentumStrategy",
    "TimeSeriesMomentumStrategy",
]
