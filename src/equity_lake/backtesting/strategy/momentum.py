import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy, strategy_identity_columns

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
        df = data.with_columns((pl.col("close").pct_change(n=lookback).over(identity_columns).shift(skip_days)).alias("past_return"))

        dates = df["date"].unique().sort()
        rebalance_dates = dates[::rebalance_days].to_list()

        weight_frames = []
        for rb_date in rebalance_dates:
            slice_df = df.filter(pl.col("date") == rb_date).drop_nulls(subset=["past_return"])

            if slice_df.height < min_stocks:
                continue

            ranked = slice_df.with_columns(pl.col("past_return").rank(descending=True).alias("rank"))
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
            return data.select(weight_keys).with_columns(pl.lit(0.0).alias("weight"))

        result = pl.concat(weight_frames)
        return (
            data.select(weight_keys)
            .join(result, on=weight_keys, how="left")
            .with_columns(pl.col("weight").fill_null(strategy="forward").over(identity_columns))
            .fill_null(0.0)
        )


__all__ = ["CrossSectionalMomentumStrategy"]
