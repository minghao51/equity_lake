"""Pointblank-based schema definitions for data quality validation."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ACTION_COVERAGE_SCHEMA_VERSION,
    ActionCoverageProof,
)
from equity_lake.core.instruments import InstrumentIdentityError, normalize_identity_frame
from equity_lake.core.polars_utils import ensure_polars
from equity_lake.core.schemas import CORPORATE_ACTION_TYPES

pb: Any
try:
    import pointblank as _pointblank
except ImportError as _exc:  # pragma: no cover - exercised via subprocess test
    pb = None
    _POINTBLANK_IMPORT_ERROR: Exception | None = _exc
else:
    pb = _pointblank
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

    def _build_validation(self, df: pl.DataFrame) -> Any:
        raise NotImplementedError


class PriceDataSchema(PointblankSchema):
    """Schema for OHLCV price data.

    Validates required columns, positive prices, price consistency,
    and no duplicate ticker+date combinations.
    """

    def _build_validation(self, df: pl.DataFrame) -> Any:
        canonical_identity = {"market", "mic", "symbol"}.issubset(df.columns)
        if canonical_identity:
            try:
                normalize_identity_frame(df)
            except InstrumentIdentityError as exc:
                raise ValueError(f"Invalid canonical price identity: {exc}") from exc
        identity_date = ["market", "mic", "symbol", "date"] if canonical_identity else ["ticker", "date"]
        v = (
            pb.Validate(data=df, label="Price data schema")
            .col_vals_gt(columns="open", value=0)
            .col_vals_gt(columns="high", value=0)
            .col_vals_gt(columns="low", value=0)
            .col_vals_gt(columns="close", value=0)
            .col_vals_ge(columns="volume", value=0)
            .rows_distinct(columns_subset=identity_date)
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

    def _build_validation(self, df: pl.DataFrame) -> Any:
        return (
            pb.Validate(data=df, label="Macro data schema")
            .col_vals_not_null(columns="date")
            .col_vals_not_null(columns="indicator")
            .col_vals_not_null(columns="value")
            .col_vals_not_null(columns="source")
        )


class NewsDataSchema(PointblankSchema):
    """Schema for news article data with sentiment."""

    def _build_validation(self, df: pl.DataFrame) -> Any:
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

    def _build_validation(self, df: pl.DataFrame) -> Any:
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


class SocialSentimentSchema(PointblankSchema):
    """Schema for social-sentiment rows (Finnhub social metrics).

    Distinct from news: social rows carry mention counts and aggregated
    scores instead of headlines/urls. Added in W01 (F20) — social frames
    previously routed through :class:`NewsDataSchema`, which rejected them
    for missing ``headline``/``url``/``sentiment_score`` columns, blocking
    every social write at the quality gate.
    """

    def _build_validation(self, df: pl.DataFrame) -> Any:
        return (
            pb.Validate(data=df, label="Social sentiment data schema")
            .col_vals_not_null(columns="ticker")
            .col_vals_not_null(columns="date")
            .col_vals_not_null(columns="datetime")
            .col_vals_not_null(columns="source")
            .col_vals_ge(columns="mention_count", value=0)
            .col_vals_between(columns="score", left=-1, right=1)
            .col_vals_ge(columns="positive_score", value=0)
            .col_vals_ge(columns="negative_score", value=0)
            .rows_distinct(columns_subset=["ticker", "datetime", "source"])
        )


class CorporateActionCoverageSchema(PointblankSchema):
    """Schema for complete source-scoped corporate-action coverage proofs.

    Complete empty windows are persisted as coverage rows, not fabricated
    corporate-action events. The source clock must be timezone-aware and is
    checked by the shared versioned Pydantic contract.
    """

    @classmethod
    def validate(cls, df: Any) -> pl.DataFrame:
        frame = ensure_polars(df)
        proofs = [ActionCoverageProof.model_validate(row) for row in frame.to_dicts()]
        for index, proof in enumerate(proofs):
            for other in proofs[index + 1 :]:
                same_source = proof.identity == other.identity and proof.source == other.source
                overlaps = proof.coverage_start <= other.coverage_end and other.coverage_start <= proof.coverage_end
                conflicting_vintage = (proof.source_available_at, proof.source_valid_until) != (other.source_available_at, other.source_valid_until)
                if same_source and overlaps and conflicting_vintage:
                    raise ValueError(f"Conflicting overlapping corporate-action coverage proofs for {proof.identity} from {proof.source!r}")
        return super().validate(frame)

    def _build_validation(self, df: pl.DataFrame) -> Any:
        return (
            pb.Validate(data=df, label="Corporate-action coverage schema")
            .col_vals_not_null(columns="market")
            .col_vals_not_null(columns="mic")
            .col_vals_not_null(columns="symbol")
            .col_vals_not_null(columns="source")
            .col_vals_not_null(columns="coverage_start")
            .col_vals_not_null(columns="coverage_end")
            .col_vals_not_null(columns="source_available_at")
            .col_vals_not_null(columns="source_valid_until")
            .col_vals_in_set(columns="coverage_status", set=["complete"])
            .col_vals_eq(columns="coverage_schema_version", value=ACTION_COVERAGE_SCHEMA_VERSION)
            .col_vals_expr(
                expr=pl.col("coverage_end") >= pl.col("coverage_start"),
                brief="coverage_end must be on or after coverage_start",
            )
            .rows_distinct(columns_subset=["market", "mic", "symbol", "source", "coverage_start", "coverage_end"])
        )


class CorporateActionSchema(PointblankSchema):
    """Schema for corporate action rows (ADR-0011: dividends and splits).

    One row per (ticker, ex_date, action); ``value`` is a non-negative cash
    dividend per share or a strictly positive split ratio, and ``ex_date``
    is never in the future.
    """

    def _build_validation(self, df: pl.DataFrame) -> Any:
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
    "social": SocialSentimentSchema,
    "article": ArticleDataSchema,
    "corporate_action": CorporateActionSchema,
    "corporate_action_coverage": CorporateActionCoverageSchema,
}
