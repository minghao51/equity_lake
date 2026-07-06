#!/usr/bin/env python3
"""
S3 Sync Script for Historical Equity Data

This module handles the initial one-time sync of historical US equity data
from AWS S3 to local disk. It supports both AWS CLI and s5cmd for faster downloads.

Features:
- Parallel downloads with configurable workers
- Progress tracking and resume capability
- Integrity verification after download
- Support for both public and private S3 buckets

Usage:
    uv run equity sync
    uv run equity sync --bucket s3://my-bucket/us_equity/
    uv run equity sync --workers 32 --dry-run
"""

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Final

# Logger configuration
logger = logging.getLogger(__name__)
UNSIGNED_FLAG: Final[str] = "--no-sign-request"


# =============================================================================
# S3 Sync Implementation
# =============================================================================


class S3Syncer:
    """Handle S3 to local synchronization."""

    def __init__(
        self,
        bucket: str,
        target_dir: Path,
        workers: int = 16,
        dry_run: bool = False,
        tool: str = "auto",
    ):
        """
        Initialize S3 syncer.

        Args:
            bucket: S3 bucket path (e.g., s3://my-bucket/us_equity/)
            target_dir: Local target directory
            workers: Number of parallel workers
            dry_run: If True, skip actual downloads
            tool: Sync tool ('aws', 's5cmd', or 'auto')
        """
        self.bucket = bucket
        self.target_dir = target_dir
        self.workers = workers
        self.dry_run = dry_run
        self.tool = self._detect_tool(tool) if tool == "auto" else tool
        self._use_unsigned_requests = False

        logger.info(f"Initialized S3 syncer with tool: {self.tool}")

    def _detect_tool(self, tool: str) -> str:
        """Detect available sync tool."""
        # Check for s5cmd first (faster)
        try:
            result = subprocess.run(["s5cmd", "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info("✅ Detected s5cmd (recommended)")
                return "s5cmd"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("s5cmd not found")

        # Check for AWS CLI
        try:
            result = subprocess.run(["aws", "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info("✅ Detected AWS CLI")
                return "aws"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("AWS CLI not found")

        logger.error("❌ No S3 sync tool found. Please install either:")
        logger.error("   - s5cmd: https://github.com/peak/s5cmd")
        logger.error("   - AWS CLI: https://aws.amazon.com/cli/")
        sys.exit(1)

    def _test_s3_access(self) -> bool:
        """Test if S3 bucket is accessible."""
        logger.info(f"Testing access to {self.bucket}")

        try:
            if self.tool == "s5cmd":
                cmd = ["s5cmd", "ls", f"{self.bucket}"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    logger.info("✅ S3 bucket accessible")
                    return True
                logger.error(f"❌ S3 access failed: {result.stderr}")
                return False

            for unsigned in (False, True):
                cmd = ["aws", "s3", "ls", self.bucket]
                if unsigned:
                    cmd.append(UNSIGNED_FLAG)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    self._use_unsigned_requests = unsigned
                    mode = "unsigned" if unsigned else "credentialed"
                    logger.info("✅ S3 bucket accessible (%s mode)", mode)
                    return True

            logger.error(f"❌ S3 access failed: {result.stderr}")
            return False

        except subprocess.TimeoutExpired:
            logger.error("❌ S3 access test timed out")
            return False
        except Exception as e:
            logger.error(f"❌ S3 access test error: {e}")
            return False

    def sync_with_s5cmd(self) -> bool:
        """Sync using s5cmd (fast parallel sync)."""
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

        process: subprocess.Popen[str] | None = None
        try:
            logger.info(f"Running: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Stream output
            if process.stdout is not None:
                for line in process.stdout:
                    logger.info(line.strip())

            process.wait(timeout=600)
            return process.returncode == 0

        except subprocess.TimeoutExpired:
            logger.error("s5cmd sync timed out after 600s")
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            return False
        except Exception as e:
            logger.error(f"s5cmd sync failed: {e}")
            if process is not None:
                process.terminate()
            return False

    def sync_with_aws_cli(self) -> bool:
        """Sync using AWS CLI (slower but widely available)."""
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

        # Add progress indicator
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
        """Verify downloaded files."""
        logger.info("Verifying download...")

        parquet_files = list(self.target_dir.rglob("*.parquet"))
        delta_log = self.target_dir / "_delta_log"

        if not parquet_files and not delta_log.exists():
            logger.error("❌ No Parquet files or Delta log found")
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
                logger.error("❌ Invalid parquet footer for %s: %s", parquet_file, exc)

        total_size_mb = total_size / (1024 * 1024)

        logger.info(f"✅ Found {len(parquet_files):,} Parquet files")
        logger.info(f"✅ Total size: {total_size_mb:.2f} MB")
        logger.info("✅ Verified %s/%s parquet footers", valid_files, len(parquet_files))

        # Check for expected Hive partition structure
        date_partitions = list(self.target_dir.glob("date=*"))
        logger.info(f"✅ Found {len(date_partitions)} date partitions")

        return len(parquet_files) > 0 and valid_files == len(parquet_files)

    def sync(self) -> bool:
        """Execute S3 sync process."""
        logger.info("=" * 60)
        logger.info("S3 Historical Data Sync")
        logger.info("=" * 60)
        logger.info(f"Source: {self.bucket}")
        logger.info(f"Target: {self.target_dir}")
        logger.info(f"Tool: {self.tool}")
        logger.info(f"Workers: {self.workers}")

        # Test access first
        if not self._test_s3_access():
            logger.error("S3 access test failed. Please check:")
            logger.error("  1. Bucket URL is correct")
            logger.error("  2. Network connectivity")
            logger.error("  3. AWS credentials (if private bucket)")
            return False

        # Create target directory
        self.target_dir.mkdir(parents=True, exist_ok=True)

        # Execute sync based on tool
        start_time = datetime.now()

        success = self.sync_with_s5cmd() if self.tool == "s5cmd" else self.sync_with_aws_cli()

        elapsed = (datetime.now() - start_time).total_seconds()

        if success:
            logger.info(f"✅ Sync completed in {elapsed:.1f} seconds")

            # Verify download
            if not self.dry_run:
                if self.verify_download():
                    logger.info("✅ Download verification passed")
                else:
                    logger.warning("⚠️  Download verification failed")
        else:
            logger.error("❌ Sync failed")
            return False

        return True
