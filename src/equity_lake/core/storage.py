import os
import subprocess
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import duckdb
import pandas as pd
import structlog
from filelock import FileLock

from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    LAKE_DIR,
    US_EQUITY_DIR,
    US_NEWS_DIR,
    US_SOCIAL_SENTIMENT_DIR,
)
from equity_lake.core.schemas import NEWS_COLUMNS, SOCIAL_COLUMNS

logger = structlog.get_logger()

UNSIGNED_FLAG: Final[str] = "--no-sign-request"


class EquityDataDB:
    MARKET_VIEWS = [
        ("us_equity", US_EQUITY_DIR, "us"),
        ("cn_ashare", CN_ASHARE_DIR, "cn"),
        ("hk_sg_equity", HK_SG_EQUITY_DIR, "hk_sg"),
        ("jpx_equity", JPX_EQUITY_DIR, "jpx"),
        ("krx_equity", KRX_EQUITY_DIR, "krx"),
    ]

    def __init__(self, db_path: str | Path | None = ":memory:"):
        self.db_path = db_path if db_path is not None else ":memory:"
        self.con = duckdb.connect(self.db_path)
        self.available_views: list[str] = []
        self._views_initialized = False

    def _ensure_views(self) -> None:
        if self._views_initialized:
            return
        self._views_initialized = True
        logger.info("Setting up unified views...")

        for view_name, data_dir, market_label in self.MARKET_VIEWS:
            self._create_market_view(view_name, data_dir, market_label)

        self._create_unified_view()

        logger.info("Views created successfully")

    def close(self) -> None:
        if hasattr(self, "con") and self.con is not None:
            self.con.close()

    def __enter__(self) -> "EquityDataDB":
        self._ensure_views()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _create_market_view(self, view_name: str, data_dir: Path, market_label: str) -> None:
        if not data_dir.exists():
            logger.warning(f"Data directory not found: {data_dir}")
            return

        parquet_pattern = str(data_dir / "date=*/*.parquet")

        sql = f"""
        CREATE OR REPLACE VIEW {view_name} AS
        SELECT
            *,
            '{market_label}' as market
        FROM read_parquet('{parquet_pattern}', hive_partitioning=1)
        """

        try:
            self.con.execute(sql)
            logger.debug(f"Created view: {view_name}")
            self.available_views.append(view_name)
        except Exception as e:
            logger.error(f"Failed to create view {view_name}: {e}")

    def _create_unified_view(self) -> None:
        if not self.available_views:
            self.con.execute("CREATE OR REPLACE VIEW equity_all AS SELECT NULL::VARCHAR AS ticker WHERE FALSE")
            return

        sql = "CREATE OR REPLACE VIEW equity_all AS " + " UNION ALL ".join(f"SELECT * FROM {view_name}" for view_name in self.available_views)

        try:
            self.con.execute(sql)
            logger.debug("Created unified view: equity_all")
        except Exception as e:
            logger.error(f"Failed to create unified view: {e}")

    def query(self, sql: str) -> pd.DataFrame:
        self._ensure_views()
        try:
            return self.con.execute(sql).df()
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return pd.DataFrame()

    def execute(self, sql: str) -> Any:
        self._ensure_views()
        try:
            return self.con.execute(sql)
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            raise


