"""Tests for monitoring.health.PipelineMonitor and helpers."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import duckdb
import polars as pl

from equity_lake.monitoring.health import DEFAULT_TABLE_MAX_AGE_DAYS, PipelineMonitor, _date_scalar

# Path constants patched where the registry resolves them: price-market dirs are
# looked up on the core.paths module at call time (ADR-0010); the unstructured/
# feature dirs are still module constants of the health module.
CORE_PATHS = "equity_lake.core.paths"
HEALTH_PATHS = "equity_lake.monitoring.health"


class _FakeAlerter:
    """Minimal Alerter implementation that records send_alert calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, dict[str, Any] | None]] = []

    def send_alert(self, alerts: list[str], *, severity: str, metrics: dict[str, Any] | None = None) -> None:
        self.calls.append((list(alerts), severity, metrics))


def _write_parquet_partition(base_dir: Path, partition_date: date, rows: list[dict], schema: dict[str, Any] | None = None) -> None:
    """Write ``rows`` under ``base_dir/date=YYYY-MM-DD/part.parquet`` (no in-file date col)."""
    part_dir = base_dir / f"date={partition_date.isoformat()}"
    part_dir.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(rows, schema=schema) if schema else pl.DataFrame(rows)
    df.write_parquet(part_dir / "part.parquet")


def _patch_market_clock(reference_date: date):
    """Patch market_now -> reference_date and is_trading_day -> True for determinism."""
    return (
        patch(f"{HEALTH_PATHS}.market_now", return_value=reference_date),
        patch(f"{HEALTH_PATHS}.is_trading_day", return_value=True),
    )


# =============================================================================
# price-market registry coverage
# =============================================================================


class TestPriceMarketRegistry:
    def test_registry_covers_all_required_price_markets(self) -> None:
        # Monitoring must cover every market classified as a required price
        # market. Hardcoding three would silently drop JPX/KRX freshness/quality.
        # Since ADR-0010 the monitor iterates core.paths.PRICE_MARKETS directly.
        from equity_lake.ingestion.types import REQUIRED_PRICE_MARKETS
        from equity_lake.monitoring.health import _price_market_paths

        assert set(_price_market_paths()) == set(REQUIRED_PRICE_MARKETS)

    def test_price_market_paths_resolve_at_call_time(self) -> None:
        """Patching a core.paths dir constant must be honoured (monkeypatch contract)."""
        from equity_lake.monitoring.health import _price_market_paths

        with patch("equity_lake.core.paths.US_EQUITY_DIR", Path("/tmp/patched-us")):
            assert _price_market_paths()["us_equity"] == Path("/tmp/patched-us")


# =============================================================================
# _date_scalar helper
# =============================================================================


class TestDateScalar:
    def test_none_returns_none(self) -> None:
        assert _date_scalar(None) is None

    def test_date_passes_through(self) -> None:
        d = date(2024, 1, 15)
        assert _date_scalar(d) == d

    def test_datetime_extracts_date(self) -> None:
        dt = datetime(2024, 1, 15, 10, 30, 45)
        assert _date_scalar(dt) == date(2024, 1, 15)

    def test_other_type_returns_none(self) -> None:
        assert _date_scalar("2024-01-15") is None
        assert _date_scalar(42) is None


# =============================================================================
# Constructor
# =============================================================================


class TestPipelineMonitorInit:
    def test_defaults(self) -> None:
        m = PipelineMonitor()
        assert m.max_age_days == 2
        assert m.null_threshold_pct == 5.0
        assert m.market_max_age_days == {}
        assert m.table_max_age_days == DEFAULT_TABLE_MAX_AGE_DAYS
        assert m.verbose is False
        assert m.alerts == []
        assert m.metrics == {}
        assert m.check_results == {}

    def test_custom_values(self) -> None:
        m = PipelineMonitor(max_age_days=5, null_threshold_pct=10.0, verbose=True)
        assert m.max_age_days == 5
        assert m.null_threshold_pct == 10.0
        assert m.verbose is True

    def test_alerter_injection(self) -> None:
        fake = _FakeAlerter()
        m = PipelineMonitor(alerter=fake)
        assert m.alerter is fake

    def test_bootstraps_delta_extension_on_connection(self) -> None:
        """The monitor's own connection must INSTALL/LOAD delta before delta_scan queries."""
        fake_conn = MagicMock(spec=duckdb.DuckDBPyConnection)
        with patch("equity_lake.monitoring.health.duckdb.connect", return_value=fake_conn):
            PipelineMonitor()
        executed = [call.args[0] for call in fake_conn.execute.call_args_list if call.args]
        assert "INSTALL delta; LOAD delta;" in executed

    def test_close_and_context_manager(self) -> None:
        fake_conn = MagicMock(spec=duckdb.DuckDBPyConnection)
        with patch("equity_lake.monitoring.health.duckdb.connect", return_value=fake_conn), PipelineMonitor() as m:
            assert m.conn is fake_conn
        fake_conn.close.assert_called_once()

        m2 = PipelineMonitor()
        m2.close()
        m2.close()  # idempotent


