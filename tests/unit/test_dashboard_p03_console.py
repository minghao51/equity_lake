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

streamlit_app = import_module("equity_lake.dashboard.streamlit_app")
panels = import_module("equity_lake.dashboard.panels")
pages = import_module("equity_lake.dashboard.pages")
components = import_module("equity_lake.dashboard.components")
portfolio = import_module("equity_lake.portfolio")
store_module = import_module("equity_lake.portfolio.store")

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
    monkeypatch.setattr(panels, "portfolio_panel", lambda *_a, **_k: pytest.fail("private read before the gate"))
    monkeypatch.setattr(panels, "render_pending_panel", lambda *_a, **_k: pytest.fail("private panel rendered before the gate"))

    if page == "portfolio":
        streamlit_app.render_p03_portfolio()
    else:
        streamlit_app.render_p03_pending(page)

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


@pytest.mark.parametrize("page", ["readiness", "research", "signals"])
def test_registered_but_unauthorized_pages_render_gated(monkeypatch: pytest.MonkeyPatch, page: str) -> None:
    """Stage 1 ships Portfolio only; the other panels are visibly ``gated``, not blank."""
    calls = _mock_streamlit(monkeypatch, address="127.0.0.1")
    streamlit_app.render_p03_pending(page)
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

    def _tree() -> dict[str, bytes]:
        return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(directory.rglob("*")) if path.is_file()}

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
