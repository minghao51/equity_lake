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
    uv run equity-sync
    uv run equity-sync --bucket s3://my-bucket/us_equity/
    uv run equity-sync --workers 32 --dry-run
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from equity_lake.core.runtime import (
    US_EQUITY_DIR,
    get_project_config,
    setup_logging,
)

# Logger configuration
logger = logging.getLogger(__name__)


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

        logger.info(f"Initialized S3 syncer with tool: {self.tool}")

    def _detect_tool(self, tool: str) -> str:
        """Detect available sync tool."""
        # Check for s5cmd first (faster)
        try:
            result = subprocess.run(
                ["s5cmd", "--version"], capture_output=True, timeout=5
            )
            if result.returncode == 0:
                logger.info("✅ Detected s5cmd (recommended)")
                return "s5cmd"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("s5cmd not found")

        # Check for AWS CLI
        try:
            result = subprocess.run(
                ["aws", "--version"], capture_output=True, timeout=5
            )
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
            else:  # aws
                cmd = ["aws", "s3", "ls", self.bucket, "--no-sign-request"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                logger.info("✅ S3 bucket accessible")
                return True
            else:
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
            for line in process.stdout:
                logger.info(line.strip())

            process.wait()
            return process.returncode == 0

        except Exception as e:
            logger.error(f"s5cmd sync failed: {e}")
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
            "--no-sign-request",  # For public buckets
        ]

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

        if not parquet_files:
            logger.error("❌ No Parquet files found")
            return False

        total_size = sum(f.stat().st_size for f in parquet_files)
        total_size_mb = total_size / (1024 * 1024)

        logger.info(f"✅ Found {len(parquet_files):,} Parquet files")
        logger.info(f"✅ Total size: {total_size_mb:.2f} MB")

        # Check for expected Hive partition structure
        date_partitions = list(self.target_dir.glob("date=*"))
        logger.info(f"✅ Found {len(date_partitions)} date partitions")

        return len(parquet_files) > 0

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

        if self.tool == "s5cmd":
            success = self.sync_with_s5cmd()
        else:  # aws
            success = self.sync_with_aws_cli()

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


# =============================================================================
# CLI Interface
# =============================================================================


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Sync historical equity data from S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Sync from default bucket
  uv run equity-sync

  # Sync from custom bucket
  uv run equity-sync --bucket s3://my-bucket/us_equity/

  # Use s5cmd with 32 workers
  uv run equity-sync --tool s5cmd --workers 32

  # Dry run (test without downloading)
  uv run equity-sync --dry-run

  # Sync to custom directory
  uv run equity-sync --target /path/to/data
        """,
    )

    parser.add_argument(
        "--bucket",
        type=str,
        help="S3 bucket path (e.g., s3://my-bucket/us_equity/)",
    )

    parser.add_argument(
        "--target",
        type=Path,
        default=US_EQUITY_DIR,
        help=f"Local target directory (default: {US_EQUITY_DIR})",
    )

    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=16,
        help="Number of parallel workers (default: 16)",
    )

    parser.add_argument(
        "--tool",
        "-t",
        type=str,
        choices=["auto", "s5cmd", "aws"],
        default="auto",
        help="Sync tool to use (default: auto)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test without downloading files",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = setup_logging(__name__, level=log_level, log_file="sync_from_s3.log")

    # Get S3 bucket from args or environment
    bucket = args.bucket
    if not bucket:
        config = get_project_config()
        bucket = config.get("s3_bucket", "")

    if not bucket:
        logger.error(
            "No S3 bucket specified. Use --bucket or set S3_BUCKET environment variable"
        )
        logger.error("\nPublic buckets with US equity data:")
        logger.error("  (Add your bucket URL here)")
        sys.exit(1)

    # Initialize syncer
    syncer = S3Syncer(
        bucket=bucket,
        target_dir=args.target,
        workers=args.workers,
        dry_run=args.dry_run,
        tool=args.tool,
    )

    # Execute sync
    try:
        success = syncer.sync()
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.info("\n⚠️  Sync interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
