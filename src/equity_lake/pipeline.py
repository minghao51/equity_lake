"""EOD pipeline orchestrator (ingestion → bronze/silver → features → ML inference)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import structlog

from equity_lake.core.calendar import classify_market_sessions, is_instrument_trading_day
from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.core.instruments import InstrumentIdentityError, InstrumentKey, LegacyIdentityUnavailableError
from equity_lake.features import NoFeatureHistoryError, run_feature_job
from equity_lake.ingestion.backfill import BackfillResult, backfill_date_range
from equity_lake.ingestion.orchestrator import run_daily_ingestion
from equity_lake.ingestion.types import (
    OPTIONAL_ENRICHMENT_MARKETS,
    PRICE_MARKET_CONFIG_KEYS,
    REQUIRED_PRICE_MARKETS,
    SEC_FILINGS_MARKETS,
    UNSTRUCTURED_MARKETS,
    SourceOutcome,
    SourceStatus,
    normalize_markets,
    resolve_explicit_scope,
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
) -> BackfillResult:
    start_date = trading_date - timedelta(days=HISTORY_BACKFILL_WINDOW_DAYS)
    return backfill_date_range(
        start_date=start_date,
        end_date=trading_date,
        markets=markets,
        ticker_config=ticker_config,
        dry_run=dry_run,
        explicit_tickers=explicit_tickers if explicit_tickers is not None else tickers,
        authorized=True,
    )


def _market_succeeded(results: dict[str, SourceOutcome], market: str) -> bool:
    """Treat a missing key as FAILED (the historical ``.get(m, False)`` semantics)."""
    return results.get(market, SourceOutcome(SourceStatus.FAILED)).succeeded


def _configured_instruments(markets: Sequence[str], ticker_config: TickerConfig, *, active_only: bool = True) -> list[InstrumentKey]:
    """Resolve all configured instrument identities without collapsing symbols."""
    resolved: list[InstrumentKey] = []
    seen: set[tuple[str, str, str]] = set()
    for market in markets:
        for config_key in PRICE_MARKET_CONFIG_KEYS.get(market, ()):
            market_config = ticker_config.get_market_info(config_key)
            if market_config is None:
                continue
            for symbol in ticker_config.get_tickers_for_market(config_key, active_only=active_only):
                metadata = ticker_config.get_ticker_metadata(symbol, market=config_key)
                if metadata is None:
                    continue
                instrument = metadata.instrument_key(market)
                if instrument.tuple not in seen:
                    seen.add(instrument.tuple)
                    resolved.append(instrument)
    return resolved


def _resolve_configured_tickers(markets: list[str], ticker_config: TickerConfig) -> list[str]:
    """Compatibility symbol projection of the complete configured identity scope."""
    return [instrument.symbol for instrument in _configured_instruments(markets, ticker_config)]


def _resolve_explicit_instruments(
    markets: Sequence[str],
    explicit_tickers: Sequence[str] | None,
    ticker_config: TickerConfig,
    *,
    instruments: Sequence[InstrumentKey] | None = None,
) -> list[InstrumentKey]:
    """Resolve explicit selectors only when current config gives one exact identity.

    Bare ticker compatibility is deliberately fail-closed for unknown or
    ambiguous symbols. Callers can select a known identity directly with
    ``instruments=``; current configuration is not historical provenance.
    """
    price_markets = [market for market in markets if market in REQUIRED_PRICE_MARKETS]
    if instruments is not None:
        allowed = set(price_markets)
        resolved: dict[tuple[str, str, str], InstrumentKey] = {}
        for instrument in instruments:
            if instrument.market not in allowed:
                raise InstrumentIdentityError(f"Explicit instrument {instrument.display_key} is outside requested price markets {price_markets}")
            resolved[instrument.tuple] = instrument
        return list(resolved.values())

    if explicit_tickers is None:
        return _configured_instruments(price_markets, ticker_config)

    resolved_keys: list[InstrumentKey] = []
    seen: set[tuple[str, str, str]] = set()
    configured = _configured_instruments(price_markets, ticker_config, active_only=False)
    for symbol in dict.fromkeys(explicit_tickers):
        candidates = [instrument for instrument in configured if instrument.symbol == symbol]
        identities = {instrument.tuple: instrument for instrument in candidates}
        if not identities:
            raise LegacyIdentityUnavailableError(
                f"Explicit ticker {symbol!r} has no exact configured exchange identity; select an InstrumentKey instead"
            )
        if len(identities) != 1:
            choices = ", ".join(InstrumentKey(**dict(zip(("market", "mic", "symbol"), key, strict=True))).display_key for key in identities)
            raise InstrumentIdentityError(
                f"Explicit ticker {symbol!r} is ambiguous across configured instruments ({choices}); select InstrumentKey values"
            )
        instrument = next(iter(identities.values()))
        if instrument.tuple not in seen:
            seen.add(instrument.tuple)
            resolved_keys.append(instrument)
    return resolved_keys


def _scheduled_instruments(instruments: Sequence[InstrumentKey], trading_date: date) -> tuple[list[InstrumentKey], list[InstrumentKey]]:
    """Classify scope against each exact exchange calendar, not market unions."""
    scheduled: list[InstrumentKey] = []
    not_scheduled: list[InstrumentKey] = []
    for instrument in instruments:
        (scheduled if is_instrument_trading_day(instrument, trading_date) else not_scheduled).append(instrument)
    return scheduled, not_scheduled


def _feature_output_instruments(frame: Any, requested: Sequence[InstrumentKey]) -> list[InstrumentKey]:
    """Read output identities, allowing legacy ticker-only frames only when unique."""
    if {"market", "mic", "symbol"}.issubset(frame.columns):
        return [InstrumentKey(**row) for row in frame.select("market", "mic", "symbol").unique().sort(["market", "mic", "symbol"]).to_dicts()]
    if "ticker" not in frame.columns:
        return []
    counts: dict[str, int] = {}
    for instrument in requested:
        counts[instrument.symbol] = counts.get(instrument.symbol, 0) + 1
    output_symbols = set(frame["ticker"].drop_nulls().unique().to_list())
    return [instrument for instrument in requested if counts[instrument.symbol] == 1 and instrument.symbol in output_symbols]


def _identity_coverage(
    requested: Sequence[InstrumentKey],
    produced: Sequence[InstrumentKey],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Build legacy ticker and lossless identity coverage views."""
    produced_tuples = {instrument.tuple for instrument in produced}
    requested_symbol_counts: dict[str, int] = {}
    for instrument in requested:
        requested_symbol_counts[instrument.symbol] = requested_symbol_counts.get(instrument.symbol, 0) + 1
    phase1_skipped: dict[str, str] = diagnostics.get("phase1_skipped", {})
    missing_instruments = {
        instrument.display_key: phase1_skipped.get(instrument.display_key, phase1_skipped.get(instrument.symbol, "missing_output_bar"))
        for instrument in requested
        if instrument.tuple not in produced_tuples
    }
    missing_tickers = {
        instrument.symbol if requested_symbol_counts[instrument.symbol] == 1 else instrument.display_key: reason
        for instrument in requested
        if instrument.display_key in missing_instruments
        for reason in [missing_instruments[instrument.display_key]]
    }
    return {
        "requested": [instrument.symbol for instrument in requested],
        "produced": [instrument.symbol for instrument in produced],
        "missing": missing_tickers,
        "requested_instruments": [instrument.model_dump() for instrument in requested],
        "produced_instruments": [instrument.model_dump() for instrument in produced],
        "missing_instruments": missing_instruments,
    }


