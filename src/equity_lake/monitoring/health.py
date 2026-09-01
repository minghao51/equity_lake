#!/usr/bin/env python3
"""
Pipeline Health Monitoring

Monitors pipeline health, data freshness, and data quality.
Sends alerts on issues. Driven via the ``equity monitor`` Typer command.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import structlog

from equity_lake.core.calendar import is_trading_day, market_now

# Unstructured/feature tables are referenced by name in the query f-strings;
# price-market bronze paths come from the registry via _price_market_paths().
from equity_lake.core.paths import (
    BRONZE_RAW_ARTICLES_DIR,
    GOLD_FEATURES_DIR,
    PRICE_MARKETS,
    SILVER_PROCESSED_ARTICLES_DIR,
    SILVER_SEC_EXTRACTIONS_DIR,
    market_dir,
)
from equity_lake.monitoring.alerting import Alerter, build_alerter
from equity_lake.storage.lake_reader import duckdb_scan_for, ensure_delta_extension

logger = structlog.get_logger()

# Default per-table freshness expectations for the unstructured check (days).
# Mirrors MonitoringSettings.table_max_age_days — news/articles refresh daily,
# SEC filings arrive quarterly. Transcripts (when split into their own table)
# would sit around 35 (monthly).
DEFAULT_TABLE_MAX_AGE_DAYS: dict[str, int] = {
    "bronze/raw_articles": 2,
    "silver/processed_articles": 2,
    "silver/sec_extractions": 95,
}

_MARKET_DISPLAY = {
    "us_equity": "US Equity",
    "cn_ashare": "China A-Share",
    "hk_sg_equity": "HK/SG Equity",
    "jpx_equity": "JPX Equity",
    "krx_equity": "KRX Equity",
}

# Price-market registry: iterating ``core.paths.PRICE_MARKETS`` drives both the
# freshness and quality checks, so every market classified as a required price
# market is monitored automatically (ADR-0010).


def _price_market_paths() -> dict[str, Path]:
    """Resolve price-market -> bronze parquet path at call time.

    Reads the registry and resolves each entry's directory via
    ``core.paths.market_dir`` (which looks up the module-level ``*_DIR``
    constants via ``getattr``) fresh on each call, so runtime patches of those
    constants — e.g. tests pointing them at tmp dirs — are honoured.
    """
    return {market: market_dir(market) for market in PRICE_MARKETS}


def _date_scalar(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return None


class PipelineMonitor:
    """Monitor pipeline health and data quality."""

    def __init__(
        self,
        max_age_days: int = 2,
        null_threshold_pct: float = 5.0,
        market_max_age_days: dict[str, int] | None = None,
        table_max_age_days: dict[str, int] | None = None,
        verbose: bool = False,
        alerter: Alerter | None = None,
    ):
        """
        Initialize pipeline monitor.

        Args:
            max_age_days: Maximum allowed data age in days (price markets and
                the feature store; per-market/per-table maps override it).
            null_threshold_pct: Max acceptable null percentage
            market_max_age_days: Per price-market freshness overrides
                (e.g. ``{"krx_equity": 3}``); falls back to *max_age_days*.
            table_max_age_days: Per-table freshness expectations for the
                unstructured check (news/articles daily, SEC quarterly);
                falls back to *max_age_days* for unlisted tables.
            verbose: Enable verbose logging
            alerter: Alert dispatcher (defaults to console)
        """
        self.max_age_days = max_age_days
        self.null_threshold_pct = null_threshold_pct
        self.market_max_age_days = market_max_age_days or {}
        self.table_max_age_days = table_max_age_days if table_max_age_days is not None else dict(DEFAULT_TABLE_MAX_AGE_DAYS)
        self.verbose = verbose
        self.alerter = alerter or build_alerter()

        self.conn = duckdb.connect(":memory:")
        ensure_delta_extension(self.conn)
        self.alerts: list[str] = []
        self._alert_keys: set[tuple[str, str]] = set()
        self.metrics: dict = {}
        self.check_results: dict[str, bool] = {}

    def close(self) -> None:
        """Close the monitor's DuckDB connection (idempotent)."""
        if self.conn is not None:
            self.conn.close()

    def __enter__(self) -> "PipelineMonitor":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _market_max_age(self, market: str) -> int:
        """Freshness expectation for one price market (override or default)."""
        return self.market_max_age_days.get(market, self.max_age_days)

    def _add_alert(self, check: str, target: str, message: str) -> None:
        """Record an alert, deduplicated by ``(check, target)``.

        Alerts accumulate per market/table per check and are never cleared
        between runs of a long-lived monitor — the first alert for a given
        (check, target) pair wins so repeated runs don't pile up duplicates.
        """
        key = (check, target)
        if key in self._alert_keys:
            return
        self._alert_keys.add(key)
        self.alerts.append(message)

    @staticmethod
    def _last_trading_day(market: str) -> date:
        from datetime import timedelta

        d = market_now(market)
        for _ in range(10):
            d -= timedelta(days=1)
            if is_trading_day(market, d):
                return d
        return d

    # -------------------------------------------------------------------------
    # Health Checks
    # -------------------------------------------------------------------------

    def check_data_freshness(self) -> bool:
        """
        Check if data is fresh (not stale).

        Returns:
            True if all markets have fresh data, False otherwise
        """
        logger.info("Checking data freshness...")

        market_arms = " UNION ALL ".join(
            f"""
            SELECT
                '{market}' as market,
                MAX(date) as latest_date,
                COUNT(DISTINCT date) as date_count
            FROM {duckdb_scan_for(path)}
            """
            for market, path in _price_market_paths().items()
        )

        query = f"SELECT market, latest_date, date_count FROM ({market_arms})"

        try:
            df = self.conn.execute(query).pl()

            fresh_markets = []
            stale_markets = []

            for row in df.iter_rows(named=True):
                market = row["market"]
                latest_date = _date_scalar(row["latest_date"])
                date_count = row["date_count"]

                if latest_date is None:
                    self._add_alert("data_freshness", market, f"\u274c {market}: No data found")
                    stale_markets.append(market)
                    continue

                market_today = market_now(market)
                is_today_trading = is_trading_day(market, market_today)
                reference_date = market_today if is_today_trading else self._last_trading_day(market)
                age_days = (reference_date - latest_date).days

                status = "\u2705" if age_days <= self._market_max_age(market) else "\u26a0\ufe0f"
                logger.info(
                    "data_freshness_check",
                    market=market,
                    latest_date=str(latest_date),
                    age_days=age_days,
                    date_count=date_count,
                    status=status,
                )

                if age_days > self._market_max_age(market):
                    self._add_alert(
                        "data_freshness",
                        market,
                        f"\u26a0\ufe0f  {market} data is stale: {age_days} days old (latest: {latest_date})",
                    )
                    stale_markets.append(market)
                else:
                    fresh_markets.append(market)

            self.metrics["data_freshness"] = {
                "fresh_markets": fresh_markets,
                "stale_markets": stale_markets,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }

            return len(stale_markets) == 0

        except Exception as e:
            logger.error("data_freshness_check_failed", error=str(e))
            self._add_alert("data_freshness", "check", f"❌ Data freshness check failed: {e}")
            return False

    def check_data_quality(self) -> bool:
        """
        Check for missing/null values in critical columns.

        Returns:
            True if data quality is acceptable, False otherwise
        """
        logger.info("Checking data quality...")

        market_selects = " UNION ALL ".join(
            f"SELECT '{market}' as market, * FROM {duckdb_scan_for(path)}" for market, path in _price_market_paths().items()
        )

        query = f"""
            SELECT
                market,
                COUNT(*) as total_rows,
                SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) as null_close,
                SUM(CASE WHEN volume IS NULL THEN 1 ELSE 0 END) as null_volume,
                SUM(CASE WHEN open IS NULL THEN 1 ELSE 0 END) as null_open,
                SUM(CASE WHEN high IS NULL THEN 1 ELSE 0 END) as null_high,
                SUM(CASE WHEN low IS NULL THEN 1 ELSE 0 END) as null_low
            FROM ({market_selects})
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY market
        """

        try:
            df = self.conn.execute(query).pl()

            quality_issues = []

            for row in df.iter_rows(named=True):
                market = row["market"]
                total_rows = row["total_rows"]

                if total_rows == 0:
                    self._add_alert("data_quality", market, f"⚠️  {market}: No data in last 7 days")
                    quality_issues.append(market)
                    continue

                null_pct_close = (row["null_close"] / total_rows) * 100
                null_pct_volume = (row["null_volume"] / total_rows) * 100

                if self.verbose:
                    logger.info(
                        "quality_detail",
                        market=market,
                        null_pct_close=round(null_pct_close, 2),
                        null_pct_volume=round(null_pct_volume, 2),
                        total_rows=total_rows,
                    )

                if null_pct_close > self.null_threshold_pct:
                    self._add_alert(
                        "data_quality",
                        f"{market} close",
                        f"⚠️  {market}: {null_pct_close:.1f}% null close prices (threshold: {self.null_threshold_pct}%)",
                    )
                    quality_issues.append(market)

                if null_pct_volume > self.null_threshold_pct:
                    self._add_alert(
                        "data_quality",
                        f"{market} volume",
                        f"⚠️  {market}: {null_pct_volume:.1f}% null volume (threshold: {self.null_threshold_pct}%)",
                    )
                    quality_issues.append(market)

            self.metrics["data_quality"] = {
                "issues_found": len(quality_issues),
                "markets_with_issues": quality_issues,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }

            return len(quality_issues) == 0

        except Exception as e:
            logger.error("data_quality_check_failed", error=str(e))
            self._add_alert("data_quality", "check", f"❌ Data quality check failed: {e}")
            return False

    # NOTE: there is deliberately no log-file check anymore. The previous
    # check_pipeline_logs scanned monitor_pipeline.log / ingest_daily.log /
    # sync_from_s3.log — files that no component in this repo writes (only
    # structlog JSON to stderr/cron redirects), so the check always passed.
    # Alerting on artifacts nothing produces is false confidence; grep-provable
    # removal rather than repointing at another guess.

    def check_feature_store(self) -> bool:
        """
        Check if feature store has recent data.

        Returns:
            True if features are fresh, False otherwise
        """
        logger.info("Checking feature store...")

        feature_dir = GOLD_FEATURES_DIR

        if not feature_dir.exists():
            self._add_alert("feature_store", "feature_store", "⚠️  Feature store does not exist")
            return False

        # Check for recent feature files
        query = f"""
            SELECT
                COUNT(*) as total_rows,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(date) as latest_date
            FROM {duckdb_scan_for(feature_dir)}
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
        """

        try:
            df = self.conn.execute(query).pl()

            if df.is_empty() or int(df["total_rows"][0]) == 0:
                self._add_alert("feature_store", "feature_store", "⚠️  No features in last 7 days")
                return False

            total_rows = int(df["total_rows"][0])
            unique_tickers = int(df["unique_tickers"][0])
            latest_date = _date_scalar(df["latest_date"][0])
            if latest_date is None:
                self._add_alert("feature_store", "feature_store", "⚠️  Feature store latest date is missing")
                return False

            age_days = (market_now("us_equity") - latest_date).days

            if age_days > self.max_age_days:
                self._add_alert("feature_store", "feature_store", f"⚠️  Features are stale: {age_days} days old (latest: {latest_date})")
                return False

            logger.info("feature_store_status", total_rows=total_rows, unique_tickers=unique_tickers, latest_date=str(latest_date))

            self.metrics["feature_store"] = {
                "total_rows": int(total_rows),
                "unique_tickers": int(unique_tickers),
                "latest_date": str(latest_date),
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }

            return True

        except Exception as e:
            # Align with check_data_freshness/check_data_quality: a corrupt or
            # unreadable feature store must read as FAILED, never as healthy.
            logger.error("feature_store_check_failed", error=str(e))
            self._add_alert("feature_store", "check", f"❌ Feature store check failed: {e}")
            return False

    def check_unstructured_freshness(self) -> bool:
        """Check freshness of bronze and silver unstructured tables.

        Monitors ``bronze/raw_articles`` and ``silver/processed_articles``
        for recent activity. Stale or empty tables indicate the ingestion
        pipeline for RSS/Reddit/StockTwits/Transcripts may be failing.
        """
        logger.info("Checking unstructured data freshness...")

        tables = [
            ("bronze/raw_articles", BRONZE_RAW_ARTICLES_DIR),
            ("silver/processed_articles", SILVER_PROCESSED_ARTICLES_DIR),
            ("silver/sec_extractions", SILVER_SEC_EXTRACTIONS_DIR),
        ]

        all_fresh = True
        unstructured_metrics: dict[str, Any] = {}
        missing_tables: list[str] = []

        for table_name, table_path in tables:
            if not table_path.exists():
                # A missing table cannot be "fresh" — it is unverifiable. Keep the
                # check passing (a fresh checkout should not alert), but surface
                # the vacuous pass loudly instead of debug-only.
                missing_tables.append(table_name)
                unstructured_metrics[table_name] = {"status": "missing"}
                continue

            try:
                query = f"""
                    SELECT
                        COUNT(*) as total_rows,
                        MAX(date) as latest_date
                    FROM {duckdb_scan_for(table_path)}
                """
                df = self.conn.execute(query).pl()

                if df.is_empty() or int(df["total_rows"][0]) == 0:
                    self._add_alert("unstructured_freshness", table_name, f"⚠️  {table_name}: No data found")
                    unstructured_metrics[table_name] = {"status": "empty"}
                    all_fresh = False
                    continue

                total_rows = int(df["total_rows"][0])
                latest_date = _date_scalar(df["latest_date"][0])

                age_days = (market_now("us_equity") - latest_date).days if latest_date else 999
                max_age = self.table_max_age_days.get(table_name, self.max_age_days)

                if age_days > max_age:
                    self._add_alert(
                        "unstructured_freshness",
                        table_name,
                        f"⚠️  {table_name}: data is stale ({age_days} days old, latest: {latest_date}; expected within {max_age})",
                    )
                    all_fresh = False
                else:
                    logger.info("unstructured_fresh", table_name=table_name, total_rows=total_rows, latest_date=str(latest_date))

                unstructured_metrics[table_name] = {
                    "total_rows": total_rows,
                    "latest_date": str(latest_date) if latest_date else None,
                    "age_days": age_days,
                    "max_age_days": max_age,
                }

            except Exception as e:
                # Align with check_data_freshness/check_data_quality: a per-table
                # read failure fails the check (and alerts) instead of being
                # logged at debug and silently treated as fresh.
                logger.error("table_check_failed", table_name=table_name, error=str(e))
                self._add_alert("unstructured_freshness", f"{table_name} check", f"❌ {table_name}: freshness check failed: {e}")
                unstructured_metrics[table_name] = {"status": "error", "error": str(e)}
                all_fresh = False

        if missing_tables:
            logger.warning("unstructured_tables_missing", tables=missing_tables, note="check passes vacuously; no data to verify")

        self.metrics["unstructured_freshness"] = {
            **unstructured_metrics,
            "missing_tables": missing_tables,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }

        return all_fresh

    # -------------------------------------------------------------------------
    # Run Health Check
    # -------------------------------------------------------------------------

    def run_health_check(self) -> bool:
        """Run all health checks, dispatch alerts, and return overall health.

        Computation and alert dispatch only — rendering the pass/fail table
        and summary is the CLI's job (per-check outcomes land in
        ``self.check_results``), and console output belongs to the alerter.
        This keeps every alert printed exactly once (by the ConsoleAlerter).

        Returns:
            True if all checks pass, False otherwise
        """
        self.check_results = {
            "Data Freshness": self.check_data_freshness(),
            "Data Quality": self.check_data_quality(),
            "Feature Store": self.check_feature_store(),
            "Unstructured Freshness": self.check_unstructured_freshness(),
        }

        all_healthy = all(self.check_results.values())

        if self.alerts:
            self.alerter.send_alert(self.alerts, severity="warning" if all_healthy else "error", metrics=self.metrics)

        return all_healthy

    def save_report(self, output_file: Path) -> None:
        """Save health report to JSON file."""
        report = {
            "alerts": self.alerts,
            "metrics": self.metrics,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }

        with open(output_file, "w") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info("health_report_saved", output_file=str(output_file))
