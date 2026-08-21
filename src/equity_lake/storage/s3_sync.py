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
- Tenacity-backed retry/backoff for transient access and sync failures

Usage:
    uv run equity sync
    uv run equity sync --bucket s3://my-bucket/us_equity/
    uv run equity sync --workers 32 --dry-run
"""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Final

import structlog

from equity_lake.core.retry import build_retry_decorator

logger = structlog.get_logger(__name__)
UNSIGNED_FLAG: Final[str] = "--no-sign-request"


class S3ToolNotFoundError(RuntimeError):
    """Raised when neither s5cmd nor the AWS CLI is available."""


class S3RetryableError(RuntimeError):
    """Transient S3 failure (timeout, connection drop) eligible for retry."""


_s3_retry = build_retry_decorator(
    attempts=3,
    wait_multiplier=1.0,
    wait_min=2.0,
    wait_max=30.0,
    retry_on=(S3RetryableError,),
)


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

        logger.info("Initialized S3 syncer", tool=self.tool)

    def _detect_tool(self, tool: str) -> str:
        """Detect available sync tool, raising if none is found."""
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

        raise S3ToolNotFoundError(
            "No S3 sync tool found. Install either s5cmd (https://github.com/peak/s5cmd) or the AWS CLI (https://aws.amazon.com/cli/)."
        )

    @_s3_retry
    def _test_s3_access(self) -> bool:
        """Test if S3 bucket is accessible (with retry on transient failures)."""
        logger.info("Testing access to bucket", bucket=self.bucket)

        if self.tool == "s5cmd":
            try:
                result = subprocess.run(["s5cmd", "ls", self.bucket], capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise S3RetryableError(f"S3 access test timed out: {exc}") from exc
            if result.returncode == 0:
                logger.info("S3 bucket accessible")
                return True
            logger.error("S3 access failed", stderr=result.stderr)
            return False

        for unsigned in (False, True):
            cmd = ["aws", "s3", "ls", self.bucket]
            if unsigned:
                cmd.append(UNSIGNED_FLAG)
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise S3RetryableError(f"S3 access test timed out: {exc}") from exc
            if result.returncode == 0:
                self._use_unsigned_requests = unsigned
                mode = "unsigned" if unsigned else "credentialed"
                logger.info("S3 bucket accessible", mode=mode)
                return True

        logger.error("S3 access failed", stderr=result.stderr)
        return False

    @_s3_retry
    def sync_with_s5cmd(self) -> bool:
        """Sync using s5cmd (fast parallel sync)."""
        logger.info("Starting sync with s5cmd", workers=self.workers)

        cmd = [
            "s5cmd",
            "--numworkers",
            str(self.workers),
            "sync",
            f"{self.bucket}",
            f"{self.target_dir}/",
        ]

        if self.dry_run:
            logger.info("DRY RUN would run", command=" ".join(cmd))
            return True

        process: subprocess.Popen[str] | None = None
        try:
            logger.info("Running", command=" ".join(cmd))

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if process.stdout is not None:
                for line in process.stdout:
                    logger.info("s5cmd", line=line.strip())

            process.wait(timeout=600)
            return process.returncode == 0

        except subprocess.TimeoutExpired as exc:
            logger.error("s5cmd sync timed out after 600s")
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            raise S3RetryableError("s5cmd sync timed out") from exc
        except Exception as e:
            logger.error("s5cmd sync failed", error=str(e))
            if process is not None:
                process.terminate()
            return False

    @_s3_retry
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

        cmd.extend(["--no-progress", "--quiet"])

        if self.dry_run:
            logger.info("DRY RUN would run", command=" ".join(cmd))
            return True

        try:
            logger.info("Running", command=" ".join(cmd))
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info("aws_cli_stdout", stdout=result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("AWS CLI sync failed", stderr=e.stderr)
            return False
        except subprocess.TimeoutExpired as e:
            logger.error("AWS CLI sync timed out", error=str(e))
            raise S3RetryableError("AWS CLI sync timed out") from e
        except Exception as e:
            logger.error("AWS CLI sync error", error=str(e))
            return False

    def verify_download(self) -> bool:
        """Verify downloaded files."""
        logger.info("Verifying download")

        parquet_files = list(self.target_dir.rglob("*.parquet"))
        delta_log = self.target_dir / "_delta_log"

        if not parquet_files and not delta_log.exists():
            logger.error("No Parquet files or Delta log found")
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
                logger.error("Invalid parquet footer", file=str(parquet_file), error=str(exc))

        total_size_mb = total_size / (1024 * 1024)

        logger.info("Found parquet files", count=len(parquet_files))
        logger.info("Total size MB", size=round(total_size_mb, 2))
        logger.info("Verified parquet footers", valid=valid_files, total=len(parquet_files))

        date_partitions = list(self.target_dir.glob("date=*"))
        logger.info("Found date partitions", count=len(date_partitions))

        return len(parquet_files) > 0 and valid_files == len(parquet_files)

    def sync(self) -> bool:
        """Execute S3 sync process."""
        logger.info("S3 Historical Data Sync")
        logger.info("Sync parameters", source=self.bucket, target=str(self.target_dir), tool=self.tool, workers=self.workers)

        try:
            if not self._test_s3_access():
                logger.error("S3 access test failed. Check: 1) bucket URL, 2) network connectivity, 3) AWS credentials (if private bucket).")
                return False
        except S3ToolNotFoundError as exc:
            logger.error("S3 tool unavailable", error=str(exc))
            return False

        self.target_dir.mkdir(parents=True, exist_ok=True)

        start_time = datetime.now()

        try:
            success = self.sync_with_s5cmd() if self.tool == "s5cmd" else self.sync_with_aws_cli()
        except S3RetryableError as exc:
            logger.error("S3 sync failed after retries", error=str(exc))
            return False

        elapsed = (datetime.now() - start_time).total_seconds()

        if success:
            logger.info("Sync completed", seconds=round(elapsed, 1))

            if not self.dry_run:
                if self.verify_download():
                    logger.info("Download verification passed")
                else:
                    logger.warning("Download verification failed")
        else:
            logger.error("Sync failed")
            return False

        return True
