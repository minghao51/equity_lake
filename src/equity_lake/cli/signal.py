"""CLI entry point for signal scanning."""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from equity_lake.core.runtime import setup_logging
from equity_lake.signals.config import load_signal_config, load_watchlist
from equity_lake.signals.scanner import SignalScanner

logger = setup_logging(__name__)


def parse_scan_args(args: argparse.Namespace) -> dict:
    """Parse and validate scan command arguments."""
    kwargs = {}

    if args.date:
        try:
            kwargs["target_date"] = date.fromisoformat(args.date)
        except ValueError:
            logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD")
            sys.exit(1)

    if args.watchlist:
        kwargs["watchlist_path"] = Path(args.watchlist)

    if args.config:
        kwargs["config_path"] = Path(args.config)

    kwargs["format"] = args.format

    if args.output:
        kwargs["output_path"] = Path(args.output)

    kwargs["dry_run"] = args.dry_run
    kwargs["verbose"] = args.verbose

    return kwargs


def cmd_scan(args: argparse.Namespace):
    """Run signal scan command."""
    kwargs = parse_scan_args(args)

    # Load configs
    watchlist_path = kwargs.get("watchlist_path")
    config_path = kwargs.get("config_path")

    try:
        watchlist = load_watchlist(watchlist_path)
        config = load_signal_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    logger.info(f"Scanning watchlist: {watchlist.name}")
    logger.info(f"Tickers: {len(watchlist.tickers)}")

    # Initialize scanner
    scanner = SignalScanner(config, watchlist)

    # Scan
    target_date = kwargs.get("target_date", date.today() - timedelta(days=1))
    logger.info(f"Generating signals for: {target_date}")

    signals = scanner.scan(target_date)

    logger.info(f"Generated {len(signals)} signals")

    # Format output
    output = scanner.format_signals(signals, kwargs["format"])

    # Print or save
    output_path = kwargs.get("output_path")
    if output_path:
        with open(output_path, "w") as f:
            f.write(output)
        logger.info(f"✅ Saved to {output_path}")
    else:
        print(output)

    # Save history (unless dry run)
    if not kwargs["dry_run"] and signals:
        scanner.save_history(signals)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Signal scanning for equity watchlists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # scan subcommand
    scan_parser = subparsers.add_parser("scan", help="Scan watchlist and generate signals")

    scan_parser.add_argument(
        "--format", "-f", choices=["json", "md", "table"], default="table", help="Output format (default: table)"
    )

    scan_parser.add_argument("--date", "-d", help="Target date (YYYY-MM-DD, default: yesterday)")

    scan_parser.add_argument("--watchlist", "-w", help="Path to watchlist config")

    scan_parser.add_argument("--config", "-c", help="Path to signal config")

    scan_parser.add_argument("--output", "-o", help="Save output to file")

    scan_parser.add_argument("--dry-run", action="store_true", help="Don't save to history")

    scan_parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
