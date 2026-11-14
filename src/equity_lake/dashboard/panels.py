"""Read-only private panels for the loopback P03 console (spec §5.4, ADR-0018).

**Display-only by construction.** Nothing here writes: the ledger is opened for reading,
the marks and statement files are only read, and no page path can append an event, create
a mark, backfill history or run inference. The panel *content* is built by pure functions
returning a :class:`PortfolioPanel`, so the state rules below are unit-testable without
Streamlit.

Stage 1 exposes the **Portfolio** panel; the other P03 pages are registered in
``dashboard/pages.py`` and render the shared ``GATED`` state until their own stage lands.
Every panel renders exactly one :class:`~equity_lake.dashboard.components.Notice` from the
shared vocabulary — no panel-specific label, colour or CSS modifier is introduced here.

Privacy: the loopback gate runs *before* any function in this module is called (see
``streamlit_app.require_loopback``), the configured private directory comes from
``Settings.console.private_dir`` (never discovered), the valuation instant is always an
explicit UTC input (never the clock), and no private value is placed in a log record or
in an error string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from equity_lake.dashboard.components import Notice, render_notice
from equity_lake.dashboard.pages import page_spec

#: File names the console reads inside the one configured private directory.
PORTFOLIO_MARKS_FILENAME = "marks.json"
PORTFOLIO_STATEMENT_FILENAME = "statement.csv"
#: Private watchlist read for held/watch-only context, inside the same configured root.
PORTFOLIO_WATCHLIST_FILENAME = "watchlist.yaml"

#: Neutral local account label, matching the CLI default (an identifier, never an
#: account number).
DEFAULT_ACCOUNT_LABEL = "main"

_MONEY_NOTE = "values are stored book marks, not research-adjusted prices and not a tax statement"


@dataclass(frozen=True, slots=True)
class PortfolioPanel:
    """The Portfolio panel's render state: one notice plus honest, sourced rows."""

    notice: Notice
    headline: str
    details: tuple[str, ...] = ()
    rows: tuple[dict[str, str], ...] = ()
    watch_only: tuple[dict[str, str], ...] = ()
    reconciliation: tuple[dict[str, str], ...] = ()
    reconciliation_state: str | None = None
    allocation: tuple[dict[str, str], ...] = field(default_factory=tuple)


def _unavailable(headline: str, *details: str) -> PortfolioPanel:
    return PortfolioPanel(notice=Notice.UNAVAILABLE, headline=headline, details=tuple(details))


def parse_as_of(text: str | None) -> datetime | None:
    """Parse the explicit as-of instant; ``None`` when absent or not timezone-aware.

    The console never substitutes the clock: an instant is either supplied by the
    operator or the panel reports that it is unavailable.
    """
    if text is None or not text.strip():
        return None
    try:
        parsed = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    if parsed.utcoffset() != timedelta(0):
        # "Explicit UTC" means UTC: a local offset is refused rather than silently
        # re-interpreted, so the operator always sees the instant they typed.
        return None
    return parsed.astimezone(UTC)


