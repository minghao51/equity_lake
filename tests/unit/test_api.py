"""Smoke tests for the Phase 2B read API (FastAPI TestClient)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from equity_lake.api import deps
from equity_lake.api.main import create_app
from equity_lake.findings.models import FindingCard
from equity_lake.storage.delta import DeltaReadError


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


def test_signals_endpoint_defaults_to_today(monkeypatch) -> None:
    monkeypatch.setattr(deps, "list_signals", lambda d: [{"ticker": "AAPL", "date": str(d)}])
    client = TestClient(create_app())
    response = client.get("/signals")
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "AAPL"


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
    monkeypatch.setattr(deps, "list_predictions", lambda **kw: [{"ticker": "AAPL", "p": 0.6}])
    client = TestClient(create_app())
    response = client.get("/predictions?ticker=AAPL&limit=5")
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "AAPL"


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
