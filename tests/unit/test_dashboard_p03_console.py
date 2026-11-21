"""P03 private-console stage 1: registry, loopback gate and the Portfolio panel.

Scope: spec §5.4 (Portfolio and Data readiness first; explicit states; loopback binding
before any private read; private data never in a static export, Pages, public API or
logs), ADR-0018 §8-9 (loopback-only private views, auxiliary private storage) and the
approved P03 plan (`docs/plans/20261107-04-p03-private-console-plan.md`: explicit UTC
as-of, one configured private path, one new panel module, reconciliation rendered).

Display-only is the invariant under test: the loopback gate must run before anything
private is read, an unconfigured or absent input must render ``unavailable`` rather than
a zero, and rendering must not modify the private directory.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("streamlit", reason="private dashboard tests require the optional dashboard dependency group")

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]  # noqa: E402

streamlit_app = import_module("equity_lake.dashboard.streamlit_app")
panels = import_module("equity_lake.dashboard.panels")
pages = import_module("equity_lake.dashboard.pages")
components = import_module("equity_lake.dashboard.components")
portfolio = import_module("equity_lake.portfolio")
store_module = import_module("equity_lake.portfolio.store")
core_paths = import_module("equity_lake.core.paths")

P03_KEYS = ("portfolio", "readiness", "research", "signals")


def _mock_streamlit(monkeypatch: pytest.MonkeyPatch, *, address: str | None) -> list[tuple[str, tuple[object, ...]]]:
    calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(streamlit_app.st, "get_option", lambda _name: address, raising=False)
    for name in ("title", "error", "caption", "subheader", "dataframe", "info", "warning", "success", "text_input"):
        monkeypatch.setattr(streamlit_app.st, name, lambda *args, _name=name, **_kwargs: calls.append((_name, args)), raising=False)
    return calls


def _position(*, valued: bool = True) -> Any:
    return SimpleNamespace(
        instrument=SimpleNamespace(),
        quantity=Decimal("10"),
        quantity_certain=True,
        basis=Decimal("1000"),
        mark_price=Decimal("125") if valued else None,
        mark_age_sessions=0 if valued else None,
        mark_provenance=portfolio.MarkProvenance.SYNTHETIC,
        value=Decimal("1250") if valued else None,
        status=portfolio.MarkStatus.VALUED if valued else portfolio.MarkStatus.MISSING_MARK,
        blocker=None if valued else "no mark for the exact venue session",
    )


def _valuation(*, complete: bool) -> Any:
    return SimpleNamespace(
        account="main",
        as_of=datetime(2024, 1, 5, 21, 0, tzinfo=UTC),
        cash=Decimal("924"),
        marked_position_value=Decimal("1250"),
        complete=complete,
        nav=Decimal("2174") if complete else None,
        unrealized_gain=Decimal("250") if complete else None,
        realized_gain=Decimal("90"),
        net_dividend_income=Decimal("11"),
        # Deliberately non-None even when incomplete: the panel must drop it anyway.
        allocation={"us_equity/XNAS/AAA": Decimal("0.57")},
        positions=(_position(),) if complete else (_position(valued=False),),
    )


def _projection() -> Any:
    return SimpleNamespace(replayed_event_count=4, as_of=datetime(2024, 1, 5, 21, 0, tzinfo=UTC))


def _stub_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    valuation: Any,
    replay_error: type[Exception] | None = None,
    marks_error: Exception | None = None,
) -> None:
    """Stub the read-only portfolio functions the panel calls (they import lazily)."""
    monkeypatch.setattr(
        store_module,
        "LedgerStore",
        lambda *a, **k: SimpleNamespace(load_events=lambda: (SimpleNamespace(event_id="e"),)),
    )
    monkeypatch.setattr(panels, "_instrument_label", lambda _instrument: "us_equity/XNAS/AAA")
    monkeypatch.setattr(panels, "_watchlist_held", lambda _private_dir: {"us_equity/XNAS/HELD"})

    def _replay(*_args: Any, **_kwargs: Any) -> Any:
        if replay_error is not None:
            raise replay_error("inconsistent basis")
        return _projection()

    def _load_marks(_path: Path) -> Any:
        if marks_error is not None:
            raise marks_error
        return SimpleNamespace()

    monkeypatch.setattr(portfolio, "replay_ledger", _replay)
    monkeypatch.setattr(portfolio, "load_marks_file", _load_marks)
    monkeypatch.setattr(portfolio, "value_positions", lambda *_a, **_k: valuation)


def _private_dir(tmp_path: Path, *, marks: bool = True, statement: bool = False) -> Path:
    directory = tmp_path / "console"
    directory.mkdir()
    if marks:
        (directory / panels.PORTFOLIO_MARKS_FILENAME).write_text("{}", encoding="utf-8")
    if statement:
        (directory / panels.PORTFOLIO_STATEMENT_FILENAME).write_text("account,as_of\n", encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# 1. Registry and export boundary
# ---------------------------------------------------------------------------


def test_p03_pages_are_registered_gated_and_never_exported() -> None:
    """The four P03 pages exist in the one registry, gated, and are not static.

    Falsified by: adding a page in the app only (drift), registering one as exportable
    (a private panel would become a published file), or renaming a label locally.
    """
    specs = {spec.key: spec for spec in pages.PAGE_SPECS}
    assert [specs[key].label for key in P03_KEYS] == ["Portfolio", "Data Readiness", "Research Runs", "Signals"]
    for key in P03_KEYS:
        assert specs[key].gated is True, key
        assert specs[key].streamlit is True, key
        assert specs[key].static_file is None, key
        assert specs[key] in pages.STREAMLIT_PAGES

    static_keys = {spec.key for spec in pages.STATIC_PAGES}
    assert static_keys.isdisjoint(P03_KEYS)  # the exporter iterates STATIC_PAGES only
    assert not {label for label, _file in pages.NAV_LINKS} & {specs[key].label for key in P03_KEYS}


# ---------------------------------------------------------------------------
# 2. The loopback gate runs before any private read
# ---------------------------------------------------------------------------


def test_a_non_loopback_bind_refuses_before_any_private_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused bind renders REFUSED and never touches a private module.

    Falsified by: reading the ledger/watchlist/marks before checking the bind — that is
    how private data would be served over a non-loopback interface.
    """
    calls = _mock_streamlit(monkeypatch, address="0.0.0.0")
    monkeypatch.setattr(panels, "portfolio_panel", lambda *_a, **_k: pytest.fail("a private read happened before the loopback gate"))

    streamlit_app.render_p03_portfolio()

    assert ("error",) in {(name,) for name, _args in calls}
    assert any("loopback" in str(args[0]).lower() for name, args in calls if name == "error")


