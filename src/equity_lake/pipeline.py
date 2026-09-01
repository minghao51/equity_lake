"""Hamilton driver builder and EOD pipeline executor."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import structlog

from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.core.paths import LONG_TO_SHORT
from equity_lake.features import NoFeatureHistoryError, run_feature_job
from equity_lake.ingestion.backfill import backfill_date_range
from equity_lake.ingestion.orchestrator import run_daily_ingestion
from equity_lake.ingestion.types import (
    OPTIONAL_ENRICHMENT_MARKETS,
    REQUIRED_PRICE_MARKETS,
    SEC_FILINGS_MARKETS,
    UNSTRUCTURED_MARKETS,
    SourceOutcome,
    SourceStatus,
    normalize_markets,
)
from equity_lake.ml import run_prediction_job

logger = structlog.get_logger()

HISTORY_BACKFILL_WINDOW_DAYS = 120


def _backfill_feature_history(
    trading_date: date,
    tickers: list[str],
    markets: list[str],
    ticker_config: TickerConfig,
    dry_run: bool = False,
    explicit_tickers: list[str] | None = None,
) -> int:
    start_date = trading_date - timedelta(days=HISTORY_BACKFILL_WINDOW_DAYS)
    return backfill_date_range(
        start_date=start_date,
        end_date=trading_date,
        markets=markets,
        ticker_config=ticker_config,
        dry_run=dry_run,
        explicit_tickers=explicit_tickers if explicit_tickers is not None else tickers,
    )


def _market_succeeded(results: dict[str, SourceOutcome], market: str) -> bool:
    """Treat a missing key as FAILED (the historical ``.get(m, False)`` semantics)."""
    return results.get(market, SourceOutcome(SourceStatus.FAILED)).succeeded


def _stage(success: bool, *, skipped: bool = False, reason: str | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"success": success}
    if skipped:
        result["skipped"] = True
    if reason is not None:
        result["reason"] = reason
    result.update(extra)
    return result


def _run_ingestion_stage(
    trading_date: date,
    markets: list[str],
    dry_run: bool,
    ticker_config: TickerConfig,
    filters: dict[str, Any] | None,
    explicit_tickers: list[str] | None,
) -> tuple[dict[str, Any], dict[str, SourceOutcome]]:
    """Run ingestion and optional bronze-to-silver processing.

    Returns ``(stage_result, market_results)``.
    """
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
    required_failures = sorted(m for m in markets if m in REQUIRED_PRICE_MARKETS and not _market_succeeded(ingestion_results, m))
    optional_failures = sorted(m for m in markets if m in OPTIONAL_ENRICHMENT_MARKETS and not _market_succeeded(ingestion_results, m))
    markets_payload = {m: outcome.status.value for m, outcome in ingestion_results.items()}
    stage = _stage(
        not required_failures,
        markets=markets_payload,
        required_failures=required_failures,
        optional_failures=optional_failures,
        partial=bool(optional_failures),
    )
    if not all(o.succeeded for o in ingestion_results.values()):
        if required_failures:
            logger.error("ingestion_required_failure", results=markets_payload, required_failures=required_failures)
        else:
            logger.warning("ingestion_partial_failure", results=markets_payload)

    # Optional bronze-to-silver processing
    unstructured_markets = UNSTRUCTURED_MARKETS
    sec_markets = SEC_FILINGS_MARKETS

    if any(m in markets for m in unstructured_markets):
        stage["bronze_to_silver"] = _process_bronze_to_silver(trading_date)

    if any(m in markets for m in sec_markets):
        stage["sec_to_silver"] = _process_sec_to_silver(trading_date)

    return stage, ingestion_results


def _process_bronze_to_silver(trading_date: date) -> dict[str, Any]:
    """Run the bronze-to-silver unstructured content processor."""
    logger.info("processing_bronze_to_silver", trading_date=str(trading_date))
    try:
        from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

        success = process_bronze_to_silver(trading_date)
        if not success:
            logger.warning("bronze_to_silver_skipped_or_failed")
        return _stage(success, reason=None if success else "optional enrichment unavailable")
    except Exception as exc:
        logger.error("bronze_to_silver_failed", error=str(exc))
        return _stage(False, reason="optional enrichment unavailable", error=str(exc))


def _process_sec_to_silver(trading_date: date) -> dict[str, Any]:
    """Run the SEC bronze-to-silver processor."""
    logger.info("processing_sec_bronze_to_silver", trading_date=str(trading_date))
    try:
        from equity_lake.ingestion.sec_processor import process_sec_bronze_to_silver

        success = process_sec_bronze_to_silver(trading_date)
        if not success:
            logger.warning("sec_to_silver_skipped_or_failed")
        return _stage(success, reason=None if success else "optional enrichment unavailable")
    except Exception as exc:
        logger.error("sec_to_silver_failed", error=str(exc))
        return _stage(False, reason="optional enrichment unavailable", error=str(exc))


def _run_feature_stage(
    trading_date: date,
    tickers: list[str],
    markets: list[str],
    ticker_config: TickerConfig,
    dry_run: bool,
    allow_history_backfill: bool,
    explicit_tickers: list[str] | None,
    ingestion_results: dict[str, SourceOutcome],
    *,
    skip_ingestion: bool,
    use_enriched: bool,
    use_analyst: bool,
    use_sec: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Run feature computation with optional backfill.

    Returns ``(stage_result, output_tickers)``.
    """
    # Block features if required price sources failed
    if (
        not skip_ingestion
        and any(m in REQUIRED_PRICE_MARKETS for m in markets)
        and any(not _market_succeeded(ingestion_results, m) for m in markets if m in REQUIRED_PRICE_MARKETS)
    ):
        failed = sorted(m for m in markets if m in REQUIRED_PRICE_MARKETS and not _market_succeeded(ingestion_results, m))
        logger.error("features_blocked_required_source_failure", markets=failed)
        return _stage(False, reason="required price source failed", error=f"Required price source failed for: {', '.join(failed)}"), tickers

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
        return _stage(True, rows=len(features_df)), sorted(features_df["ticker"].drop_nulls().unique().to_list())
    except NoFeatureHistoryError:
        return _handle_missing_feature_history(
            trading_date,
            tickers,
            markets,
            ticker_config,
            dry_run,
            allow_history_backfill,
            explicit_tickers,
            use_enriched,
            use_analyst,
            use_sec,
        )
    except Exception as exc:
        logger.error("feature_pipeline_failed", error=str(exc))
        return _stage(False, error=str(exc)), tickers


