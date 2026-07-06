import polars as pl
import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

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
            "volatility_target": 0.15,
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

        df = data.with_columns((pl.col("close").pct_change(n=lookback).over("ticker").shift(skip_days)).alias("past_return"))

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
            top_tickers = set(ranked.sort("rank").head(top_count)["ticker"].to_list())

            if long_only:
                weights = slice_df.with_columns(
                    pl.when(pl.col("ticker").is_in(top_tickers)).then(1.0 / top_count).otherwise(0.0).alias("weight")
                ).select("date", "ticker", "weight")
            else:
                bottom_pct = self.get_param("bottom_pct")
                bottom_count = max(int(n * bottom_pct), 1)
                bottom_tickers = set(ranked.sort("rank", descending=True).head(bottom_count)["ticker"].to_list())
                long_w = 1.0 / top_count if top_count > 0 else 0.0
                short_w = -1.0 / bottom_count if bottom_count > 0 else 0.0

                weights = slice_df.with_columns(
                    pl.when(pl.col("ticker").is_in(top_tickers))
                    .then(long_w)
                    .when(pl.col("ticker").is_in(bottom_tickers))
                    .then(short_w)
                    .otherwise(0.0)
                    .alias("weight")
                ).select("date", "ticker", "weight")

            weight_frames.append(weights)

        if not weight_frames:
            return data.select("date", "ticker").with_columns(pl.lit(0.0).alias("weight"))

        result = pl.concat(weight_frames)
        return (
            data.select("date", "ticker")
            .join(result, on=["date", "ticker"], how="left")
            .with_columns(pl.col("weight").fill_null(strategy="forward").over("ticker"))
            .fill_null(0.0)
        )


__all__ = ["CrossSectionalMomentumStrategy"]