class QueryExamples:
    def __init__(self, db: EquityDataDB):
        self.db = db

    def query_1_latest_data_summary(self) -> pd.DataFrame:
        logger.info("Running Query 1: Latest Data Summary")

        sql = """
        WITH market_latest AS (
            SELECT market, MAX(date) AS latest_date
            FROM equity_all
            GROUP BY market
        )
        SELECT
            equity_all.market,
            equity_all.date as latest_date,
            COUNT(DISTINCT ticker) as num_tickers,
            COUNT(*) as total_records,
            SUM(volume) as total_volume
        FROM equity_all
        JOIN market_latest
            ON equity_all.market = market_latest.market
           AND equity_all.date = market_latest.latest_date
        GROUP BY equity_all.market, equity_all.date
        ORDER BY equity_all.market
        """

        return self.db.query(sql)

    def query_2_top_volume_stocks(self, days: int = 7) -> pd.DataFrame:
        logger.info(f"Running Query 2: Top {days}-Day Volume Leaders")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        latest_volume AS (
            SELECT
                ticker,
                market,
                SUM(volume) as total_volume,
                AVG(volume) as avg_daily_volume,
                COUNT(DISTINCT date) as trading_days
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
            GROUP BY ticker, market
        )
        SELECT
            ticker,
            market,
            total_volume,
            avg_daily_volume,
            trading_days
        FROM latest_volume
        ORDER BY total_volume DESC
        LIMIT 20
        """

        return self.db.query(sql)

    def query_3_top_gainers_losers(self, days: int = 7) -> pd.DataFrame:
        logger.info(f"Running Query 3: Top {days}-Day Gainers & Losers")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        price_change AS (
            SELECT
                ticker,
                market,
                FIRST(close ORDER BY date) as start_price,
                LAST(close ORDER BY date) as end_price,
                (LAST(close ORDER BY date) - FIRST(close ORDER BY date)) / FIRST(close ORDER BY date) * 100 as pct_change
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
            GROUP BY ticker, market
            HAVING COUNT(DISTINCT date) >= {max(1, days - 2)}
        )
        SELECT
            ticker,
            market,
            start_price,
            end_price,
            pct_change,
            CASE
                WHEN pct_change >= 0 THEN 'GAINER'
                ELSE 'LOSER'
            END as category
        FROM price_change
        ORDER BY pct_change DESC
        LIMIT 30
        """

        return self.db.query(sql)

    def query_4_cross_market_comparison(self, ticker: str) -> pd.DataFrame:
        logger.info(f"Running Query 4: Cross-Market Comparison for {ticker}")

        sql = f"""
        SELECT
            date,
            market,
            ticker,
            close,
            volume
        FROM equity_all
        WHERE ticker = '{ticker.upper()}'
        ORDER BY market, date
        LIMIT 100
        """

        return self.db.query(sql)

    def query_5_moving_averages(self, ticker: str, ma_days: int = 20) -> pd.DataFrame:
        logger.info(f"Running Query 5: {ma_days}-Day Moving Average for {ticker}")

        sql = f"""
        WITH stock_data AS (
            SELECT
                date,
                ticker,
                close,
                AVG(close) OVER (
                    PARTITION BY ticker
                    ORDER BY date
                    ROWS BETWEEN {ma_days - 1} PRECEDING AND CURRENT ROW
                ) as ma_{ma_days}
            FROM equity_all
            WHERE ticker = '{ticker.upper()}'
            ORDER BY date DESC
            LIMIT {ma_days * 2}
        )
        SELECT
            date,
            ticker,
            close,
            ma_{ma_days},
            (close - ma_{ma_days}) / ma_{ma_days} * 100 as pct_diff_from_ma
        FROM stock_data
        WHERE ma_{ma_days} IS NOT NULL
        ORDER BY date DESC
        """

        return self.db.query(sql)

    def query_6_volatility_analysis(self, days: int = 30) -> pd.DataFrame:
        logger.info(f"Running Query 6: {days}-Day Volatility Analysis")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        daily_returns AS (
            SELECT
                ticker,
                market,
                date,
                close,
                LAG(close) OVER (PARTITION BY ticker ORDER BY date) as prev_close,
                (close - LAG(close) OVER (PARTITION BY ticker ORDER BY date)) / LAG(close) OVER (PARTITION BY ticker ORDER BY date) as daily_return
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
        ),
        volatility_stats AS (
            SELECT
                ticker,
                market,
                AVG(ABS(daily_return)) * 100 as avg_daily_move_pct,
                STDDEV(daily_return) * 100 as volatility_pct,
                COUNT(DISTINCT date) as trading_days
            FROM daily_returns
            WHERE daily_return IS NOT NULL
            GROUP BY ticker, market
            HAVING COUNT(DISTINCT date) >= {max(5, days // 2)}
        )
        SELECT
            ticker,
            market,
            avg_daily_move_pct,
            volatility_pct,
            trading_days
        FROM volatility_stats
        ORDER BY volatility_pct DESC
        LIMIT 20
        """

        return self.db.query(sql)

    def query_7_market_summary_stats(self) -> pd.DataFrame:
        logger.info("Running Query 7: Market Summary Statistics")

        sql = """
        WITH market_stats AS (
            SELECT
                market,
                date,
                COUNT(DISTINCT ticker) as num_tickers,
                SUM(volume) as daily_volume,
                AVG(CASE WHEN volume > 0 THEN close END) as avg_price
            FROM equity_all
            WHERE volume > 0
            GROUP BY market, date
        )
        SELECT
            market,
            COUNT(DISTINCT date) as trading_days_in_data,
            AVG(num_tickers) as avg_tickers_per_day,
            AVG(daily_volume) as avg_daily_volume,
            AVG(avg_price) as avg_stock_price
        FROM market_stats
        GROUP BY market
        ORDER BY market
        """

        return self.db.query(sql)

    def query_8_price_range_analysis(self, days: int = 30) -> pd.DataFrame:
        logger.info(f"Running Query 8: {days}-Day Price Range Analysis")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        price_ranges AS (
            SELECT
                ticker,
                market,
                MIN(close) as period_low,
                MAX(close) as period_high,
                LAST(close ORDER BY date) as current_price,
                (LAST(close ORDER BY date) - MIN(close)) / MIN(close) * 100 as pct_from_low,
                (MAX(close) - LAST(close ORDER BY date)) / MAX(close) * 100 as pct_from_high
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
            GROUP BY ticker, market
            HAVING COUNT(DISTINCT date) >= {max(5, days // 2)}
        )
        SELECT
            ticker,
            market,
            period_low,
            period_high,
            current_price,
            pct_from_low,
            pct_from_high
        FROM price_ranges
        ORDER BY pct_from_low DESC
        LIMIT 30
        """

        return self.db.query(sql)


def benchmark_queries(db: EquityDataDB) -> dict[str, float]:
    logger.info("Running query performance benchmarks...")

    queries = QueryExamples(db)
    benchmarks = {}

    benchmark_items = [
        ("latest_summary", lambda: queries.query_1_latest_data_summary()),
        ("top_volume", lambda: queries.query_2_top_volume_stocks(7)),
        ("gainers_losers", lambda: queries.query_3_top_gainers_losers(7)),
        ("volatility", lambda: queries.query_6_volatility_analysis(30)),
        ("market_stats", lambda: queries.query_7_market_summary_stats()),
    ]

    for name, query_func in benchmark_items:
        start = time.time()
        try:
            result = query_func()
            elapsed = time.time() - start
            benchmarks[name] = elapsed
            logger.info(f"  {name}: {elapsed:.3f}s ({len(result)} rows)")
        except Exception as e:
            logger.error(f"  {name}: FAILED - {e}")
            benchmarks[name] = -1

    return benchmarks


class S3Syncer:
    def __init__(
        self,
        bucket: str,
        target_dir: Path,
        workers: int = 16,
        dry_run: bool = False,
        tool: str = "auto",
    ):
        self.bucket = bucket
        self.target_dir = target_dir
        self.workers = workers
        self.dry_run = dry_run
        self.tool = self._detect_tool(tool) if tool == "auto" else tool
        self._use_unsigned_requests = False

        logger.info(f"Initialized S3 syncer with tool: {self.tool}")

    def _detect_tool(self, tool: str) -> str:
        try:
            result = subprocess.run(["s5cmd", "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info("Detected s5cmd (recommended)")
                return "s5cmd"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("s5cmd not found")

        try:
            result = subprocess.run(["aws", "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info("Detected AWS CLI")
                return "aws"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("AWS CLI not found")

        logger.error("No S3 sync tool found. Install s5cmd or AWS CLI.")
        raise RuntimeError("No S3 sync tool found. Install s5cmd or AWS CLI.")

    def _test_s3_access(self) -> bool:
        logger.info(f"Testing access to {self.bucket}")

        try:
            if self.tool == "s5cmd":
                cmd = ["s5cmd", "ls", f"{self.bucket}"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    logger.info("S3 bucket accessible")
                    return True
                logger.error(f"S3 access failed: {result.stderr}")
                return False

            for unsigned in (False, True):
                cmd = ["aws", "s3", "ls", self.bucket]
                if unsigned:
                    cmd.append(UNSIGNED_FLAG)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    self._use_unsigned_requests = unsigned
                    mode = "unsigned" if unsigned else "credentialed"
                    logger.info("S3 bucket accessible (%s mode)", mode)
                    return True

            logger.error(f"S3 access failed: {result.stderr}")
            return False

        except subprocess.TimeoutExpired:
            logger.error("S3 access test timed out")
            return False
        except Exception as e:
            logger.error(f"S3 access test error: {e}")
            return False

    def sync_with_s5cmd(self) -> bool:
        logger.info(f"Starting sync with s5cmd ({self.workers} workers)")

        cmd = [
            "s5cmd",
            "--numworkers",
            str(self.workers),
            "sync",
            f"{self.bucket}",
            f"{self.target_dir}/",
        ]

        if self.dry_run:
            logger.info(f"[DRY RUN] Would run: {' '.join(cmd)}")
            return True

        try:
            logger.info(f"Running: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if process.stdout is not None:
                for line in process.stdout:
                    logger.info(line.strip())

            process.wait()
            return process.returncode == 0

        except Exception as e:
            logger.error(f"s5cmd sync failed: {e}")
            return False

    def sync_with_aws_cli(self) -> bool:
        logger.info("Starting sync with AWS CLI")

        cmd = [
            "aws",
            "s3",
            "sync",
            self.bucket,
            str(self.target_dir),
        ]

        if self._use_unsigned_requests:
            cmd.append(UNSIGNED_FLAG)

        cmd.extend(["--no-progress", "--quiet"])

        if self.dry_run:
            logger.info(f"[DRY RUN] Would run: {' '.join(cmd)}")
            return True

        try:
            logger.info(f"Running: {' '.join(cmd)}")

            result = subprocess.run(cmd, check=True, capture_output=True, text=True)

            logger.info(result.stdout)
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"AWS CLI sync failed: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"AWS CLI sync error: {e}")
            return False

    def verify_download(self) -> bool:
        logger.info("Verifying download...")

        parquet_files = list(self.target_dir.rglob("*.parquet"))

        if not parquet_files:
            logger.error("No Parquet files found")
            return False

        total_size = 0
        valid_files = 0
        try:
            import pyarrow.parquet as pq
        except ImportError:
            pq = None

        for parquet_file in parquet_files:
            total_size += parquet_file.stat().st_size
            if pq is None:
                valid_files += 1
                continue
            try:
                _ = pq.ParquetFile(parquet_file).metadata
                valid_files += 1
            except Exception as exc:
                logger.error("Invalid parquet footer for %s: %s", parquet_file, exc)

        total_size_mb = total_size / (1024 * 1024)

        logger.info(f"Found {len(parquet_files):,} Parquet files")
        logger.info(f"Total size: {total_size_mb:.2f} MB")
        logger.info("Verified %s/%s parquet footers", valid_files, len(parquet_files))

        date_partitions = list(self.target_dir.glob("date=*"))
        logger.info(f"Found {len(date_partitions)} date partitions")

        return len(parquet_files) > 0 and valid_files == len(parquet_files)

    def sync(self) -> bool:
        logger.info("=" * 60)
        logger.info("S3 Historical Data Sync")
        logger.info("=" * 60)
        logger.info(f"Source: {self.bucket}")
        logger.info(f"Target: {self.target_dir}")
        logger.info(f"Tool: {self.tool}")
        logger.info(f"Workers: {self.workers}")

        if not self._test_s3_access():
            logger.error("S3 access test failed.")
            return False

        self.target_dir.mkdir(parents=True, exist_ok=True)

        start_time = datetime.now()

        success = self.sync_with_s5cmd() if self.tool == "s5cmd" else self.sync_with_aws_cli()

        elapsed = (datetime.now() - start_time).total_seconds()

        if success:
            logger.info(f"Sync completed in {elapsed:.1f} seconds")

            if not self.dry_run:
                if self.verify_download():
                    logger.info("Download verification passed")
                else:
                    logger.warning("Download verification failed")
        else:
            logger.error("Sync failed")
            return False

        return True


def compact_market(
    market_dir: Path,
    max_days_per_file: int = 30,
    dry_run: bool = False,
) -> int:
    if not market_dir.exists():
        logger.warning("market_dir_not_found", path=str(market_dir))
        return 0

    partition_dirs = sorted(market_dir.glob("date=*"))
    if not partition_dirs:
        return 0

    groups = _group_consecutive_dates(partition_dirs, max_days_per_file)
    total_compacted = 0

    for group in groups:
        if len(group) <= 1:
            continue

        logger.info(
            "compacting_date_range",
            dates=f"{group[0].name}..{group[-1].name}",
            partitions=len(group),
            dry_run=dry_run,
        )

        if dry_run:
            total_compacted += len(group)
            continue

        frames: list[pd.DataFrame] = []
        for partition_dir in group:
            for pq_file in partition_dir.glob("*.parquet"):
                frames.append(pd.read_parquet(pq_file))

        if not frames:
            continue

        merged = pd.concat(frames, ignore_index=True)
        key_cols = [c for c in ("ticker", "date") if c in merged.columns]
        if key_cols:
            merged = merged.drop_duplicates(subset=key_cols, keep="last")
            merged = merged.sort_values(key_cols).reset_index(drop=True)

        if "date" not in merged.columns:
            logger.warning("compaction_missing_date_column", market=str(market_dir), dates=[d.name for d in group])
            continue

        merged["date"] = pd.to_datetime(merged["date"])
        for partition_dir in group:
            partition_date = _extract_date(partition_dir.name)
            if partition_date is None:
                continue

            partition_frame = merged[merged["date"].dt.date == partition_date].copy()
            if partition_frame.empty:
                continue

            target_file = partition_dir / f"{partition_date}.parquet"
            for pq_file in partition_dir.glob("*.parquet"):
                if pq_file != target_file and pq_file.exists():
                    pq_file.unlink()

            partition_frame.to_parquet(target_file, index=False, compression="snappy")
            total_compacted += 1

    return total_compacted


def _group_consecutive_dates(
    partition_dirs: list[Path],
    max_per_group: int,
) -> list[list[Path]]:
    if not partition_dirs:
        return []

    groups: list[list[Path]] = []
    current_group: list[Path] = [partition_dirs[0]]
    prev_date = _extract_date(partition_dirs[0].name)

    for pd_dir in partition_dirs[1:]:
        cur_date = _extract_date(pd_dir.name)
        if cur_date is None or prev_date is None:
            if current_group:
                groups.append(current_group)
            current_group = [pd_dir]
            prev_date = cur_date
            continue

        gap_days = (cur_date - prev_date).days
        if gap_days <= 3 and len(current_group) < max_per_group:
            current_group.append(pd_dir)
        else:
            if current_group:
                groups.append(current_group)
            current_group = [pd_dir]

        prev_date = cur_date

    if current_group:
        groups.append(current_group)

    return groups


def _extract_date(partition_name: str) -> date | None:
    try:
        return date.fromisoformat(partition_name.split("=", 1)[1])
    except (ValueError, IndexError):
        return None


def compact_all_markets(
    lake_dir: Path | None = None,
    max_days_per_file: int = 30,
    dry_run: bool = False,
) -> dict[str, int]:
    lake = lake_dir or LAKE_DIR
    results: dict[str, int] = {}

    for market_dir in sorted(lake.iterdir()):
        if not market_dir.is_dir():
            continue
        count = compact_market(market_dir, max_days_per_file=max_days_per_file, dry_run=dry_run)
        if count > 0:
            results[market_dir.name] = count

    logger.info("compaction_complete", markets_compacted=len(results), dry_run=dry_run)
    return results


def _dedupe_key_columns(market: str) -> list[str]:
    if market == "us_news":
        return ["url"]
    if market == "us_social_sentiment":
        return ["ticker", "datetime", "source"]
    return ["ticker", "date"]


def _merge_partition_frames(existing_df: pd.DataFrame, incoming_df: pd.DataFrame, market: str) -> pd.DataFrame:
    if existing_df.empty:
        return incoming_df.copy()
    if incoming_df.empty:
        return existing_df.copy()

    key_columns = _dedupe_key_columns(market)
    combined = pd.concat([existing_df, incoming_df], ignore_index=True)
    merged = combined.drop_duplicates(subset=key_columns, keep="last")
    sort_columns = [column for column in ["date", "datetime", "ticker"] if column in merged.columns]
    if sort_columns:
        merged = merged.sort_values(sort_columns).reset_index(drop=True)
    return merged


def _atomic_write_parquet(df: pd.DataFrame, output_file: os.PathLike[str] | str) -> None:
    output_path = os.fspath(output_file)
    output_dir = os.path.dirname(output_path)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".parquet", dir=output_dir)
    os.close(fd)
    try:
        df.to_parquet(temp_path, index=False, compression="snappy")
        os.replace(temp_path, output_path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def write_to_partitioned_parquet(
    df: pd.DataFrame,
    market: str,
    trading_date: date,
    dry_run: bool = False,
    validate_quality: bool = False,
) -> bool:
    if df.empty:
        logger.warning(
            "Empty DataFrame for %s on %s, skipping write",
            market,
            trading_date,
        )
        return False

    if validate_quality:
        from equity_lake.validation.pipeline import ValidationPipeline

        data_type = "news" if market in ("us_news", "us_social_sentiment") else "price"
        vp = ValidationPipeline()
        result = vp.validate(df, data_type=data_type, name=f"{market}_{trading_date}")
        if not result.success:
            logger.error("Quality validation failed", market=market, errors=result.errors)
            return False
        if result.warnings:
            for w in result.warnings:
                logger.warning("Quality warning", market=market, warning=w)

    market_dirs = {
        "us_equity": US_EQUITY_DIR,
        "cn_ashare": CN_ASHARE_DIR,
        "hk_sg_equity": HK_SG_EQUITY_DIR,
        "jpx_equity": JPX_EQUITY_DIR,
        "krx_equity": KRX_EQUITY_DIR,
        "us_news": US_NEWS_DIR,
        "us_social_sentiment": US_SOCIAL_SENTIMENT_DIR,
    }

    output_dir = market_dirs.get(market)
    if output_dir is None:
        logger.error("Unknown market: %s", market)
        return False

    partition_dir = output_dir / f"date={trading_date}"
    output_file = partition_dir / f"{trading_date}.parquet"

    if dry_run:
        logger.info("[DRY RUN] Would write %s rows to %s", len(df), output_file)
        return True

    try:
        partition_dir.mkdir(parents=True, exist_ok=True)
        lock_path = partition_dir / ".write.lock"
        with FileLock(str(lock_path)):
            df_write = df.copy()
            if "date" in df_write.columns:
                df_write["date"] = pd.to_datetime(df_write["date"])
            if "datetime" in df_write.columns:
                df_write["datetime"] = pd.to_datetime(df_write["datetime"])

            existing_df = pd.read_parquet(output_file) if output_file.exists() else pd.DataFrame()
            merged_df = _merge_partition_frames(existing_df, df_write, market)
            records_added = max(len(merged_df) - len(existing_df), 0)
            duplicate_count = len(existing_df) + len(df_write) - len(merged_df)

            if duplicate_count > 0:
                logger.warning(
                    "Skipped %s duplicate records while merging %s",
                    duplicate_count,
                    output_file,
                )

            _atomic_write_parquet(merged_df, output_file)

        file_size_kb = output_file.stat().st_size / 1024
        logger.info(
            "Wrote %s total rows to %s (added %s new rows, %.1f KB)",
            len(merged_df),
            output_file,
            records_added,
            file_size_kb,
        )
        return True
    except Exception as exc:
        logger.error("Failed to write Parquet file: %s", exc)
        return False


def validate_schema(df: pd.DataFrame, market: str) -> bool:
    if market == "us_news":
        required_cols = NEWS_COLUMNS
    elif market == "us_social_sentiment":
        required_cols = SOCIAL_COLUMNS
    else:
        required_cols = ["ticker", "date", "open", "high", "low", "close", "volume"]

    missing_cols = set(required_cols) - set(df.columns)
    if missing_cols:
        logger.error("%s: Missing required columns: %s", market, missing_cols)
        return False

    for column in required_cols:
        if column in df.columns and bool(df[column].isnull().all()):
            logger.warning("%s: Column '%s' is all null", market, column)

    return True


def validate_news_data_quality(df: pd.DataFrame) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "total_rows": len(df),
        "missing_headlines": 0,
        "missing_urls": 0,
        "invalid_dates": 0,
        "duplicate_urls": 0,
        "sentiment_distribution": {},
        "date_range": None,
    }

    if df.empty:
        logger.warning("Empty DataFrame provided for quality validation")
        return metrics

    if "headline" in df.columns:
        metrics["missing_headlines"] = int(df["headline"].isna().sum())

    if "url" in df.columns:
        metrics["missing_urls"] = int(df["url"].isna().sum())
        metrics["duplicate_urls"] = int(df["url"].duplicated().sum())

    if "date" in df.columns:
        try:
            pd.to_datetime(df["date"])
        except Exception:
            metrics["invalid_dates"] = len(df)

        metrics["date_range"] = {
            "min": str(df["date"].min()),
            "max": str(df["date"].max()),
        }

    if "sentiment_label" in df.columns:
        metrics["sentiment_distribution"] = df["sentiment_label"].value_counts().to_dict()

    logger.info(
        "News data quality: %s rows, %s missing headlines, %s missing URLs, %s duplicate URLs",
        metrics["total_rows"],
        metrics["missing_headlines"],
        metrics["missing_urls"],
        metrics["duplicate_urls"],
    )

    return metrics


__all__ = [
    "EquityDataDB",
    "QueryExamples",
    "S3Syncer",
    "benchmark_queries",
    "compact_all_markets",
    "compact_market",
    "validate_news_data_quality",
    "validate_schema",
    "write_to_partitioned_parquet",
]
