import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy, strategy_identity_columns
from equity_lake.core.instruments import InstrumentIdentityError

logger = structlog.get_logger(__name__)


class CrossSectionalMomentumStrategy(BaseStrategy):
    def __init__(self, params: dict[str, object] | None = None):
        default_params = {
            "lookback_days": 252,
            "skip_days": 21,
            "top_pct": 0.3,
            "bottom_pct": 0.3,
            "rebalance_days": 21,
            "long_only": True,
            "min_stocks": 10,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pl.DataFrame) -> None:
        self._data = data
        logger.info(
            "CrossSectionalMomentumStrategy initialized",
            num_tickers=data["ticker"].n_unique(),
        )

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        lookback = self.get_param("lookback_days")
        skip_days = self.get_param("skip_days")
        top_pct = self.get_param("top_pct")
        rebalance_days = self.get_param("rebalance_days")
        long_only = self.get_param("long_only")
        min_stocks = self.get_param("min_stocks")

        identity_columns = strategy_identity_columns(data)
        weight_keys = ["date", *identity_columns]
        # Canonical instrument/date order before any time operation: pct_change and
        # shift below both read the immediately preceding row inside one instrument
        # window, so an unsorted frame would let a lag cross instruments or venues.
        ordered = data.sort([*identity_columns, "date"])

        duplicates = ordered.select(weight_keys).is_duplicated()
        if duplicates.any():
            duplicate_row = ordered.filter(duplicates).row(0, named=True)
            identity_text = ", ".join(f"{column}={duplicate_row[column]!r}" for column in identity_columns)
            raise InstrumentIdentityError(
                f"Duplicate instrument/date rows for {identity_text} on {duplicate_row['date']}: "
                "momentum weights require exactly one observation per instrument-session"
            )

        df = ordered.with_columns(
            # Lookback return and skip lag evaluated in the same instrument-local
            # window; an instrument has no return before lookback + skip own bars.
            pl.col("close").pct_change(n=lookback).shift(skip_days).over(identity_columns).alias("past_return")
        )

        dates = df["date"].unique().sort()
        rebalance_dates = dates[::rebalance_days].to_list()

        weight_frames = []
        for rb_date in rebalance_dates:
            slice_df = df.filter(pl.col("date") == rb_date).drop_nulls(subset=["past_return"])

            if slice_df.height < min_stocks:
                continue

            # Ordinal rank over a canonical order: past return descending, then
            # identity ascending. Ties are broken deterministically by identity, so
            # the cut selects exactly top_count names regardless of input order.
            ranked = slice_df.sort(
                ["past_return", *identity_columns],
                descending=[True, *([False] * len(identity_columns))],
                nulls_last=True,
            ).with_row_index("rank", offset=1)
            n = ranked.height
            top_count = max(int(n * top_pct), 1)

            if long_only:
                weights = ranked.with_columns(pl.when(pl.col("rank") <= top_count).then(1.0 / top_count).otherwise(0.0).alias("weight")).select(
                    *weight_keys, "weight"
                )
            else:
                bottom_pct = self.get_param("bottom_pct")
                bottom_count = max(int(n * bottom_pct), 1)
                long_w = 1.0 / top_count if top_count > 0 else 0.0
                short_w = -1.0 / bottom_count if bottom_count > 0 else 0.0

                weights = ranked.with_columns(
                    pl.when(pl.col("rank") <= top_count)
                    .then(long_w)
                    .when(pl.col("rank") > n - bottom_count)
                    .then(short_w)
                    .otherwise(0.0)
                    .alias("weight")
                ).select(*weight_keys, "weight")

            weight_frames.append(weights)

        if not weight_frames:
            return ordered.select(weight_keys).with_columns(pl.lit(0.0).alias("weight"))

        result = pl.concat(weight_frames)
        return (
            ordered.select(weight_keys)
            .join(result, on=weight_keys, how="left")
            .sort([*identity_columns, "date"])
            .with_columns(pl.col("weight").fill_null(strategy="forward").over(identity_columns))
            .with_columns(pl.col("weight").fill_null(0.0))
            .sort(weight_keys)
        )


__all__ = ["CrossSectionalMomentumStrategy"]