class TestAlertDedupe:
    def test_repeated_check_target_alerts_are_deduped(self) -> None:
        m = PipelineMonitor()
        m._add_alert("data_freshness", "us_equity", "stale once")
        m._add_alert("data_freshness", "us_equity", "stale twice")
        m._add_alert("data_freshness", "krx_equity", "other market")
        assert m.alerts == ["stale once", "other market"]

    def test_distinct_targets_within_a_check_are_kept(self) -> None:
        m = PipelineMonitor()
        m._add_alert("data_quality", "us_equity close", "null close")
        m._add_alert("data_quality", "us_equity volume", "null volume")
        assert len(m.alerts) == 2


# =============================================================================
# check_feature_store
# =============================================================================


class TestCheckFeatureStore:
    def test_missing_dir_is_unhealthy(self, tmp_path) -> None:
        with patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", tmp_path / "absent"):
            m = PipelineMonitor()
            assert m.check_feature_store() is False
        assert any("Feature store does not exist" in a for a in m.alerts)

    def test_no_recent_features_is_unhealthy(self, tmp_path) -> None:
        # Parquet exists but outside the 7-day CURRENT_DATE window -> filtered out -> 0 rows.
        old_date = date(2020, 1, 1)
        _write_parquet_partition(tmp_path, old_date, [{"ticker": "AAPL"}])
        with patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", tmp_path):
            m = PipelineMonitor()
            assert m.check_feature_store() is False
        assert any("No features in last 7 days" in a for a in m.alerts)

    def test_fresh_features_are_healthy(self, tmp_path) -> None:
        recent = date.today()
        _write_parquet_partition(tmp_path, recent, [{"ticker": "AAPL"}, {"ticker": "MSFT"}])
        now_patch, _ = _patch_market_clock(recent)
        with patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", tmp_path), now_patch:
            m = PipelineMonitor(max_age_days=2)
            assert m.check_feature_store() is True
        assert "feature_store" in m.metrics

    def test_stale_features_are_unhealthy(self, tmp_path) -> None:
        recent = date.today()
        # Within the 7-day CURRENT_DATE window but 5 days older than the patched clock.
        _write_parquet_partition(tmp_path, recent, [{"ticker": "AAPL"}])
        now_patch, _ = _patch_market_clock(recent + timedelta(days=5))
        with patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", tmp_path), now_patch:
            m = PipelineMonitor(max_age_days=2)
            assert m.check_feature_store() is False
        assert any("Features are stale" in a for a in m.alerts)

    def test_corrupt_feature_store_fails_the_check(self, tmp_path) -> None:
        # A corrupt (unreadable) feature directory must read as FAILED with an
        # alert — never as a healthy pass.
        part_dir = tmp_path / f"date={date.today().isoformat()}"
        part_dir.mkdir(parents=True)
        (part_dir / "part.parquet").write_bytes(b"not a parquet file")
        with patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", tmp_path):
            m = PipelineMonitor()
            assert m.check_feature_store() is False
        assert any("Feature store check failed" in a for a in m.alerts)


# =============================================================================
# check_data_freshness
# =============================================================================


def _ohlcv_row(ticker: str = "AAPL", close: float | None = 150.0) -> dict:
    return {
        "ticker": ticker,
        "open": 149.0,
        "high": 155.0,
        "low": 148.0,
        "close": close,
        "volume": 1_000_000.0,
    }


