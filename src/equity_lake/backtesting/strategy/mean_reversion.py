import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


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


__all__ = ["BBMeanReversionStrategy"]
