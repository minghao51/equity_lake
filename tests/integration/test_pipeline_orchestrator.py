"""Tests for the EOD pipeline executor (pipeline.py)."""

from datetime import date

from equity_lake.core.config import TickerConfig
from equity_lake.core.dates import resolve_trading_date
from equity_lake.features import NoFeatureHistoryError
from equity_lake.ingestion.types import SourceOutcome, SourceStatus
from equity_lake.pipeline import _backfill_feature_history, execute_eod_pipeline

_WRITTEN = SourceOutcome(SourceStatus.WRITTEN)
_FAILED = SourceOutcome(SourceStatus.FAILED)


def _backfill_result(total: int = 1, **overrides):
    """Build a BackfillResult for tests that monkeypatch backfill_date_range."""
    from equity_lake.ingestion.backfill import BackfillResult

    fields = {
        "start_date": date(2023, 9, 4),
        "end_date": date(2024, 1, 2),
        "markets": ("us_equity",),
        "instruments": ("AAPL",),
        "authorized": True,
        "trading_dates": 1,
        "market_date_attempts": total,
        "succeeded": total,
        "failed": 0,
        "dates_recovered": 1 if total else 0,
        "per_market": {"us_equity": total},
    }
    fields.update(overrides)
    return BackfillResult(**fields)


def test_execute_eod_pipeline_dry_run_skips_writes_and_processors(monkeypatch):
    """Dry-run is a plan-only execution and never enters write-capable stages."""

    def fake_run_daily_ingestion(*, trading_date, markets, dry_run, parallel, ticker_config, filters, explicit_tickers, skip_existing):
        assert trading_date == date(2024, 1, 2)
        assert markets == ["us_equity", "cn_ashare"]
        assert dry_run is True
        return {"us_equity": _WRITTEN, "cn_ashare": _FAILED}

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", fake_run_daily_ingestion)
    monkeypatch.setattr(
        "equity_lake.pipeline.run_feature_job",
        lambda **_: (_ for _ in ()).throw(AssertionError("features called")),
    )
    monkeypatch.setattr(
        "equity_lake.pipeline.run_prediction_job",
        lambda **_: (_ for _ in ()).throw(AssertionError("ML called")),
    )
    monkeypatch.setattr(
        "equity_lake.pipeline.backfill_date_range",
        lambda **_: (_ for _ in ()).throw(AssertionError("backfill called")),
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "cn_ashare"],
        tickers=["AAPL"],
        dry_run=True,
        skip_features=True,
        skip_ml=True,
    )

    assert results["ingestion"] == {
        "success": True,
        "skipped": True,
        "reason": "dry_run",
        "markets": {},
        "scheduled": ["us_equity", "cn_ashare"],
        "not_scheduled": [],
    }
    assert results["features"] == {"success": True, "skipped": True, "reason": "dry_run"}
    assert results["ml"] == {"success": True, "skipped": True, "reason": "dry_run"}


def test_missing_history_requires_explicit_authorization(monkeypatch):
    """A missing warm-up window fails clearly without starting recovery."""

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: (_ for _ in ()).throw(NoFeatureHistoryError("No features generated")))
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **_: (_ for _ in ()).throw(AssertionError("backfill called")))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        skip_ingestion=True,
        skip_ml=True,
    )

    assert results["features"]["success"] is False
    assert results["features"]["reason"] == "history_backfill_not_authorized"


def test_authorized_history_recovery_is_scoped_and_forwards_dry_run(monkeypatch):
    """Authorized recovery receives the exact date, market, ticker, and mode."""

    import polars as pl

    feature_calls = iter([pl.DataFrame({"ticker": ["AAPL"]})])

    def run_feature_job(**kwargs):
        if not hasattr(run_feature_job, "called"):
            run_feature_job.called = True
            raise NoFeatureHistoryError("No features generated")
        return next(feature_calls)

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)
    backfill_calls = []
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **kwargs: backfill_calls.append(kwargs) or _backfill_result(1))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        dry_run=False,
        skip_ingestion=True,
        skip_ml=True,
        allow_history_backfill=True,
    )

    assert results["features"]["success"] is True
    assert results["features"]["history_backfilled_dates"] == 1
    # W03/F25: the structured recovery payload reports the frozen scope and counts.
    assert results["features"]["recovery"]["authorized"] is True
    assert results["features"]["recovery"]["succeeded"] == 1
    assert results["features"]["recovery"]["instruments"] == ["AAPL"]
    assert backfill_calls == [
        {
            "start_date": date(2023, 9, 4),
            "end_date": date(2024, 1, 2),
            "markets": ["us_equity"],
            "ticker_config": backfill_calls[0]["ticker_config"],
            "dry_run": False,
            "explicit_tickers": ["AAPL"],
            "authorized": True,
        }
    ]


