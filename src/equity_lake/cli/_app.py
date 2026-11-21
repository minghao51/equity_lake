"""Shared Typer app instances and parse helpers for CLI commands."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

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
def _main_callback(ctx: typer.Context) -> None:
    """Defer signal paths until dry-run is known; keep doctor read-only.

    ``portfolio`` is skipped too: the private ledger only ever creates its own
    ``data/portfolio`` directory, so an import or its ``--dry-run`` never touches
    the lake (ADR-0024 decision 13).
    """
    if ctx.invoked_subcommand not in {"signal", "doctor", "tracking", "portfolio"}:
        ensure_dirs()


def _default_window(days: int) -> tuple[date, date]:
    """Rolling CLI default window ``(today - days, today)`` shared by ml/intelligence."""
    today = date.today()
    return today - timedelta(days=days), today


signal_app = typer.Typer(help="Signal scanning for equity watchlists")
tracking_app = typer.Typer(help="Private canonical watchlists, readiness, and opportunity delivery")
dashboard_app = typer.Typer(help="Dashboard build and serve")
bootstrap_app = typer.Typer(help="Data bootstrapping and sample generation")
config_app = typer.Typer(help="Configuration management")
validate_app = typer.Typer(help="Data quality validation and profiling")
arena_app = typer.Typer(help="Strategy arena: run strategies x cost regimes, emit FindingCards")
report_app = typer.Typer(help="Report generation (e.g. backtest reports)")
demo_app = typer.Typer(help="Demo lake seeding for the Strategy Lab showcase")
ml_app = typer.Typer(help="ML comparison, ablation, and training")
api_app = typer.Typer(help="Read API server (FastAPI, Phase 2B)")
portfolio_app = typer.Typer(help="Private portfolio ledger: canonical CSV import into the local US-equity/USD book")


def _init_logging(verbose: bool = False) -> None:
    from equity_lake.core.logging import setup_structured_logging

    setup_structured_logging(level="DEBUG" if verbose else "INFO")


def _resolve_lake_dir(value: str | None) -> Path | None:
    """Validate ``--lake-dir`` at the CLI seam (ADR-0030).

    ``None`` keeps the canonical lake, so every existing command is unchanged. A
    rejected root exits non-zero rather than writing somewhere unexpected; the
    message never echoes a resolved path.
    """
    from equity_lake.core.paths import resolve_lake_root

    try:
        return resolve_lake_root(value)
    except ValueError as exc:
        typer.secho(f"--lake-dir rejected: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc


def _require_real_root_scope(markets: Sequence[str], lake_dir: Path | None) -> None:
    """Refuse an out-of-scope market on a non-canonical root (ADR-0030 decision 5).

    A non-canonical root carries the `us_equity` slice only. The canonical root keeps
    its existing multi-market behaviour, so this guard changes nothing by default.

    Only price markets name a slice of the lake that could exist under the real root.
    ``--markets`` also accepts enrichment dataset identifiers (``macro``,
    ``us_news``, ...) and ``ingest`` defaults to the configured market list, which
    contains them; they carry no ``us_equity`` slice either, so they are refused like
    any other out-of-scope market instead of raising an unknown price market from
    :func:`canonical_market` — that crash would replace this refusal with a traceback
    on the default landing command.
    """
    from equity_lake.core.paths import LAKE_DIR, PRICE_MARKETS, REAL_ROOT_PRICE_MARKETS, SHORT_TO_LONG

    if lake_dir is None or lake_dir == LAKE_DIR.resolve():
        return
    out_of_scope: set[str] = set()
    for market in markets:
        price_market = market if market in PRICE_MARKETS else SHORT_TO_LONG.get(market)
        if price_market not in REAL_ROOT_PRICE_MARKETS:
            out_of_scope.add(market)
    if out_of_scope:
        typer.secho(
            f"--lake-dir carries the us_equity slice only; refused market(s): {', '.join(sorted(out_of_scope))}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)


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
