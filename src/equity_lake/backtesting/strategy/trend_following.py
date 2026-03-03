"""
Trend-following trading strategies.

This module implements trend-following strategies including moving average
crossovers, breakouts, and MACD-based strategies.
"""


import pandas as pd
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class SMACrossoverStrategy(BaseStrategy):
    """
    Simple moving average crossover strategy.

    Goes long when fast MA crosses above slow MA (Golden Cross).
    Exits when fast MA crosses below slow MA (Death Cross).

    Parameters:
        fast_period: Fast MA period (default: 50)
        slow_period: Slow MA period (default: 200)
        use_ema: Use exponential MA instead of simple MA (default: False)
        use_adx_filter: Only trade when ADX > 25 (strong trend) (default: False)

    Example:
        >>> strategy = SMACrossoverStrategy(params={
        ...     "fast_period": 50,
        ...     "slow_period": 200,
        ...     "use_adx_filter": True
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "fast_period": 50,
            "slow_period": 200,
            "use_ema": False,
            "use_adx_filter": False,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize SMA crossover strategy."""
        # Extract close prices
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs("close", level="field", axis=1)
        else:
            close_df = data

        fast_period = self.get_param("fast_period")
        slow_period = self.get_param("slow_period")
        use_ema = self.get_param("use_ema")

        # Compute moving averages
        if use_ema:
            fast_ma = close_df.ewm(span=fast_period, adjust=False).mean()
            slow_ma = close_df.ewm(span=slow_period, adjust=False).mean()
        else:
            fast_ma = close_df.rolling(window=fast_period).mean()
            slow_ma = close_df.rolling(window=slow_period).mean()

        self.indicators["fast_ma"] = fast_ma
        self.indicators["slow_ma"] = slow_ma
        self.indicators["close"] = close_df

        logger.info(
            "SMACrossoverStrategy initialized",
            fast_period=fast_period,
            slow_period=slow_period,
            use_ema=use_ema,
        )

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate SMA crossover signals.

        Entry: Fast MA crosses above slow MA
        Exit: Fast MA crosses below slow MA

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        fast_ma = self.indicators["fast_ma"]
        slow_ma = self.indicators["slow_ma"]

        # Detect crossovers
        # Golden cross: fast crosses above slow
        golden_cross = (fast_ma > slow_ma).astype(int).diff() == 1

        # Death cross: fast crosses below slow
        death_cross = (fast_ma < slow_ma).astype(int).diff() == 1

        # Aggregate across all tickers
        entry_signals = golden_cross.any(axis=1)
        exit_signals = death_cross.any(axis=1)

        result = pd.DataFrame(
            {
                "entry": entry_signals,
                "exit": exit_signals,
            }
        )

        return result


class DonchianBreakoutStrategy(BaseStrategy):
    """
    Donchian channel breakout strategy.

    Buys when price breaks above N-day high, exits when price breaks
    below N-day low (or uses trailing stop).

    Parameters:
        channel_period: Period for high/low calculation (default: 20)
        use_exit_channel: Use opposite band for exit (default: True)
        atr_period: ATR period for trailing stop (default: 14)
        atr_multiplier: ATR multiplier for stop (default: 3.0)

    Example:
        >>> strategy = DonchianBreakoutStrategy(params={
        ...     "channel_period": 20,
        ...     "atr_multiplier": 3.0
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "channel_period": 20,
            "use_exit_channel": True,
            "atr_period": 14,
            "atr_multiplier": 3.0,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize Donchian breakout strategy."""
        # Extract OHLC data
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs("close", level="field", axis=1)
            high_df = (
                data.xs("high", level="field", axis=1)
                if "high" in data.columns.get_level_values(1)
                else close_df
            )
            low_df = (
                data.xs("low", level="field", axis=1)
                if "low" in data.columns.get_level_values(1)
                else close_df
            )
        else:
            close_df = data
            high_df = data
            low_df = data

        period = self.get_param("channel_period")

        # Compute Donchian channels
        upper_channel = high_df.rolling(window=period).max()
        lower_channel = low_df.rolling(window=period).min()

        self.indicators["upper_channel"] = upper_channel
        self.indicators["lower_channel"] = lower_channel
        self.indicators["close"] = close_df

        logger.info(
            "DonchianBreakoutStrategy initialized",
            channel_period=period,
        )

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate Donchian breakout signals.

        Entry: Price breaks above upper channel
        Exit: Price breaks below lower channel

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        close_df = self.indicators["close"]
        upper_channel = self.indicators["upper_channel"]
        lower_channel = self.indicators["lower_channel"]

        # Detect breakouts
        # Entry: Close breaks above upper channel
        breakout_up = (close_df > upper_channel).astype(int).diff() == 1

        # Exit: Close breaks below lower channel
        breakout_down = (close_df < lower_channel).astype(int).diff() == 1

        # Aggregate across all tickers
        entry_signals = breakout_up.any(axis=1)
        exit_signals = breakout_down.any(axis=1)

        result = pd.DataFrame(
            {
                "entry": entry_signals,
                "exit": exit_signals,
            }
        )

        return result


