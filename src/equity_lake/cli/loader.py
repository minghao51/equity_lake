"""CLI for loader management."""

from __future__ import annotations

import argparse

from equity_lake.loaders import registry


def parse_args() -> argparse.Namespace:
    """Parse loader CLI args."""
    parser = argparse.ArgumentParser(description="Manage Equity Lake data loaders")
    parser.add_argument("command", choices=["list", "show", "test"])
    parser.add_argument("name", nargs="?")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    if args.command == "list":
        for metadata in registry.list():
            print(f"{metadata.name}\t{','.join(metadata.supported_markets)}\t{metadata.description}")
        return

    if not args.name:
        raise SystemExit("Loader name is required for this command.")

    if args.command == "show":
        metadata = registry.get(args.name).metadata
        print(metadata.model_dump_json(indent=2))
        return

    if args.command == "test":
        loader = registry.create(args.name, {})
        print("ok" if loader.validate_connection() else "failed")


__all__ = ["main"]