@pytest.mark.parametrize("page", ["portfolio", "readiness", "research", "signals"])
def test_every_private_page_refuses_on_a_non_loopback_bind(monkeypatch: pytest.MonkeyPatch, page: str) -> None:
    """Each P03 page (and the pre-existing private tracking page) gates before reading.

    Falsified by: adding a page whose branch reads or imports private state before the
    loopback check.
    """
    calls = _mock_streamlit(monkeypatch, address="0.0.0.0")
    for name in ("portfolio_panel", "readiness_panel", "research_panel", "signals_panel"):
        monkeypatch.setattr(panels, name, lambda *_a, **_k: pytest.fail("private read before the gate"))

    # The shipped wrappers, not the placeholder: each must gate before its own read.
    if page == "portfolio":
        streamlit_app.render_p03_portfolio()
    elif page == "readiness":
        streamlit_app.render_p03_readiness([], None)
    elif page == "research":
        streamlit_app.render_p03_research()
    else:
        streamlit_app.render_p03_signals()

    assert any(name == "error" for name, _args in calls)


def test_a_loopback_bind_allows_the_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """127.0.0.1 passes the gate and the panel renders."""
    _mock_streamlit(monkeypatch, address="127.0.0.1")
    rendered: list[str] = []

    def _render_panel(*_args: Any, **_kwargs: Any) -> Any:
        rendered.append("panel")
        return panels.PortfolioPanel(components.Notice.SUCCESS, "ok")

    monkeypatch.setattr(panels, "portfolio_panel", _render_panel)

    streamlit_app.render_p03_portfolio()

    assert rendered == ["panel"]


def test_a_page_without_a_renderer_is_visibly_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registered page with no renderer renders the shared ``gated`` state, not a blank page."""
    calls = _mock_streamlit(monkeypatch, address="127.0.0.1")
    streamlit_app.render_p03_pending("readiness")
    assert ("info",) in {(name,) for name, _args in calls}  # Notice.GATED renders as info
    assert any("not authorized" in str(args[0]) for name, args in calls if name == "info")


# ---------------------------------------------------------------------------
# 3. Explicit inputs: never a default instant, never a discovered path
# ---------------------------------------------------------------------------


def test_an_unconfigured_private_path_is_unavailable_and_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset ``console.private_dir`` renders unavailable without opening anything."""
    monkeypatch.setattr(store_module, "LedgerStore", lambda *a, **k: pytest.fail("the ledger was opened without a configured path"))
    panel = panels.portfolio_panel(None, "2024-01-05T21:00:00Z")
    assert panel.notice is components.Notice.UNAVAILABLE
    assert "not configured" in panel.headline


def test_the_clock_is_never_the_valuation_instant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No as-of (or a naive one) is unavailable; a naive timestamp is not silently localised."""
    monkeypatch.setattr(store_module, "LedgerStore", lambda *a, **k: pytest.fail("the ledger was opened without an explicit instant"))
    directory = _private_dir(tmp_path)
    # naive, empty, and a non-UTC offset: all refused, never silently interpreted
    for text in (None, "", "2024-01-05T21:00:00", "2024-01-05T21:00:00+01:00"):
        panel = panels.portfolio_panel(directory, text)
        assert panel.notice is components.Notice.UNAVAILABLE
        assert "explicit UTC as-of" in panel.headline
    assert panels.parse_as_of("2024-01-05T21:00:00Z") == datetime(2024, 1, 5, 21, 0, tzinfo=UTC)
    assert panels.parse_as_of("2024-01-05T21:00:00+00:00") == datetime(2024, 1, 5, 21, 0, tzinfo=UTC)


def test_an_empty_ledger_and_missing_marks_are_unavailable_not_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Absent inputs render unavailable with a reason; never ``0`` or an empty table."""
    directory = _private_dir(tmp_path, marks=False)
    monkeypatch.setattr(store_module, "LedgerStore", lambda *a, **k: SimpleNamespace(load_events=lambda: ()))

    no_events = panels.portfolio_panel(directory, "2024-01-05T21:00:00Z")
    assert no_events.notice is components.Notice.UNAVAILABLE and "no events" in no_events.headline

    monkeypatch.setattr(store_module, "LedgerStore", lambda *a, **k: SimpleNamespace(load_events=lambda: (SimpleNamespace(event_id="e"),)))
    monkeypatch.setattr(portfolio, "replay_ledger", lambda *_a, **_k: _projection())

    no_marks = panels.portfolio_panel(directory, "2024-01-05T21:00:00Z")
    assert no_marks.notice is components.Notice.UNAVAILABLE and "Book marks are unavailable" in no_marks.headline
    assert "never fetches" in " ".join(no_marks.details)


