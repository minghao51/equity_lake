"""Loopback gate and private canonical-tracking view tests (optional dashboard extra)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest


def _streamlit_app() -> Any:
    return import_module("equity_lake.dashboard.streamlit_app")


pytest.importorskip("streamlit", reason="private dashboard tests require the optional dashboard dependency group")
streamlit_app = _streamlit_app()
tracking = import_module("equity_lake.signals.tracking")
InstrumentKey = import_module("equity_lake.core.instruments").InstrumentKey


def _mock_streamlit(monkeypatch: pytest.MonkeyPatch, *, address: str | None) -> list[tuple[str, tuple[object, ...]]]:
    calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(streamlit_app.st, "get_option", lambda _name: address, raising=False)
    for name in ("title", "error", "caption", "subheader", "dataframe", "info", "warning", "write"):
        monkeypatch.setattr(streamlit_app.st, name, lambda *args, _name=name, **_kwargs: calls.append((_name, args)), raising=False)
    return calls


def _snapshot() -> Any:
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
    candidate = tracking.TrackingCandidate(
        instrument=instrument,
        session=date(2024, 12, 2),
        source="price_sma20",
        action="SELL",
        rule_version=tracking.TRACKING_RULE_VERSION,
        reasoning="synthetic downside crossing",
        price=98.0,
        sma20=100.0,
        deviation=-0.02,
        threshold=-0.01,
    )
    outcome = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=candidate)
    report = SimpleNamespace(evaluations=[SimpleNamespace(instrument=instrument, rules=[outcome])])
    watch_item = SimpleNamespace(
        instrument=instrument,
        held=True,
        groups=["core"],
        tags=["review"],
        notes="private local note",
        price_zone=None,
    )
    event = SimpleNamespace(
        event=SimpleNamespace(
            event_id="a" * 64,
            instrument=instrument,
            session=date(2024, 12, 2),
            action="SELL",
            source="price_sma20",
        ),
        status="sent",
        attempts=1,
        error_code=None,
    )
    return SimpleNamespace(
        history_status="available",
        last_successful_scan_at=datetime(2024, 12, 2, 22, tzinfo=UTC),
        stale_run_warning=None,
        watchlist=SimpleNamespace(instruments=[watch_item]),
        readiness=[
            SimpleNamespace(
                instrument=instrument,
                status="signal",
                expected_session=date(2024, 12, 2),
                last_evaluated_session=date(2024, 12, 2),
                detail=None,
            )
        ],
        recent_reports=[report],
        conflicts=[[candidate, candidate.model_copy(update={"action": "BUY"})]],
        recent_events=[event],
    )


def test_loopback_address_validation_is_strict() -> None:
    assert streamlit_app.is_loopback_bind_address("127.0.0.1")
    assert streamlit_app.is_loopback_bind_address("::1")
    assert streamlit_app.is_loopback_bind_address("localhost")
    assert not streamlit_app.is_loopback_bind_address(None)
    assert not streamlit_app.is_loopback_bind_address("")
    assert not streamlit_app.is_loopback_bind_address("0.0.0.0")
    assert not streamlit_app.is_loopback_bind_address("192.0.2.4")


def test_non_loopback_fails_closed_before_private_config_or_report_load(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_streamlit(monkeypatch, address="0.0.0.0")
    load_private = MagicMock(side_effect=AssertionError("private watchlist or events must not load"))
    monkeypatch.setattr(streamlit_app, "import_module", load_private)

    streamlit_app.render_private_tracking_preview()

    load_private.assert_not_called()
    assert any(name == "error" for name, _ in calls)


def test_watch_only_sell_is_presented_as_downside_caution(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_streamlit(monkeypatch, address="localhost")
    snapshot = _snapshot()
    snapshot.watchlist.instruments[0].held = False
    build = MagicMock(return_value=snapshot)

    def load_private_module(name: str) -> Any:
        if name != "equity_lake.signals.tracking":
            pytest.fail(name)
        return SimpleNamespace(private_tracking_snapshot=build)

    monkeypatch.setattr(streamlit_app, "import_module", load_private_module)
    streamlit_app.render_private_tracking_preview()

    dataframes = [cast(list[dict[str, object]], args[0]) for name, args in calls if name == "dataframe"]
    assert dataframes[2][0]["context"] == "downside watch"
    assert "never an order" in repr(calls)


def test_loopback_view_shows_private_notes_candidates_conflicts_and_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_streamlit(monkeypatch, address="127.0.0.1")
    snapshot = _snapshot()
    build = MagicMock(return_value=snapshot)

    def load_private_module(name: str) -> Any:
        if name != "equity_lake.signals.tracking":
            pytest.fail(name)
        return SimpleNamespace(private_tracking_snapshot=build)

    monkeypatch.setattr(streamlit_app, "import_module", load_private_module)

    streamlit_app.render_private_tracking_preview()

    build.assert_called_once_with()
    assert any(name == "warning" and "Conflicting BUY/SELL candidates" in str(args[0]) for name, args in calls)
    dataframes = [cast(list[dict[str, object]], args[0]) for name, args in calls if name == "dataframe"]
    assert len(dataframes) == 5
    assert dataframes[0][0]["notes"] == "private local note"
    assert dataframes[2][0]["context"] == "held exit candidate"
    assert {row["action"] for row in dataframes[3]} == {"BUY", "SELL"}
    assert dataframes[4][0]["delivery"] == "sent"
    rendered = repr(calls)
    assert "never an order" in rendered
    assert "not calibrated probabilities" in rendered
