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
) -> None:
    start_date = trading_date - timedelta(days=120)
    backfill_date_range(
        start_date=start_date,
        end_date=trading_date,
        markets=markets,
        ticker_config=ticker_config,
        dry_run=False,
    )


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

    if not skip_ingestion:
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
        results["ingestion"] = ingestion_results
        if not all(ingestion_results.values()):
            logger.warning("ingestion_partial_failure", results=ingestion_results)

        unstructured_markets = {"rss_news", "reddit_posts", "stocktwits_messages"}
        if any(m in markets for m in unstructured_markets) and not dry_run:
            logger.info("processing_bronze_to_silver", trading_date=str(trading_date))
            try:
                from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

                silver_success = process_bronze_to_silver(trading_date)
                results["bronze_to_silver"] = silver_success
                if not silver_success:
                    logger.warning("bronze_to_silver_skipped_or_failed")
            except Exception as exc:
                logger.error("bronze_to_silver_failed", error=str(exc))
                results["bronze_to_silver"] = False

    if not skip_features:
        use_enriched = results.get("bronze_to_silver", False)
        try:
            features_df = run_feature_job(
                tickers=tickers,
                output_start_date=trading_date,
                output_end_date=trading_date,
                compute_target=True,
                include_enriched_sentiment=use_enriched,
            )
            feature_output_tickers = sorted(features_df["ticker"].drop_nulls().unique().to_list())
            results["features"] = {"success": True, "rows": len(features_df)}
        except Exception as exc:
            if str(exc) == "No features generated":
                logger.warning("feature_pipeline_missing_history", tickers=tickers, markets=markets)
                _backfill_feature_history(trading_date, tickers, markets, ticker_config)
                try:
                    features_df = run_feature_job(
                        tickers=tickers,
                        output_start_date=trading_date,
                        output_end_date=trading_date,
                        compute_target=True,
                        include_enriched_sentiment=use_enriched,
                    )
                    feature_output_tickers = sorted(features_df["ticker"].drop_nulls().unique().to_list())
                    results["features"] = {"success": True, "rows": len(features_df)}
                except Exception as retry_exc:
                    logger.error("feature_pipeline_failed", error=str(retry_exc))
                    results["features"] = {"success": False, "error": str(retry_exc)}
            else:
                logger.error("feature_pipeline_failed", error=str(exc))
                results["features"] = {"success": False, "error": str(exc)}

    if not skip_ml:
        if not results.get("features", {}).get("success", skip_features):
            logger.warning("ml_skipped_due_to_feature_failure")
            results["ml"] = {"success": False, "skipped": True, "reason": "feature stage failed"}
            logger.info("pipeline_completed", stages=len(results))
            return results
        try:
            all_success, ml_results = run_prediction_job(
                trading_date=trading_date,
                tickers=feature_output_tickers,
            )
            results["ml"] = {"success": all_success, "results": ml_results}
        except Exception as exc:
            logger.warning("ml_inference_failed", error=str(exc))
            results["ml"] = {"success": False, "error": str(exc)}

    logger.info("pipeline_completed", stages=len(results))
    return results