def _coverage_payload(requested: Sequence[InstrumentKey], diagnostics: dict[str, Any], produced: Sequence[InstrumentKey]) -> dict[str, Any]:
    """Build the requested/produced/missing identity coverage payload (F07)."""
    return _identity_coverage(requested, produced, diagnostics)


def _coverage_from_diagnostics(requested: Sequence[InstrumentKey], diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Coverage for failure paths, without fabricating computation diagnostics."""
    if "phase1_skipped" not in diagnostics:
        return _identity_coverage(requested, [], {}) | {"missing": {}, "missing_instruments": {}}
    produced = [InstrumentKey.model_validate(item) for item in diagnostics.get("produced_instruments", [])]
    if not produced:
        produced_symbols = set(diagnostics.get("produced", []))
        requested_symbol_counts: dict[str, int] = {}
        for instrument in requested:
            requested_symbol_counts[instrument.symbol] = requested_symbol_counts.get(instrument.symbol, 0) + 1
        produced = [
            instrument for instrument in requested if requested_symbol_counts[instrument.symbol] == 1 and instrument.symbol in produced_symbols
        ]
    coverage = _identity_coverage(requested, produced, diagnostics)
    if diagnostics.get("missing_instruments") is not None:
        coverage["missing_instruments"] = dict(diagnostics["missing_instruments"])
        coverage["missing"] = dict(diagnostics.get("missing", {}))
    return coverage


def _coverage_error(missing: dict[str, str]) -> str:
    return "missing requested instrument coverage: " + ", ".join(f"{identity}={reason}" for identity, reason in missing.items())


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
    *,
    instrument_scope: Sequence[InstrumentKey] | None = None,
    explicit_scope: bool = False,
) -> tuple[dict[str, Any], dict[str, SourceOutcome]]:
    """Run ingestion and optional bronze-to-silver processing.

    Returns ``(stage_result, market_results)``. Explicit instrument scope is
    routed one price market at a time so identical symbols in different markets
    never become ambiguous string selectors inside the legacy router API.
    """
    if explicit_scope and instrument_scope is not None:
        ingestion_results: dict[str, SourceOutcome] = {}
        for market in markets:
            if market not in REQUIRED_PRICE_MARKETS:
                continue
            market_symbols = list(dict.fromkeys(item.symbol for item in instrument_scope if item.market == market))
            if not market_symbols:
                continue
            ingestion_results.update(
                run_daily_ingestion(
                    trading_date=trading_date,
                    markets=[market],
                    dry_run=dry_run,
                    parallel=True,
                    ticker_config=ticker_config,
                    filters=filters,
                    explicit_tickers=market_symbols,
                    skip_existing=True,
                )
            )
        non_price_markets = [market for market in markets if market not in REQUIRED_PRICE_MARKETS]
        if non_price_markets:
            ingestion_results.update(
                run_daily_ingestion(
                    trading_date=trading_date,
                    markets=non_price_markets,
                    dry_run=dry_run,
                    parallel=True,
                    ticker_config=ticker_config,
                    filters=filters,
                    explicit_tickers=explicit_tickers,
                    skip_existing=True,
                )
            )
        price_scope_markets = list(dict.fromkeys(item.market for item in instrument_scope))
    else:
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
        price_scope_markets = list(dict.fromkeys(item.market for item in instrument_scope or ()))

    required_failures = sorted(m for m in price_scope_markets if not _market_succeeded(ingestion_results, m))
    not_scheduled = sorted(
        m for m in price_scope_markets if ingestion_results.get(m, SourceOutcome(SourceStatus.FAILED)).status is SourceStatus.NOT_SCHEDULED
    )
    optional_failures = sorted(
        m
        for m in markets
        if m in OPTIONAL_ENRICHMENT_MARKETS and ingestion_results.get(m, SourceOutcome(SourceStatus.FAILED)).status is SourceStatus.FAILED
    )
    markets_payload = {m: outcome.status.value for m, outcome in ingestion_results.items()}
    errors = {m: outcome.error for m, outcome in ingestion_results.items() if outcome.error}
    price_scope = {
        market: [instrument.symbol for instrument in (instrument_scope or ()) if instrument.market == market]
        for market in markets
        if market in REQUIRED_PRICE_MARKETS
    }
    non_price_markets = [market for market in markets if market not in REQUIRED_PRICE_MARKETS]
    resolved_scope = {
        **price_scope,
        **resolve_explicit_scope(non_price_markets, explicit_tickers, ticker_config),
    }
    stage = _stage(
        not required_failures,
        markets=markets_payload,
        required_failures=required_failures,
        optional_failures=optional_failures,
        not_scheduled=not_scheduled,
        partial=bool(optional_failures),
        errors=errors,
        ticker_scope={market: resolved_scope.get(market) for market in markets},
    )
    if not all(o.succeeded for o in ingestion_results.values()):
        if required_failures:
            logger.error("ingestion_required_failure", results=markets_payload, required_failures=required_failures)
        elif optional_failures:
            logger.warning("ingestion_partial_failure", results=markets_payload, optional_failures=optional_failures)
        else:
            # NO_DATA-only outcomes: visible in the status map, not a degradation.
            logger.info("ingestion_no_data", results=markets_payload)
    elif not_scheduled:
        logger.info("ingestion_not_scheduled", results=markets_payload, not_scheduled=not_scheduled)

    # Optional bronze-to-silver processing
    unstructured_markets = UNSTRUCTURED_MARKETS
    sec_markets = SEC_FILINGS_MARKETS

    source_instruments = list(
        dict.fromkeys(explicit_tickers if explicit_tickers is not None else ticker_config.get_tickers_for_market("us", active_only=True))
    )
    if any(m in markets for m in unstructured_markets):
        if source_instruments:
            from equity_lake.ingestion.replay import ReplayScope

            source_type_by_market = {
                "rss_news": "rss",
                "reddit_posts": "reddit",
                "stocktwits_messages": "stocktwits",
                "us_earnings_transcripts": "transcript",
            }
            source_stages: dict[str, dict[str, Any]] = {}
            for source_market in markets:
                source_type = source_type_by_market.get(source_market)
                if source_type is None:
                    continue
                article_scope = ReplayScope(
                    source=source_type,
                    trading_date=trading_date,
                    market="us_equity",
                    instruments=(*source_instruments, "__market_wide__"),
                )
                source_stages[source_market] = _process_bronze_to_silver(trading_date, article_scope)
            if len(source_stages) == 1:
                stage["bronze_to_silver"] = next(iter(source_stages.values()))
            else:
                skipped_sources = [result for result in source_stages.values() if result.get("skipped")]
                all_skipped = bool(source_stages) and len(skipped_sources) == len(source_stages)
                skip_reasons = {result.get("reason") for result in skipped_sources}
                processed_count = 0
                for result in source_stages.values():
                    source_count = result.get("processed")
                    if isinstance(source_count, int) and not isinstance(source_count, bool):
                        processed_count += source_count
                stage["bronze_to_silver"] = _stage(
                    all(result.get("success", False) for result in source_stages.values()),
                    skipped=all_skipped,
                    reason=next(iter(skip_reasons)) if all_skipped and len(skip_reasons) == 1 else None,
                    sources=source_stages,
                    processed=processed_count,
                )
        else:
            stage["bronze_to_silver"] = _stage(True, skipped=True, reason="no_instrument_scope")

    if any(m in markets for m in sec_markets):
        if source_instruments:
            from equity_lake.ingestion.replay import ReplayScope

            sec_scope = ReplayScope(
                source="sec_filing",
                trading_date=trading_date,
                market="us_equity",
                instruments=tuple(source_instruments),
            )
            stage["sec_to_silver"] = _process_sec_to_silver(trading_date, sec_scope)
        else:
            stage["sec_to_silver"] = _stage(True, skipped=True, reason="no_instrument_scope")

    return stage, ingestion_results


def _process_bronze_to_silver(trading_date: date, scope: Any) -> dict[str, Any]:
    """Run the bronze-to-silver unstructured content processor.

    W02/F25: a genuine no-work outcome (no bronze corpus, or everything already
    processed) is a planned skip, not a source failure; a processing or write
    failure is reported with its cause.
    """
    logger.info("processing_bronze_to_silver", trading_date=str(trading_date))
    diagnostics: dict[str, Any] = {}
    try:
        from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

        success = process_bronze_to_silver(trading_date, scope=scope, diagnostics=diagnostics)
    except Exception as exc:
        logger.error("bronze_to_silver_failed", error=str(exc))
        return _stage(False, reason="optional enrichment failed", error=str(exc))

    mode = diagnostics.get("mode")
    if mode == "no_bronze_data":
        return _stage(True, skipped=True, reason="no_bronze_work")
    if mode == "already_processed":
        return _stage(True, skipped=True, reason="already_processed")
    if mode == "paid_processing_disabled":
        return _stage(True, skipped=True, reason="paid_processing_disabled")
    if success:
        return _stage(True, processed=diagnostics.get("rows"))
    logger.warning("bronze_to_silver_skipped_or_failed", mode=mode)
    return _stage(False, reason="optional enrichment failed", error=diagnostics.get("error"))


def _process_sec_to_silver(trading_date: date, scope: Any) -> dict[str, Any]:
    """Run the SEC bronze-to-silver processor (same no-work/failure contract)."""
    logger.info("processing_sec_bronze_to_silver", trading_date=str(trading_date))
    diagnostics: dict[str, Any] = {}
    try:
        from equity_lake.ingestion.sec_processor import process_sec_bronze_to_silver

        success = process_sec_bronze_to_silver(trading_date, scope=scope, diagnostics=diagnostics)
    except Exception as exc:
        logger.error("sec_to_silver_failed", error=str(exc))
        return _stage(False, reason="optional enrichment failed", error=str(exc))

    mode = diagnostics.get("mode")
    if mode == "no_bronze_data":
        return _stage(True, skipped=True, reason="no_bronze_work")
    if mode == "already_processed":
        return _stage(True, skipped=True, reason="already_processed")
    if mode == "paid_processing_disabled":
        return _stage(True, skipped=True, reason="paid_processing_disabled")
    if success:
        return _stage(True, processed=diagnostics.get("rows"))
    logger.warning("sec_to_silver_skipped_or_failed", mode=mode)
    return _stage(False, reason="optional enrichment failed", error=diagnostics.get("error"))


def _run_feature_stage(
    trading_date: date,
    instruments: list[InstrumentKey],
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
) -> tuple[dict[str, Any], list[InstrumentKey]]:
    """Run feature computation with optional backfill.

    Returns the exact output instruments for identity-scoped ML inference.
    """
    tickers = [instrument.symbol for instrument in instruments]
    # Block features only for required price markets that own scheduled scope.
    if not skip_ingestion and any(not _market_succeeded(ingestion_results, market) for market in markets):
        failed = sorted(market for market in markets if not _market_succeeded(ingestion_results, market))
        logger.error("features_blocked_required_source_failure", markets=failed)
        return _stage(False, reason="required price source failed", error=f"Required price source failed for: {', '.join(failed)}"), instruments

    diagnostics: dict[str, Any] = {}
    try:
        features_df = run_feature_job(
            tickers=tickers,
            instruments=instruments,
            output_start_date=trading_date,
            output_end_date=trading_date,
            compute_target=True,
            include_enriched_sentiment=use_enriched,
            include_analyst_ratings=use_analyst,
            include_sec_features=use_sec,
            diagnostics=diagnostics,
        )
        produced = _feature_output_instruments(features_df, instruments)
        coverage = _coverage_payload(instruments, diagnostics, produced)
        missing: dict[str, str] = coverage["missing_instruments"]
        stage = _stage(not missing, rows=len(features_df), coverage=coverage)
        label_payload = diagnostics.get("label_finalization")
        if label_payload is not None:
            stage["labels"] = label_payload
        if missing:
            logger.error("feature_coverage_incomplete", missing=missing)
            stage["error"] = _coverage_error(missing)
        return stage, produced
    except NoFeatureHistoryError:
        return _handle_missing_feature_history(
            trading_date,
            instruments,
            markets,
            ticker_config,
            dry_run,
            allow_history_backfill,
            explicit_tickers,
            use_enriched,
            use_analyst,
            use_sec,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        logger.error("feature_pipeline_failed", error=str(exc))
        return _stage(False, error=str(exc), coverage=_coverage_from_diagnostics(instruments, diagnostics)), instruments


def _handle_missing_feature_history(
    trading_date: date,
    instruments: list[InstrumentKey],
    markets: list[str],
    ticker_config: TickerConfig,
    dry_run: bool,
    allow_history_backfill: bool,
    explicit_tickers: list[str] | None,
    use_enriched: bool,
    use_analyst: bool,
    use_sec: bool,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[InstrumentKey]]:
    """Handle missing feature history without dropping canonical identities."""
    tickers = [instrument.symbol for instrument in instruments]
    logger.warning("feature_pipeline_missing_history", tickers=tickers, markets=markets)
    diagnostics = diagnostics if diagnostics is not None else {}

    if not allow_history_backfill:
        logger.error("feature_history_backfill_not_authorized", markets=markets, tickers=tickers)
        coverage = _coverage_from_diagnostics(instruments, diagnostics)
        return (
            _stage(
                False,
                reason="history_backfill_not_authorized",
                error="Feature history is missing. Re-run with --allow-history-backfill to authorize the 120-day recovery.",
                coverage=coverage,
            ),
            instruments,
        )

    if len(tickers) != len(set(tickers)):
        error = "Identity-scoped history backfill cannot distinguish duplicate symbols across instruments; no backfill was run."
        return _stage(
            False,
            reason="identity_scoped_backfill_unavailable",
            error=error,
            coverage=_coverage_from_diagnostics(instruments, diagnostics),
        ), instruments

    price_markets = list(dict.fromkeys(instrument.market for instrument in instruments))
    start_date = trading_date - timedelta(days=HISTORY_BACKFILL_WINDOW_DAYS)
    logger.warning(
        "feature_history_backfill_authorized",
        start_date=str(start_date),
        end_date=str(trading_date),
        markets=price_markets,
        ticker_count=len(instruments),
        explicit_instruments=[instrument.model_dump() for instrument in instruments],
        dry_run=dry_run,
    )

    try:
        backfill_result = _backfill_feature_history(trading_date, tickers, price_markets, ticker_config, dry_run=dry_run, explicit_tickers=tickers)
    except Exception as backfill_exc:
        logger.error("feature_history_backfill_failed", error=str(backfill_exc))
        return _stage(
            False,
            reason="history_backfill_failed",
            error=str(backfill_exc),
            recovery={"authorized": True, "error": str(backfill_exc)},
            coverage=_coverage_from_diagnostics(instruments, diagnostics),
        ), instruments

    retry_diagnostics: dict[str, Any] = {}
    try:
        features_df = run_feature_job(
            tickers=tickers,
            instruments=instruments,
            output_start_date=trading_date,
            output_end_date=trading_date,
            compute_target=True,
            include_enriched_sentiment=use_enriched,
            include_analyst_ratings=use_analyst,
            include_sec_features=use_sec,
            diagnostics=retry_diagnostics,
        )
        produced = _feature_output_instruments(features_df, instruments)
        coverage = _coverage_payload(instruments, retry_diagnostics, produced)
        missing: dict[str, str] = coverage["missing_instruments"]
        stage = _stage(
            not missing,
            rows=len(features_df),
            history_backfilled_dates=backfill_result.total_dates,
            recovery=backfill_result.to_payload(),
            coverage=coverage,
        )
        label_payload = retry_diagnostics.get("label_finalization")
        if label_payload is not None:
            stage["labels"] = label_payload
        if missing:
            logger.error("feature_coverage_incomplete", missing=missing)
            stage["error"] = _coverage_error(missing)
        return stage, produced
    except Exception as retry_exc:
        logger.error("feature_pipeline_failed", error=str(retry_exc))
        return _stage(
            False,
            error=str(retry_exc),
            history_backfilled_dates=backfill_result.total_dates,
            recovery=backfill_result.to_payload(),
            coverage=_coverage_from_diagnostics(instruments, retry_diagnostics),
        ), instruments


def _run_ml_stage(trading_date: date, instruments: Sequence[InstrumentKey]) -> dict[str, Any]:
    """Run identity-scoped ML inference with candidates resolved at this caller boundary."""
    try:
        from equity_lake.ml.candidates import DEFAULT_BACKTEST_STRATEGY
        from equity_lake.signals.config import load_signal_config

        signal_config = load_signal_config()
        configured = signal_config.ml.get("candidate_strategies") or signal_config.backtest.get("strategies")
        candidate_strategies = (
            [strategy.model_dump() if hasattr(strategy, "model_dump") else dict(strategy) for strategy in configured]
            if configured
            else [dict(DEFAULT_BACKTEST_STRATEGY)]
        )
        all_success, ml_results = run_prediction_job(
            trading_date=trading_date,
            tickers=instruments,
            candidate_strategies=candidate_strategies,
        )
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
    instruments: Sequence[InstrumentKey] | None = None,
) -> dict[str, Any]:
    """Execute the full EOD pipeline: ingestion -> features -> ML."""
    settings = get_settings()
    ticker_config = ticker_config or TickerConfig()
    # ADR-0010: canonicalize short price-market aliases to long keys once, at
    # the pipeline boundary. Unknown keys raise here (loud failure).
    markets = normalize_markets(markets) if markets is not None else list(settings.ingestion.default_markets)
    # D04/F11: classify the requested price markets for the resolved plan date
    # once, before any work. A closed market is a planned skip (`not_scheduled`);
    # features and ML are planned only for the scheduled scope, so a closed
    # market never blocks an open one and required-market NO_DATA blocking (W02)
    # is confined to a genuinely empty scheduled session.
    requested_price_markets = [market for market in markets if market in REQUIRED_PRICE_MARKETS]
    scheduled_price_markets, not_scheduled_markets = classify_market_sessions(requested_price_markets, trading_date)
    # ``None`` means configured scope; explicit symbols remain a compatibility
    # selector only when exact current configuration resolves one identity.
    if tickers is not None and not tickers:
        raise ValueError("Explicit ticker scope is empty; omit tickers to use the configured scope. An explicit empty scope is never broadened.")
    if instruments is not None and not instruments:
        raise ValueError("Explicit instrument scope is empty; it is never broadened to the configured universe.")
    if tickers is not None and instruments is not None and sorted(tickers) != sorted(item.symbol for item in instruments):
        raise ValueError("tickers and instruments specify different scopes; pass one selector form")
    explicit_scope = tickers is not None or instruments is not None
    resolved_instruments = _resolve_explicit_instruments(markets, tickers, ticker_config, instruments=instruments)
    scheduled_instruments, not_scheduled_instruments = _scheduled_instruments(resolved_instruments, trading_date)
    feature_markets = list(dict.fromkeys(instrument.market for instrument in scheduled_instruments))
    ticker_symbols = [instrument.symbol for instrument in scheduled_instruments]

    results: dict[str, Any] = {}
    feature_output_instruments = scheduled_instruments
    # Enrichment-only requests have no price scope to compute features from;
    # they planned-skip features/ML rather than inventing default tickers or
    # failing on missing history (D02, documented CLI behavior change).
    enrichment_only = not any(m in REQUIRED_PRICE_MARKETS for m in markets)

    results["scope"] = {
        "trading_date": str(trading_date),
        "markets": list(markets),
        "explicit_tickers": explicit_scope,
        "tickers": list(ticker_symbols),
        "instruments": [instrument.model_dump() for instrument in scheduled_instruments],
        "requested_instruments": [instrument.model_dump() for instrument in resolved_instruments],
        "not_scheduled_instruments": [instrument.model_dump() for instrument in not_scheduled_instruments],
        "dry_run": dry_run,
        "enrichment_only": enrichment_only,
        "scheduled": list(scheduled_price_markets),
        "not_scheduled": list(not_scheduled_markets),
    }

    logger.info(
        "pipeline_started",
        date=str(trading_date),
        markets=markets,
        tickers=len(ticker_symbols),
        instruments=[instrument.display_key for instrument in scheduled_instruments],
        not_scheduled=not_scheduled_markets,
        dry_run=dry_run,
    )

    # --- Stage 1: Ingestion ---
    ingestion_market_results: dict[str, SourceOutcome] = {}
    if dry_run:
        results["ingestion"] = _stage(
            True,
            skipped=True,
            reason="dry_run",
            markets={},
            scheduled=list(scheduled_price_markets),
            not_scheduled=list(not_scheduled_markets),
        )
    elif skip_ingestion:
        results["ingestion"] = _stage(
            True,
            skipped=True,
            reason="skip_ingestion",
            scheduled=list(scheduled_price_markets),
            not_scheduled=list(not_scheduled_markets),
        )
    else:
        ingestion_stage, ingestion_market_results = _run_ingestion_stage(
            trading_date,
            markets,
            dry_run,
            ticker_config,
            filters,
            explicit_tickers,
            instrument_scope=scheduled_instruments,
            explicit_scope=explicit_scope,
        )
        results["ingestion"] = ingestion_stage

    # --- Stage 2: Features ---
    if dry_run:
        results["features"] = _stage(True, skipped=True, reason="dry_run")
    elif skip_features:
        results["features"] = _stage(True, skipped=True, reason="skip_features")
    elif enrichment_only:
        results["features"] = _stage(True, skipped=True, reason="enrichment_only_no_price_scope")
    elif not scheduled_instruments:
        results["features"] = _stage(True, skipped=True, reason="not_scheduled_scope")
    else:
        ingestion_stage = results.get("ingestion", {})
        use_enriched = ingestion_stage.get("bronze_to_silver", {}).get("success", False)
        use_analyst = "us_analyst_ratings" in markets
        use_sec = ingestion_stage.get("sec_to_silver", {}).get("success", False)
        feature_stage, feature_output_instruments = _run_feature_stage(
            trading_date,
            scheduled_instruments,
            feature_markets,
            ticker_config,
            dry_run,
            allow_history_backfill,
            ticker_symbols if explicit_scope else None,
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
    elif enrichment_only:
        results["ml"] = _stage(True, skipped=True, reason="enrichment_only_no_price_scope")
    elif not scheduled_instruments:
        results["ml"] = _stage(True, skipped=True, reason="not_scheduled_scope")
    else:
        if not results.get("features", {}).get("success", skip_features):
            logger.warning("ml_skipped_due_to_feature_failure")
            results["ml"] = _stage(False, skipped=True, reason="feature stage failed")
            logger.info("pipeline_completed", stages=len(results))
            return results
        results["ml"] = _run_ml_stage(trading_date, feature_output_instruments)

    logger.info("pipeline_completed", stages=len(results))
    return results
