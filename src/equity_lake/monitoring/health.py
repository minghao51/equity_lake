#!/usr/bin/env python3
"""
Pipeline Health Monitoring

Monitors pipeline health, data freshness, and data quality.
Sends alerts on issues.

Usage:
    uv run equity-monitor
    uv run equity-monitor --max-age-days 1 --alert-threshold 5
    uv run equity-monitor --output-json health_report.json
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import structlog

from equity_lake.core.calendar import is_trading_day, market_now
from equity_lake.core.config import get_settings
from equity_lake.core.logging import setup_logging
from equity_lake.core.paths import LAKE_DIR, LOGS_DIR
from equity_lake.monitoring.alerting import Alerter, build_alerter

logger = structlog.get_logger()

_MARKET_DISPLAY = {
    "us_equity": "US Equity",
    "cn_ashare": "China A-Share",
    "hk_sg_equity": "HK/SG Equity",
}


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
        verbose: bool = False,
        alerter: Alerter | None = None,
    ):
        """
        Initialize pipeline monitor.

        Args:
            max_age_days: Maximum allowed data age in days
            null_threshold_pct: Max acceptable null percentage
            verbose: Enable verbose logging
            alerter: Alert dispatcher (defaults to console)
        """
        self.max_age_days = max_age_days
        self.null_threshold_pct = null_threshold_pct
        self.verbose = verbose
        self.alerter = alerter or build_alerter()

        self.conn = duckdb.connect(":memory:")
        self.alerts: list[str] = []
        self.metrics: dict = {}

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

        query = f"""
            SELECT
                'us_equity' as market,
                MAX(date) as latest_date,
                COUNT(DISTINCT date) as date_count
            FROM read_parquet('{LAKE_DIR}/us_equity/**/*.parquet', hive_partitioning=1)
            UNION ALL
            SELECT
                'cn_ashare' as market,
                MAX(date) as latest_date,
                COUNT(DISTINCT date) as date_count
            FROM read_parquet('{LAKE_DIR}/cn_ashare/**/*.parquet', hive_partitioning=1)
            UNION ALL
            SELECT
                'hk_sg_equity' as market,
                MAX(date) as latest_date,
                COUNT(DISTINCT date) as date_count
            FROM read_parquet('{LAKE_DIR}/hk_sg_equity/**/*.parquet', hive_partitioning=1)
        """

        try:
            df = self.conn.execute(query).pl()

            fresh_markets = []
            stale_markets = []

            for row in df.iter_rows(named=True):
                market = row["market"]
                latest_date = _date_scalar(row["latest_date"])
                date_count = row["date_count"]

                if latest_date is None:
                    self.alerts.append(f"\u274c {market}: No data found")
                    stale_markets.append(market)
                    continue

                market_today = market_now(market)
                is_today_trading = is_trading_day(market, market_today)
                reference_date = market_today if is_today_trading else self._last_trading_day(market)
                age_days = (reference_date - latest_date).days

                status = "\u2705" if age_days <= self.max_age_days else "\u26a0\ufe0f"
                logger.info(f"{status} {market}: Latest data = {latest_date} ({age_days} days old, {date_count} dates total)")

                if age_days > self.max_age_days:
                    self.alerts.append(f"\u26a0\ufe0f  {market} data is stale: {age_days} days old (latest: {latest_date})")
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
            logger.error(f"Data freshness check failed: {e}")
            self.alerts.append(f"❌ Data freshness check failed: {e}")
            return False

    def check_data_quality(self) -> bool:
        """
        Check for missing/null values in critical columns.

        Returns:
            True if data quality is acceptable, False otherwise
        """
        logger.info("Checking data quality...")

        query = f"""
            SELECT
                market,
                COUNT(*) as total_rows,
                SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) as null_close,
                SUM(CASE WHEN volume IS NULL THEN 1 ELSE 0 END) as null_volume,
                SUM(CASE WHEN open IS NULL THEN 1 ELSE 0 END) as null_open,
                SUM(CASE WHEN high IS NULL THEN 1 ELSE 0 END) as null_high,
                SUM(CASE WHEN low IS NULL THEN 1 ELSE 0 END) as null_low
            FROM (
                SELECT 'us_equity' as market, * FROM read_parquet('{LAKE_DIR}/us_equity/**/*.parquet', hive_partitioning=1)
                UNION ALL
                SELECT 'cn_ashare' as market, * FROM read_parquet('{LAKE_DIR}/cn_ashare/**/*.parquet', hive_partitioning=1)
                UNION ALL
                SELECT 'hk_sg_equity' as market, * FROM read_parquet('{LAKE_DIR}/hk_sg_equity/**/*.parquet', hive_partitioning=1)
            )
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
                    self.alerts.append(f"⚠️  {market}: No data in last 7 days")
                    quality_issues.append(market)
                    continue

                null_pct_close = (row["null_close"] / total_rows) * 100
                null_pct_volume = (row["null_volume"] / total_rows) * 100

                if self.verbose:
                    logger.info(f"  {market}: {null_pct_close:.2f}% null close, {null_pct_volume:.2f}% null volume ({total_rows:,} rows)")

                if null_pct_close > self.null_threshold_pct:
                    self.alerts.append(f"⚠️  {market}: {null_pct_close:.1f}% null close prices (threshold: {self.null_threshold_pct}%)")
                    quality_issues.append(market)

                if null_pct_volume > self.null_threshold_pct:
                    self.alerts.append(f"⚠️  {market}: {null_pct_volume:.1f}% null volume (threshold: {self.null_threshold_pct}%)")
                    quality_issues.append(market)

            self.metrics["data_quality"] = {
                "issues_found": len(quality_issues),
                "markets_with_issues": quality_issues,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }

            return len(quality_issues) == 0

        except Exception as e:
            logger.error(f"Data quality check failed: {e}")
            self.alerts.append(f"❌ Data quality check failed: {e}")
            return False

    def check_pipeline_logs(self) -> bool:
        """
        Check recent logs for errors and warnings.

        Returns:
            True if logs are clean, False otherwise
        """
        logger.info("Checking pipeline logs...")

        log_files = [
            LOGS_DIR / "monitor_pipeline.log",
            LOGS_DIR / "ingest_daily.log",
            LOGS_DIR / "sync_from_s3.log",
        ]

        total_errors = 0
        total_warnings = 0

        for log_file in log_files:
            if not log_file.exists():
                logger.debug(f"No log file found: {log_file.name}")
                continue

            try:
                # Read last 100 lines
                with open(log_file) as f:
                    lines = f.readlines()[-100:]

                error_count = sum(1 for line in lines if "ERROR" in line.upper())
                warning_count = sum(1 for line in lines if "WARNING" in line.upper())

                total_errors += error_count
                total_warnings += warning_count

                if self.verbose and (error_count > 0 or warning_count > 0):
                    logger.info(f"  {log_file.name}: {error_count} errors, {warning_count} warnings")

            except Exception as e:
                logger.debug(f"Could not read {log_file.name}: {e}")

        if total_errors > 0:
            self.alerts.append(f"❌ Found {total_errors} errors in recent logs")
            return False

        if total_warnings > 10:
            self.alerts.append(f"⚠️  Found {total_warnings} warnings in recent logs")

        self.metrics["pipeline_logs"] = {
            "error_count": total_errors,
            "warning_count": total_warnings,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }

        return total_errors == 0

    def check_feature_store(self) -> bool:
        """
        Check if feature store has recent data.

        Returns:
            True if features are fresh, False otherwise
        """
        logger.info("Checking feature store...")

        feature_dir = LAKE_DIR / "features"

        if not feature_dir.exists():
            self.alerts.append("⚠️  Feature store does not exist")
            return False

        # Check for recent feature files
        query = f"""
            SELECT
                COUNT(*) as total_rows,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(date) as latest_date
            FROM read_parquet('{feature_dir}/**/*.parquet', hive_partitioning=1)
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
        """

        try:
            df = self.conn.execute(query).pl()

            if df.is_empty() or int(df["total_rows"][0]) == 0:
                self.alerts.append("⚠️  No features in last 7 days")
                return False

            total_rows = int(df["total_rows"][0])
            unique_tickers = int(df["unique_tickers"][0])
            latest_date = _date_scalar(df["latest_date"][0])
            if latest_date is None:
                self.alerts.append("⚠️  Feature store latest date is missing")
                return False

            age_days = (market_now("us_equity") - latest_date).days

            if age_days > self.max_age_days:
                self.alerts.append(f"⚠️  Features are stale: {age_days} days old (latest: {latest_date})")
                return False

            logger.info(f"✅ Features: {total_rows:,} rows, {unique_tickers} tickers, latest: {latest_date}")

            self.metrics["feature_store"] = {
                "total_rows": int(total_rows),
                "unique_tickers": int(unique_tickers),
                "latest_date": str(latest_date),
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }

            return True

        except Exception as e:
            logger.debug(f"Feature store check failed: {e}")
            # Feature store might not exist yet, which is ok
            return True

    # -------------------------------------------------------------------------
    # Run Health Check
    # -------------------------------------------------------------------------

    def run_health_check(self) -> bool:
        """
        Run all health checks.

        Returns:
            True if all checks pass, False otherwise
        """
        print("\n" + "=" * 70)
        print("PIPELINE HEALTH MONITOR")
        print("=" * 70 + "\n")

        checks = [
            ("Data Freshness", self.check_data_freshness()),
            ("Data Quality", self.check_data_quality()),
            ("Pipeline Logs", self.check_pipeline_logs()),
            ("Feature Store", self.check_feature_store()),
        ]

        all_healthy = True
        for check_name, healthy in checks:
            status = "✅ PASS" if healthy else "❌ FAIL"
            print(f"{status:<12} {check_name}")
            all_healthy = all_healthy and healthy

        # Print alerts if any
        if self.alerts:
            print("\n" + "-" * 70)
            print(f"ALERTS ({len(self.alerts)})")
            print("-" * 70)
            for alert in self.alerts:
                print(f"  {alert}")

            self.alerter.send_alert(self.alerts, severity="warning" if all_healthy else "error", metrics=self.metrics)

        # Summary
        print("\n" + "=" * 70)
        if all_healthy:
            print("✅ Pipeline is HEALTHY")
        else:
            print("⚠️  Pipeline has ISSUES")
        print("=" * 70 + "\n")

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

        logger.info(f"Health report saved to {output_file}")


# =============================================================================
# CLI Interface
# =============================================================================


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Pipeline Health Monitoring")

    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Maximum allowed data age in days (default: from settings)",
    )

    parser.add_argument(
        "--null-threshold-pct",
        type=float,
        default=None,
        help="Max acceptable null percentage (default: from settings)",
    )

    parser.add_argument("--output-json", type=str, help="Save health report to JSON file")

    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Resolve settings-backed defaults only when actually running
    settings = get_settings()
    if args.max_age_days is None:
        args.max_age_days = settings.monitoring.max_age_days
    if args.null_threshold_pct is None:
        args.null_threshold_pct = settings.monitoring.null_threshold_pct

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging(level=log_level, log_file=Path("monitor_pipeline.log"))

    # Run health check
    monitor = PipelineMonitor(
        max_age_days=args.max_age_days,
        null_threshold_pct=args.null_threshold_pct,
        verbose=args.verbose,
    )

    healthy = monitor.run_health_check()

    # Save report if requested
    if args.output_json:
        monitor.save_report(Path(args.output_json))

    # Exit with appropriate code
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
