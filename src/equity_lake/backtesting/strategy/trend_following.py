import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class SMACrossoverStrategy(BaseStrategy):
    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "fast_period": 50,
            "slow_period": 200,
            "use_ema": False,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pl.DataFrame) -> None:
        fast_period = self.get_param("fast_period")
        slow_period = self.get_param("slow_period")
        use_ema = self.get_param("use_ema")

        if use_ema:
            fast_ma = data.with_columns(
                pl.col("close").ewm_mean(span=fast_period).over("ticker").alias("fast_ma"),
                pl.col("close").ewm_mean(span=slow_period).over("ticker").alias("slow_ma"),
            )
        else:
            fast_ma = data.with_columns(
                pl.col("close").rolling_mean(window_size=fast_period).over("ticker").alias("fast_ma"),
                pl.col("close").rolling_mean(window_size=slow_period).over("ticker").alias("slow_ma"),
            )

        self.indicators["fast_ma"] = fast_ma.select("date", "ticker", "fast_ma")
        self.indicators["slow_ma"] = fast_ma.select("date", "ticker", "slow_ma")
        self._data_with_indicators = fast_ma

        logger.info(
            "SMACrossoverStrategy initialized",
            fast_period=fast_period,
            slow_period=slow_period,
            use_ema=use_ema,
        )

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        df = self._data_with_indicators
        golden_cross = pl.col("fast_ma") > pl.col("slow_ma")
        prev_fast = pl.col("fast_ma").shift(1).over("ticker")
        prev_slow = pl.col("slow_ma").shift(1).over("ticker")
        golden_cross_now = golden_cross & (prev_fast <= prev_slow)

        return df.with_columns(pl.when(golden_cross_now).then(1.0).otherwise(0.0).alias("weight")).select("date", "ticker", "weight")


__all__ = ["SMACrossoverStrategy"]
