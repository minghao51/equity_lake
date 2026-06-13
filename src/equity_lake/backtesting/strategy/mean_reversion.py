import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


def _rsi_expr(close: pl.Expr, period: int) -> pl.Expr:
    delta = close.diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    avg_gain = gain.rolling_mean(window_size=period)
    avg_loss = loss.rolling_mean(window_size=period)
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class BBMeanReversionStrategy(BaseStrategy):
    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "period": 20,
            "num_std": 2.0,
            "position_size": 0.95,
            "use_trend_filter": True,
            "stop_loss_pct": 0.05,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pl.DataFrame) -> None:
        period = self.get_param("period")
        num_std = self.get_param("num_std")
        use_trend_filter = self.get_param("use_trend_filter")

        sma = pl.col("close").rolling_mean(window_size=period).over("ticker")
        std = pl.col("close").rolling_std(window_size=period).over("ticker")

        cols = [
            sma.alias("middle_band"),
            (sma + num_std * std).alias("upper_band"),
            (sma - num_std * std).alias("lower_band"),
        ]
        if use_trend_filter:
            cols.append(pl.col("close").rolling_mean(window_size=200).over("ticker").alias("trend_filter"))

        self._data_with_indicators = data.with_columns(cols)
        logger.info("BBMeanReversionStrategy initialized", period=period, num_std=num_std)

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        df = self._data_with_indicators
        below_lower = pl.col("close") < pl.col("lower_band")
        prev_below = (pl.col("close").shift(1).over("ticker")) < (pl.col("lower_band").shift(1).over("ticker"))
        entry = below_lower & ~prev_below

        use_trend_filter = self.get_param("use_trend_filter")
        if use_trend_filter and "trend_filter" in df.columns:
            entry = entry & (pl.col("close") > pl.col("trend_filter"))

        return df.with_columns(pl.when(entry).then(self.get_param("position_size")).otherwise(0.0).alias("weight")).select("date", "ticker", "weight")


class RSIMeanReversionStrategy(BaseStrategy):
    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "period": 14,
            "oversold_threshold": 30,
            "overbought_threshold": 70,
            "use_extreme": False,
            "confirmation_periods": 1,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pl.DataFrame) -> None:
        period = self.get_param("period")
        self._data_with_indicators = data.with_columns(_rsi_expr(pl.col("close"), period).over("ticker").alias("rsi"))
        logger.info("RSIMeanReversionStrategy initialized", period=period)

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        df = self._data_with_indicators
        oversold = self.get_param("oversold_threshold")
        overbought = self.get_param("overbought_threshold")

        if self.get_param("use_extreme"):
            oversold = 10
            overbought = 90

        below_os = pl.col("rsi") < oversold
        prev_below_os = pl.col("rsi").shift(1).over("ticker") < oversold
        entry = below_os & ~prev_below_os

        above_ob = pl.col("rsi") > overbought
        prev_above_ob = pl.col("rsi").shift(1).over("ticker") > overbought
        exit_signal = above_ob & ~prev_above_ob

        return df.with_columns(pl.when(entry).then(1.0).when(exit_signal).then(0.0).otherwise(0.0).alias("weight")).select("date", "ticker", "weight")


class CombinedMeanReversionStrategy(BaseStrategy):
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

    def initialize(self, data: pl.DataFrame) -> None:
        bb_period = self.get_param("bb_period")
        bb_std = self.get_param("bb_std")
        rsi_period = self.get_param("rsi_period")

        sma = pl.col("close").rolling_mean(window_size=bb_period).over("ticker")
        std = pl.col("close").rolling_std(window_size=bb_period).over("ticker")

        self._data_with_indicators = data.with_columns(
            (sma - bb_std * std).alias("bb_lower"),
            (sma + bb_std * std).alias("bb_upper"),
            _rsi_expr(pl.col("close"), rsi_period).over("ticker").alias("rsi"),
        )
        logger.info("CombinedMeanReversionStrategy initialized")

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        df = self._data_with_indicators
        rsi_oversold = self.get_param("rsi_oversold")
        rsi_overbought = self.get_param("rsi_overbought")

        bb_entry = pl.col("close") < pl.col("bb_lower")
        rsi_entry = pl.col("rsi") < rsi_oversold
        entry = bb_entry & rsi_entry

        bb_exit = pl.col("close") > pl.col("bb_upper")
        rsi_exit = pl.col("rsi") > rsi_overbought
        exit_signal = bb_exit & rsi_exit

        return df.with_columns(pl.when(entry).then(1.0).when(exit_signal).then(0.0).otherwise(0.0).alias("weight")).select("date", "ticker", "weight")


__all__ = [
    "BBMeanReversionStrategy",
    "RSIMeanReversionStrategy",
    "CombinedMeanReversionStrategy",
]