class MACDStrategy(BaseStrategy):
    """
    MACD (Moving Average Convergence Divergence) strategy.

    Generates buy/sell signals based on MACD line crossovers with
    the signal line.

    Parameters:
        fast_period: Fast EMA period for MACD (default: 12)
        slow_period: Slow EMA period for MACD (default: 26)
        signal_period: Signal line EMA period (default: 9)
        histogram_threshold: Minimum histogram magnitude for signal (default: 0)

    Example:
        >>> strategy = MACDStrategy(params={
        ...     "fast_period": 12,
        ...     "slow_period": 26,
        ...     "signal_period": 9
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "histogram_threshold": 0,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize MACD strategy."""
        # Extract close prices
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs("close", level="field", axis=1)
        else:
            close_df = data

        fast_period = self.get_param("fast_period")
        slow_period = self.get_param("slow_period")
        signal_period = self.get_param("signal_period")

        # Compute MACD for each ticker
        macd_line = pd.DataFrame(index=close_df.index, columns=close_df.columns)
        signal_line = pd.DataFrame(index=close_df.index, columns=close_df.columns)
        histogram = pd.DataFrame(index=close_df.index, columns=close_df.columns)

        for ticker in close_df.columns:
            prices = close_df[ticker].dropna()

            # Calculate EMAs
            ema_fast = prices.ewm(span=fast_period, adjust=False).mean()
            ema_slow = prices.ewm(span=slow_period, adjust=False).mean()

            # MACD line
            macd_values = ema_fast - ema_slow

            # Signal line
            signal_values = macd_values.ewm(span=signal_period, adjust=False).mean()

            # Histogram
            hist_values = macd_values - signal_values

            macd_line[ticker] = macd_values
            signal_line[ticker] = signal_values
            histogram[ticker] = hist_values

        self.indicators["macd"] = macd_line
        self.indicators["signal"] = signal_line
        self.indicators["histogram"] = histogram

        logger.info(
            "MACDStrategy initialized",
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        )

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate MACD signals.

        Entry: MACD crosses above signal line (bullish crossover)
        Exit: MACD crosses below signal line (bearish crossover)

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        macd = self.indicators["macd"]
        signal = self.indicators["signal"]
        histogram_threshold = self.get_param("histogram_threshold")

        # Detect crossovers
        # Bullish crossover: MACD crosses above signal
        bullish_cross = (macd > signal).astype(int).diff() == 1

        # Bearish crossover: MACD crosses below signal
        bearish_cross = (macd < signal).astype(int).diff() == 1

        # Apply histogram threshold if specified
        if histogram_threshold > 0:
            histogram = self.indicators["histogram"]
            bullish_cross = bullish_cross & (histogram.abs() > histogram_threshold)
            bearish_cross = bearish_cross & (histogram.abs() > histogram_threshold)

        # Aggregate across all tickers
        entry_signals = bullish_cross.any(axis=1)
        exit_signals = bearish_cross.any(axis=1)

        result = pd.DataFrame(
            {
                "entry": entry_signals,
                "exit": exit_signals,
            }
        )

        return result


