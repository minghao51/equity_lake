import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class SMACrossoverStrategy(BaseStrategy):
    """SMA/EMA crossover trend-following strategy.

    Holding semantics (hold until the opposite signal — not a one-day trade):
    a golden cross (fast MA crossing above slow MA) opens a full long position
    that is held on every bar until the opposite event, a cross-under
    (fast MA crossing below slow MA), closes it. The position is forward-filled
    between those two events, mirroring ``momentum.py``'s rebalance fills, so
    the weight forms one contiguous nonzero block per trend leg instead of a
    single spike on the cross bar.

    The engine executes at the next bar's close after each weight change
    (no same-bar lookahead).
    """

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

        data = data.sort(["ticker", "date"])

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
        fast_above = pl.col("fast_ma") > pl.col("slow_ma")
        prev_fast_above = (pl.col("fast_ma").shift(1).over("ticker")) > (pl.col("slow_ma").shift(1).over("ticker"))

        golden_cross_now = fast_above & ~prev_fast_above
        cross_under_now = ~fast_above & prev_fast_above
        # First bar where both MAs exist: the cross events are null there (no
        # previous value), so seed the state with the observed regime instead.
        first_valid = fast_above.is_not_null() & fast_above.shift(1).over("ticker").is_null()

        # Enter on the golden cross, hold until the cross-under: emit the event
        # weight and forward-fill the state between events (0.0 before the first
        # cross and during warm-up, where the MAs are still null).
        weight = (
            pl.when(first_valid)
            .then(fast_above.cast(pl.Float64))
            .when(golden_cross_now)
            .then(1.0)
            .when(cross_under_now)
            .then(0.0)
            .otherwise(None)
            .forward_fill()
            .over("ticker")
            .fill_null(0.0)
        )

        return df.with_columns(weight.alias("weight")).select("date", "ticker", "weight")


__all__ = ["SMACrossoverStrategy"]
