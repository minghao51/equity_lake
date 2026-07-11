"""Tests for the EOD pipeline executor (pipeline.py)."""

from datetime import date

from equity_lake.core.dates import resolve_trading_date
from equity_lake.pipeline import execute_eod_pipeline


def test_execute_eod_pipeline_dry_run_skips_writes_and_processors(monkeypatch):
    """Dry-run is a plan-only execution and never enters write-capable stages."""

    def fake_run_daily_ingestion(*, trading_date, markets, dry_run, parallel, ticker_config, filters, explicit_tickers, skip_existing):
        assert trading_date == date(2024, 1, 2)
        assert markets == ["us", "cn"]
        assert dry_run is True
        return {"us": True, "cn": False}

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
        markets=["us", "cn"],
        tickers=["AAPL"],
        dry_run=True,
        skip_features=True,
        skip_ml=True,
    )

    assert results["ingestion"] == {"success": True, "skipped": True, "reason": "dry_run", "markets": {}}
    assert results["features"] == {"success": True, "skipped": True, "reason": "dry_run"}
    assert results["ml"] == {"success": True, "skipped": True, "reason": "dry_run"}


def test_missing_history_requires_explicit_authorization(monkeypatch):
    """A missing warm-up window fails clearly without starting recovery."""

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: (_ for _ in ()).throw(RuntimeError("No features generated")))
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **_: (_ for _ in ()).throw(AssertionError("backfill called")))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us"],
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
            raise RuntimeError("No features generated")
        return next(feature_calls)

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)
    backfill_calls = []
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **kwargs: backfill_calls.append(kwargs) or 1)

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us"],
        tickers=["AAPL"],
        dry_run=False,
        skip_ingestion=True,
        skip_ml=True,
        allow_history_backfill=True,
    )

    assert results["features"]["success"] is True
    assert backfill_calls == [
        {
            "start_date": date(2023, 9, 4),
            "end_date": date(2024, 1, 2),
            "markets": ["us"],
            "ticker_config": backfill_calls[0]["ticker_config"],
            "dry_run": False,
            "explicit_tickers": ["AAPL"],
        }
    ]


def test_authorized_history_recovery_scopes_backfill_to_price_markets(monkeypatch):
    """Recovery only re-ingests required price markets, not enrichments."""

    backfill_calls = []
    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", lambda **kwargs: backfill_calls.append(kwargs) or 1)
    monkeypatch.setattr(
        "equity_lake.pipeline.run_feature_job",
        lambda **_: (_ for _ in ()).throw(RuntimeError("No features generated")),
    )

    execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us", "rss_news"],
        tickers=["AAPL"],
        skip_ingestion=True,
        skip_ml=True,
        allow_history_backfill=True,
    )

    assert backfill_calls[0]["markets"] == ["us"]


def test_authorized_history_recovery_records_failure_when_backfill_raises(monkeypatch):
    """A backfill exception must not crash the pipeline; it records a failed stage."""

    def raising_backfill(**_):
        raise RuntimeError("network down")

    monkeypatch.setattr("equity_lake.pipeline.backfill_date_range", raising_backfill)
    feature_calls = []

    def run_feature_job(**kwargs):
        feature_calls.append(kwargs)
        raise RuntimeError("No features generated")

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us"],
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

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us": False})
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: (_ for _ in ()).throw(AssertionError("features called")))
    monkeypatch.setattr("equity_lake.pipeline.run_prediction_job", lambda **_: (_ for _ in ()).throw(AssertionError("ML called")))

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us"], tickers=["AAPL"])

    assert results["ingestion"]["success"] is False
    assert results["features"]["reason"] == "required price source failed"
    assert results["ml"]["skipped"] is True


def test_optional_ingestion_failure_keeps_core_features_eligible(monkeypatch):
    """Optional enrichment degradation does not block core feature generation."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us": True, "us_news": False})
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", lambda **_: pl.DataFrame({"ticker": ["AAPL"]}))

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us", "us_news"], tickers=["AAPL"], skip_ml=True)

    assert results["ingestion"]["success"] is True
    assert results["ingestion"]["partial"] is True
    assert results["features"]["success"] is True


def test_bronze_to_silver_failure_only_disables_article_enrichment(monkeypatch):
    """Core features remain eligible when optional article processing fails."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us": True, "rss_news": True})
    monkeypatch.setattr("equity_lake.ingestion.bronze_silver.process_bronze_to_silver", lambda *_: False)
    feature_kwargs = {}

    def run_feature_job(**kwargs):
        feature_kwargs.update(kwargs)
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us", "rss_news"], tickers=["AAPL"], skip_ml=True)

    assert results["bronze_to_silver"]["success"] is False
    assert results["features"]["success"] is True
    assert feature_kwargs["include_enriched_sentiment"] is False


def test_sec_processing_failure_only_disables_sec_enrichment(monkeypatch):
    """Core features remain eligible when optional SEC processing fails."""

    import polars as pl

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", lambda **_: {"us": True, "sec_filings_fulltext": True})
    monkeypatch.setattr("equity_lake.ingestion.sec_processor.process_sec_bronze_to_silver", lambda *_: False)
    feature_kwargs = {}

    def run_feature_job(**kwargs):
        feature_kwargs.update(kwargs)
        return pl.DataFrame({"ticker": ["AAPL"]})

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", run_feature_job)

    results = execute_eod_pipeline(trading_date=date(2024, 1, 2), markets=["us", "sec_filings_fulltext"], tickers=["AAPL"], skip_ml=True)

    assert results["sec_to_silver"]["success"] is False
    assert results["features"]["success"] is True
    assert feature_kwargs["include_sec_features"] is False


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
        markets=["us"],
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
        markets=["us"],
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
        markets=["us"],
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