class TestCheckDataFreshness:
    _OHLCV_SCHEMA = {
        "ticker": pl.Utf8,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    }

    def _patch_dirs(self, tmp_path):
        return [
            patch(f"{CORE_PATHS}.US_EQUITY_DIR", tmp_path / "us"),
            patch(f"{CORE_PATHS}.CN_ASHARE_DIR", tmp_path / "cn"),
            patch(f"{CORE_PATHS}.HK_SG_EQUITY_DIR", tmp_path / "hk_sg"),
            patch(f"{CORE_PATHS}.JPX_EQUITY_DIR", tmp_path / "jpx"),
            patch(f"{CORE_PATHS}.KRX_EQUITY_DIR", tmp_path / "krx"),
        ]

    def test_fresh_data_is_healthy(self, tmp_path) -> None:
        ref = date(2024, 1, 10)
        for sub in ("us", "cn", "hk_sg", "jpx", "krx"):
            _write_parquet_partition(tmp_path / sub, date(2024, 1, 9), [_ohlcv_row()], schema=self._OHLCV_SCHEMA)
        now_patch, td_patch = _patch_market_clock(ref)
        patches = self._patch_dirs(tmp_path) + [now_patch, td_patch]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(max_age_days=2)
            assert m.check_data_freshness() is True
            assert m.metrics["data_freshness"]["stale_markets"] == []
        finally:
            for p in patches:
                p.stop()

    def test_stale_data_is_unhealthy(self, tmp_path) -> None:
        ref = date(2024, 1, 10)
        for sub in ("us", "cn", "hk_sg", "jpx", "krx"):
            _write_parquet_partition(tmp_path / sub, date(2024, 1, 1), [_ohlcv_row()], schema=self._OHLCV_SCHEMA)
        now_patch, td_patch = _patch_market_clock(ref)
        patches = self._patch_dirs(tmp_path) + [now_patch, td_patch]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(max_age_days=2)
            assert m.check_data_freshness() is False
            assert len(m.alerts) == 5  # one per market
        finally:
            for p in patches:
                p.stop()

    def test_per_market_max_age_overrides(self, tmp_path) -> None:
        # KRX gets a 5-day expectation via market_max_age_days; 3-day-old data
        # is stale for the default markets but fresh for KRX.
        ref = date(2024, 1, 10)
        for sub in ("us", "cn", "hk_sg", "jpx"):
            _write_parquet_partition(tmp_path / sub, date(2024, 1, 7), [_ohlcv_row()], schema=self._OHLCV_SCHEMA)
        _write_parquet_partition(tmp_path / "krx", date(2024, 1, 7), [_ohlcv_row()], schema=self._OHLCV_SCHEMA)
        now_patch, td_patch = _patch_market_clock(ref)
        patches = self._patch_dirs(tmp_path) + [now_patch, td_patch]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(max_age_days=2, market_max_age_days={"krx_equity": 5})
            assert m.check_data_freshness() is False
            assert m.metrics["data_freshness"]["fresh_markets"] == ["krx_equity"]
            assert len(m.metrics["data_freshness"]["stale_markets"]) == 4
        finally:
            for p in patches:
                p.stop()

    def test_missing_parquet_fails_gracefully(self, tmp_path) -> None:
        # Dirs patched but no parquet written -> duckdb glob fails -> except branch.
        patches = self._patch_dirs(tmp_path)
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor()
            assert m.check_data_freshness() is False
            assert any("Data freshness check failed" in a for a in m.alerts)
        finally:
            for p in patches:
                p.stop()


# =============================================================================
# check_data_quality
# =============================================================================


class TestCheckDataQuality:
    _OHLCV_SCHEMA = {
        "ticker": pl.Utf8,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    }

    def _patch_dirs(self, tmp_path):
        return [
            patch(f"{CORE_PATHS}.US_EQUITY_DIR", tmp_path / "us"),
            patch(f"{CORE_PATHS}.CN_ASHARE_DIR", tmp_path / "cn"),
            patch(f"{CORE_PATHS}.HK_SG_EQUITY_DIR", tmp_path / "hk_sg"),
            patch(f"{CORE_PATHS}.JPX_EQUITY_DIR", tmp_path / "jpx"),
            patch(f"{CORE_PATHS}.KRX_EQUITY_DIR", tmp_path / "krx"),
        ]

    def test_clean_data_is_healthy(self, tmp_path) -> None:
        recent = date.today()
        for sub in ("us", "cn", "hk_sg", "jpx", "krx"):
            _write_parquet_partition(tmp_path / sub, recent, [_ohlcv_row(), _ohlcv_row("MSFT")], schema=self._OHLCV_SCHEMA)
        for p in self._patch_dirs(tmp_path):
            p.start()
        try:
            m = PipelineMonitor(null_threshold_pct=5.0)
            assert m.check_data_quality() is True
            assert m.metrics["data_quality"]["issues_found"] == 0
        finally:
            for p in self._patch_dirs(tmp_path):
                p.stop()

    def test_high_nulls_are_flagged(self, tmp_path) -> None:
        recent = date.today()
        # 6 of 10 rows have null close -> 60% >> 5% threshold.
        rows = [_ohlcv_row(f"T{i}") for i in range(4)] + [_ohlcv_row(f"T{i}", close=None) for i in range(6)]
        for sub in ("us", "cn", "hk_sg", "jpx", "krx"):
            _write_parquet_partition(tmp_path / sub, recent, rows, schema=self._OHLCV_SCHEMA)
        for p in self._patch_dirs(tmp_path):
            p.start()
        try:
            m = PipelineMonitor(null_threshold_pct=5.0)
            assert m.check_data_quality() is False
            # Each of the 5 markets contributes a null-close alert.
            assert sum(1 for a in m.alerts if "null close" in a) == 5
        finally:
            for p in self._patch_dirs(tmp_path):
                p.stop()


