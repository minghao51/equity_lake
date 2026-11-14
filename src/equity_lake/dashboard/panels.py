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
        from equity_lake.core.paths import LAKE_DIR
        from equity_lake.signals import tracking

        # Both the tracking database *and* the watchlist file are bound explicitly: the
        # default watchlist path is process-global, so passing only ``data_dir`` would let
        # one read escape the configured directory.

        snapshot = tracking.private_tracking_snapshot(
            data_dir=private_dir,
            watchlist_file=private_dir / PORTFOLIO_WATCHLIST_FILENAME,
            # The lake is public market data, not private state; passing it explicitly
            # keeps the only non-private read visible instead of an implicit default.
            lake_dir=LAKE_DIR,
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


# ---------------------------------------------------------------------------
# Data readiness (stage 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadinessPanel:
    """Per-dataset readiness plus the freshness evidence available for it."""

    notice: Notice
    headline: str
    details: tuple[str, ...] = ()
    rows: tuple[dict[str, str], ...] = ()


def readiness_panel(datasets: list[dict[str, Any]], health: dict[str, Any] | None) -> ReadinessPanel:
    """Build the Data Readiness panel from already-loaded summaries and health.

    ``datasets`` is the per-market summary the app already computes (rows, symbols,
    latest session) and ``health`` the pipeline health report. This function only reads
    those mappings: it performs no lake scan, no write and no inference.

    A **market-level union is not per-instrument coverage**, so the panel says so
    explicitly: it can show that a market table is fresh, never that each instrument in
    it is complete.
    """
    from equity_lake.core.paths import PRICE_MARKETS

    # ``datasets`` mixes price markets with medallion tables (e.g. ``features``): only a
    # price market can answer "is a market available", so the two are counted separately
    # and each row says which it is. Counting a feature table as a market would let an
    # empty lake look available.
    markets = [row for row in datasets if str(row.get("name", "")) in PRICE_MARKETS]
    tables = [row for row in datasets if str(row.get("name", "")) not in PRICE_MARKETS]
    available = [row for row in markets if row.get("available")]
    if not markets:
        return ReadinessPanel(Notice.UNAVAILABLE, "No price market is registered in this lake.")
    if not available:
        return ReadinessPanel(
            Notice.UNAVAILABLE,
            "No price market is available.",
            ("Ingest a market first (`equity ingest`); an absent market is not an empty market.",),
        )

    rows = tuple(
        {
            "dataset": str(row.get("name", "")),
            "kind": "market" if str(row.get("name", "")) in PRICE_MARKETS else "table",
            "available": "yes" if row.get("available") else "no",
            "rows": str(row.get("rows", 0)),
            "symbols": str(row.get("symbols", 0)),
            "latest session": str(row.get("latest_date") or "unknown"),
        }
        for row in (*markets, *tables)
    )
    market_count = len(markets)
    details = [
        "A market-level summary is a union over the table: it never proves per-instrument coverage.",
        "Each price row's origin is recorded separately (see the run manifests); a fresh table is not evidence of where its values came from.",
    ]
    if health is None:
        return ReadinessPanel(
            Notice.PARTIAL,
            f"{len(available)} of {market_count} price market(s) available; freshness evidence unavailable.",
            (*details, "No health report exists: run `equity monitor` to record per-table freshness."),
            rows,
        )
    alerts = health.get("alerts", [])
    metrics = health.get("metrics") or {}
    if alerts:
        return ReadinessPanel(
            Notice.WARNING,
            f"{len(available)} of {market_count} price market(s) available; {len(alerts)} health alert(s).",
            (*details, *(str(alert) for alert in alerts)),
            rows,
        )
    if not metrics:
        # A report that carries neither alerts nor metrics says nothing about freshness:
        # that is missing evidence, never a healthy result.
        return ReadinessPanel(
            Notice.PARTIAL,
            f"{len(available)} of {market_count} price market(s) available; freshness evidence is empty.",
            (*details, "The health report has no metrics: rerun `equity monitor` to record per-table freshness."),
            rows,
        )
    if health.get("healthy") is False:
        # The report's own verdict wins: metrics without a healthy flag are not health.
        return ReadinessPanel(
            Notice.WARNING,
            f"{len(available)} of {market_count} price market(s) available; the health report is not healthy.",
            (*details, "The report records `healthy: false`; inspect `equity monitor` for the failing checks."),
            rows,
        )
    return ReadinessPanel(Notice.SUCCESS, f"{len(available)} of {market_count} price market(s) available and healthy.", tuple(details), rows)


def render_readiness(st: Any, *, datasets: list[dict[str, Any]], health: dict[str, Any] | None) -> None:
    """Render the Data Readiness page (display-only)."""
    st.title(page_spec("readiness").label)
    st.caption("Read-only readiness and freshness. Nothing here ingests, backfills or repairs a table.")
    panel = readiness_panel(datasets, health)
    render_notice(st, panel.notice, panel.headline)
    for line in panel.details:
        st.caption(line)
    if panel.rows:
        st.dataframe(list(panel.rows), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Research runs (stage 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchPanel:
    """Immutable research evidence: validated runs and the last attempt."""

    notice: Notice
    headline: str
    details: tuple[str, ...] = ()
    rows: tuple[dict[str, str], ...] = ()


def research_panel(root: Path | None = None) -> ResearchPanel:
    """Build the Research Runs panel from validated run evidence (read-only).

    Only runs that pass :func:`validate_run_directory` are shown; a directory without a
    valid manifest is not evidence. The manifest's own derived ``data_provenance`` is
    displayed as recorded — never re-derived or upgraded here — with its scope limit, and
    legacy v3 runs are labelled as such.
    """
    from equity_lake.backtesting.evidence import RUN_PROTOCOL_V4, iter_run_directories
    from equity_lake.backtesting.evidence_view import latest_attempt, validated_runs

    try:
        verdicts = validated_runs(root)
        # ``iter_run_directories`` takes the *findings* root (it appends ``runs/``).
        candidates = len(list(iter_run_directories(root)))
        attempt = latest_attempt(root)
    except OSError:
        return ResearchPanel(Notice.UNAVAILABLE, "Research evidence cannot be read.")

    if not verdicts:
        return ResearchPanel(
            Notice.UNAVAILABLE,
            "No finalized research run exists.",
            ("A directory without a validated manifest is not a run; run `equity arena run` or `equity report backtest` first.",),
        )

    now = datetime.now(UTC)
    rows = []
    for verdict in verdicts:
        manifest = verdict.manifest
        if manifest is None:  # pragma: no cover - a validated run always carries one
            continue
        age_days = max((now - manifest.finalized_at.astimezone(UTC)).days, 0)
        rows.append(
            {
                "run": str(Path(verdict.directory).name),
                "protocol": manifest.protocol,
                "data provenance": manifest.data_provenance,
                "finalized": manifest.finalized_at.astimezone(UTC).isoformat(),
                "age (days)": str(age_days),
                "universe": str(len(manifest.universe)),
                "strategies": ", ".join(manifest.strategies),
            }
        )

    details = [
        "Price origin only: data_provenance classifies the consumed price inputs, not corporate-action, feature or prediction inputs.",
        "Runs are immutable evidence: nothing here re-derives, relabels or rewrites a manifest.",
    ]
    uninspectable = candidates - len(verdicts)
    notice = Notice.SUCCESS
    headline = f"{len(verdicts)} finalized run(s)."
    if uninspectable > 0:
        notice = Notice.PARTIAL
        headline = f"{len(verdicts)} finalized run(s); {uninspectable} directory(ies) are not valid evidence."
        details.append("A directory that does not validate is skipped, never counted as a run.")
    legacy = sum(1 for verdict in verdicts if verdict.manifest is not None and verdict.manifest.protocol != RUN_PROTOCOL_V4)
    if legacy:
        details.append(f"{legacy} legacy protocol-v3 run(s): readable under their own rules, with no price-origin summaries.")
    if attempt is not None:
        finalized = attempt.record.finalized_at.astimezone(UTC).isoformat()
        details.append(f"Last attempt: {attempt.record.status} at {finalized} (kept distinct from the last success).")
    return ResearchPanel(notice, headline, tuple(details), tuple(rows))


def render_research(st: Any, *, root: Path | None = None) -> None:
    """Render the Research Runs page (display-only, immutable evidence)."""
    st.title(page_spec("research").label)
    st.caption("Read-only immutable run evidence. Nothing here runs a backtest, refits a model or writes a manifest.")
    panel = research_panel(root)
    render_notice(st, panel.notice, panel.headline)
    for line in panel.details:
        st.caption(line)
    if panel.rows:
        st.dataframe(list(panel.rows), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Signals (stage 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalsPanel:
    """Established candidates only — never orders, never probabilities."""

    notice: Notice
    headline: str
    details: tuple[str, ...] = ()
    rows: tuple[dict[str, str], ...] = ()


def signals_panel(private_dir: Path | None) -> SignalsPanel:
    """Build the Signals panel from the configured private tracking evidence.

    Only ``signal`` outcomes are listed: a rule that did not evaluate, or whose data was
    stale, contributes no candidate and is reported as ``unavailable`` rather than as
    "no signal". Candidates are never orders and never calibrated probabilities.
    """
    if private_dir is None:
        return SignalsPanel(
            Notice.UNAVAILABLE,
            "The private console path is not configured.",
            ("Set EQUITY_CONSOLE__PRIVATE_DIR to the directory holding the watchlist and tracking state.",),
        )
    try:
        from equity_lake.core.paths import LAKE_DIR
        from equity_lake.signals import tracking

        snapshot = tracking.private_tracking_snapshot(
            data_dir=private_dir,
            watchlist_file=private_dir / PORTFOLIO_WATCHLIST_FILENAME,
            # The lake is public market data, not private state: it is passed explicitly
            # so the one non-private read is visible rather than an implicit default.
            lake_dir=LAKE_DIR,
        )
    except Exception:  # noqa: BLE001 - unreadable private state is unavailable, never "no signal"
        return SignalsPanel(Notice.UNAVAILABLE, "Private tracking state cannot be read.", ("No candidate is inferred from missing evidence.",))

    rows: list[dict[str, str]] = []
    for report in snapshot.recent_reports:
        for evaluation in report.evaluations:
            for outcome in evaluation.rules:
                candidate = outcome.candidate
                if candidate is None:
                    continue
                rows.append(
                    {
                        "instrument": _instrument_label(candidate.instrument),
                        "action": candidate.action,
                        "session": candidate.session.isoformat(),
                        "source": candidate.source,
                        "reason": candidate.reasoning,
                        "price": "unavailable" if candidate.price is None else str(candidate.price),
                        "deviation": "unavailable" if candidate.deviation is None else str(candidate.deviation),
                        "threshold": "unavailable" if candidate.threshold is None else str(candidate.threshold),
                    }
                )

    history_status = str(getattr(snapshot, "history_status", "missing"))
    if history_status == "corrupt":
        return SignalsPanel(
            Notice.UNAVAILABLE,
            "Private tracking history is corrupt, so no candidate is current.",
            ("A corrupt history is not evidence of a fresh scan; repair or re-run the local scan.",),
        )

    details = [
        "Candidates only: never an order, never a calibrated profit probability.",
        "A rule that could not evaluate (stale or missing session) reports unavailable; it is not a neutral signal.",
        f"History: {history_status}.",
    ]
    if snapshot.last_successful_scan_at is not None:
        details.append(f"Last successful scan: {snapshot.last_successful_scan_at.astimezone(UTC).isoformat()}.")
    stale_warning = getattr(snapshot, "stale_run_warning", None)
    if stale_warning:
        # A stale run is never presented as a current one: the warning is part of the state.
        return SignalsPanel(
            Notice.WARNING if rows else Notice.UNAVAILABLE,
            f"{len(rows)} candidate(s) recorded from a stale run." if rows else "No current candidate: the last run is stale.",
            (*details, str(stale_warning)),
            tuple(rows),
        )
    if not rows:
        return SignalsPanel(Notice.UNAVAILABLE, "No established candidate is recorded.", tuple(details))
    return SignalsPanel(Notice.INFO, f"{len(rows)} candidate(s) recorded.", tuple(details), tuple(rows))


def render_signals(st: Any, *, private_dir: Path | None) -> None:
    """Render the Signals page (display-only, candidates only)."""
    st.title(page_spec("signals").label)
    st.caption("Read-only candidate view. Nothing here scans, scans again, or places an order.")
    panel = signals_panel(private_dir)
    render_notice(st, panel.notice, panel.headline)
    for line in panel.details:
        st.caption(line)
    if panel.rows:
        st.dataframe(list(panel.rows), hide_index=True, use_container_width=True)
