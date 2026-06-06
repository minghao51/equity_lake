"""Shared Typer app instances for CLI command registration."""

from __future__ import annotations

from datetime import date, timedelta

import typer

app = typer.Typer(
    name="equity",
    help="Equity Lake: Local-first equity data pipeline",
    add_completion=False,
    rich_markup_mode="rich",
)

signal_app = typer.Typer(help="Signal scanning for equity watchlists")
dashboard_app = typer.Typer(help="Dashboard build and serve")
bootstrap_app = typer.Typer(help="Data bootstrapping and sample generation")
loader_app = typer.Typer(help="Manage data loaders")
config_app = typer.Typer(help="Configuration management")
validate_app = typer.Typer(help="Data quality validation and profiling")


def _init_logging(verbose: bool = False) -> None:
    from equity_lake.core.logging import setup_structured_logging

    setup_structured_logging(level="DEBUG" if verbose else "INFO")


def _resolve_date(date_str: str | None, days_back: int = 1) -> date:
    if date_str:
        return date.fromisoformat(date_str)
    return date.today() - timedelta(days=days_back)


def _parse_comma_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [t.strip() for t in value.split(",") if t.strip()]