def test_backfill_feature_history_scopes_tickers_only_callers(monkeypatch):
    """A tickers-only caller must not trigger an unscoped all-ticker backfill."""

    backfill_calls = []
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **kwargs: backfill_calls.append(kwargs) or _backfill_result(3))

    # Tickers-only caller: previously forwarded explicit_tickers=None → unscoped 120-day all-ticker backfill.
    written = _backfill_feature_history(date(2024, 1, 2), ["MSFT", "NVDA"], ["us_equity"], TickerConfig())
    assert written.total_dates == 3
    assert backfill_calls[0]["explicit_tickers"] == ["MSFT", "NVDA"]
    assert backfill_calls[0]["authorized"] is True

    # Explicit tickers still take precedence when provided.
    _backfill_feature_history(date(2024, 1, 2), ["MSFT"], ["us_equity"], TickerConfig(), explicit_tickers=["TSLA"])
    assert backfill_calls[1]["explicit_tickers"] == ["TSLA"]

    # An empty list is forwarded as [] (no fall-back to tickers) at this seam.
    # Downstream, run_daily_ingestion keeps it a list, but the router's
    # ``not explicit_tickers`` fallback treats it like an unscoped request, so []
    # must not be relied on as a scoping mechanism by callers.
    _backfill_feature_history(date(2024, 1, 2), ["MSFT"], ["us_equity"], TickerConfig(), explicit_tickers=[])
    assert backfill_calls[2]["explicit_tickers"] == []


def test_authorized_history_recovery_scopes_backfill_to_price_markets(monkeypatch):
    """Recovery only re-ingests required price markets, not enrichments."""

    backfill_calls = []
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **kwargs: backfill_calls.append(kwargs) or _backfill_result(1))
    monkeypatch.setattr(
        "equity_lake.pipeline.run_feature_job",
        lambda **_: (_ for _ in ()).throw(NoFeatureHistoryError("No features generated")),
    )

    execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "rss_news"],
        tickers=["AAPL"],
        skip_ingestion=True,
        skip_ml=True,
        allow_history_backfill=True,
    )

    assert backfill_calls[0]["markets"] == ["us_equity"]


def test_authorized_history_recovery_records_failure_when_backfill_raises(monkeypatch):
    """A backfill exception must not crash the pipeline; it records a failed stage."""

    def raising_backfill(**_):
        raise RuntimeError("network down")

    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", raising_backfill)
    feature_calls = []

    def run_feature_job(**kwargs):
        feature_calls.append(kwargs)
        raise NoFeatureHistoryError("No features generated")

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        skip_ingestion=True,
        skip_ml=True,
        allow_history_backfill=True,
    )

    assert results["features"]["success"] is False
    assert results["features"]["reason"] == "history_backfill_failed"
    assert "network down" in results["features"]["error"]
    assert len(feature_calls) == 1


def test_required_price_failure_blocks_features_and_ml(monkeypatch):
    """A required price failure prevents derived writes."""

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _FAILED})
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: (_ for _ in ()).throw(AssertionError("features called")))
    monkeypatch.setattr("equity_lake.pipeline.run_prediction_job", lambda **_: (_ for _ in ()).throw(AssertionError("ML called")))

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity"], tickers=["AAPL"])

    assert results["ingestion"]["success"] is False
    assert results["features"]["reason"] == "required price source failed"
    assert results["ml"]["skipped"] is True


def test_optional_ingestion_failure_keeps_core_features_eligible(monkeypatch):
    """Optional enrichment degradation does not block core feature generation."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN, "us_news": _FAILED})
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: pl.DataFrame({"ticker": ["AAPL"]}))

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity", "us_news"], tickers=["AAPL"], skip_ml=True)

    assert results["ingestion"]["success"] is True
    assert results["ingestion"]["partial"] is True
    assert results["features"]["success"] is True


def test_idempotent_rerun_does_not_block_features(monkeypatch):
    """Regression test (P0): an idempotent rerun (all markets already present)
    must not classify required price markets as failures.

    Previously ``run_daily_ingestion`` dropped already-present markets from its
    result dict, so ``execute_eod_pipeline`` saw them as missing and blocked the
    feature/ML stages with ``required price source failed``.
    """
    import polars as pl

    # Simulates the post-P0-#2 behavior: every requested market is reported as success,
    # including ones that were skipped because the partition already existed.
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN, "cn_ashare": _WRITTEN})
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: pl.DataFrame({"ticker": ["AAPL"]}))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "cn_ashare"],
        tickers=["AAPL"],
        skip_ml=True,
    )

    assert results["ingestion"]["success"] is True
    assert results["ingestion"]["required_failures"] == []
    assert results["features"]["success"] is True


def test_bronze_to_silver_failure_only_disables_article_enrichment(monkeypatch):
    """Core features remain eligible when optional article processing fails."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN, "rss_news": _WRITTEN})
    monkeypatch.setattr("equity_lake.ingestion.bronze_silver.process_bronze_to_silver", lambda *_, **__: False)
    feature_kwargs = {}

    def run_feature_job(**kwargs):
        feature_kwargs.update(kwargs)
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity", "rss_news"], tickers=["AAPL"], skip_ml=True)

    assert results["ingestion"]["bronze_to_silver"]["success"] is False
    assert results["features"]["success"] is True
    assert feature_kwargs["include_enriched_sentiment"] is False


