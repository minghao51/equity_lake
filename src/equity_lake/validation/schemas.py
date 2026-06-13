"""Pointblank-based schema definitions for data quality validation."""

from __future__ import annotations

from typing import Any

import pointblank as pb
import polars as pl

from equity_lake.core.polars_utils import ensure_polars


class PointblankSchema:
    """Base class for pointblank-based schema validators.

    Subclasses implement ``_build_validation`` to define column-level
    and row-level checks using the pointblank builder API.
    """

    @classmethod
    def validate(cls, df: Any) -> pl.DataFrame:
        df_polars = ensure_polars(df)
        if df_polars.is_empty():
            return df_polars
        validation = cls()._build_validation(df_polars).interrogate()
        failed_steps = [s for s in validation.validation_info if not s.all_passed]
        if failed_steps:
            msgs = [f"- {s.autobrief} ({s.n_failed} failed)" for s in failed_steps]
            raise ValueError("Schema validation failed:\n" + "\n".join(msgs))
        return df_polars

    def _build_validation(self, df: pl.DataFrame) -> pb.Validate:
        raise NotImplementedError


class PriceDataSchema(PointblankSchema):
    """Schema for OHLCV price data.

    Validates required columns, positive prices, price consistency,
    and no duplicate ticker+date combinations.
    """

    def _build_validation(self, df: pl.DataFrame) -> pb.Validate:
        v = (
            pb.Validate(data=df, label="Price data schema")
            .col_vals_gt(columns="open", value=0)
            .col_vals_gt(columns="high", value=0)
            .col_vals_gt(columns="low", value=0)
            .col_vals_gt(columns="close", value=0)
            .col_vals_ge(columns="volume", value=0)
            .rows_distinct(columns_subset=["ticker", "date"])
        )

        v = v.col_vals_expr(
            expr=(
                (pl.col("high") >= pl.col("low"))
                & (pl.col("high") >= pl.col("open"))
                & (pl.col("high") >= pl.col("close"))
                & (pl.col("low") <= pl.col("open"))
                & (pl.col("low") <= pl.col("close"))
            )
        )

        if "adj_close" in df.columns:
            v = v.col_vals_gt(columns="adj_close", value=0)

        return v


class MacroDataSchema(PointblankSchema):
    """Schema for macro economic indicator data."""

    def _build_validation(self, df: pl.DataFrame) -> pb.Validate:
        return (
            pb.Validate(data=df, label="Macro data schema")
            .col_vals_not_null(columns="date")
            .col_vals_not_null(columns="indicator")
            .col_vals_not_null(columns="value")
            .col_vals_not_null(columns="source")
        )


class NewsDataSchema(PointblankSchema):
    """Schema for news article data with sentiment."""

    def _build_validation(self, df: pl.DataFrame) -> pb.Validate:
        return (
            pb.Validate(data=df, label="News data schema")
            .col_vals_not_null(columns="ticker")
            .col_vals_not_null(columns="date")
            .col_vals_not_null(columns="datetime")
            .col_vals_not_null(columns="source")
            .col_vals_not_null(columns="headline")
            .col_vals_not_null(columns="url")
            .col_vals_between(columns="sentiment_score", left=-1, right=1)
            .rows_distinct(columns_subset=["url"])
        )


SCHEMA_REGISTRY: dict[str, type[PointblankSchema]] = {
    "price": PriceDataSchema,
    "macro": MacroDataSchema,
    "news": NewsDataSchema,
}
