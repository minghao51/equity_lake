"""Smoke tests for the Phase 2B read API (FastAPI TestClient)."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
from fastapi.testclient import TestClient

from equity_lake.api import deps
from equity_lake.api.main import create_app
from equity_lake.core.dates import resolve_trading_date
from equity_lake.findings.models import FindingCard
from equity_lake.storage.delta import DeltaReadError, write_delta


def _fake_card() -> FindingCard:
    return FindingCard(
        id="demo-card",
        axis="model",
        claim="demo claim",
        verdict="positive",
        conclusion="demo conclusion",
        metrics={"accuracy": 0.5},
        evidence_refs=[],
        run_date=date.today(),
        scope={"tickers": ["AAPL"]},
    )


# --- health + findings --------------------------------------------------------


def test_health_endpoint_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_findings_list_serializes_cards(monkeypatch) -> None:
    monkeypatch.setattr(deps, "list_findings", lambda: [_fake_card()])
    client = TestClient(create_app())
    response = client.get("/findings")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "demo-card"
    assert data[0]["scope"]["tickers"] == ["AAPL"]


def test_findings_detail_404_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(deps, "list_findings", lambda: [])
    client = TestClient(create_app())
    response = client.get("/findings/no-such-card")
    assert response.status_code == 404


# --- signals ------------------------------------------------------------------


def test_signals_endpoint_defaults_to_last_trading_day(monkeypatch) -> None:
    """No date param resolves to the last completed trading day (calendar-aware), not date.today()."""
    captured: dict[str, date] = {}
    monkeypatch.setattr(deps, "list_signals", lambda d: captured.update(d=d) or [{"ticker": "AAPL", "date": str(d)}])  # type: ignore[func-returns-value]
    client = TestClient(create_app())
    response = client.get("/signals")
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "AAPL"
    assert captured["d"] == resolve_trading_date(None)


def test_signals_endpoint_parses_date_param(monkeypatch) -> None:
    captured: dict[str, date] = {}
    monkeypatch.setattr(deps, "list_signals", lambda d: captured.update(d=d) or [])  # type: ignore[func-returns-value]
    client = TestClient(create_app())
    response = client.get("/signals?target_date=2024-01-02")
    assert response.status_code == 200
    assert captured["d"] == date(2024, 1, 2)


def test_signals_maps_delta_read_error_to_503(monkeypatch) -> None:
    """A broken signal-history table must surface as 503, not a silent 200 []."""

    def _boom(d: date) -> list[dict[str, Any]]:
        raise DeltaReadError("signals", RuntimeError("corrupt _delta_log"))

    monkeypatch.setattr(deps, "list_signals", _boom)
    client = TestClient(create_app())
    response = client.get("/signals")
    assert response.status_code == 503
    assert "unreadable" in response.json()["detail"]


# --- models / predictions / backtests ----------------------------------------


def test_models_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(deps, "list_models", lambda: [{"ticker": "AAPL", "backend": "xgboost"}])
    client = TestClient(create_app())
    response = client.get("/models")
    assert response.status_code == 200
    assert response.json()[0]["backend"] == "xgboost"


def test_predictions_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        deps,
        "list_predictions",
        lambda **kw: [{"ticker": "AAPL", "p": 0.6, "date": str(kw.get("target_date"))}],
    )
    client = TestClient(create_app())
    response = client.get("/predictions?ticker=AAPL&limit=5")
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "AAPL"


def test_predictions_endpoint_passes_date_param(monkeypatch) -> None:
    """The date query param flows through to deps.list_predictions."""
    captured: dict[str, Any] = {}

    def _capture(**kw: Any) -> list[dict[str, Any]]:
        captured.update(kw)
        return []

    monkeypatch.setattr(deps, "list_predictions", _capture)
    client = TestClient(create_app())
    response = client.get("/predictions?target_date=2024-01-02")
    assert response.status_code == 200
    assert captured["target_date"] == date(2024, 1, 2)


def test_predictions_maps_delta_read_error_to_503(monkeypatch) -> None:
    """A missing/corrupt predictions table must surface as 503, not a silent 200 []."""

    def _boom(**kw: Any) -> list[dict[str, Any]]:
        raise DeltaReadError("04_platinum/predictions", RuntimeError("table missing"))

    monkeypatch.setattr(deps, "list_predictions", _boom)
    client = TestClient(create_app())
    response = client.get("/predictions")
    assert response.status_code == 503
    assert "unreadable" in response.json()["detail"]


def test_predictions_rejects_out_of_range_limit() -> None:
    client = TestClient(create_app())
    response = client.get("/predictions?limit=0")
    assert response.status_code == 422  # Query(ge=1) validation


def test_backtests_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(deps, "list_backtests", lambda: [{"slug": "momentum__zero"}])
    client = TestClient(create_app())
    response = client.get("/backtests")
    assert response.status_code == 200
    assert response.json()[0]["slug"] == "momentum__zero"


# --- deps.list_predictions partition pruning ---------------------------------


def _write_predictions_table(lake_dir, monkeypatch) -> None:
    """Write a two-partition predictions Delta table and point the lake at it."""
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "date": [date(2024, 1, 2), date(2024, 1, 1), date(2024, 1, 2)],
            "direction": ["up", "down", "up"],
            "probability": [0.6, 0.55, 0.7],
        },
        schema={"ticker": pl.Utf8, "date": pl.Date, "direction": pl.Utf8, "probability": pl.Float64},
    )
    assert write_delta(frame, "04_platinum/predictions", lake_dir=lake_dir)
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", lake_dir)


def test_list_predictions_prunes_by_date_partition(tmp_path, monkeypatch) -> None:
    """A date-scoped read returns only that partition's rows."""
    _write_predictions_table(tmp_path, monkeypatch)
    rows = deps.list_predictions(target_date=date(2024, 1, 2))
    assert {r["ticker"] for r in rows} == {"AAPL", "MSFT"}
    assert all(r["date"] == date(2024, 1, 2) for r in rows)


def test_list_predictions_without_date_reads_all(tmp_path, monkeypatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch)
    rows = deps.list_predictions()
    assert len(rows) == 3


def test_list_predictions_missing_table_raises_delta_read_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    import pytest

    with pytest.raises(DeltaReadError):
        deps.list_predictions()