def test_sec_processing_failure_only_disables_sec_enrichment(monkeypatch):
    """Core features remain eligible when optional SEC processing fails."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN, "sec_filings_fulltext": _WRITTEN})
    monkeypatch.setattr("equity_lake.ingestion.sec_processor.process_sec_bronze_to_silver", lambda *_, **__: False)
    feature_kwargs = {}

    def run_feature_job(**kwargs):
        feature_kwargs.update(kwargs)
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity", "sec_filings_fulltext"], tickers=["AAPL"], skip_ml=True)

    assert results["ingestion"]["sec_to_silver"]["success"] is False
    assert results["features"]["success"] is True
    assert feature_kwargs["include_sec_features"] is False


def test_bronze_to_silver_success_enables_article_enrichment(monkeypatch):
    """A successful optional article processing run flips the enrichment gate ON."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN, "rss_news": _WRITTEN})
    monkeypatch.setattr("equity_lake.ingestion.bronze_silver.process_bronze_to_silver", lambda *_, **__: True)
    feature_kwargs = {}

    def run_feature_job(**kwargs):
        feature_kwargs.update(kwargs)
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity", "rss_news"], tickers=["AAPL"], skip_ml=True)

    assert results["ingestion"]["bronze_to_silver"]["success"] is True
    assert results["features"]["success"] is True
    assert feature_kwargs["include_enriched_sentiment"] is True
    assert feature_kwargs["include_sec_features"] is False  # SEC processor not run without an SEC market.


