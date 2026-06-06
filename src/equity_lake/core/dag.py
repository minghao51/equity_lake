"""Hamilton driver builder and EOD pipeline executor."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import structlog

from equity_lake.backfill_data import backfill_cn, backfill_yfinance
from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.ingestion.orchestrator import run_daily_ingestion
from equity_lake.pipelines.features import run_feature_pipeline
from equity_lake.pipelines.ml import run_ml_inference

logger = structlog.get_logger()


def _market_tickers(ticker_config: TickerConfig, tickers: list[str], market: str) -> list[str]:
    """Filter requested tickers down to a configured market."""
    if market == "hk_sg":
        return [
            ticker
            for ticker in tickers
            if ticker_config.get_ticker_metadata(ticker, market="hk") is not None
            or ticker_config.get_ticker_metadata(ticker, market="sg") is not None
        ]
    return [ticker for ticker in tickers if ticker_config.get_ticker_metadata(ticker, market=market) is not None]


def _backfill_feature_history(
    trading_date: date,
    tickers: list[str],
    markets: list[str],
    ticker_config: TickerConfig,
) -> None:
    """Fetch enough lookback history for feature generation and retry once."""
    start_date = trading_date - timedelta(days=120)
    cn_tickers = _market_tickers(ticker_config, tickers, "cn") if "cn" in markets else []
    hk_sg_tickers = _market_tickers(ticker_config, tickers, "hk_sg") if "hk_sg" in markets else []
    us_tickers = _market_tickers(ticker_config, tickers, "us") if "us" in markets else []
    assigned = set(cn_tickers) | set(hk_sg_tickers) | set(us_tickers)
    unmatched_tickers = [ticker for ticker in tickers if ticker not in assigned]

    if "us" in markets:
        us_tickers = list(dict.fromkeys([*us_tickers, *unmatched_tickers]))
        if us_tickers:
            backfill_yfinance(us_tickers, "us_equity", start_date, trading_date, dry_run=False)

    if "cn" in markets and cn_tickers:
        backfill_cn(cn_tickers, start_date, trading_date, dry_run=False)

    if "hk_sg" in markets and hk_sg_tickers:
        backfill_yfinance(hk_sg_tickers, "hk_sg_equity", start_date, trading_date, dry_run=False)


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

    if not skip_features:
        try:
            features_df = run_feature_pipeline(
                tickers=tickers,
                output_start_date=trading_date,
                output_end_date=trading_date,
                compute_target=True,
            )
            feature_output_tickers = sorted(features_df["ticker"].dropna().unique().tolist())
            results["features"] = {"success": True, "rows": len(features_df)}
        except Exception as exc:
            if str(exc) == "No features generated":
                logger.warning("feature_pipeline_missing_history", tickers=tickers, markets=markets)
                _backfill_feature_history(trading_date, tickers, markets, ticker_config)
                try:
                    features_df = run_feature_pipeline(
                        tickers=tickers,
                        output_start_date=trading_date,
                        output_end_date=trading_date,
                        compute_target=True,
                    )
                    feature_output_tickers = sorted(features_df["ticker"].dropna().unique().tolist())
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
            all_success, ml_results = run_ml_inference(
                trading_date=trading_date,
                tickers=feature_output_tickers,
            )
            results["ml"] = {"success": all_success, "results": ml_results}
        except Exception as exc:
            logger.warning("ml_inference_failed", error=str(exc))
            results["ml"] = {"success": False, "error": str(exc)}

    logger.info("pipeline_completed", stages=len(results))
    return results
