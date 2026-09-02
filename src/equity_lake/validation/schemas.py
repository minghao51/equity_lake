"""Pointblank-based schema definitions for data quality validation."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from equity_lake.core.polars_utils import ensure_polars
from equity_lake.core.schemas import CORPORATE_ACTION_TYPES

try:
    import pointblank as pb
except ImportError as _exc:  # pragma: no cover - exercised via subprocess test
    pb = None
    _POINTBLANK_IMPORT_ERROR: Exception | None = _exc
else:
    _POINTBLANK_IMPORT_ERROR = None

_POINTBLANK_INSTALL_HINT = (
    "Data-quality validation requires 'pointblank', which is not available in this "
    "environment. Re-sync the project environment with `uv sync` (pointblank is a "
    "core dependency of equity-lake)."
)


class PointblankSchema:
    """Base class for pointblank-based schema validators.

    Subclasses implement ``_build_validation`` to define column-level
    and row-level checks using the pointblank builder API.
    """

    @classmethod
    def validate(cls, df: Any) -> pl.DataFrame:
        if pb is None:
            raise RuntimeError(_POINTBLANK_INSTALL_HINT) from _POINTBLANK_IMPORT_ERROR
        df_polars = ensure_polars(df)
        if df_polars.is_empty():
            return df_polars
        validation = cls()._build_validation(df_polars).interrogate()
        failed_steps = [s for s in validation.validation_info if not s.all_passed]
        if failed_steps:
            msgs = [f"- {s.brief or s.autobrief} ({s.n_failed} failed)" for s in failed_steps]
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


class ArticleDataSchema(PointblankSchema):
    """Schema for unstructured article rows (bronze raw + silver article pairs).

    Only structurally-required columns are enforced (``article_id``, and
    ``date`` when present). Enrichment columns are checked when present and
    pass on nulls — article rows are sparse by design (null tickers for
    market-wide news, optional LLM enrichment).
    """

    def _build_validation(self, df: pl.DataFrame) -> pb.Validate:
        v = pb.Validate(data=df, label="Article data schema").col_vals_not_null(columns="article_id")
        if "date" in df.columns:
            v = v.col_vals_not_null(columns="date")
        if "sentiment_score" in df.columns:
            v = v.col_vals_expr(expr=pl.col("sentiment_score").is_null() | pl.col("sentiment_score").is_between(-1, 1))
        if "confidence" in df.columns:
            v = v.col_vals_expr(expr=pl.col("confidence").is_null() | pl.col("confidence").is_between(0, 1))
        if "market_relevance" in df.columns:
            v = v.col_vals_expr(expr=pl.col("market_relevance").is_null() | pl.col("market_relevance").is_between(0, 1))
        return v


class CorporateActionSchema(PointblankSchema):
    """Schema for corporate action rows (ADR-0011: dividends and splits).

    One row per (ticker, ex_date, action); ``value`` is a non-negative cash
    dividend per share or a strictly positive split ratio, and ``ex_date``
    is never in the future.
    """

    def _build_validation(self, df: pl.DataFrame) -> pb.Validate:
        return (
            pb.Validate(data=df, label="Corporate action data schema")
            .col_vals_not_null(columns="ticker")
            .col_vals_not_null(columns="ex_date")
            .col_vals_not_null(columns="action")
            .col_vals_not_null(columns="value")
            .col_vals_in_set(columns="action", set=CORPORATE_ACTION_TYPES)
            .col_vals_ge(columns="value", value=0)
            .col_vals_expr(
                expr=(pl.col("action") != "split") | (pl.col("value") > 0),
                brief="Split rows must have a strictly positive ratio (value > 0)",
            )
            .col_vals_expr(
                expr=pl.col("ex_date") <= date.today(),
                brief="ex_date must not be in the future",
            )
            .col_vals_expr(
                expr=~pl.struct("ticker", "ex_date", "action").is_duplicated(),
                brief="(ticker, ex_date, action) rows must be unique",
            )
        )


SCHEMA_REGISTRY: dict[str, type[PointblankSchema]] = {
    "price": PriceDataSchema,
    "macro": MacroDataSchema,
    "news": NewsDataSchema,
    "article": ArticleDataSchema,
    "corporate_action": CorporateActionSchema,
}