def test_sec_processing_success_enables_sec_enrichment(monkeypatch):
    """A successful optional SEC processing run flips the SEC enrichment gate ON."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN, "sec_filings_fulltext": _WRITTEN})
    monkeypatch.setattr("equity_lake.ingestion.sec_processor.process_sec_bronze_to_silver", lambda *_, **__: True)
    feature_kwargs = {}

    def run_feature_job(**kwargs):
        feature_kwargs.update(kwargs)
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity", "sec_filings_fulltext"], tickers=["AAPL"], skip_ml=True)

    assert results["ingestion"]["sec_to_silver"]["success"] is True
    assert results["features"]["success"] is True
    assert feature_kwargs["include_sec_features"] is True
    assert feature_kwargs["include_enriched_sentiment"] is False  # No unstructured market ingested.


def test_execute_eod_pipeline_feature_stage(monkeypatch):
    """Feature stage should record row count on success."""

    def fake_run_feature_pipeline(*, tickers, output_start_date, output_end_date, compute_target, **kwargs):
        import polars as pl

        return pl.DataFrame(
            {
                "ticker": ["AAPL", "MSFT"],
                "date": ["2024-01-02", "2024-01-02"],
                "rsi_14": [50.0, 60.0],
            }
        )

    monkeypatch.setattr(
        "equity_lake.pipeline.run_feature_job",
        fake_run_feature_pipeline,
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL", "MSFT"],
        skip_ingestion=True,
        skip_ml=True,
    )

    assert results["features"]["success"] is True
    assert results["features"]["rows"] == 2


def test_execute_eod_pipeline_ml_stage(monkeypatch):
    """ML stage should record per-ticker inference results."""

    def fake_run_prediction_job(*, trading_date, tickers):
        return True, {
            "AAPL": {
                "success": True,
                "prediction": {
                    "ticker": "AAPL",
                    "date": date(2024, 1, 2),
                    "prediction": 1,
                    "probability": 0.73,
                },
            },
        }

    monkeypatch.setattr(
        "equity_lake.pipeline.run_prediction_job",
        fake_run_prediction_job,
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        skip_ingestion=True,
        skip_features=True,
    )

    assert results["ml"]["success"] is True
    assert results["ml"]["results"]["AAPL"]["success"] is True


def test_execute_eod_pipeline_feature_failure_skips_ml(monkeypatch):
    """ML stage should be skipped when features fail."""

    def fake_run_feature_job(*, tickers, output_start_date, output_end_date, compute_target):
        raise RuntimeError("feature pipeline exploded")

    monkeypatch.setattr(
        "equity_lake.pipeline.run_feature_job",
        fake_run_feature_job,
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        skip_ingestion=True,
    )

    assert results["features"]["success"] is False
    assert results["ml"]["skipped"] is True


def _find_clean_week() -> tuple[date, ...]:
    """Find a Mon-Fri week where all 5 days + the previous Friday are trading days."""
    from datetime import timedelta

    from equity_lake.core.calendar import is_trading_day

    d = date(2025, 1, 6)
    while True:
        week = [d + timedelta(days=i) for i in range(7)]
        mon = week[0]
        prev_fri = mon - timedelta(days=3)
        if (
            mon.weekday() == 0
            and all(is_trading_day("us_equity", week[i]) for i in range(5))
            and not any(is_trading_day("us_equity", week[i]) for i in range(5, 7))
            and is_trading_day("us_equity", prev_fri)
        ):
            return prev_fri, mon, week[1], week[2], week[3], week[4]
        d += timedelta(days=1)


def test_resolve_trading_date_explicit() -> None:
    """Explicit date should be used as-is."""
    resolved = resolve_trading_date("2025-01-15", days_back=1)
    assert resolved == date(2025, 1, 15)


def test_resolve_trading_date_rolls_monday_to_friday() -> None:
    """Default date on Monday should map to previous Friday."""
    prev_fri, mon, *_ = _find_clean_week()
    resolved = resolve_trading_date(None, days_back=1, today=mon)
    assert resolved == prev_fri


def test_resolve_trading_date_rolls_sunday_to_friday() -> None:
    """Default date on Sunday should map to previous Friday."""
    prev_fri, mon, tue, wed, thu, fri = _find_clean_week()
    sun = fri + __import__("datetime").timedelta(days=2)
    resolved = resolve_trading_date(None, days_back=1, today=sun)
    assert resolved == fri


def test_resolve_trading_date_counts_trading_days() -> None:
    """Relative dates should skip weekends when days_back > 1."""
    prev_fri, mon, *_ = _find_clean_week()
    from datetime import timedelta

    from equity_lake.core.calendar import is_trading_day

    d = prev_fri - timedelta(days=1)
    while not is_trading_day("us_equity", d):
        d -= timedelta(days=1)
    expected = d
    resolved = resolve_trading_date(None, days_back=2, today=mon)
    assert resolved == expected


def test_resolve_trading_date_relative_weekday() -> None:
    """Relative weekday dates should still subtract one trading day."""
    _, mon, tue, *_ = _find_clean_week()
    resolved = resolve_trading_date(None, days_back=1, today=tue)
    assert resolved == mon


# =============================================================================
# W02: D02 scope/coverage policy, truthful outcomes, dry-run reporting
# =============================================================================


def test_configured_scope_is_not_truncated(monkeypatch):
    """W02/F06 (D02): the configured active scope is resolved in full — the
    historical ten-ticker cap is gone — and reported exactly."""
    config = TickerConfig()
    active_us = config.get_tickers_for_market("us", active_only=True)
    assert len(active_us) > 10  # the old [:10] cap would have hidden the rest

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {})

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        ticker_config=config,
        skip_ingestion=True,
        skip_features=True,
        skip_ml=True,
    )

    assert results["scope"]["tickers"] == active_us
    assert results["scope"]["explicit_tickers"] is False
    assert results["scope"]["enrichment_only"] is False
    assert results["scope"]["markets"] == ["us_equity"]


def test_configured_scope_dedupes_across_markets_deterministically(monkeypatch):
    """W02/F06: repeated tickers across requested markets are deduplicated in
    resolution order, with no silent cap."""
    config = TickerConfig()
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {})

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "us_equity"],
        ticker_config=config,
        skip_ingestion=True,
        skip_features=True,
        skip_ml=True,
    )

    tickers = results["scope"]["tickers"]
    assert tickers == list(dict.fromkeys(tickers))
    assert tickers == config.get_tickers_for_market("us", active_only=True)


def test_explicit_tickers_are_deduplicated(monkeypatch):
    """W02/F06 (D02): explicit scope is deduplicated deterministically (D02), so
    duplicates cannot double the scope or coverage entries."""
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {})

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL", "MSFT", "AAPL"],
        skip_ingestion=True,
        skip_features=True,
        skip_ml=True,
    )

    assert results["scope"]["tickers"] == ["AAPL", "MSFT"]
    assert results["scope"]["explicit_tickers"] is True


def test_feature_write_failure_still_reports_coverage(monkeypatch):
    """W02/F07/F25: a persistence failure on the feature stage keeps the computed
    coverage (produced set and reasons) instead of discarding it."""
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN})

    def failing_run_feature_job(**kwargs):
        kwargs["diagnostics"].update({"requested": ["AAPL"], "produced": ["AAPL"], "missing": {}, "phase1_skipped": {}})
        raise RuntimeError("Feature write to 03_gold/features failed")

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", failing_run_feature_job)

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity"], tickers=["AAPL"], skip_ml=True)

    assert results["features"]["success"] is False
    assert results["features"]["coverage"] == {"requested": ["AAPL"], "produced": ["AAPL"], "missing": {}}
    assert "Feature write" in results["features"]["error"]


def test_feature_job_crash_does_not_fabricate_coverage_reasons(monkeypatch):
    """W02/F07: a whole-job crash before per-ticker diagnostics exists must not
    invent data-gap reasons for every requested ticker."""
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN})

    def crashing_run_feature_job(**_kwargs):
        raise RuntimeError("duckdb connection lost")

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", crashing_run_feature_job)

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL", "MSFT"],
        skip_ml=True,
    )

    assert results["features"]["success"] is False
    assert results["features"]["coverage"] == {"requested": ["AAPL", "MSFT"], "produced": [], "missing": {}}


def test_explicit_empty_scope_is_rejected_not_broadened():
    """W02/F06 (D02): an explicit empty scope raises instead of silently
    expanding to the configured universe."""
    import pytest

    with pytest.raises(ValueError, match="empty"):
        execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity"], tickers=[])


def test_explicit_tickers_are_reported_as_explicit(monkeypatch):
    """W02/F06: explicit scope is preserved unchanged and flagged as explicit."""
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {})

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["MSFT", "AAPL"],
        skip_ingestion=True,
        skip_features=True,
        skip_ml=True,
    )

    assert results["scope"]["tickers"] == ["MSFT", "AAPL"]
    assert results["scope"]["explicit_tickers"] is True


def test_enrichment_only_request_planned_skips_features_and_ml(monkeypatch):
    """W02/D02: a request with no price market planned-skips features/ML with a
    stable reason instead of inventing default tickers or failing on history."""
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"macro": _WRITTEN})
    monkeypatch.setattr(
        "equity_lake.pipeline.run_feature_job",
        lambda **_: (_ for _ in ()).throw(AssertionError("features called for enrichment-only request")),
    )
    monkeypatch.setattr(
        "equity_lake.pipeline.run_prediction_job",
        lambda **_: (_ for _ in ()).throw(AssertionError("ML called for enrichment-only request")),
    )

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["macro"])

    assert results["scope"]["enrichment_only"] is True
    assert results["features"] == {"success": True, "skipped": True, "reason": "enrichment_only_no_price_scope"}
    assert results["ml"] == {"success": True, "skipped": True, "reason": "enrichment_only_no_price_scope"}


def test_feature_coverage_failure_fails_stage_and_blocks_ml(monkeypatch):
    """W02/F07 (D02): a requested ticker missing from the output fails the feature
    stage with its reason and prevents dependent ML output."""
    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN})

    def fake_run_feature_job(**kwargs):
        diag = kwargs["diagnostics"]
        diag.update(
            {
                "requested": ["AAPL", "MSFT"],
                "produced": ["AAPL"],
                "missing": {"MSFT": "insufficient_history"},
                "phase1_skipped": {"MSFT": "insufficient_history"},
            }
        )
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", fake_run_feature_job)
    monkeypatch.setattr(
        "equity_lake.pipeline.run_prediction_job",
        lambda **_: (_ for _ in ()).throw(AssertionError("ML ran despite incomplete coverage")),
    )

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity"], tickers=["AAPL", "MSFT"])

    assert results["features"]["success"] is False
    assert results["features"]["coverage"] == {
        "requested": ["AAPL", "MSFT"],
        "produced": ["AAPL"],
        "missing": {"MSFT": "insufficient_history"},
    }
    assert "MSFT=insufficient_history" in results["features"]["error"]
    assert results["ml"]["skipped"] is True


def test_full_coverage_reports_requested_produced_missing(monkeypatch):
    """W02/F07: even a successful stage publishes requested/produced coverage."""
    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN})

    def fake_run_feature_job(**kwargs):
        kwargs["diagnostics"].update({"requested": ["AAPL"], "produced": ["AAPL"], "missing": {}, "phase1_skipped": {}})
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", fake_run_feature_job)

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        skip_ml=True,
    )

    assert results["features"]["success"] is True
    assert results["features"]["coverage"] == {"requested": ["AAPL"], "produced": ["AAPL"], "missing": {}}


def test_optional_failure_is_visible_and_non_blocking(monkeypatch):
    """W02/F08: an optional failure is reported in optional_failures + errors and
    sets partial=true, while required success keeps the stage eligible."""
    import polars as pl

    monkeypatch.setattr(
        "equity_lake.pipeline.run_daily_ingestion",
        lambda **_: {"us_equity": _WRITTEN, "us_news": SourceOutcome(SourceStatus.FAILED, error="news timeout")},
    )
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: pl.DataFrame({"ticker": ["AAPL"]}))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "us_news"],
        tickers=["AAPL"],
        skip_ml=True,
    )

    ingestion = results["ingestion"]
    assert ingestion["success"] is True
    assert ingestion["partial"] is True
    assert ingestion["optional_failures"] == ["us_news"]
    assert ingestion["errors"] == {"us_news": "news timeout"}
    assert ingestion["markets"] == {"us_equity": "written", "us_news": "failed"}
    assert results["features"]["success"] is True


def test_no_data_optional_market_is_visible_but_not_partial(monkeypatch):
    """W02/F10: a genuine no-data fetch is distinct from an optional failure."""
    import polars as pl

    monkeypatch.setattr(
        "equity_lake.pipeline.run_daily_ingestion",
        lambda **_: {"us_equity": _WRITTEN, "macro": SourceOutcome(SourceStatus.NO_DATA)},
    )
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: pl.DataFrame({"ticker": ["AAPL"]}))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "macro"],
        tickers=["AAPL"],
        skip_ml=True,
    )

    assert results["ingestion"]["success"] is True
    assert results["ingestion"]["partial"] is False
    assert results["ingestion"]["optional_failures"] == []
    assert results["ingestion"]["markets"]["macro"] == "no_data"


def test_dry_run_reports_scope_and_no_write_stages(monkeypatch):
    """W02/F25: dry-run reports resolved scope and never enters write-capable stages."""
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {})

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        dry_run=True,
    )

    assert results["scope"]["dry_run"] is True
    assert results["scope"]["tickers"] == ["AAPL"]
    assert results["ingestion"]["reason"] == "dry_run"
    assert results["features"]["reason"] == "dry_run"
    assert results["ml"]["reason"] == "dry_run"


def test_bronze_to_silver_no_work_is_a_skip_not_a_failure(monkeypatch):
    """W02/F25: no bronze corpus is an intentional no-work skip, while a real
    processing failure is still a failure."""
    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN, "rss_news": _WRITTEN})

    def no_bronze(trading_date, diagnostics=None):
        if diagnostics is not None:
            diagnostics.update({"mode": "no_bronze_data"})
        return False

    monkeypatch.setattr("equity_lake.ingestion.bronze_silver.process_bronze_to_silver", no_bronze)
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: pl.DataFrame({"ticker": ["AAPL"]}))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "rss_news"],
        tickers=["AAPL"],
        skip_ml=True,
    )

    stage = results["ingestion"]["bronze_to_silver"]
    assert stage["success"] is True
    assert stage["skipped"] is True
    assert stage["reason"] == "no_bronze_work"

    def processing_failed(trading_date, diagnostics=None):
        if diagnostics is not None:
            diagnostics.update({"mode": "processing_failed", "error": "llm quota"})
        return False

    monkeypatch.setattr("equity_lake.ingestion.bronze_silver.process_bronze_to_silver", processing_failed)

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity", "rss_news"],
        tickers=["AAPL"],
        skip_ml=True,
    )

    stage = results["ingestion"]["bronze_to_silver"]
    assert stage["success"] is False
    assert stage["reason"] == "optional enrichment failed"
    assert stage["error"] == "llm quota"


# =============================================================================
# W03/F25: structured recovery reporting
# =============================================================================


def test_zero_recovered_dates_are_reported_without_fabricating_success(monkeypatch):
    """W03/F25: zero recovered dates are visible and never asserted as success."""
    import polars as pl

    calls = {"n": 0}

    def run_feature_job(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise NoFeatureHistoryError("No features generated")
        kwargs["diagnostics"].update({"requested": ["AAPL"], "produced": ["AAPL"], "missing": {}, "phase1_skipped": {}})
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)
    monkeypatch.setattr(
        "equity_lake.pipeline.backfill_date_range",
        lambda **_: _backfill_result(0, market_date_attempts=2, failed=2, per_market={"us_equity": 0}, dates_recovered=0),
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        skip_ingestion=True,
        skip_ml=True,
        allow_history_backfill=True,
    )

    assert results["features"]["success"] is True
    assert results["features"]["history_backfilled_dates"] == 0
    recovery = results["features"]["recovery"]
    assert recovery["succeeded"] == 0
    assert recovery["failed"] == 2
    assert recovery["dates_recovered"] == 0
    assert recovery["market_date_attempts"] == 2


def test_missing_coverage_after_recovery_fails_stage(monkeypatch):
    """W03/F25: the post-recovery completeness check still fails missing scope."""
    import polars as pl

    calls = {"n": 0}

    def run_feature_job(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise NoFeatureHistoryError("No features generated")
        kwargs["diagnostics"].update(
            {
                "requested": ["AAPL", "MSFT"],
                "produced": ["AAPL"],
                "missing": {"MSFT": "insufficient_history"},
                "phase1_skipped": {"MSFT": "insufficient_history"},
            }
        )
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **_: _backfill_result(1))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL", "MSFT"],
        skip_ingestion=True,
        skip_ml=True,
        allow_history_backfill=True,
    )

    assert results["features"]["success"] is False
    assert "MSFT=insufficient_history" in results["features"]["error"]
    assert results["features"]["recovery"]["succeeded"] == 1
    assert results["features"]["coverage"]["missing"] == {"MSFT": "insufficient_history"}


def test_ingestion_stage_reports_resolved_ticker_scope(monkeypatch):
    """W03/F04: the ingestion stage publishes the resolver's per-market scope."""
    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us_equity": _WRITTEN})

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us_equity"],
        tickers=["AAPL"],
        explicit_tickers=["AAPL"],
        skip_features=True,
        skip_ml=True,
    )

    assert results["ingestion"]["ticker_scope"] == {"us_equity": ["AAPL"]}


