"""Shared Typer app instances and parse helpers for CLI commands."""

from __future__ import annotations

from datetime import date

import structlog
import typer

from equity_lake.core.paths import ensure_dirs

logger = structlog.get_logger()

app = typer.Typer(
    name="equity",
    help="Equity Lake: Local-first equity data pipeline",
    add_completion=False,
    rich_markup_mode="rich",
)


@app.callback()
def _main_callback() -> None:
    """Ensure runtime directories exist before any command runs."""
    ensure_dirs()


signal_app = typer.Typer(help="Signal scanning for equity watchlists")
dashboard_app = typer.Typer(help="Dashboard build and serve")
bootstrap_app = typer.Typer(help="Data bootstrapping and sample generation")
config_app = typer.Typer(help="Configuration management")
validate_app = typer.Typer(help="Data quality validation and profiling")
arena_app = typer.Typer(help="Strategy arena: run strategies x cost regimes, emit FindingCards")
report_app = typer.Typer(help="Report generation (e.g. backtest reports)")
demo_app = typer.Typer(help="Demo lake seeding for the Strategy Lab showcase")
ml_app = typer.Typer(help="ML comparison, ablation, and training")
api_app = typer.Typer(help="Read API server (FastAPI, Phase 2B)")


def _init_logging(verbose: bool = False) -> None:
    from equity_lake.core.logging import setup_structured_logging

    setup_structured_logging(level="DEBUG" if verbose else "INFO")


def _resolve_date(date_str: str | None, days_back: int = 1, market: str = "us_equity") -> date:
    """Resolve a trading date on ``market``'s exchange calendar (default US)."""
    from equity_lake.core.dates import resolve_trading_date

    return resolve_trading_date(date_str, days_back=days_back, market=market)


def _resolve_run_date(date_str: str | None, days_back: int, markets: list[str] | None) -> date:
    """Resolve the run-level trading date per ADR-0010 Decision 5.

    A run with exactly one price market resolves with that market's calendar
    (``equity pipeline --markets cn_ashare`` uses the XSHG calendar). With zero
    or multiple price markets the run-level date resolves with the US calendar
    and the assumption is logged as a warning; ingestion's idempotent re-fetch
    remains the safety net.
    """
    from equity_lake.core.paths import PRICE_MARKETS

    price_markets = [m for m in (markets or []) if m in PRICE_MARKETS]
    if len(price_markets) == 1:
        return _resolve_date(date_str, days_back, market=price_markets[0])
    logger.warning(
        "run_date_resolved_with_us_calendar",
        markets=markets,
        note="no single price-market context; resolving the run date with the US calendar",
    )
    return _resolve_date(date_str, days_back)


def _parse_comma_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [t.strip() for t in value.split(",") if t.strip()]


def _parse_markets(value: str | None) -> list[str] | None:
    """Parse a ``--markets`` flag value into canonical long keys (ADR-0010).

    Both vocabularies are accepted at the CLI boundary: short aliases
    (``--markets us,cn``) are canonicalized to the long keys
    (``us_equity,cn_ashare``); long keys and enrichment dataset identifiers
    pass through. Unknown values abort with exit code 1 so a typo never
    becomes a silent no-op.
    """
    from equity_lake.core.paths import OPTIONAL_ENRICHMENT_MARKETS, SHORT_TO_LONG
    from equity_lake.ingestion.types import VALID_MARKETS

    tokens = _parse_comma_list(value)
    if tokens is None:
        return None
    canonical: list[str] = []
    for token in tokens:
        if token in SHORT_TO_LONG:
            canonical.append(SHORT_TO_LONG[token])
        elif token in VALID_MARKETS:
            canonical.append(token)
        else:
            typer.secho(
                f"Unknown market '{token}'. Price markets: {', '.join(SHORT_TO_LONG.values())} "
                f"(short aliases {', '.join(SHORT_TO_LONG)} accepted); dataset ids: {', '.join(sorted(OPTIONAL_ENRICHMENT_MARKETS))}",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
    return canonical