# ---------------------------------------------------------------------------
# 4. Valuation states: partial is never a total
# ---------------------------------------------------------------------------


def test_a_complete_valuation_reports_nav_positions_and_allocation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Complete coverage renders NAV, position detail and allocation."""
    _stub_reads(monkeypatch, valuation=_valuation(complete=True))
    panel = panels.portfolio_panel(_private_dir(tmp_path), "2024-01-05T21:00:00Z")

    assert panel.notice is components.Notice.SUCCESS
    assert "As-of NAV 2174" in panel.headline
    assert panel.rows
    assert panel.rows[0]["mark provenance"] == "synthetic"
    assert panel.rows[0]["mark age (sessions)"] == "0"
    assert panel.allocation == ({"instrument": "us_equity/XNAS/AAA", "share": "0.57"},)
    assert panel.watch_only == ({"instrument": "us_equity/XNAS/HELD", "flag": "watchlist held flag — not a ledger position"},)


def test_an_incomplete_valuation_is_partial_and_never_a_total(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A blocked position withholds NAV/P&L/allocation while showing the marked subtotal.

    Falsified by: rendering the marked subtotal as NAV, omitting the unpriced holding, or
    emitting an allocation over a denominator that silently drops it.
    """
    _stub_reads(monkeypatch, valuation=_valuation(complete=False))
    panel = panels.portfolio_panel(_private_dir(tmp_path), "2024-01-05T21:00:00Z")

    assert panel.notice is components.Notice.PARTIAL
    assert "NAV, P&L and allocation are unavailable" in panel.headline
    assert "Cash 924" in panel.headline  # cash is always shown
    assert "not a total" in panel.headline
    assert panel.allocation == ()
    assert not any("realized gain" in detail for detail in panel.details)  # P&L withheld
    assert any("P&L and allocation are withheld" in detail for detail in panel.details)
    assert any("no mark for the exact venue session" in detail for detail in panel.details)


def test_replay_and_marks_defects_are_refused_not_downgraded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An inconsistent book or unreadable marks is refused; nothing is written or guessed."""
    directory = _private_dir(tmp_path)
    _stub_reads(monkeypatch, valuation=_valuation(complete=True), replay_error=portfolio.ProjectionError)
    refused_book = panels.portfolio_panel(directory, "2024-01-05T21:00:00Z")
    assert refused_book.notice is components.Notice.REFUSED and "replay" in refused_book.headline
    assert refused_book.rows == ()

    _stub_reads(monkeypatch, valuation=_valuation(complete=True), marks_error=portfolio.MarksError("bad"))
    refused_marks = panels.portfolio_panel(directory, "2024-01-05T21:00:00Z")
    assert refused_marks.notice is components.Notice.REFUSED and "marks" in refused_marks.headline


# ---------------------------------------------------------------------------
# 5. Reconciliation and the no-write invariant
# ---------------------------------------------------------------------------


def test_the_watchlist_read_is_bound_to_the_configured_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Held/watch-only context consults the configured private path, not a default one.

    Falsified by: calling the tracking snapshot without ``data_dir``, which would read
    whatever private watchlist/database the process default points at.
    """
    # Only the valuation path is stubbed; the real `_watchlist_held` helper runs.
    monkeypatch.setattr(
        store_module,
        "LedgerStore",
        lambda *a, **k: SimpleNamespace(load_events=lambda: (SimpleNamespace(event_id="e"),)),
    )
    monkeypatch.setattr(panels, "_instrument_label", lambda _instrument: "us_equity/XNAS/AAA")
    monkeypatch.setattr(portfolio, "replay_ledger", lambda *_a, **_k: _projection())
    monkeypatch.setattr(portfolio, "load_marks_file", lambda _path: SimpleNamespace())
    monkeypatch.setattr(portfolio, "value_positions", lambda *_a, **_k: _valuation(complete=True))

    seen: list[tuple[Path | None, Path | None]] = []
    tracking = import_module("equity_lake.signals.tracking")

    def _snapshot(*, data_dir: Path | None = None, watchlist_file: Path | None = None, **_kwargs: Any) -> Any:
        seen.append((data_dir, watchlist_file))
        return SimpleNamespace(watchlist=SimpleNamespace(instruments=[]))

    monkeypatch.setattr(tracking, "private_tracking_snapshot", _snapshot)
    directory = _private_dir(tmp_path)

    panels.portfolio_panel(directory, "2024-01-05T21:00:00Z")

    # Both private reads are bound: the tracking database *and* the watchlist file.
    assert seen == [(directory, directory / panels.PORTFOLIO_WATCHLIST_FILENAME)]


def test_reconciliation_is_rendered_from_the_configured_statement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reconciliation reads the configured statement and reports its overall state."""
    _stub_reads(monkeypatch, valuation=_valuation(complete=True))
    directory = _private_dir(tmp_path, statement=True)
    line = SimpleNamespace(
        instrument=None,
        label="",
        statement_value=Decimal("924"),
        ledger_value=Decimal("924"),
        delta=Decimal("0"),
        state=SimpleNamespace(value="matched"),
        reason="within tolerance",
    )
    reconcile_module = import_module("equity_lake.portfolio.reconcile")
    monkeypatch.setattr(reconcile_module, "parse_statement_file", lambda _path: SimpleNamespace(as_of=date(2024, 1, 5)))
    monkeypatch.setattr(reconcile_module, "reconcile", lambda *_a, **_k: SimpleNamespace(state=SimpleNamespace(value="matched"), lines=(line,)))

    panel = panels.portfolio_panel(directory, "2024-01-05T21:00:00Z")

    assert panel.reconciliation_state == "matched"
    assert panel.reconciliation == (
        {"line": "cash", "statement": "924", "ledger": "924", "delta": "0", "state": "matched", "reason": "within tolerance"},
    )


def test_reconciliation_is_not_requested_without_a_statement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No statement means "not requested" — never an implied match."""
    _stub_reads(monkeypatch, valuation=_valuation(complete=True))
    panel = panels.portfolio_panel(_private_dir(tmp_path), "2024-01-05T21:00:00Z")
    assert panel.reconciliation_state == "not requested"
    assert panel.reconciliation == ()


