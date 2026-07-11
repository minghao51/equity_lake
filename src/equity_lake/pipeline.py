"""Hamilton driver builder and EOD pipeline executor."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import structlog

from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.features import run_feature_job
from equity_lake.ingestion.backfill import backfill_date_range
from equity_lake.ingestion.orchestrator import run_daily_ingestion
from equity_lake.ml import run_prediction_job

logger = structlog.get_logger()


def _backfill_feature_history(
    trading_date: date,
    tickers: list[str],
    markets: list[str],
    ticker_config: TickerConfig,
    dry_run: bool = False,
    explicit_tickers: list[str] | None = None,
) -> int:
    start_date = trading_date - timedelta(days=120)
    return backfill_date_range(
        start_date=start_date,
        end_date=trading_date,
        markets=markets,
        ticker_config=ticker_config,
        dry_run=dry_run,
        explicit_tickers=explicit_tickers,
    )


_REQUIRED_PRICE_MARKETS = {"us", "cn", "hk_sg", "jpx", "krx"}
_OPTIONAL_ENRICHMENT_MARKETS = {
    "macro",
    "us_news",
    "us_social_sentiment",
    "rss_news",
    "reddit_posts",
    "stocktwits_messages",
    "us_earnings_transcripts",
    "us_analyst_ratings",
    "sec_filings_fulltext",
    "us_sec_financials",
}


def _stage(success: bool, *, skipped: bool = False, reason: str | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"success": success}
    if skipped:
        result["skipped"] = True
    if reason is not None:
        result["reason"] = reason
    result.update(extra)
    return result


def execute_eod_pipeline(
    trading_date: date,
    markets: list[str] | None = None,
    tickers: list[str] | None = None,
    dry_run: bool = False,
    skip_ingestion: bool = False,
    skip_features: bool = False,
    skip_ml: bool = False,
    ticker_config: Any = None,
    filters: dict[str, Any] | None = None,
    explicit_tickers: list[str] | None = None,
    allow_history_backfill: bool = False,
) -> dict[str, Any]:
    """Execute the full EOD pipeline: ingestion -> features -> ML.

    This replaces PipelineOrchestrator.run_pipeline().
    """
    settings = get_settings()
    ticker_config = ticker_config or TickerConfig()
    markets = markets or list(settings.ingestion.default_markets)
    tickers = tickers or ticker_config.get_tickers_for_market("us", active_only=True)[:10]

    results: dict[str, Any] = {}
    feature_output_tickers = tickers

    logger.info("pipeline_started", date=str(trading_date), markets=markets, tickers=len(tickers), dry_run=dry_run)

    ingestion_market_results: dict[str, bool] = {}
    if dry_run:
        results["ingestion"] = _stage(True, skipped=True, reason="dry_run", markets={})
    elif skip_ingestion:
        results["ingestion"] = _stage(True, skipped=True, reason="skip_ingestion")
    elif not skip_ingestion:
        ingestion_results = run_daily_ingestion(
            trading_date=trading_date,
            markets=markets,
            dry_run=dry_run,
            parallel=True,
            ticker_config=ticker_config,
            filters=filters,
            explicit_tickers=explicit_tickers,
            skip_existing=True,
        )
        ingestion_market_results = ingestion_results
        required_failures = sorted(market for market in markets if market in _REQUIRED_PRICE_MARKETS and not ingestion_results.get(market, False))
        optional_failures = sorted(
            market for market in markets if market in _OPTIONAL_ENRICHMENT_MARKETS and not ingestion_results.get(market, False)
        )
        results["ingestion"] = _stage(
            not required_failures,
            markets=ingestion_results,
            required_failures=required_failures,
            optional_failures=optional_failures,
            partial=bool(optional_failures),
        )
        if not all(ingestion_results.values()):
            logger.warning("ingestion_partial_failure", results=ingestion_results)

        unstructured_markets = {"rss_news", "reddit_posts", "stocktwits_messages", "us_earnings_transcripts"}
        sec_markets = {"sec_filings_fulltext"}

        if any(m in markets for m in unstructured_markets):
            logger.info("processing_bronze_to_silver", trading_date=str(trading_date))
            try:
                from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

                silver_success = process_bronze_to_silver(trading_date)
                results["bronze_to_silver"] = _stage(silver_success, reason=None if silver_success else "optional enrichment unavailable")
                if not silver_success:
                    logger.warning("bronze_to_silver_skipped_or_failed")
            except Exception as exc:
                logger.error("bronze_to_silver_failed", error=str(exc))
                results["bronze_to_silver"] = _stage(False, reason="optional enrichment unavailable", error=str(exc))

        if any(m in markets for m in sec_markets):
            logger.info("processing_sec_bronze_to_silver", trading_date=str(trading_date))
            try:
                from equity_lake.ingestion.sec_processor import process_sec_bronze_to_silver

                sec_success = process_sec_bronze_to_silver(trading_date)
                results["sec_to_silver"] = _stage(sec_success, reason=None if sec_success else "optional enrichment unavailable")
                if not sec_success:
                    logger.warning("sec_to_silver_skipped_or_failed")
            except Exception as exc:
                logger.error("sec_to_silver_failed", error=str(exc))
                results["sec_to_silver"] = _stage(False, reason="optional enrichment unavailable", error=str(exc))

    if dry_run:
        results["features"] = _stage(True, skipped=True, reason="dry_run")
    elif skip_features:
        results["features"] = _stage(True, skipped=True, reason="skip_features")
    elif (
        not skip_ingestion
        and any(m in _REQUIRED_PRICE_MARKETS for m in markets)
        and any(not ingestion_market_results.get(m, False) for m in markets if m in _REQUIRED_PRICE_MARKETS)
    ):
        failed_markets = sorted(m for m in markets if m in _REQUIRED_PRICE_MARKETS and not ingestion_market_results.get(m, False))
        results["features"] = _stage(
            False,
            reason="required price source failed",
            error=f"Required price source failed for: {', '.join(failed_markets)}",
        )
        logger.error("features_blocked_required_source_failure", markets=failed_markets)
    else:
        use_enriched = results.get("bronze_to_silver", {}).get("success", False)
        use_analyst = "us_analyst_ratings" in markets
        use_sec = results.get("sec_to_silver", {}).get("success", False)
        try:
            features_df = run_feature_job(
                tickers=tickers,
                output_start_date=trading_date,
                output_end_date=trading_date,
                compute_target=True,
                include_enriched_sentiment=use_enriched,
                include_analyst_ratings=use_analyst,
                include_sec_features=use_sec,
            )
            feature_output_tickers = sorted(features_df["ticker"].drop_nulls().unique().to_list())
            results["features"] = _stage(True, rows=len(features_df))
        except Exception as exc:
            if str(exc) == "No features generated":
                logger.warning("feature_pipeline_missing_history", tickers=tickers, markets=markets)
                backfill_ok = False
                if not allow_history_backfill:
                    results["features"] = _stage(
                        False,
                        reason="history_backfill_not_authorized",
                        error=("Feature history is missing. Re-run with --allow-history-backfill to authorize the 120-day recovery."),
                    )
                    logger.error("feature_history_backfill_not_authorized", markets=markets, tickers=tickers)
                else:
                    price_markets = sorted(m for m in markets if m in _REQUIRED_PRICE_MARKETS)
                    start_date = trading_date - timedelta(days=120)
                    logger.warning(
                        "feature_history_backfill_authorized",
                        start_date=str(start_date),
                        end_date=str(trading_date),
                        markets=price_markets,
                        ticker_count=len(tickers),
                        explicit_tickers=tickers,
                        dry_run=dry_run,
                    )
                    try:
                        _backfill_feature_history(
                            trading_date,
                            tickers,
                            price_markets,
                            ticker_config,
                            dry_run=dry_run,
                            explicit_tickers=tickers,
                        )
                        backfill_ok = True
                    except Exception as backfill_exc:
                        logger.error("feature_history_backfill_failed", error=str(backfill_exc))
                        results["features"] = _stage(False, reason="history_backfill_failed", error=str(backfill_exc))
                try:
                    if allow_history_backfill and backfill_ok:
                        features_df = run_feature_job(
                            tickers=tickers,
                            output_start_date=trading_date,
                            output_end_date=trading_date,
                            compute_target=True,
                            include_enriched_sentiment=use_enriched,
                            include_analyst_ratings=use_analyst,
                            include_sec_features=use_sec,
                        )
                        feature_output_tickers = sorted(features_df["ticker"].drop_nulls().unique().to_list())
                        results["features"] = _stage(True, rows=len(features_df))
                except Exception as retry_exc:
                    logger.error("feature_pipeline_failed", error=str(retry_exc))
                    results["features"] = _stage(False, error=str(retry_exc))
            else:
                logger.error("feature_pipeline_failed", error=str(exc))
                results["features"] = _stage(False, error=str(exc))

    if dry_run:
        results["ml"] = _stage(True, skipped=True, reason="dry_run")
    elif skip_ml:
        results["ml"] = _stage(True, skipped=True, reason="skip_ml")
    else:
        if not results.get("features", {}).get("success", skip_features):
            logger.warning("ml_skipped_due_to_feature_failure")
            results["ml"] = _stage(False, skipped=True, reason="feature stage failed")
            logger.info("pipeline_completed", stages=len(results))
            return results
        try:
            all_success, ml_results = run_prediction_job(
                trading_date=trading_date,
                tickers=feature_output_tickers,
            )
            results["ml"] = _stage(all_success, results=ml_results)
        except Exception as exc:
            logger.warning("ml_inference_failed", error=str(exc))
            results["ml"] = _stage(False, error=str(exc))

    logger.info("pipeline_completed", stages=len(results))
    return results
