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


class DonchianBreakoutStrategy(BaseStrategy):
    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "channel_period": 20,
            "atr_period": 14,
            "atr_multiplier": 3.0,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pl.DataFrame) -> None:
        period = self.get_param("channel_period")
        self._data_with_indicators = data.with_columns(
            pl.col("high").rolling_max(window_size=period).over("ticker").alias("upper_channel"),
            pl.col("low").rolling_min(window_size=period).over("ticker").alias("lower_channel"),
        )
        logger.info("DonchianBreakoutStrategy initialized", channel_period=period)

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        df = self._data_with_indicators
        breakout_up = pl.col("close") > pl.col("upper_channel")
        prev_close = pl.col("close").shift(1).over("ticker")
        prev_upper = pl.col("upper_channel").shift(1).over("ticker")
        breakout_now = breakout_up & (prev_close <= prev_upper)

        return df.with_columns(pl.when(breakout_now).then(1.0).otherwise(0.0).alias("weight")).select("date", "ticker", "weight")


class MACDStrategy(BaseStrategy):
    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "histogram_threshold": 0,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pl.DataFrame) -> None:
        fast_period = self.get_param("fast_period")
        slow_period = self.get_param("slow_period")
        signal_period = self.get_param("signal_period")

        ema_fast = pl.col("close").ewm_mean(span=fast_period).over("ticker")
        ema_slow = pl.col("close").ewm_mean(span=slow_period).over("ticker")
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm_mean(span=signal_period).over("ticker")

        self._data_with_indicators = data.with_columns(
            macd_line.alias("macd"),
            signal_line.alias("signal"),
            (macd_line - signal_line).alias("histogram"),
        )

        logger.info(
            "MACDStrategy initialized",
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        )

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        df = self._data_with_indicators
        bullish = pl.col("macd") > pl.col("signal")
        prev_macd = pl.col("macd").shift(1).over("ticker")
        prev_signal = pl.col("signal").shift(1).over("ticker")
        bullish_cross = bullish & (prev_macd <= prev_signal)

        threshold = self.get_param("histogram_threshold")
        if threshold > 0:
            bullish_cross = bullish_cross & (pl.col("histogram").abs() > threshold)

        return df.with_columns(pl.when(bullish_cross).then(1.0).otherwise(0.0).alias("weight")).select("date", "ticker", "weight")


class AdaptiveTrendStrategy(BaseStrategy):
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

    def initialize(self, data: pl.DataFrame) -> None:
        fast_period = self.get_param("fast_ma_period")
        slow_period = self.get_param("slow_ma_period")
        adx_period = self.get_param("adx_period")

        df = data.with_columns(
            pl.col("close").rolling_mean(window_size=fast_period).over("ticker").alias("fast_ma"),
            pl.col("close").rolling_mean(window_size=slow_period).over("ticker").alias("slow_ma"),
        )
        adx = self._compute_adx(df, adx_period)
        self._data_with_indicators = df.with_columns(adx.alias("adx"))

        logger.info(
            "AdaptiveTrendStrategy initialized",
            fast_ma_period=fast_period,
            slow_ma_period=slow_period,
            adx_threshold=self.get_param("adx_threshold"),
        )

    def _compute_adx(self, data: pl.DataFrame, period: int) -> pl.Expr:
        tr1 = pl.col("high") - pl.col("low")
        tr2 = (pl.col("high") - pl.col("close").shift(1).over("ticker")).abs()
        tr3 = (pl.col("low") - pl.col("close").shift(1).over("ticker")).abs()
        tr = pl.max_horizontal(tr1, tr2, tr3)

        up_move = pl.col("high") - pl.col("high").shift(1).over("ticker")
        down_move = pl.col("low").shift(1).over("ticker") - pl.col("low")

        plus_dm = pl.when((up_move > down_move) & (up_move > 0)).then(up_move).otherwise(0.0)
        minus_dm = pl.when((down_move > up_move) & (down_move > 0)).then(down_move).otherwise(0.0)

        atr = tr.rolling_mean(window_size=period).over("ticker")
        plus_di = pl.when(atr != 0).then(100 * (plus_dm.rolling_mean(window_size=period).over("ticker") / atr)).otherwise(0.0)
        minus_di = pl.when(atr != 0).then(100 * (minus_dm.rolling_mean(window_size=period).over("ticker") / atr)).otherwise(0.0)
        di_sum = plus_di + minus_di
        dx = pl.when(di_sum != 0).then(100 * (plus_di - minus_di).abs() / di_sum).otherwise(0.0)
        return dx.rolling_mean(window_size=period).over("ticker")

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        df = self._data_with_indicators
        adx_threshold = self.get_param("adx_threshold")
        golden = pl.col("fast_ma") > pl.col("slow_ma")
        prev_fast = pl.col("fast_ma").shift(1).over("ticker")
        prev_slow = pl.col("slow_ma").shift(1).over("ticker")
        golden_cross = golden & (prev_fast <= prev_slow)
        entry = golden_cross & (pl.col("adx") > adx_threshold)

        return df.with_columns(pl.when(entry).then(1.0).otherwise(0.0).alias("weight")).select("date", "ticker", "weight")


__all__ = [
    "SMACrossoverStrategy",
    "DonchianBreakoutStrategy",
    "MACDStrategy",
    "AdaptiveTrendStrategy",
]
