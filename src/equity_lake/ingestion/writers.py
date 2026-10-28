"""Writer helpers for ingestion.

All writes go through the Delta Lake storage layer (ACID transactions,
merge/upsert, time-travel). The canonical writer is ``upsert_dataset``.
"""

from datetime import date

import polars as pl
import structlog

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AVAILABILITY_SCHEMA_VERSION,
    FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    PRICE_ADJUSTMENT_VERSION,
    AvailabilityContractError,
    validate_feature_action_coverage,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_DATE_COLUMNS,
    IDENTITY_SCHEMA_VERSION,
    InstrumentIdentityError,
    normalize_identity_frame,
    prepare_source_identity_frame,
)
from equity_lake.core.paths import PRICE_MARKETS, canonical_market
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.core.schemas import (
    CORPORATE_ACTION_COVERAGE_COLUMNS,
    CORPORATE_ACTION_REQUIRED_COLUMNS,
    CORPORATE_ACTION_SCHEMA,
    FEATURE_SCHEMA_VERSION,
    INSTRUMENT_IDENTITY_COLUMNS,
    MACRO_COLUMNS,
    NEWS_COLUMNS,
    SOCIAL_COLUMNS,
)

logger = structlog.get_logger()


def _dedupe_key_columns(market: str) -> list[str]:
    if market in ("01_bronze/macro", "macro"):
        return ["indicator", "date"]
    if market in ("02_silver/news_sentiment", "us_news"):
        return ["url"]
    if market in ("02_silver/social_sentiment", "us_social_sentiment"):
        return ["ticker", "datetime", "source"]
    if market in (
        "01_bronze/raw_articles",
        "rss_news",
        "reddit_posts",
        "stocktwits_messages",
        "us_earnings_transcripts",
        "sec_filings_fulltext",
    ):
        return ["source_type", "source_url"]
    if market == "02_silver/processed_articles":
        return ["article_id", "ticker"]
    if market in ("02_silver/analyst_ratings", "us_analyst_ratings"):
        return ["ticker", "date"]
    if market in ("02_silver/sec_financials", "us_sec_financials"):
        return ["ticker", "date", "filing_type"]
    if _is_corporate_action_coverage(market):
        return ["market", "mic", "symbol", "source", "coverage_start", "coverage_end"]
    if _is_corporate_actions(market):
        return ["ticker", "ex_date", "action"]
    if market in ("03_gold/features", "04_platinum/predictions", "predictions"):
        return list(IDENTITY_DATE_COLUMNS)
    if _is_price_market_route(market):
        return ["ticker", "date"]
    logger.warning("unknown_market_key_ohlcv_default", market=market)
    return ["ticker", "date"]


_NEWS_QUALITY_MARKETS = frozenset({"us_news", "02_silver/news_sentiment"})
_SOCIAL_QUALITY_MARKETS = frozenset({"us_social_sentiment", "02_silver/social_sentiment"})
_MACRO_QUALITY_MARKETS = frozenset({"macro", "01_bronze/macro"})


def _price_source_market(dataset: str) -> str | None:
    """Resolve a price dataset route; non-price auxiliary tables return None."""
    route = dataset.removeprefix("01_bronze/market_data/")
    try:
        return canonical_market(route)
    except ValueError:
        return None


def _is_price_market_route(market: str) -> bool:
    """True for the five canonical price markets and their bronze path forms."""
    return market in PRICE_MARKETS or _price_source_market(market) is not None


def _is_corporate_actions(market: str) -> bool:
    """True for the corporate-actions event dataset (root or per-market path)."""
    return market in {"corporate_actions", "01_bronze/corporate_actions", "02_silver/corporate_actions"} or market.startswith(
        ("01_bronze/corporate_actions/", "02_silver/corporate_actions/")
    )


def _is_corporate_action_coverage(market: str) -> bool:
    """True for source-scoped action coverage proofs."""
    return market == "corporate_action_coverage" or market.startswith("02_silver/corporate_action_coverage/")


# Datasets with no cheap pointblank schema: article-type tables are enforced at
# the silver merge path (ingestion/bronze_silver.py), the rest rely on the
# column-presence `validate_schema` contract only.
_UNTYPED_QUALITY_MARKETS = frozenset(
    {
        "01_bronze/raw_articles",
        "rss_news",
        "reddit_posts",
        "stocktwits_messages",
        "us_earnings_transcripts",
        "sec_filings_fulltext",
        "02_silver/processed_articles",
        "02_silver/sec_extractions",
        "us_analyst_ratings",
        "02_silver/analyst_ratings",
        "us_sec_financials",
        "02_silver/sec_financials",
        "03_gold/features",
        "04_platinum/predictions",
    }
)