def test_rendering_a_panel_never_modifies_the_private_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A full render leaves the private directory byte-identical (display-only).

    Falsified by: any page path that writes an event, a mark, a cache file or a report.
    """
    _stub_reads(monkeypatch, valuation=_valuation(complete=True))
    directory = _private_dir(tmp_path, statement=True)

    def _tree() -> dict[str, bytes | None]:
        # Directories count too: a render that created an empty directory must fail here.
        return {str(path.relative_to(directory)): (path.read_bytes() if path.is_file() else None) for path in sorted(directory.rglob("*"))}

    before = _tree()
    _mock_streamlit(monkeypatch, address="127.0.0.1")
    monkeypatch.setattr(panels, "portfolio_private_dir", lambda: directory)
    panels.render_portfolio(streamlit_app.st, private_dir=directory, as_of_text="2024-01-05T21:00:00Z")
    assert _tree() == before

    # Rendering an *unreadable* ledger must not create the database or any other file.
    monkeypatch.undo()
    unreadable = tmp_path / "empty-private"
    unreadable.mkdir()
    _mock_streamlit(monkeypatch, address="127.0.0.1")
    panels.render_portfolio(streamlit_app.st, private_dir=unreadable, as_of_text="2024-01-05T21:00:00Z")
    assert list(unreadable.rglob("*")) == []


def _populated_private_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real ledger, real marks and a canonical statement under the configured root.

    Populated through the shipped writers (``LedgerStore.append`` and the documented
    marks/statement text), not through stubs, so a render over it exercises the real
    SQLite read, valuation and reconciliation path.
    """
    directory = tmp_path / "console"
    directory.mkdir()
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAA")
    effective = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    received = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)

    def _event(**overrides: Any) -> Any:
        kwargs: dict[str, Any] = {
            "parser_version": "fixture-v1",
            "source_digest": "0" * 64,
            "account": "main",
            "external_source": "fixture",
            "external_id": "evt-0001",
            "event_type": portfolio.LedgerEventType.OPENING,
            "effective_at": effective,
            "received_at": received,
            "sequence": 1,
            "instrument": None,
            "currency": "USD",
            "cash_effect": Decimal("1000.00"),
            "quantity": None,
            "price": None,
            "fees": Decimal("0"),
            "basis": None,
            "basis_status": portfolio.BasisStatus.KNOWN,
            "reverses_external_id": None,
        }
        kwargs.update(overrides)
        return portfolio.LedgerEvent(**kwargs)

    opening = _event(
        instrument=instrument,
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        basis=Decimal("1000.00"),
    )
    buy = _event(
        event_type=portfolio.LedgerEventType.BUY,
        external_id="evt-0002",
        sequence=2,
        instrument=instrument,
        cash_effect=Decimal("-605.00"),
        quantity=Decimal("5"),
        price=Decimal("120.00"),
        fees=Decimal("5.00"),
    )
    # The store emits one ``portfolio_ledger_append`` record on append. This fixture is
    # not a logging test, and structlog's configuration is process-wide: using the real
    # store logger here would bind it under whatever ``setup_structured_logging()`` an
    # earlier test left active and make ``structlog.testing.capture_logs`` in
    # ``test_portfolio_cli`` silently see nothing. Same convention as
    # ``test_ingestion_writers._RecordingLogger``: keep the module logger out of it.
    original_logger = store_module.logger
    monkeypatch.setattr(store_module, "logger", SimpleNamespace(info=lambda *args, **kwargs: None))
    try:
        assert store_module.LedgerStore(data_dir=directory).append((opening, buy)).inserted == 2
    finally:
        monkeypatch.setattr(store_module, "logger", original_logger)
    (directory / panels.PORTFOLIO_MARKS_FILENAME).write_text(
        '{"schema_version": 1, "marks": [{"market": "us_equity", "mic": "XNAS", "symbol": "AAA", '
        '"currency": "USD", "price": "125", "session": "2024-01-05", '
        '"as_of": "2024-01-05T21:00:00+00:00", "source": "fixture", "price_basis": "unadjusted", '
        '"provenance": "synthetic"}], "valuation_blocks": []}',
        encoding="utf-8",
    )
    (directory / panels.PORTFOLIO_STATEMENT_FILENAME).write_text(
        "account,as_of,line_type,market,mic,symbol,currency,quantity,cash\n"
        "main,2024-01-05T21:00:00Z,CASH,,,,USD,,395\n"
        "main,2024-01-05T21:00:00Z,POSITION,us_equity,XNAS,AAA,USD,15,\n",
        encoding="utf-8",
    )
    return directory