# =============================================================================
# W03/D04/F11: explicit-date session scheduling
# =============================================================================


class TestPipelineSessionScheduling:
    """Features/ML are planned only for the scheduled price scope."""

    def test_closed_market_is_not_scheduled_and_open_market_still_planned(self, monkeypatch) -> None:
        """US Thanksgiving: US is a planned skip; CN work remains eligible."""
        import polars as pl

        config = TickerConfig()
        cn_tickers = config.get_tickers_for_market("cn", active_only=True)
        us_tickers = config.get_tickers_for_market("us", active_only=True)
        monkeypatch.setattr(
            "equity_lake.pipeline.run_daily_ingestion",
            lambda **_: {"us_equity": SourceOutcome(SourceStatus.NOT_SCHEDULED), "cn_ashare": _WRITTEN},
        )
        seen: dict[str, list[str]] = {}

        def fake_run_feature_job(**kwargs):
            seen["tickers"] = kwargs["tickers"]
            return pl.DataFrame({"ticker": cn_tickers})

        monkeypatch.setattr("equity_lake.pipeline.run_feature_job", fake_run_feature_job)
        monkeypatch.setattr("equity_lake.pipeline.run_prediction_job", lambda **_: (True, {}))

        results = execute_eod_pipeline(
            trading_date=date(2024, 11, 28),
            markets=["us_equity", "cn_ashare"],
            ticker_config=config,
        )

        assert results["ingestion"]["success"] is True
        assert results["ingestion"]["not_scheduled"] == ["us_equity"]
        assert results["ingestion"]["required_failures"] == []
        assert results["ingestion"]["partial"] is False
        assert results["scope"]["not_scheduled"] == ["us_equity"]
        assert results["scope"]["scheduled"] == ["cn_ashare"]
        assert seen["tickers"] == cn_tickers
        assert not set(us_tickers) & set(seen["tickers"])
        assert results["features"]["success"] is True

    def test_all_requested_price_markets_closed_planned_skips_features_and_ml(self, monkeypatch) -> None:
        """When every requested price market is closed there is no scheduled feature work."""
        monkeypatch.setattr(
            "equity_lake.pipeline.run_daily_ingestion",
            lambda **_: {"us_equity": SourceOutcome(SourceStatus.NOT_SCHEDULED), "cn_ashare": SourceOutcome(SourceStatus.NOT_SCHEDULED)},
        )
        monkeypatch.setattr(
            "equity_lake.pipeline.run_feature_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("features called for a closed-only request")),
        )
        monkeypatch.setattr(
            "equity_lake.pipeline.run_prediction_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("ML called for a closed-only request")),
        )

        results = execute_eod_pipeline(trading_date=date(2024, 1, 1), markets=["us_equity", "cn_ashare"])

        assert results["ingestion"]["success"] is True
        assert results["ingestion"]["not_scheduled"] == ["cn_ashare", "us_equity"]
        assert results["ingestion"]["required_failures"] == []
        assert results["scope"]["not_scheduled"] == ["us_equity", "cn_ashare"]
        assert results["features"] == {"success": True, "skipped": True, "reason": "not_scheduled_scope"}
        assert results["ml"] == {"success": True, "skipped": True, "reason": "not_scheduled_scope"}

    def test_dry_run_reports_not_scheduled_scope_without_writes(self, monkeypatch) -> None:
        """Dry-run classifies the plan date, reports the closed scope, and writes nothing."""
        monkeypatch.setattr(
            "equity_lake.pipeline.run_daily_ingestion",
            lambda **_: (_ for _ in ()).throw(AssertionError("dry-run must not ingest")),
        )
        monkeypatch.setattr(
            "equity_lake.pipeline.run_feature_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("dry-run must not compute features")),
        )
        monkeypatch.setattr(
            "equity_lake.pipeline.run_prediction_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("dry-run must not infer")),
        )

        results = execute_eod_pipeline(trading_date=date(2024, 1, 1), markets=["us_equity", "cn_ashare"], dry_run=True)

        assert results["ingestion"]["reason"] == "dry_run"
        assert results["ingestion"]["not_scheduled"] == ["us_equity", "cn_ashare"]
        assert results["ingestion"]["scheduled"] == []
        assert results["features"]["reason"] == "dry_run"
        assert results["ml"]["reason"] == "dry_run"

    def test_scheduled_required_market_no_data_still_blocks(self, monkeypatch) -> None:
        """W02 blocking is confined to genuine data absence on a scheduled session."""
        monkeypatch.setattr(
            "equity_lake.pipeline.run_daily_ingestion",
            lambda **_: {"us_equity": SourceOutcome(SourceStatus.NO_DATA)},
        )
        monkeypatch.setattr(
            "equity_lake.pipeline.run_feature_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("features called despite required NO_DATA")),
        )

        results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us_equity"], tickers=["AAPL"])

        assert results["ingestion"]["not_scheduled"] == []
        assert results["ingestion"]["required_failures"] == ["us_equity"]
        assert results["ingestion"]["success"] is False
        assert results["features"]["reason"] == "required price source failed"

    def test_hk_sg_union_session_plans_the_open_instrument(self, monkeypatch) -> None:
        """A Singapore-only holiday does not drop an open HK instrument (union semantics)."""
        import polars as pl

        monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"hk_sg_equity": _WRITTEN})
        seen: dict[str, list[str]] = {}

        def fake_run_feature_job(**kwargs):
            seen["tickers"] = kwargs["tickers"]
            return pl.DataFrame({"ticker": ["0700.HK"]})

        monkeypatch.setattr("equity_lake.pipeline.run_feature_job", fake_run_feature_job)
        monkeypatch.setattr("equity_lake.pipeline.run_prediction_job", lambda **_: (True, {}))

        results = execute_eod_pipeline(trading_date=date(2024, 8, 9), markets=["hk_sg_equity"], tickers=["0700.HK"])

        assert results["scope"]["not_scheduled"] == []
        assert results["ingestion"]["not_scheduled"] == []
        assert seen["tickers"] == ["0700.HK"]
        assert results["features"]["success"] is True

    def test_explicit_closed_market_is_never_substituted_with_an_earlier_session(self, monkeypatch) -> None:
        """An explicit closed date is not_scheduled; no earlier-session fetch happens."""
        monkeypatch.setattr(
            "equity_lake.pipeline.run_daily_ingestion",
            lambda **_: {"us_equity": SourceOutcome(SourceStatus.NOT_SCHEDULED)},
        )
        monkeypatch.setattr(
            "equity_lake.pipeline.run_feature_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("features called for an explicit closed date")),
        )

        results = execute_eod_pipeline(trading_date=date(2024, 11, 28), markets=["us_equity"], tickers=["AAPL"], skip_ingestion=True)

        assert results["scope"]["not_scheduled"] == ["us_equity"]
        assert results["ingestion"]["not_scheduled"] == ["us_equity"]
        assert results["features"]["reason"] == "not_scheduled_scope"

    def test_explicit_ticker_owned_by_a_closed_market_planned_skips_features(self, monkeypatch) -> None:
        """An explicit ticker whose only market is closed has no scheduled work."""
        monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"cn_ashare": _WRITTEN})
        monkeypatch.setattr(
            "equity_lake.pipeline.run_feature_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("features called for a closed-market ticker")),
        )
        monkeypatch.setattr(
            "equity_lake.pipeline.run_prediction_job",
            lambda **_: (_ for _ in ()).throw(AssertionError("ML called for a closed-market ticker")),
        )

        results = execute_eod_pipeline(
            trading_date=date(2024, 11, 28),
            markets=["us_equity", "cn_ashare"],
            tickers=["AAPL"],
            explicit_tickers=["AAPL"],
            skip_ingestion=True,
        )

        assert results["scope"]["not_scheduled"] == ["us_equity"]
        assert results["scope"]["scheduled"] == ["cn_ashare"]
        assert results["scope"]["tickers"] == []
        assert results["features"]["reason"] == "not_scheduled_scope"
        assert results["ml"]["reason"] == "not_scheduled_scope"
