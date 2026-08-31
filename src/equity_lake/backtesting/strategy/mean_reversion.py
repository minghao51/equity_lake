import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class BBMeanReversionStrategy(BaseStrategy):
    """Bollinger-band mean-reversion strategy.

    Holding semantics (hold until the opposite signal — not a one-day trade):
    the first close below the lower band opens a ``position_size`` long position
    that is held on every bar until the opposite event — a close back at or
    above the middle band (SMA) — closes it. The position is forward-filled
    between entry and exit events, mirroring ``momentum.py``'s rebalance fills,
    so the weight forms one contiguous nonzero block per band round-trip instead
    of a single spike on the entry bar. A fresh lower-band entry after an exit
    re-opens the position.

    Exit rule choice: middle-band touch (close >= SMA), the classic
    mean-reversion target; a re-cross above the lower band alone is NOT an exit.

    The engine executes at the next bar's close after each weight change
    (no same-bar lookahead).
    """

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

        data = data.sort(["ticker", "date"])

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
        # Opposite event: close back at/above the middle band ends the trade.
        exit_signal = pl.col("close") >= pl.col("middle_band")

        use_trend_filter = self.get_param("use_trend_filter")
        if use_trend_filter and "trend_filter" in df.columns:
            entry = entry & (pl.col("close") > pl.col("trend_filter"))

        # Enter on the first lower-band close, hold until the middle-band touch:
        # emit the event weight and forward-fill the state between events (0.0
        # before the first entry and during warm-up, where the bands are null).
        weight = (
            pl.when(entry)
            .then(float(self.get_param("position_size")))
            .when(exit_signal)
            .then(0.0)
            .otherwise(None)
            .forward_fill()
            .over("ticker")
            .fill_null(0.0)
        )

        return df.with_columns(weight.alias("weight")).select("date", "ticker", "weight")


__all__ = ["BBMeanReversionStrategy"]
