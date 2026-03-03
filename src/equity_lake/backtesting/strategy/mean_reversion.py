"""
Mean reversion trading strategies.

This module implements mean reversion strategies including Bollinger Bands
and RSI-based strategies.
"""


import pandas as pd
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class BBMeanReversionStrategy(BaseStrategy):
    """
    Bollinger Bands mean reversion strategy.

    Buys when price touches lower band, sells when price touches upper band.
    Exits when price returns to middle (SMA).

    Parameters:
        period: Period for SMA and standard deviation (default: 20)
        num_std: Number of standard deviations for bands (default: 2.0)
        position_size: Position size as fraction of capital (default: 0.95)
        use_trend_filter: Only trade when price above 200 MA (default: True)
        stop_loss_pct: Stop loss percentage (default: 0.05 = 5%)

    Example:
        >>> strategy = BBMeanReversionStrategy(params={
        ...     "period": 20,
        ...     "num_std": 2.0,
        ...     "use_trend_filter": True
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "period": 20,
            "num_std": 2.0,
            "position_size": 0.95,
            "use_trend_filter": True,
            "stop_loss_pct": 0.05,  # 5% stop loss
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize Bollinger Bands strategy."""
        # Extract close prices
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs("close", level="field", axis=1)
        else:
            close_df = data

        period = self.get_param("period")
        num_std = self.get_param("num_std")
        use_trend_filter = self.get_param("use_trend_filter")

        # Compute Bollinger Bands for each ticker
        sma = close_df.rolling(window=period).mean()
        std = close_df.rolling(window=period).std()

        self.indicators["upper_band"] = sma + (num_std * std)
        self.indicators["middle_band"] = sma
        self.indicators["lower_band"] = sma - (num_std * std)
        self.indicators["close"] = close_df

        # Optional trend filter (200 MA)
        if use_trend_filter:
            self.indicators["trend_filter"] = close_df.rolling(window=200).mean()

        logger.info(
            "BBMeanReversionStrategy initialized",
            period=period,
            num_std=num_std,
            use_trend_filter=use_trend_filter,
        )

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate Bollinger Bands mean reversion signals.

        Entry: Price crosses below lower band
        Exit: Price crosses above upper band OR returns to middle band

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        close_df = self.indicators["close"]
        upper_band = self.indicators["upper_band"]
        lower_band = self.indicators["lower_band"]
        middle_band = self.indicators["middle_band"]
        use_trend_filter = self.get_param("use_trend_filter")

        # Detect band touches
        # Entry: close crosses below lower band
        touches_lower = (close_df < lower_band).astype(int).diff() == 1

        # Exit: close crosses above upper band
        touches_upper = (close_df > upper_band).astype(int).diff() == 1

        # Also exit when price returns to middle band
        returns_to_middle = (close_df > middle_band).astype(int).diff() == 1

        # Apply trend filter if enabled
        if use_trend_filter and "trend_filter" in self.indicators:
            trend_filter = self.indicators["trend_filter"]
            # Only take long signals when price above 200 MA
            touches_lower = touches_lower & (close_df > trend_filter)

        # Aggregate across all tickers
        entry_signals = touches_lower.any(axis=1)
        exit_signals = (touches_upper | returns_to_middle).any(axis=1)

        result = pd.DataFrame(
            {
                "entry": entry_signals,
                "exit": exit_signals,
            }
        )

        return result


