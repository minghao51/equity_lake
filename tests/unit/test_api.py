"""Smoke tests for the Phase 2B read API (FastAPI TestClient)."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from equity_lake.api import deps
from equity_lake.api.main import create_app
from equity_lake.findings.models import FindingCard


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