# =============================================================================
# check_unstructured_freshness
# =============================================================================


class TestCheckUnstructuredFreshness:
    def _patch_dirs(self, tmp_path):
        return [
            patch(f"{HEALTH_PATHS}.BRONZE_RAW_ARTICLES_DIR", tmp_path / "bronze"),
            patch(f"{HEALTH_PATHS}.SILVER_PROCESSED_ARTICLES_DIR", tmp_path / "silver"),
            patch(f"{HEALTH_PATHS}.SILVER_SEC_EXTRACTIONS_DIR", tmp_path / "sec"),
        ]

    def test_missing_dirs_are_treated_as_fresh(self, tmp_path) -> None:
        # Missing dirs log debug + record "missing" status but do NOT flip all_fresh.
        for p in self._patch_dirs(tmp_path):
            p.start()
        try:
            m = PipelineMonitor()
            assert m.check_unstructured_freshness() is True
            uf = m.metrics["unstructured_freshness"]
            assert uf["bronze/raw_articles"]["status"] == "missing"
        finally:
            for p in self._patch_dirs(tmp_path):
                p.stop()

    def test_fresh_data_is_healthy(self, tmp_path) -> None:
        ref = date(2024, 1, 10)
        for sub in ("bronze", "silver", "sec"):
            _write_parquet_partition(tmp_path / sub, date(2024, 1, 9), [{"url": "x"}])
        now_patch, _ = _patch_market_clock(ref)
        patches = self._patch_dirs(tmp_path) + [now_patch]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(max_age_days=2)
            assert m.check_unstructured_freshness() is True
        finally:
            for p in patches:
                p.stop()

    def test_stale_data_is_unhealthy(self, tmp_path) -> None:
        ref = date(2024, 1, 10)
        for sub in ("bronze", "silver", "sec"):
            _write_parquet_partition(tmp_path / sub, date(2024, 1, 1), [{"url": "x"}])
        now_patch, _ = _patch_market_clock(ref)
        patches = self._patch_dirs(tmp_path) + [now_patch]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(max_age_days=2)
            assert m.check_unstructured_freshness() is False
            # News tables are stale at 9 days; SEC extractions have a quarterly
            # (95-day) expectation so they stay fresh — no alert for them.
            assert len(m.alerts) == 2
            assert all("sec_extractions" not in a for a in m.alerts)
        finally:
            for p in patches:
                p.stop()

    def test_sec_extractions_use_quarterly_expectation(self, tmp_path) -> None:
        # SEC filings are quarterly: 26-day-old extractions must stay fresh even
        # with max_age_days=2 (the per-table expectation is 95 days).
        ref = date(2024, 1, 10)
        for sub in ("bronze", "silver"):
            _write_parquet_partition(tmp_path / sub, date(2024, 1, 9), [{"url": "x"}])
        _write_parquet_partition(tmp_path / "sec", date(2023, 12, 15), [{"url": "x"}])  # 26 days old
        now_patch, _ = _patch_market_clock(ref)
        patches = self._patch_dirs(tmp_path) + [now_patch]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(max_age_days=2)
            assert m.check_unstructured_freshness() is True
            assert m.metrics["unstructured_freshness"]["silver/sec_extractions"]["max_age_days"] == 95
        finally:
            for p in patches:
                p.stop()

    def test_corrupt_table_fails_the_check(self, tmp_path) -> None:
        # Per-table read errors must fail the check and alert — not log at debug
        # and silently count as fresh.
        part_dir = tmp_path / "sec" / f"date={date.today().isoformat()}"
        part_dir.mkdir(parents=True)
        (part_dir / "part.parquet").write_bytes(b"not a parquet file")
        for sub in ("bronze", "silver"):
            _write_parquet_partition(tmp_path / sub, date.today(), [{"url": "x"}])
        for p in self._patch_dirs(tmp_path):
            p.start()
        try:
            m = PipelineMonitor()
            assert m.check_unstructured_freshness() is False
        finally:
            for p in self._patch_dirs(tmp_path):
                p.stop()
        assert any("freshness check failed" in a for a in m.alerts)
        assert m.metrics["unstructured_freshness"]["silver/sec_extractions"]["status"] == "error"