def _quality_data_type(market: str) -> str | None:
    """Map a dataset path to its pointblank schema type; None when no schema applies.

    W01/F20: social sentiment routes to the dedicated ``social`` schema — its
    rows share no columns with the news contract beyond identity keys.
    """
    if market in _NEWS_QUALITY_MARKETS:
        return "news"
    if market in _SOCIAL_QUALITY_MARKETS:
        return "social"
    if market in _MACRO_QUALITY_MARKETS:
        return "macro"
    if _is_corporate_action_coverage(market):
        return "corporate_action_coverage"
    if _is_corporate_actions(market):
        return "corporate_action"
    if market in _UNTYPED_QUALITY_MARKETS:
        return None
    return "price"


def upsert_dataset(
    df: FrameLike,
    market: str,
    trading_date: date,
    dry_run: bool = False,
    validate_quality: bool = True,
    skip_schema_validation: bool = False,
    partition_by: list[str] | None = None,
) -> bool:
    """Upsert a DataFrame into a date-partitioned Delta table.

    Args:
        df: Data to write.
        market: Dataset path or routable market identifier.
        trading_date: Trading date for the partition.
        dry_run: If True, short-circuit before any validation or persistence —
            a dry run never writes, not even profile artifacts.
        validate_quality: If True (the default, ADR-0007), run pointblank
            quality validation before writing; batches that fail their schema
            contract do not land. Pass False to opt out for devtools/backfills.
        skip_schema_validation: If True, bypass column-level schema checks.
        partition_by: Optional partition columns for tables that are not
            date-partitioned (e.g. ``["ex_date"]`` for corporate actions).
            None keeps the default ``["date"]`` layout.
    """
    df_polars = ensure_polars(df)

    if _is_corporate_actions(market) and "source_available_at" not in df_polars.columns:
        df_polars = df_polars.with_columns(pl.lit(None, dtype=CORPORATE_ACTION_SCHEMA["source_available_at"]).alias("source_available_at"))

    if market in ("03_gold/features", "04_platinum/predictions", "predictions"):
        try:
            df_polars = normalize_identity_frame(df_polars)
        except InstrumentIdentityError as exc:
            logger.error("shared_table_identity_invalid", market=market, error=str(exc))
            return False

    if df_polars.is_empty():
        logger.warning(
            "Empty DataFrame for %s on %s, skipping write",
            market,
            trading_date,
        )
        return False

    if dry_run:
        # Must precede validation: dry-run is side-effect free by construction
        # (no lake writes, no profile artifacts).
        logger.info("[DRY RUN] Would upsert %s rows to Delta table %s", len(df_polars), market)
        return True

    source_market = _price_source_market(market)
    if source_market is not None and {"open", "high", "low", "close", "volume"}.issubset(df_polars.columns):
        try:
            df_polars = prepare_source_identity_frame(df_polars, source_market)
        except (InstrumentIdentityError, ValueError, TypeError) as exc:
            logger.error("price_source_identity_invalid", market=source_market, error=str(exc))
            return False

    if not skip_schema_validation and not validate_schema(df_polars, market):
        logger.error("Schema validation failed, refusing to write", market=market)
        return False

    if validate_quality:
        data_type = _quality_data_type(market)
        if data_type is not None:
            from equity_lake.validation.pipeline import ValidationPipeline

            vp = ValidationPipeline()
            result = vp.validate(df_polars, data_type=data_type, name=f"{market}_{trading_date}")
            if not result.success:
                logger.error("Quality validation failed", market=market, errors=result.errors)
                return False
            if result.warnings:
                for w in result.warnings:
                    logger.warning("Quality warning", market=market, warning=w)
        else:
            logger.debug("Quality validation skipped: no pointblank schema registered", market=market)

    from equity_lake.storage.delta import DeltaError, merge_delta

    key_columns = list(IDENTITY_DATE_COLUMNS) if source_market is not None else _dedupe_key_columns(market)
    if _is_corporate_action_coverage(market):
        key_columns = ["market", "mic", "symbol", "source", "coverage_start", "coverage_end"]
    try:
        return merge_delta(df_polars, market, key_columns=key_columns, partition_by=partition_by)
    except DeltaError:
        return False


