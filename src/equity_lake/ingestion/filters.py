"""CLI filter builders for ingestion commands."""

import argparse

from equity_lake.ingestion.models import FilterConfig


def build_filters_from_args(args: argparse.Namespace) -> FilterConfig:
    """Build the filter dict consumed by ingestion fetchers."""
    filters: FilterConfig = {}

    if args.tags:
        filters["tags"] = [tag.strip() for tag in args.tags.split(",")]
        filters["match_all_tags"] = args.match_all_tags

    if args.sectors:
        filters["sectors"] = args.sectors

    if args.groups:
        filters["groups"] = [group.strip() for group in args.groups.split(",")]

    if args.min_priority:
        filters["min_priority"] = args.min_priority

    return filters

__all__ = ["build_filters_from_args"]