# =============================================================================
# save_report
# =============================================================================


class TestSaveReport:
    def test_writes_json_round_trip(self, tmp_path) -> None:
        m = PipelineMonitor()
        m.alerts = ["⚠️ something"]
        m.metrics = {"data_freshness": {"fresh_markets": ["us_equity"]}}
        out = tmp_path / "report.json"
        m.save_report(out)

        report = json.loads(out.read_text())
        assert report["alerts"] == ["⚠️ something"]
        assert report["metrics"]["data_freshness"]["fresh_markets"] == ["us_equity"]
        assert "timestamp" in report


# =============================================================================
# run_health_check (orchestration)
# =============================================================================


class TestRunHealthCheck:
    def test_dispatches_alerts_on_failure(self, tmp_path, capsys) -> None:
        # Force every check to fail by pointing all paths at a missing dir.
        fake = _FakeAlerter()
        patches = [
            patch(f"{CORE_PATHS}.US_EQUITY_DIR", tmp_path / "absent_us"),
            patch(f"{CORE_PATHS}.CN_ASHARE_DIR", tmp_path / "absent_cn"),
            patch(f"{CORE_PATHS}.HK_SG_EQUITY_DIR", tmp_path / "absent_hk"),
            patch(f"{CORE_PATHS}.JPX_EQUITY_DIR", tmp_path / "absent_jpx"),
            patch(f"{CORE_PATHS}.KRX_EQUITY_DIR", tmp_path / "absent_krx"),
            patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", tmp_path / "absent_gold"),
            patch(f"{HEALTH_PATHS}.BRONZE_RAW_ARTICLES_DIR", tmp_path / "absent_bronze"),
            patch(f"{HEALTH_PATHS}.SILVER_PROCESSED_ARTICLES_DIR", tmp_path / "absent_silver"),
            patch(f"{HEALTH_PATHS}.SILVER_SEC_EXTRACTIONS_DIR", tmp_path / "absent_sec"),
        ]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(alerter=fake)
            healthy = m.run_health_check()
        finally:
            for p in patches:
                p.stop()

        assert healthy is False
        assert len(m.alerts) >= 1
        # Alerts were dispatched via the injected alerter (error severity since unhealthy).
        assert len(fake.calls) == 1
        _, severity, _ = fake.calls[0]
        assert severity == "error"
        capsys.readouterr()  # drain printed output

    def test_computation_is_print_free(self, tmp_path, capsys) -> None:
        # run_health_check computes and dispatches only — the CLI renders.
        fake = _FakeAlerter()
        patches = [
            patch(f"{CORE_PATHS}.US_EQUITY_DIR", tmp_path / "absent_us"),
            patch(f"{CORE_PATHS}.CN_ASHARE_DIR", tmp_path / "absent_cn"),
            patch(f"{CORE_PATHS}.HK_SG_EQUITY_DIR", tmp_path / "absent_hk"),
            patch(f"{CORE_PATHS}.JPX_EQUITY_DIR", tmp_path / "absent_jpx"),
            patch(f"{CORE_PATHS}.KRX_EQUITY_DIR", tmp_path / "absent_krx"),
            patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", tmp_path / "absent_gold"),
            patch(f"{HEALTH_PATHS}.BRONZE_RAW_ARTICLES_DIR", tmp_path / "absent_bronze"),
            patch(f"{HEALTH_PATHS}.SILVER_PROCESSED_ARTICLES_DIR", tmp_path / "absent_silver"),
            patch(f"{HEALTH_PATHS}.SILVER_SEC_EXTRACTIONS_DIR", tmp_path / "absent_sec"),
        ]
        for p in patches:
            p.start()
        try:
            m = PipelineMonitor(alerter=fake)
            m.run_health_check()
        finally:
            for p in patches:
                p.stop()
        # No report rendering from the library — the CLI renders (structlog
        # diagnostics may still hit stdout; the banner must not).
        out = capsys.readouterr().out
        assert "PIPELINE HEALTH MONITOR" not in out
        assert "Pipeline is" not in out
        assert "PASS" not in out
        # Per-check outcomes are exposed for the CLI to render (missing
        # unstructured dirs stay "fresh" by design; the other three fail).
        assert set(m.check_results) == {"Data Freshness", "Data Quality", "Feature Store", "Unstructured Freshness"}
        assert not all(m.check_results.values())