def validate_schema(df: FrameLike, market: str) -> bool:
    df_pl = ensure_polars(df)
    if market in ("macro", "01_bronze/macro"):
        required_cols = MACRO_COLUMNS
    elif market in ("us_news", "02_silver/news_sentiment"):
        required_cols = NEWS_COLUMNS
    elif market in ("us_social_sentiment", "02_silver/social_sentiment"):
        required_cols = SOCIAL_COLUMNS
    elif market in ("rss_news", "reddit_posts", "stocktwits_messages", "us_earnings_transcripts", "sec_filings_fulltext"):
        required_cols = ["article_id", "source_type", "source_url", "title", "date"]
    elif market in ("us_analyst_ratings", "02_silver/analyst_ratings"):
        required_cols = ["ticker", "date"]
    elif market in ("us_sec_financials", "02_silver/sec_financials"):
        required_cols = ["ticker", "date", "filing_type"]
    elif _is_corporate_action_coverage(market):
        required_cols = CORPORATE_ACTION_COVERAGE_COLUMNS
    elif _is_corporate_actions(market):
        required_cols = CORPORATE_ACTION_REQUIRED_COLUMNS
    elif market in ("01_bronze/raw_articles", "02_silver/processed_articles"):
        required_cols = ["article_id", "date"]
    elif market in ("03_gold/features",):
        required_cols = [
            *INSTRUMENT_IDENTITY_COLUMNS,
            "identity_schema_version",
            "feature_schema_version",
            "price_adjustment_version",
            "availability_schema_version",
            "action_coverage",
            "action_source_age_days",
            *FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
            "ticker",
            "date",
        ]
    elif market in ("predictions", "04_platinum/predictions"):
        required_cols = [*INSTRUMENT_IDENTITY_COLUMNS, "identity_schema_version", "ticker", "date", "direction", "probability"]
    else:
        if not _is_price_market_route(market):
            logger.warning("unknown_market_key_ohlcv_default", market=market)
        required_cols = ["ticker", "date", "open", "high", "low", "close", "volume"]

    if market in ("03_gold/features", "predictions", "04_platinum/predictions"):
        try:
            normalize_identity_frame(df_pl)
        except InstrumentIdentityError as exc:
            logger.error("%s: invalid canonical instrument identity: %s", market, exc)
            return False
        versions = df_pl["identity_schema_version"].drop_nulls().unique().to_list() if "identity_schema_version" in df_pl.columns else []
        if versions != [IDENTITY_SCHEMA_VERSION] or df_pl["identity_schema_version"].null_count() > 0:
            logger.error("%s: unsupported or unknown identity schema version: %s", market, versions)
            return False
        if market == "03_gold/features":
            feature_versions = df_pl["feature_schema_version"].drop_nulls().unique().to_list() if "feature_schema_version" in df_pl.columns else []
            adjustment_versions = (
                df_pl["price_adjustment_version"].drop_nulls().unique().to_list() if "price_adjustment_version" in df_pl.columns else []
            )
            availability_versions = (
                df_pl["availability_schema_version"].drop_nulls().unique().to_list() if "availability_schema_version" in df_pl.columns else []
            )
            coverage_values = df_pl["action_coverage"].drop_nulls().unique().to_list() if "action_coverage" in df_pl.columns else []
            if feature_versions != [FEATURE_SCHEMA_VERSION] or adjustment_versions != [PRICE_ADJUSTMENT_VERSION]:
                logger.error("%s: unsupported feature/price adjustment contract", market)
                return False
            if (
                availability_versions != [AVAILABILITY_SCHEMA_VERSION]
                or "action_coverage" not in df_pl.columns
                or df_pl["action_coverage"].null_count() > 0
                or coverage_values != [1]
                or "action_source_age_days" not in df_pl.columns
                or df_pl["action_source_age_days"].null_count() > 0
            ):
                logger.error("%s: unsupported feature availability contract", market)
                return False
            try:
                for row in df_pl.to_dicts():
                    validate_feature_action_coverage(row)
            except AvailabilityContractError as exc:
                logger.error("%s: invalid source action-coverage proof: %s", market, exc)
                return False

    missing_cols = set(required_cols) - set(df_pl.columns)
    if missing_cols:
        logger.error("%s: Missing required columns: %s", market, missing_cols)
        return False

    null_counts = df_pl.null_count().row(0, named=True)
    for col in required_cols:
        if col in df_pl.columns and col in null_counts and null_counts[col] == df_pl.height:
            logger.error("%s: Required column '%s' is all null", market, col)
            return False

    return True


__all__ = [
    "validate_schema",
    "upsert_dataset",
]
