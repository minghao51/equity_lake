"""CLI for application settings."""

from __future__ import annotations

import argparse
import json

from equity_lake.config.settings import load_settings


def _resolve_path(data: object, dotted_path: str) -> object:
    current = data
    for segment in dotted_path.split("."):
        if not isinstance(current, dict):
            raise KeyError(dotted_path)
        current = current[segment]
    return current


def parse_args() -> argparse.Namespace:
    """Parse config CLI arguments."""
    parser = argparse.ArgumentParser(description="Inspect Equity Lake settings")
    parser.add_argument("command", choices=["show", "get", "validate", "export"])
    parser.add_argument("path", nargs="?")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    settings = load_settings()
    payload = settings.model_dump()

    if args.command == "validate":
        print("valid")
        return

    if args.command == "show":
        print(json.dumps(payload, indent=2))
        return

    if args.command == "export":
        print(json.dumps(payload, indent=2))
        return

    if args.command == "get":
        if not args.path:
            raise SystemExit("A dotted config path is required for `get`.")
        print(json.dumps(_resolve_path(payload, args.path), indent=2))


__all__ = ["main"]
