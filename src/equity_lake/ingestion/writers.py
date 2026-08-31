"""Writer helpers for ingestion.

All writes go through the Delta Lake storage layer (ACID transactions,
merge/upsert, time-travel). The canonical writer is ``upsert_dataset``.
"""

from datetime import date

import structlog

from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.core.schemas import MACRO_COLUMNS, NEWS_COLUMNS, SOCIAL_COLUMNS

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
    if market in ("04_platinum/predictions", "predictions"):
        return ["ticker", "date"]
    return ["ticker", "date"]


_NEWS_QUALITY_MARKETS = frozenset({"us_news", "us_social_sentiment", "02_silver/news_sentiment", "02_silver/social_sentiment"})
_MACRO_QUALITY_MARKETS = frozenset({"macro", "01_bronze/macro"})
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
    """Map a dataset path to its pointblank schema type; None when no schema applies."""
    if market in _NEWS_QUALITY_MARKETS:
        return "news"
    if market in _MACRO_QUALITY_MARKETS:
        return "macro"
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
    """
    df_polars = ensure_polars(df)

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

    key_columns = _dedupe_key_columns(market)
    try:
        return merge_delta(df_polars, market, key_columns=key_columns)
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
    elif market in ("01_bronze/raw_articles", "02_silver/processed_articles"):
        required_cols = ["article_id", "date"]
    elif market in ("predictions", "04_platinum/predictions"):
        required_cols = ["ticker", "date", "direction", "probability"]
    else:
        required_cols = ["ticker", "date", "open", "high", "low", "close", "volume"]

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