class AdaptiveTrendStrategy(BaseStrategy):
    """
    Adaptive trend-following strategy using multiple indicators.

    Combines SMA crossover, ADX filter, and ATR-based position sizing
    for a more robust trend-following approach.

    Parameters:
        fast_ma_period: Fast MA period (default: 10)
        slow_ma_period: Slow MA period (default: 30)
        adx_period: ADX calculation period (default: 14)
        adx_threshold: Minimum ADX for trend (default: 25)
        atr_period: ATR period for stops (default: 14)
        atr_multiplier: ATR multiplier for stop loss (default: 2.0)

    Example:
        >>> strategy = AdaptiveTrendStrategy(params={
        ...     "fast_ma_period": 10,
        ...     "slow_ma_period": 30,
        ...     "adx_threshold": 25
        ... })
    """

    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "fast_ma_period": 10,
            "slow_ma_period": 30,
            "adx_period": 14,
            "adx_threshold": 25,
            "atr_period": 14,
            "atr_multiplier": 2.0,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """Initialize adaptive trend strategy."""
        # Extract OHLC data
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs("close", level="field", axis=1)
            high_df = (
                data.xs("high", level="field", axis=1)
                if "high" in data.columns.get_level_values(1)
                else close_df
            )
            low_df = (
                data.xs("low", level="field", axis=1)
                if "low" in data.columns.get_level_values(1)
                else close_df
            )
        else:
            close_df = data
            high_df = data
            low_df = data

        fast_period = self.get_param("fast_ma_period")
        slow_period = self.get_param("slow_ma_period")

        # Compute moving averages
        fast_ma = close_df.rolling(window=fast_period).mean()
        slow_ma = close_df.rolling(window=slow_period).mean()

        self.indicators["fast_ma"] = fast_ma
        self.indicators["slow_ma"] = slow_ma
        self.indicators["close"] = close_df

        # Compute ADX (simplified version)
        adx_period = self.get_param("adx_period")
        self.indicators["adx"] = self._compute_adx(
            high_df, low_df, close_df, adx_period
        )

        logger.info(
            "AdaptiveTrendStrategy initialized",
            fast_ma_period=fast_period,
            slow_ma_period=slow_period,
            adx_threshold=self.get_param("adx_threshold"),
        )

    def _compute_adx(
        self,
        high: pd.DataFrame,
        low: pd.DataFrame,
        close: pd.DataFrame,
        period: int,
    ) -> pd.DataFrame:
        """
        Compute Average Directional Index (ADX).

        Args:
            high: High prices
            low: Low prices
            close: Close prices
            period: Calculation period

        Returns:
            ADX values
        """
        # Calculate True Range
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(level=0, axis=1)

        # Calculate +DM and -DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)

        # Calculate smoothed TR and DM
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

        # Calculate DX and ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()

        return adx

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate adaptive trend signals.

        Entry: Fast MA crosses above slow MA AND ADX > threshold
        Exit: Fast MA crosses below slow MA

        Returns:
            DataFrame with 'entry' and 'exit' columns
        """
        fast_ma = self.indicators["fast_ma"]
        slow_ma = self.indicators["slow_ma"]
        adx = self.indicators["adx"]
        adx_threshold = self.get_param("adx_threshold")

        # Detect crossovers
        golden_cross = (fast_ma > slow_ma).astype(int).diff() == 1
        death_cross = (fast_ma < slow_ma).astype(int).diff() == 1

        # Apply ADX filter (only trade strong trends)
        strong_trend = adx > adx_threshold
        entry_with_filter = golden_cross & strong_trend

        # Aggregate across all tickers
        entry_signals = entry_with_filter.any(axis=1)
        exit_signals = death_cross.any(axis=1)

        result = pd.DataFrame(
            {
                "entry": entry_signals,
                "exit": exit_signals,
            }
        )

        return result


__all__ = [
    "SMACrossoverStrategy",
    "DonchianBreakoutStrategy",
    "MACDStrategy",
    "AdaptiveTrendStrategy",
]