def _handle_missing_feature_history(
    trading_date: date,
    tickers: list[str],
    markets: list[str],
    ticker_config: TickerConfig,
    dry_run: bool,
    allow_history_backfill: bool,
    explicit_tickers: list[str] | None,
    use_enriched: bool,
    use_analyst: bool,
    use_sec: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Handle NoFeatureHistoryError with optional backfill."""
    logger.warning("feature_pipeline_missing_history", tickers=tickers, markets=markets)

    if not allow_history_backfill:
        logger.error("feature_history_backfill_not_authorized", markets=markets, tickers=tickers)
        return (
            _stage(
                False,
                reason="history_backfill_not_authorized",
                error="Feature history is missing. Re-run with --allow-history-backfill to authorize the 120-day recovery.",
            ),
            tickers,
        )

    price_markets = sorted(m for m in markets if m in REQUIRED_PRICE_MARKETS)
    start_date = trading_date - timedelta(days=HISTORY_BACKFILL_WINDOW_DAYS)
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
        _backfill_feature_history(trading_date, tickers, price_markets, ticker_config, dry_run=dry_run, explicit_tickers=explicit_tickers)
    except Exception as backfill_exc:
        logger.error("feature_history_backfill_failed", error=str(backfill_exc))
        return _stage(False, reason="history_backfill_failed", error=str(backfill_exc)), tickers

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
        return _stage(True, rows=len(features_df)), sorted(features_df["ticker"].drop_nulls().unique().to_list())
    except Exception as retry_exc:
        logger.error("feature_pipeline_failed", error=str(retry_exc))
        return _stage(False, error=str(retry_exc)), tickers


def _run_ml_stage(trading_date: date, tickers: list[str]) -> dict[str, Any]:
    """Run ML inference."""
    try:
        all_success, ml_results = run_prediction_job(trading_date=trading_date, tickers=tickers)
        return _stage(all_success, results=ml_results)
    except Exception as exc:
        logger.warning("ml_inference_failed", error=str(exc))
        return _stage(False, error=str(exc))


def execute_eod_pipeline(
    trading_date: date,
    markets: list[str] | None = None,
    tickers: list[str] | None = None,
    dry_run: bool = False,
    skip_ingestion: bool = False,
    skip_features: bool = False,
    skip_ml: bool = False,
    ticker_config: TickerConfig | None = None,
    filters: dict[str, Any] | None = None,
    explicit_tickers: list[str] | None = None,
    allow_history_backfill: bool = False,
) -> dict[str, Any]:
    """Execute the full EOD pipeline: ingestion -> features -> ML."""
    settings = get_settings()
    ticker_config = ticker_config or TickerConfig()
    # ADR-0010: canonicalize short price-market aliases to long keys once, at
    # the pipeline boundary. Unknown keys raise here (loud failure).
    markets = normalize_markets(markets) if markets is not None else list(settings.ingestion.default_markets)
    # Ticker-config sections still use the short keys (config/tickers.yaml is a
    # separate vocabulary, out of ADR-0010 scope) — bridge via the registry alias.
    tickers = tickers or [t for m in markets for t in ticker_config.get_tickers_for_market(LONG_TO_SHORT.get(m, m), active_only=True)][:10]

    results: dict[str, Any] = {}
    feature_output_tickers = tickers

    logger.info("pipeline_started", date=str(trading_date), markets=markets, tickers=len(tickers), dry_run=dry_run)

    # --- Stage 1: Ingestion ---
    ingestion_market_results: dict[str, SourceOutcome] = {}
    if dry_run:
        results["ingestion"] = _stage(True, skipped=True, reason="dry_run", markets={})
    elif skip_ingestion:
        results["ingestion"] = _stage(True, skipped=True, reason="skip_ingestion")
    else:
        ingestion_stage, ingestion_market_results = _run_ingestion_stage(
            trading_date,
            markets,
            dry_run,
            ticker_config,
            filters,
            explicit_tickers,
        )
        results["ingestion"] = ingestion_stage

    # --- Stage 2: Features ---
    if dry_run:
        results["features"] = _stage(True, skipped=True, reason="dry_run")
    elif skip_features:
        results["features"] = _stage(True, skipped=True, reason="skip_features")
    else:
        ingestion_stage = results.get("ingestion", {})
        use_enriched = ingestion_stage.get("bronze_to_silver", {}).get("success", False)
        use_analyst = "us_analyst_ratings" in markets
        use_sec = ingestion_stage.get("sec_to_silver", {}).get("success", False)
        feature_stage, feature_output_tickers = _run_feature_stage(
            trading_date,
            tickers,
            markets,
            ticker_config,
            dry_run,
            allow_history_backfill,
            explicit_tickers,
            ingestion_market_results,
            skip_ingestion=skip_ingestion,
            use_enriched=use_enriched,
            use_analyst=use_analyst,
            use_sec=use_sec,
        )
        results["features"] = feature_stage

    # --- Stage 3: ML ---
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
        results["ml"] = _run_ml_stage(trading_date, feature_output_tickers)

    logger.info("pipeline_completed", stages=len(results))
    return results