def test_a_populated_ledger_render_leaves_the_private_directory_byte_identical(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The no-write proof runs over a *real* populated ledger (spec §10 residual).

    The earlier proof stubbed ``LedgerStore``, so no SQLite read ever happened. This one
    opens the real database, values real positions against a real marks file and
    reconciles a real statement, then requires the tree to be byte-identical.

    Falsified by: any render-path write (event, mark, cache or report file), or by a
    vacuous render — a page that shows nothing proves nothing.
    """
    directory = _populated_private_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(core_paths, "LAKE_DIR", tmp_path / "lake")

    def _tree() -> dict[str, bytes | None]:
        # Directories are part of the tree too: a render that created an empty directory
        # would otherwise pass a files-only comparison.
        return {str(path.relative_to(directory)): (path.read_bytes() if path.is_file() else None) for path in sorted(directory.rglob("*"))}

    # Snapshot *before* any console read: a read-path write must be caught too, not just
    # a write after an identical probe read.
    before = _tree()
    assert "portfolio/portfolio.sqlite3" in before  # the ledger really is populated
    calls = _mock_streamlit(monkeypatch, address="127.0.0.1")
    panels.render_portfolio(streamlit_app.st, private_dir=directory, as_of_text="2024-01-05T21:00:00Z")

    # Non-vacuity: the real SQLite read produced a complete valuation, the real marks file
    # valued the position and the real statement reconciled, so the comparison below is
    # not trivially true.
    assert any(name == "success" and args and "As-of NAV" in str(args[0]) for name, args in calls), calls
    assert any(name == "dataframe" and args and "XNAS:AAA" in str(args[0]) for name, args in calls)
    assert any(name == "caption" and args and "Overall: matched" in str(args[0]) for name, args in calls)
    assert _tree() == before


# ---------------------------------------------------------------------------
# 6. Data readiness (stage 2)
# ---------------------------------------------------------------------------


def test_readiness_reports_market_coverage_without_claiming_per_instrument_coverage() -> None:
    """Readiness shows per-market availability and tracked per-instrument coverage.

    Falsified by: presenting a market-level union as instrument coverage, or treating an
    absent market as an empty one.
    """
    datasets = [
        {"name": "us_equity", "available": True, "rows": 10, "symbols": 2, "latest_date": "2024-01-04"},
        {"name": "cn_ashare", "available": False, "rows": 0, "symbols": 0, "latest_date": None},
        {"name": "features", "available": True, "rows": 99, "symbols": 3, "latest_date": "2024-01-04"},
    ]
    healthy = {"alerts": [], "healthy": True, "metrics": {"us_equity": {"max_age_days": 2, "latest_session": "2024-01-04"}}}
    coverage = panels.InstrumentCoverage((_coverage_row("AAA", "observed"),), date(2024, 1, 5))
    panel = panels.readiness_panel(datasets, healthy, coverage)

    assert panel.notice is components.Notice.SUCCESS
    assert any("never proves per-instrument coverage" in detail for detail in panel.details)
    assert [(row["dataset"], row["kind"]) for row in panel.rows] == [
        ("us_equity", "market"),
        ("cn_ashare", "market"),
        ("features", "table"),  # listed, but never counted as a market
    ]
    assert panel.rows[1]["available"] == "no"
    assert panel.rows[1]["latest session"] == "unknown"  # absent is not a zero-date
    # The instrument table is its own evidence, never derived from the market rows.
    assert [row["instrument"] for row in panel.instruments] == ["us_equity/XNAS/AAA"]
    assert "1 of 1 tracked instrument(s)" in panel.headline
    assert "scoped to the private watchlist" in " ".join(panel.details)


def _coverage_row(symbol: str, status: str) -> dict[str, str]:
    observed = status == "observed"
    return {
        "instrument": f"us_equity/XNAS/{symbol}",
        "price status": status,
        "latest price session": "2024-01-04" if observed else "unknown",
        "price age (days)": "1" if observed else "unknown",
        "feature status": status,
        "latest feature session": "2024-01-04" if observed else "unknown",
        "latest scan session": "none",
        "scan status": "none",
        "signal history rows": "0",
    }


def test_readiness_is_partial_when_per_instrument_coverage_is_unavailable() -> None:
    """A green market summary never stands in for instrument coverage.

    Falsified by: leaving ``SUCCESS`` when no per-instrument evidence was read, which is
    exactly the "market-level union presented as per-instrument coverage" the page must
    refuse.
    """
    datasets = [{"name": "us_equity", "available": True, "rows": 10, "symbols": 2, "latest_date": "2024-01-04"}]
    healthy = {"alerts": [], "healthy": True, "metrics": {"us_equity": {"max_age_days": 2}}}

    for missing in (None, panels.InstrumentCoverage((), None, "the private watchlist has no tracked instrument")):
        panel = panels.readiness_panel(datasets, healthy, missing)
        assert panel.notice is components.Notice.PARTIAL
        assert "Per-instrument coverage is unavailable" in panel.headline
        assert panel.instruments == ()
        assert panel.rows  # the market table is still shown
    assert "no tracked instrument" in " ".join(panel.details)


def test_readiness_is_partial_when_a_tracked_instrument_is_unobserved() -> None:
    """One tracked identity without rows is a subtotal, not a healthy total.

    Falsified by: keeping ``SUCCESS`` whenever at least one instrument is observed.
    """
    datasets = [{"name": "us_equity", "available": True, "rows": 10, "symbols": 2, "latest_date": "2024-01-04"}]
    healthy = {"alerts": [], "healthy": True, "metrics": {"us_equity": {"max_age_days": 2}}}
    coverage = panels.InstrumentCoverage((_coverage_row("AAA", "observed"), _coverage_row("BBB", "no_rows_as_of")), date(2024, 1, 5))

    panel = panels.readiness_panel(datasets, healthy, coverage)

    assert panel.notice is components.Notice.PARTIAL
    assert "1 of 2 tracked instrument(s)" in panel.headline
    assert len(panel.instruments) == 2  # the unobserved identity is named, never dropped


def test_per_instrument_coverage_is_bound_to_the_configured_private_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Coverage reads the configured watchlist and tracking directory, never defaults.

    Falsified by: calling ``build_tracking_readiness`` without ``watchlist_path``/``data_dir``,
    which would read whatever private location the process default points at instead of
    the one configured private path.
    """
    readiness = import_module("equity_lake.signals.readiness")
    lake = tmp_path / "lake"
    monkeypatch.setattr(core_paths, "LAKE_DIR", lake)
    seen: list[dict[str, Any]] = []

    def _fake_readiness(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return SimpleNamespace(as_of=date(2024, 1, 5), scan_history_status="available", signal_history_status="available", tickers=[])

    monkeypatch.setattr(readiness, "build_tracking_readiness", _fake_readiness)
    directory = tmp_path / "console"
    directory.mkdir()

    coverage = panels.per_instrument_coverage(directory)

    assert seen == [{"watchlist_path": directory / panels.PORTFOLIO_WATCHLIST_FILENAME, "data_dir": directory, "lake_dir": lake}]
    assert coverage.rows == ()
    assert coverage.reason == "the private watchlist has no tracked instrument"


def test_read_per_instrument_coverage_creates_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reading coverage over an empty private root leaves it empty (display-only).

    Falsified by: a readiness read that creates the tracking database, a watchlist
    default or any cache file.
    """
    monkeypatch.setattr(core_paths, "LAKE_DIR", tmp_path / "lake")
    directory = tmp_path / "console"
    directory.mkdir()

    coverage = panels.per_instrument_coverage(directory)

    assert coverage.rows == ()
    assert list(directory.rglob("*")) == []


def test_an_unconfigured_console_reports_coverage_unavailable_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """No configured path means unavailable with a reason, never ``0 instruments``.

    Falsified by: reading a process default, or returning an empty result that a page
    could render as a measured zero.
    """
    coverage = panels.per_instrument_coverage(None)
    assert coverage.rows == ()
    assert "not configured" in (coverage.reason or "")


def _fake_readiness_report(*, scan_status: str = "available", signal_status: str = "available") -> Any:
    """One tracked, fully observed identity with a configurable history state."""
    return SimpleNamespace(
        as_of=date(2024, 1, 5),
        scan_history_status=scan_status,
        signal_history_status=signal_status,
        tickers=[
            SimpleNamespace(
                market="us_equity",
                mic="XNAS",
                ticker="AAA",
                price_status="observed",
                latest_price_session=date(2024, 1, 4),
                price_age_days=1,
                feature_status="observed",
                latest_feature_session=date(2024, 1, 4),
                latest_scan_target_date=date(2024, 1, 4),
                latest_scan_report_statuses=["success"],
                latest_scan_outcomes=[],
                signal_history_rows=0,
            )
        ],
    )


def test_unreadable_history_makes_its_columns_unavailable_never_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A history that cannot be read yields ``unavailable``, not `0` and not `none`.

    Falsified by: mapping ``signal_history_rows``/scan fields straight through, which
    renders an unreadable count as a measured zero; and by leaving the page green over
    evidence that is partly unreadable.
    """
    readiness = import_module("equity_lake.signals.readiness")
    datasets = [{"name": "us_equity", "available": True, "rows": 10, "symbols": 2, "latest_date": "2024-01-04"}]
    healthy = {"alerts": [], "healthy": True, "metrics": {"us_equity": {"max_age_days": 2}}}

    for scan_status, signal_status, column in (
        ("available", "corrupt", "signal history rows"),
        ("missing", "available", "latest scan session"),
    ):
        report = _fake_readiness_report(scan_status=scan_status, signal_status=signal_status)
        # Default-argument binding: the fake must close over *this* iteration's report.
        monkeypatch.setattr(readiness, "build_tracking_readiness", lambda _report=report, **kwargs: _report)
        coverage = panels.per_instrument_coverage(tmp_path)

        assert coverage.rows[0][column] == "unavailable"
        assert coverage.limitations, (scan_status, signal_status)
        panel = panels.readiness_panel(datasets, healthy, coverage)
        assert panel.notice is components.Notice.PARTIAL, (scan_status, signal_status)
        assert any("unavailable, not" in detail for detail in panel.details)


def test_coverage_is_still_shown_when_no_market_is_available() -> None:
    """Coverage that was read is displayed even when the market summary is unavailable.

    Falsified by: returning early on an absent market (or no registered market), which
    makes the page claim that no per-instrument evidence was read although it was.
    """
    coverage = panels.InstrumentCoverage((_coverage_row("AAA", "no_rows_as_of"),), date(2024, 1, 5))
    for datasets in (
        [{"name": "us_equity", "available": False, "rows": 0, "symbols": 0, "latest_date": None}],
        [{"name": "features", "available": True, "rows": 5, "symbols": 2, "latest_date": "2024-01-04"}],
    ):
        panel = panels.readiness_panel(datasets, {"alerts": []}, coverage)

        assert panel.notice is components.Notice.UNAVAILABLE
        assert [row["instrument"] for row in panel.instruments] == ["us_equity/XNAS/AAA"]
        assert panel.rows == ()


def test_readiness_without_freshness_evidence_is_partial() -> None:
    """Datasets without a health report are ``partial``: freshness is unknown, not fine."""
    panel = panels.readiness_panel([{"name": "us_equity", "available": True, "rows": 1, "symbols": 1, "latest_date": "2024-01-04"}], None)
    assert panel.notice is components.Notice.PARTIAL
    assert "freshness evidence unavailable" in panel.headline


def test_a_feature_table_is_not_a_price_market() -> None:
    """A medallion table is listed as a ``table`` and never counts as an available market.

    Falsified by: counting `features` (which `MARKET_DATASETS` includes) as a price market,
    which would let an empty lake report availability.
    """
    only_features = [{"name": "features", "available": True, "rows": 5, "symbols": 2, "latest_date": "2024-01-04"}]
    assert panels.readiness_panel(only_features, {"metrics": {"x": 1}}).notice is components.Notice.UNAVAILABLE


def test_a_report_that_says_unhealthy_is_not_success() -> None:
    """The health report's own `healthy: false` wins over "metrics exist".

    Falsified by: asserting success whenever metrics are present but no alert is listed.
    """
    datasets = [{"name": "us_equity", "available": True, "rows": 1, "symbols": 1, "latest_date": "2024-01-04"}]
    panel = panels.readiness_panel(datasets, {"alerts": [], "healthy": False, "metrics": {"x": 1}})
    assert panel.notice is components.Notice.WARNING
    assert "not healthy" in panel.headline


def test_every_private_wrapper_gates_before_it_imports_the_panel_module() -> None:
    """The gate call must precede the panel import in each wrapper's source.

    Falsified by: importing ``equity_lake.dashboard.panels`` (or reading private state)
    before ``require_loopback`` returns, which the runtime stubs cannot observe once the
    import has happened at module scope.
    """
    import inspect

    for wrapper in (
        streamlit_app.render_p03_portfolio,
        streamlit_app.render_p03_readiness,
        streamlit_app.render_p03_research,
        streamlit_app.render_p03_signals,
        streamlit_app.render_p03_pending,
    ):
        source = inspect.getsource(wrapper)
        assert source.count("require_loopback") >= 1, wrapper.__name__
        assert source.index("require_loopback") < source.index("from equity_lake.dashboard import panels"), wrapper.__name__


def test_an_empty_health_report_is_not_a_healthy_result() -> None:
    """A report with neither alerts nor metrics proves nothing about freshness.

    Falsified by: treating ``{}`` (or ``{"alerts": []}``) as "all healthy", which would
    present missing evidence as a passing check.
    """
    datasets = [{"name": "us_equity", "available": True, "rows": 1, "symbols": 1, "latest_date": "2024-01-04"}]
    empty_reports: tuple[dict[str, Any], ...] = ({}, {"alerts": []}, {"metrics": {}})
    for empty in empty_reports:
        panel = panels.readiness_panel(datasets, empty)
        assert panel.notice is components.Notice.PARTIAL
        assert "freshness evidence is empty" in panel.headline


def test_readiness_with_no_available_market_is_unavailable_not_empty() -> None:
    """No market at all renders ``unavailable`` with guidance."""
    panel = panels.readiness_panel([{"name": "us_equity", "available": False, "rows": 0, "symbols": 0, "latest_date": None}], {"alerts": []})
    assert panel.notice is components.Notice.UNAVAILABLE
    assert "absent market is not an empty market" in " ".join(panel.details)


# ---------------------------------------------------------------------------
# 7. Research runs (stage 3)
# ---------------------------------------------------------------------------


def _research_lake(tmp_path: Path) -> Any:
    """A finalized run written by the real writer (immutable evidence, not a stub)."""
    import polars as pl

    from equity_lake.backtesting.evidence import (  # type: ignore[import-untyped]
        CodeIdentity,
        CostApplication,
        RunScope,
        finalise_run,
    )
    from equity_lake.backtesting.result import BacktestResult  # type: ignore[import-untyped]

    result = BacktestResult(
        strategy_name="stub",
        tickers=["AAA"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        initial_cash=100.0,
        final_cash=100.0,
        equity_curve=pl.Series("equity", [100.0, 110.0, 100.0], dtype=pl.Float64),
        trades=[],
        metrics={"total_return": 0.0},
        equity_dates=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
    )
    scope = RunScope(
        universe=["us_equity/XNYS/AAA"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        strategies=["stub"],
        costs=[CostApplication(regime="zero", fee_ratio=0.0, tax_ratio=0.0)],
        initial_cash=100.0,
    )
    return finalise_run(result, scope=scope, code=CodeIdentity(commit="a" * 40), root=tmp_path)


def test_research_panel_shows_immutable_runs_with_their_recorded_provenance(tmp_path: Path) -> None:
    """Validated runs are listed with the manifest's own derived provenance.

    Falsified by: re-deriving or upgrading a label here, or presenting an unvalidated
    directory as a run.
    """
    run = _research_lake(tmp_path)
    (tmp_path / "runs" / "not-a-run").mkdir()
    panel = panels.research_panel(tmp_path)

    assert panel.notice is components.Notice.PARTIAL  # one directory is not evidence
    assert "not valid evidence" in panel.headline
    assert panel.rows[0]["run"] == run.directory.name
    assert panel.rows[0]["protocol"] == "equity-run-manifest/4"
    assert panel.rows[0]["data provenance"] == "unknown"  # as recorded, never upgraded
    assert any("Price origin only" in detail for detail in panel.details)


def test_research_panel_displays_a_recorded_claim_verbatim(tmp_path: Path) -> None:
    """A stored non-unknown label is shown as recorded — the panel never re-derives it.

    The run below is a v3 manifest carrying a self-asserted claim (legal under v3 rules),
    so a panel that re-derived provenance from lake evidence would show `unknown` here.

    Falsified by: recomputing or upgrading the label in the view.
    """
    import polars as pl

    from equity_lake.backtesting.evidence import (
        RUN_PROTOCOL_V3,
        CodeIdentity,
        CostApplication,
        InputSnapshot,
        RunScope,
        finalise_run,
    )
    from equity_lake.backtesting.result import BacktestResult

    result = BacktestResult(
        strategy_name="stub",
        tickers=["AAA"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        initial_cash=100.0,
        final_cash=100.0,
        equity_curve=pl.Series("equity", [100.0, 110.0, 100.0], dtype=pl.Float64),
        trades=[],
        metrics={"total_return": 0.0},
        equity_dates=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
    )
    scope = RunScope(
        protocol=RUN_PROTOCOL_V3,
        universe=["us_equity/XNYS/AAA"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        strategies=["stub"],
        costs=[CostApplication(regime="zero", fee_ratio=0.0, tax_ratio=0.0)],
        initial_cash=100.0,
    )
    snapshot = InputSnapshot(name="price_marks:us_equity", sha256="a" * 64, source="lake:delta", version="3")
    finalise_run(
        result,
        scope=scope,
        input_snapshots=[snapshot],
        data_provenance="synthetic",
        code=CodeIdentity(commit="a" * 40),
        root=tmp_path,
    )

    panel = panels.research_panel(tmp_path)
    assert panel.rows[0]["data provenance"] == "synthetic"  # recorded, not re-derived
    assert any("legacy protocol-v3" in detail for detail in panel.details)


def test_research_panel_is_unavailable_without_evidence(tmp_path: Path) -> None:
    """No run means unavailable with guidance, not an empty table."""
    panel = panels.research_panel(tmp_path)
    assert panel.notice is components.Notice.UNAVAILABLE
    assert "not a run" in " ".join(panel.details)


# ---------------------------------------------------------------------------
# 8. Signals (stage 3): candidates only, bound to the configured path
# ---------------------------------------------------------------------------


def test_signals_lists_candidates_with_reasons_and_never_an_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Candidates carry their reason/session and the panel says they are not orders."""
    tracking = import_module("equity_lake.signals.tracking")
    instrument = import_module("equity_lake.core.instruments").InstrumentKey(market="us_equity", mic="XNAS", symbol="AAA")
    candidate = SimpleNamespace(
        instrument=instrument,
        action="BUY",
        session=date(2024, 12, 2),
        source="price_sma20",
        reasoning="deviation +2% over SMA20",
        price=102.0,
        deviation=0.02,
        threshold=0.02,
    )
    report = SimpleNamespace(
        evaluations=[SimpleNamespace(rules=[SimpleNamespace(candidate=candidate), SimpleNamespace(candidate=None)])],
    )
    snapshot = SimpleNamespace(recent_reports=[report], last_successful_scan_at=datetime(2024, 12, 2, 22, 0, tzinfo=UTC))
    monkeypatch.setattr(tracking, "private_tracking_snapshot", lambda **_kwargs: snapshot)
    monkeypatch.setattr(panels, "_instrument_label", lambda _instrument: "us_equity/XNAS/AAA")

    panel = panels.signals_panel(tmp_path)

    assert panel.notice is components.Notice.INFO
    assert panel.rows[0]["action"] == "BUY"
    assert panel.rows[0]["reason"] == "deviation +2% over SMA20"
    assert panel.rows[0]["session"] == "2024-12-02"
    assert any("never an order" in detail for detail in panel.details)
    assert len(panel.rows) == 1  # a non-signal rule contributes no row


def test_signals_surfaces_a_corrupt_history_and_a_stale_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Corrupt history is unavailable and a stale run is a warning, never fresh candidates.

    Falsified by: listing candidates without consulting ``history_status`` or
    ``stale_run_warning``, which would present stale state as current.
    """
    tracking = import_module("equity_lake.signals.tracking")
    candidate = SimpleNamespace(
        instrument=import_module("equity_lake.core.instruments").InstrumentKey(market="us_equity", mic="XNAS", symbol="AAA"),
        action="BUY",
        session=date(2024, 12, 2),
        source="price_sma20",
        reasoning="deviation",
        price=1.0,
        deviation=0.02,
        threshold=0.02,
    )
    report = SimpleNamespace(evaluations=[SimpleNamespace(rules=[SimpleNamespace(candidate=candidate)])])
    monkeypatch.setattr(panels, "_instrument_label", lambda _instrument: "us_equity/XNAS/AAA")

    corrupt = SimpleNamespace(
        recent_reports=[report],
        history_status="corrupt",
        stale_run_warning=None,
        last_successful_scan_at=None,
    )
    monkeypatch.setattr(tracking, "private_tracking_snapshot", lambda **_kwargs: corrupt)
    corrupt_panel = panels.signals_panel(tmp_path)
    assert corrupt_panel.notice is components.Notice.UNAVAILABLE
    assert corrupt_panel.rows == ()

    stale = SimpleNamespace(
        recent_reports=[report],
        history_status="available",
        stale_run_warning="last run is 9 days old",
        last_successful_scan_at=datetime(2024, 12, 2, 22, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(tracking, "private_tracking_snapshot", lambda **_kwargs: stale)
    stale_panel = panels.signals_panel(tmp_path)
    assert stale_panel.notice is components.Notice.WARNING
    assert "stale run" in stale_panel.headline
    assert any("9 days old" in detail for detail in stale_panel.details)


def test_signals_without_private_evidence_is_unavailable_not_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreadable or unconfigured private state is ``unavailable``, never "no signal"."""
    assert panels.signals_panel(None).notice is components.Notice.UNAVAILABLE
    tracking = import_module("equity_lake.signals.tracking")
    monkeypatch.setattr(tracking, "private_tracking_snapshot", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    panel = panels.signals_panel(Path("/nonexistent"))
    assert panel.notice is components.Notice.UNAVAILABLE
    assert "No candidate is inferred from missing evidence" in " ".join(panel.details)