def _money(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    return format(value, "f")


def portfolio_panel(private_dir: Path | None, as_of_text: str | None, account: str = DEFAULT_ACCOUNT_LABEL) -> PortfolioPanel:
    """Build the Portfolio panel state from the configured private directory (read-only).

    Order matters: configuration, then the explicit instant, then the ledger, then the
    marks, then reconciliation. Each missing input produces ``unavailable`` rather than a
    zero, a guess or a partial number that could be read as a total.
    """
    if private_dir is None:
        return _unavailable(
            "The private console path is not configured.",
            "Set EQUITY_CONSOLE__PRIVATE_DIR (or console.private_dir in config/settings.yaml) to the directory holding marks.json and statement.csv.",
            "Nothing is read until it is configured: the console never discovers a private location.",
        )

    as_of = parse_as_of(as_of_text)
    if as_of is None:
        return _unavailable(
            "An explicit UTC as-of instant is required.",
            "Enter a timezone-aware instant such as 2024-01-05T21:00:00Z; the console never uses the clock as a valuation time.",
        )

    from equity_lake.portfolio import MarksError, MarksValidationError, ProjectionError, load_marks_file, replay_ledger, value_positions
    from equity_lake.portfolio.reconcile import StatementError, parse_statement_file, reconcile
    from equity_lake.portfolio.store import LedgerStore, LedgerStoreError

    # One configured path governs every private read: the ledger lives under it too
    # (`<private_dir>/portfolio/portfolio.sqlite3`), so the console can never mix
    # one book's ledger with another location's marks.
    try:
        events = LedgerStore(data_dir=private_dir).load_events()
    except LedgerStoreError:
        return _unavailable("The private ledger cannot be read.", "Run `equity portfolio import` locally first; the console never creates a ledger.")
    if not events:
        return _unavailable("The private ledger has no events.", "Import a canonical CSV with `equity portfolio import` first.")

    try:
        projection = replay_ledger(events, account=account, as_of=as_of)
    except ProjectionError:
        return PortfolioPanel(
            notice=Notice.REFUSED,
            headline="The stored ledger does not replay consistently.",
            details=("A replayed event fails a ledger invariant (oversell, negative cash or an inconsistent basis), so no valuation is shown.",),
        )

    marks_path = private_dir / PORTFOLIO_MARKS_FILENAME
    if not marks_path.is_file():
        return _unavailable(
            "Book marks are unavailable.",
            f"{PORTFOLIO_MARKS_FILENAME} was not found in the configured private directory; the console never fetches, guesses or creates marks.",
        )
    try:
        valuation = value_positions(projection, load_marks_file(marks_path))
    except MarksValidationError as exc:
        return PortfolioPanel(
            notice=Notice.REFUSED,
            headline="The stored book marks were rejected.",
            details=(f"{len(exc.issues)} validation issue(s); fix the marks file, then reload.",),
        )
    except MarksError:
        return PortfolioPanel(
            notice=Notice.REFUSED,
            headline="The stored book marks cannot be used for valuation.",
            details=("The marks do not satisfy the venue/session contract; no valuation is shown.",),
        )

    details: list[str] = [
        f"as-of {valuation.as_of.isoformat()} · account {valuation.account}",
        f"{projection.replayed_event_count} replayed event(s); effective-date reconstruction, not a historical-knowledge proof",
        _MONEY_NOTE,
    ]
    if valuation.complete:
        notice = Notice.SUCCESS
        headline = f"As-of NAV {_money(valuation.nav)} (cash {_money(valuation.cash)} + marked positions {_money(valuation.marked_position_value)})"
    else:
        notice = Notice.PARTIAL
        blocked = [position for position in valuation.positions if position.value is None]
        headline = (
            f"NAV, P&L and allocation are unavailable: {len(blocked)} of {len(valuation.positions)} position(s) cannot be valued. "
            f"Cash {_money(valuation.cash)} · marked subtotal {_money(valuation.marked_position_value)} (not a total)."
        )
        details.extend(f"{_instrument_label(position.instrument)}: {position.blocker or 'mark unavailable'}" for position in blocked)

    if valuation.complete:
        details.append(
            f"realized gain {_money(valuation.realized_gain)} · unrealized {_money(valuation.unrealized_gain)}"
            f" · income {_money(valuation.net_dividend_income)}"
        )
    else:
        details.append("P&L and allocation are withheld while any required coverage is unavailable.")

    rows = tuple(
        {
            "instrument": _instrument_label(position.instrument),
            "quantity": format(position.quantity, "f") + ("" if position.quantity_certain else " (uncertain)"),
            "basis": "unknown" if position.basis is None else _money(position.basis),
            "mark": _money(position.mark_price),
            "mark age (sessions)": "unavailable" if position.mark_age_sessions is None else str(position.mark_age_sessions),
            "mark provenance": position.mark_provenance.value if position.mark_provenance is not None else "unavailable",
            "value": _money(position.value),
            "status": position.status.value,
        }
        for position in valuation.positions
    )
    # Allocation is a denominator statement: it exists only when every holding is valued.
    allocation = (
        tuple({"instrument": label, "share": _money(share)} for label, share in sorted(valuation.allocation.items()))
        if valuation.complete and valuation.allocation
        else ()
    )

    held = _watchlist_held(private_dir)
    held_instruments = {row["instrument"] for row in rows}
    watch_only = tuple({"instrument": label, "flag": "watchlist held flag — not a ledger position"} for label in sorted(held - held_instruments))

    reconciliation, reconciliation_state = _reconciliation(
        projection, private_dir, parse=parse_statement_file, reconcile=reconcile, error=StatementError
    )

    return PortfolioPanel(
        notice=notice,
        headline=headline,
        details=tuple(details),
        rows=rows,
        watch_only=watch_only,
        reconciliation=reconciliation,
        reconciliation_state=reconciliation_state,
        allocation=allocation,
    )


def _instrument_label(instrument: Any) -> str:
    from equity_lake.portfolio import instrument_label

    return str(instrument_label(instrument))


def _watchlist_held(private_dir: Path) -> set[str]:
    """Instruments whose *private watchlist* flag says held (never a ledger position).

    Bound to the configured private path like every other private read: the watchlist
    file and tracking database live under it, so the console cannot consult a different
    private location than the one it was configured with.
    """
    try:
        from equity_lake.signals import tracking

        # Both the tracking database *and* the watchlist file are bound explicitly: the
        # default watchlist path is process-global, so passing only ``data_dir`` would let
        # one read escape the configured directory.
        snapshot = tracking.private_tracking_snapshot(
            data_dir=private_dir,
            watchlist_file=private_dir / PORTFOLIO_WATCHLIST_FILENAME,
        )
    except Exception:  # noqa: BLE001 - a watchlist that cannot be read must not break valuation
        return set()
    return {_instrument_label(item.instrument) for item in snapshot.watchlist.instruments if item.held}


def _reconciliation(
    projection: Any, private_dir: Path, *, parse: Any, reconcile: Any, error: type[Exception]
) -> tuple[tuple[dict[str, str], ...], str | None]:
    statement_path = private_dir / PORTFOLIO_STATEMENT_FILENAME
    if not statement_path.is_file():
        return (), "not requested"
    try:
        report = reconcile(projection, parse(statement_path))
    except error:
        return (), "unavailable"
    rows = tuple(
        {
            "line": "cash" if line.instrument is None else f"quantity {line.label}",
            "statement": "unavailable" if line.statement_value is None else _money(line.statement_value),
            "ledger": _money(line.ledger_value),
            "delta": "unavailable" if line.delta is None else _money(line.delta),
            "state": line.state.value,
            "reason": line.reason,
        }
        for line in report.lines
    )
    return rows, report.state.value


def render_portfolio(st: Any, *, private_dir: Path | None, as_of_text: str | None, account: str = DEFAULT_ACCOUNT_LABEL) -> None:
    """Render the Portfolio page through the shared notice vocabulary."""
    st.title(page_spec("portfolio").label)
    st.caption("Display-only. Read-only book view: no ledger event is written, no mark is created and no valuation is cached.")
    panel = portfolio_panel(private_dir, as_of_text, account)
    render_notice(st, panel.notice, panel.headline)
    for line in panel.details:
        st.caption(line)
    if panel.rows:
        st.subheader("Positions")
        st.dataframe(list(panel.rows), hide_index=True, use_container_width=True)
    if panel.allocation:
        st.subheader("Allocation")
        st.dataframe(list(panel.allocation), hide_index=True, use_container_width=True)
    elif panel.rows:
        st.caption("Allocation and concentration are unavailable while any position is unvalued.")
    st.subheader("Held and watch-only context")
    if panel.watch_only:
        st.dataframe(list(panel.watch_only), hide_index=True, use_container_width=True)
    else:
        st.caption("No watchlist-held instrument outside the ledger positions. A watchlist flag is never a holding.")
    st.subheader("Reconciliation")
    if panel.reconciliation_state == "not requested":
        st.caption(
            f"Not requested: no {PORTFOLIO_STATEMENT_FILENAME} in the configured private directory. "
            "The ledger is never adjusted to make numbers agree."
        )
    elif panel.reconciliation_state == "unavailable":
        st.caption("Unavailable: the statement snapshot could not be read or reconciled; the ledger was not adjusted.")
    else:
        st.caption(f"Overall: {panel.reconciliation_state}. Statement comparison only — not tax certification; the ledger is not adjusted.")
        st.dataframe(list(panel.reconciliation), hide_index=True, use_container_width=True)


def render_pending_panel(st: Any, page_key: str) -> None:
    """Render a registered-but-unauthorized P03 page as ``gated`` (never interactive)."""
    spec = page_spec(page_key)
    st.title(spec.label)
    render_notice(
        st,
        Notice.GATED,
        f"{spec.label} is planned and not authorized: it renders no data until its own approved stage lands.",
    )


def portfolio_private_dir() -> Path | None:
    """The one configured private location, or ``None`` (never discovered, never guessed)."""
    from equity_lake.core.settings import get_settings

    return get_settings().console.private_dir


__all__ = [
    "DEFAULT_ACCOUNT_LABEL",
    "PORTFOLIO_MARKS_FILENAME",
    "PORTFOLIO_STATEMENT_FILENAME",
    "PortfolioPanel",
    "parse_as_of",
    "portfolio_panel",
    "portfolio_private_dir",
    "render_pending_panel",
    "render_portfolio",
]