class RSIMeanReversionStrategy(BaseStrategy):
    """
    RSI mean reversion strategy.

    Buys when RSI becomes oversold, sells when RSI becomes overbought.
    Uses standard 14-period RSI with 30/70 thresholds.

    Parameters:
        period: RSI calculation period (default: 14)
        oversold_threshold: Oversold threshold (default: 30)
        overbought_threshold: Overbought threshold (default: 70)
        use_extreme: Use extreme thresholds (10/90) for more signals (default: False)
        confirmation_periods: Require RSI to stay threshold for N periods (default: 1)

    Example:
        >>> strategy = RSIMeanReversionStrategy(params={
        ...     "period": 14,
        ...     "oversold_threshold": 30,
        ...     "overbought_threshold": 70
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "period": 14,
            "oversold_threshold": 30,
            "overbought_threshold": 70,
            "use_extreme": False,  # If True, use 10/90 instead of 30/70
            "confirmation_periods": 1,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize RSI strategy."""
        # Extract close prices
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs("close", level="field", axis=1)
        else:
            close_df = data

        period = self.get_param("period")

        # Compute RSI for each ticker
        rsi_df = close_df.apply(lambda col: self._compute_rsi(col, period))

        self.indicators["rsi"] = rsi_df
        self.indicators["close"] = close_df

        logger.info(
            "RSIMeanReversionStrategy initialized",
            period=period,
            oversold=self.get_param("oversold_threshold"),
            overbought=self.get_param("overbought_threshold"),
        )

    def _compute_rsi(self, prices: pd.Series, period: int) -> pd.Series:
        """
        Compute RSI for a single price series.

        Args:
            prices: Price series
            period: RSI period

        Returns:
            RSI values (0-100)
        """
        # Calculate price changes
        delta = prices.diff()

        # Separate gains and losses
        gains = delta.where(delta > 0, 0)
        losses = -delta.where(delta < 0, 0)

        # Calculate average gains and losses (Wilder's smoothing)
        avg_gains = gains.rolling(window=period, min_periods=1).mean()
        avg_losses = losses.rolling(window=period, min_periods=1).mean()

        # Calculate RS and RSI
        rs = avg_gains / avg_losses
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate RSI mean reversion signals.

        Entry: RSI crosses below oversold threshold
        Exit: RSI crosses above overbought threshold

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        rsi_df = self.indicators["rsi"]

        oversold = self.get_param("oversold_threshold")
        overbought = self.get_param("overbought_threshold")

        # Use extreme thresholds if specified
        if self.get_param("use_extreme"):
            oversold = 10
            overbought = 90

        # Detect threshold crosses
        # Entry: RSI crosses below oversold
        oversold_signal = (rsi_df < oversold).astype(int).diff() == 1

        # Exit: RSI crosses above overbought
        overbought_signal = (rsi_df > overbought).astype(int).diff() == 1

        # Aggregate across all tickers
        entry_signals = oversold_signal.any(axis=1)
        exit_signals = overbought_signal.any(axis=1)

        result = pd.DataFrame(
            {
                "entry": entry_signals,
                "exit": exit_signals,
            }
        )

        return result


class CombinedMeanReversionStrategy(BaseStrategy):
    """
    Combined mean reversion strategy using multiple indicators.

    Requires both Bollinger Bands and RSI to agree before entering.

    Parameters:
        bb_period: Bollinger Bands period (default: 20)
        bb_std: Bollinger Bands std dev (default: 2.0)
        rsi_period: RSI period (default: 14)
        rsi_oversold: RSI oversold threshold (default: 30)
        rsi_overbought: RSI overbought threshold (default: 70)

    Example:
        >>> strategy = CombinedMeanReversionStrategy(params={
        ...     "bb_period": 20,
        ...     "rsi_period": 14
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "bb_period": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize combined strategy."""
        # Extract close prices
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs("close", level="field", axis=1)
        else:
            close_df = data

        # Bollinger Bands
        bb_period = self.get_param("bb_period")
        bb_std = self.get_param("bb_std")
        sma = close_df.rolling(window=bb_period).mean()
        std = close_df.rolling(window=bb_period).std()
        self.indicators["bb_lower"] = sma - (bb_std * std)
        self.indicators["bb_upper"] = sma + (bb_std * std)
        self.indicators["close"] = close_df

        # RSI
        rsi_period = self.get_param("rsi_period")
        rsi_df = close_df.apply(lambda col: self._compute_rsi(col, rsi_period))
        self.indicators["rsi"] = rsi_df

        logger.info(
            "CombinedMeanReversionStrategy initialized",
            bb_period=bb_period,
            rsi_period=rsi_period,
        )

    def _compute_rsi(self, prices: pd.Series, period: int) -> pd.Series:
        """Compute RSI for a single price series."""
        delta = prices.diff()
        gains = delta.where(delta > 0, 0)
        losses = -delta.where(delta < 0, 0)
        avg_gains = gains.rolling(window=period, min_periods=1).mean()
        avg_losses = losses.rolling(window=period, min_periods=1).mean()
        rs = avg_gains / avg_losses
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate combined mean reversion signals.

        Entry: Both BB lower band touch AND RSI oversold
        Exit: Both BB upper band touch AND RSI overbought

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        close_df = self.indicators["close"]
        bb_lower = self.indicators["bb_lower"]
        bb_upper = self.indicators["bb_upper"]
        rsi_df = self.indicators["rsi"]

        rsi_oversold = self.get_param("rsi_oversold")
        rsi_overbought = self.get_param("rsi_overbought")

        # BB signals
        bb_entry = (close_df < bb_lower).astype(int).diff() == 1
        bb_exit = (close_df > bb_upper).astype(int).diff() == 1

        # RSI signals
        rsi_entry = (rsi_df < rsi_oversold).astype(int).diff() == 1
        rsi_exit = (rsi_df > rsi_overbought).astype(int).diff() == 1

        # Combined signals (both must agree)
        entry_signals = (bb_entry & rsi_entry).any(axis=1)
        exit_signals = (bb_exit & rsi_exit).any(axis=1)

        result = pd.DataFrame(
            {
                "entry": entry_signals,
                "exit": exit_signals,
            }
        )

        return result


__all__ = [
    "BBMeanReversionStrategy",
    "RSIMeanReversionStrategy",
    "CombinedMeanReversionStrategy",
]
