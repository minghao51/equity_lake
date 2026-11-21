"""Health reporting scoped to a real-source root (ADR-0030 decision 5).

A real root carries the `us_equity` slice only and no Gold/enrichment tables by
design, so `equity monitor --lake-dir data/lake_real` must report the checks it can
actually evaluate. Each test names its falsifier: dropping the market scope makes
the price sweeps error on absent tables, and reporting an unevaluable check as PASS
(or as FAIL) changes an assertion here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl

from equity_lake.monitoring.health import (  # type: ignore[import-untyped]
    CHECK_NOT_APPLICABLE,
    CHECK_PASS,
    PipelineMonitor,
)

CORE_PATHS = "equity_lake.core.paths"
HEALTH_PATHS = "equity_lake.monitoring.health"

_OHLCV_SCHEMA = {
    "ticker": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}


def _write_partition(base_dir: Path, partition_date: date, rows: list[dict[str, object]]) -> None:
    part_dir = base_dir / f"date={partition_date.isoformat()}"
    part_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=_OHLCV_SCHEMA).write_parquet(part_dir / "part.parquet")


def _ohlcv_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {"ticker": "AAA", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100.0}
    row.update(overrides)
    return row


class _Roots:
    """Temporary canonical root plus a sibling real-source root, fully patched."""

    def __init__(self, tmp_path: Path) -> None:
        self.lake = tmp_path / "lake"
        self.real = tmp_path / "lake_real"
        self.subdirs = {"us_equity": "us", "cn_ashare": "cn", "hk_sg_equity": "hk_sg", "jpx_equity": "jpx", "krx_equity": "krx"}

    def patches(self) -> list[object]:
        patchers = [
            patch(f"{CORE_PATHS}.LAKE_DIR", self.lake),
            patch(f"{HEALTH_PATHS}.LAKE_DIR", self.lake),
            patch(f"{CORE_PATHS}.US_EQUITY_DIR", self.lake / "us"),
            patch(f"{CORE_PATHS}.CN_ASHARE_DIR", self.lake / "cn"),
            patch(f"{CORE_PATHS}.HK_SG_EQUITY_DIR", self.lake / "hk_sg"),
            patch(f"{CORE_PATHS}.JPX_EQUITY_DIR", self.lake / "jpx"),
            patch(f"{CORE_PATHS}.KRX_EQUITY_DIR", self.lake / "krx"),
            patch(f"{HEALTH_PATHS}.BRONZE_RAW_ARTICLES_DIR", self.lake / "raw_articles"),
            patch(f"{HEALTH_PATHS}.SILVER_PROCESSED_ARTICLES_DIR", self.lake / "processed_articles"),
            patch(f"{HEALTH_PATHS}.SILVER_SEC_EXTRACTIONS_DIR", self.lake / "sec_extractions"),
            patch(f"{HEALTH_PATHS}.GOLD_FEATURES_DIR", self.lake / "features"),
            patch(f"{HEALTH_PATHS}.market_now", return_value=date(2024, 1, 10)),
            patch(f"{HEALTH_PATHS}.is_trading_day", return_value=True),
        ]
        return patchers

    def write_price(self, root: Path, market: str, partition_date: date) -> None:
        _write_partition(root / self.subdirs[market], partition_date, [_ohlcv_row()])


class _Started:
    """Context manager that starts a patch list and stops it in reverse order."""

    def __init__(self, patchers: list[object]) -> None:
        self.patchers = patchers

    def __enter__(self) -> None:
        for patcher in self.patchers:
            patcher.start()  # type: ignore[attr-defined]

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()  # type: ignore[attr-defined]


class TestPriceMarketScope:
    def test_a_real_root_is_judged_on_its_own_slice_only(self, tmp_path: Path) -> None:
        """Four absent markets must not be read as four failing markets.

        Falsified by: sweeping ``PRICE_MARKETS`` regardless of root, which makes the
        us_equity-only root's freshness query error on `cn_ashare` parquet and report
        a check failure that says nothing about the landed slice.
        """
        roots = _Roots(tmp_path)
        roots.write_price(roots.real, "us_equity", date(2024, 1, 9))
        with _Started(roots.patches()):
            monitor = PipelineMonitor(max_age_days=2, lake_dir=roots.real)
            try:
                assert monitor.price_markets == ("us_equity",)
                assert monitor.check_data_freshness() is True
                assert monitor.metrics["data_freshness"]["fresh_markets"] == ["us_equity"]
                assert monitor.check_data_quality() is True
                assert [alert for alert in monitor.alerts if "check failed" in alert] == []
            finally:
                monitor.close()

    def test_the_canonical_root_still_sweeps_all_five_markets(self, tmp_path: Path) -> None:
        """The canonical report is unchanged: absent markets still fail it.

        Falsified by: scoping every root to the real-root slice, which would hide a
        genuinely missing market in the demo lake.
        """
        roots = _Roots(tmp_path)
        roots.write_price(roots.lake, "us_equity", date(2024, 1, 9))
        with _Started(roots.patches()):
            monitor = PipelineMonitor(max_age_days=2)
            try:
                assert len(monitor.price_markets) == 5
                assert monitor.check_data_freshness() is False
                assert any("check failed" in alert for alert in monitor.alerts)
            finally:
                monitor.close()

    def test_an_empty_real_root_still_fails(self, tmp_path: Path) -> None:
        """Scoping is not leniency: a real root with no price table has no evidence.

        Falsified by: treating a scoped-but-absent table as not-applicable, which would
        report a never-written root as acceptable.
        """
        roots = _Roots(tmp_path)
        roots.real.mkdir(parents=True)
        with _Started(roots.patches()):
            monitor = PipelineMonitor(max_age_days=2, lake_dir=roots.real)
            try:
                assert monitor.check_data_freshness() is False
            finally:
                monitor.close()


class TestNotApplicableChecks:
    def test_the_feature_store_is_not_applicable_on_a_real_root(self, tmp_path: Path) -> None:
        """Gold is absent by design there, so it is neither a pass nor a failure.

        Falsified by: returning False (the pre-fix verdict, a defect of the design) or
        True without recording why (a pass nobody verified).
        """
        roots = _Roots(tmp_path)
        with _Started(roots.patches()):
            monitor = PipelineMonitor(lake_dir=roots.real)
            try:
                assert monitor.check_feature_store() is True
                assert monitor.check_status["Feature Store"] == CHECK_NOT_APPLICABLE
                assert "no Gold" in monitor.check_notes["Feature Store"]
                assert monitor.alerts == []
            finally:
                monitor.close()

    def test_the_unstructured_tables_are_not_applicable_on_a_real_root(self, tmp_path: Path) -> None:
        """Enrichment tables are never written to a real root, so absence proves nothing.

        Falsified by: the vacuous PASS this replaces — three missing tables passing a
        freshness check that read none of them.
        """
        roots = _Roots(tmp_path)
        with _Started(roots.patches()):
            monitor = PipelineMonitor(lake_dir=roots.real)
            try:
                assert monitor.check_unstructured_freshness() is True
                assert monitor.check_status["Unstructured Freshness"] == CHECK_NOT_APPLICABLE
                assert "never written to a real-source root" in monitor.check_notes["Unstructured Freshness"]
            finally:
                monitor.close()

    def test_a_closed_quality_window_is_not_applicable_on_a_real_root(self, tmp_path: Path) -> None:
        """The quality window measures recent rows; a closed slice has none to measure.

        Falsified by: claiming PASS for a window that contained no rows at all, which is
        the behavioural equivalent of the old vacuous pass.
        """
        roots = _Roots(tmp_path)
        roots.write_price(roots.real, "us_equity", date(2024, 1, 9))  # outside the 7-day window
        with _Started([p for p in roots.patches()]):
            monitor = PipelineMonitor(lake_dir=roots.real)
            try:
                # The window is relative to CURRENT_DATE, so no row can fall inside it.
                assert monitor.check_data_quality() is True
                assert monitor.check_status["Data Quality"] == CHECK_NOT_APPLICABLE
                assert "7-day window" in monitor.check_notes["Data Quality"]
            finally:
                monitor.close()

    def test_the_canonical_root_keeps_its_missing_feature_store_failure(self, tmp_path: Path) -> None:
        """Unchanged behaviour for the demo lake: no Gold is a real defect there."""
        roots = _Roots(tmp_path)
        with _Started(roots.patches()):
            monitor = PipelineMonitor(lake_dir=None)
            try:
                assert monitor.check_feature_store() is False
                assert monitor.check_status == {}
                assert monitor.alerts
            finally:
                monitor.close()

    def test_the_canonical_root_keeps_its_unverified_quality_pass(self, tmp_path: Path) -> None:
        """Pinned: only the real root changes, so the canonical report stays the same.

        With every market's table present but no row inside the 7-day window the query
        succeeds and returns no groups, which the canonical root has always reported as
        a plain PASS. The real root's not-applicable status is the only difference.
        """
        roots = _Roots(tmp_path)
        for market in roots.subdirs:
            roots.write_price(roots.lake, market, date(2024, 1, 9))
        with _Started(roots.patches()):
            monitor = PipelineMonitor(lake_dir=None)
            try:
                assert monitor.check_data_quality() is True
                assert monitor.check_status == {}
                assert monitor.check_notes == {}
            finally:
                monitor.close()


class TestReportShape:
    def test_the_report_adds_status_notes_and_scope_without_changing_check_results(self, tmp_path: Path) -> None:
        """`check_results` keeps its boolean shape for the dashboard exporter.

        Falsified by: putting status strings into `check_results`, which breaks every
        consumer that reads it as pass/fail (`dashboard/exporter.py` loads this file).
        """
        roots = _Roots(tmp_path)
        roots.write_price(roots.real, "us_equity", date(2024, 1, 9))
        with _Started(roots.patches()):
            monitor = PipelineMonitor(max_age_days=2, lake_dir=roots.real)
            try:
                monitor.run_health_check()
                report_path = tmp_path / "health-report.json"
                monitor.save_report(report_path)
            finally:
                monitor.close()

        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["check_results"] == {"Data Freshness": True, "Data Quality": True, "Feature Store": True, "Unstructured Freshness": True}
        assert report["check_status"]["Feature Store"] == CHECK_NOT_APPLICABLE
        assert report["check_status"]["Data Freshness"] == CHECK_PASS
        assert report["check_notes"]["Unstructured Freshness"]
        assert report["scope"] == {"lake_root": "real_source", "price_markets": ["us_equity"]}
        assert report["healthy"] is True

    def test_a_canonical_report_keeps_the_canonical_scope(self, tmp_path: Path) -> None:
        """The demo root's report states the full registry, unchanged."""
        roots = _Roots(tmp_path)
        for market in roots.subdirs:
            roots.write_price(roots.lake, market, date(2024, 1, 9))
        with _Started(roots.patches()):
            monitor = PipelineMonitor(max_age_days=2)
            try:
                monitor.run_health_check()
                report_path = tmp_path / "canonical-report.json"
                monitor.save_report(report_path)
            finally:
                monitor.close()

        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["scope"]["lake_root"] == "canonical"
        assert len(report["scope"]["price_markets"]) == 5
