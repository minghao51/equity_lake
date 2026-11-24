"""End-to-end writer -> Delta table -> read API proof (R11).

The writer test (``test_forecast_provenance.py``) asserts the in-memory frame and
the API tests hand-build their table, so a column-name drift between
``run_prediction_job`` and ``api/deps.py`` would pass both suites. This test goes
through the real storage seam: the stub forecaster supplies the payload, the real
``merge_delta``/``write_delta`` write a tmp lake, and the read API derives the
statuses from the persisted table.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from equity_lake.api.main import create_app
from equity_lake.core.availability import (
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_SCHEMA_VERSION,
    InstrumentKey,
)
from equity_lake.core.schemas import (
    FEATURE_SCHEMA_VERSION,
    FORECAST_PROVENANCE_COLUMNS,
    FORECAST_PROVENANCE_VERSION,
    PREDICTION_SEMANTICS_VERSION,
    PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
)
from equity_lake.ml import run_prediction_job
from equity_lake.ml.forecasting import (
    DEFAULT_MAX_MODEL_AGE_DAYS,
    MISSING_VALUE_POLICY,
    MODEL_PROVENANCE_VERSION,
)

_TRADING_DATE = date(2024, 1, 2)
_MODEL_TRAINED_AT = date(2023, 12, 29)
_ARTIFACT_SHA256 = "0123456789abcdef" * 4
_TICKER = "AAPL"


def _v1_payload(ticker: str, trading_date: date) -> dict[str, Any]:
    """Inference payload shaped like a verified ``v1_direction`` ``predict()``."""
    identity = InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker)
    return {
        **identity.as_columns(),
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "date": trading_date,
        "prediction": 1,
        "probability": 0.62,
        "model_mode": "v1_direction",
        "model_version": "instrument-v1-xgboost_v1_direction_2023-12-29",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_provenance_version": MODEL_PROVENANCE_VERSION,
        "model_artifact_sha256": _ARTIFACT_SHA256,
        "model_trained_at": _MODEL_TRAINED_AT,
        "model_age_days": (trading_date - _MODEL_TRAINED_AT).days,
        "model_max_age_days": DEFAULT_MAX_MODEL_AGE_DAYS,
        "training_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "inference_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "missing_value_policy": MISSING_VALUE_POLICY,
        "candidate_strategy_fingerprint": None,
        "horizon_days": 1,
        "threshold_used": 0.55,
        "threshold_source": "stored_optimized",
    }


class _StubForecaster:
    """Stub exposing the verified payload without touching model artifacts."""

    def __init__(self, model_dir: str | None = None, candidate_strategies: list[dict[str, Any]] | None = None) -> None:
        del model_dir, candidate_strategies
        self.closed = False

    def predict(self, *, ticker: str | InstrumentKey, date: date) -> dict[str, Any]:
        symbol = ticker if isinstance(ticker, str) else ticker.symbol
        return _v1_payload(symbol, date)

    def close(self) -> None:
        self.closed = True


def _install_forecaster(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.__dict__["PriceForecaster"] = _StubForecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)


def test_persisted_prediction_is_current_usable_through_the_read_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One real writer -> Delta -> API round trip must report a usable row."""
    _install_forecaster(monkeypatch)
    # The real merge/write path resolves the lake root from this module global;
    # storage itself is NOT stubbed.
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)

    success, _results = run_prediction_job(trading_date=_TRADING_DATE, tickers=[_TICKER])

    assert success is True
    assert (tmp_path / "04_platinum" / "predictions" / "_delta_log").is_dir()

    response = TestClient(create_app()).get(
        "/predictions",
        params={"ticker": _TICKER, "target_date": _TRADING_DATE.isoformat()},
    )

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == _TICKER

    # Every proof column the writer persists must survive the table read under
    # the exact name the API derives its statuses from.
    for column in FORECAST_PROVENANCE_COLUMNS:
        assert column in row, f"{column} did not round-trip writer -> Delta -> API"
        if column == "candidate_strategy_fingerprint":
            # E3 binds the fingerprint to the meta-label contract only.
            assert row[column] is None
        else:
            assert row[column] is not None, f"{column} did not round-trip writer -> Delta -> API"

    assert row["forecast_provenance_version"] == FORECAST_PROVENANCE_VERSION
    assert row["model_provenance_version"] == MODEL_PROVENANCE_VERSION
    assert row["model_artifact_sha256"] == _ARTIFACT_SHA256
    assert row["model_trained_at"] == _MODEL_TRAINED_AT.isoformat()
    assert row["model_age_days"] == (_TRADING_DATE - _MODEL_TRAINED_AT).days
    assert row["model_max_age_days"] == DEFAULT_MAX_MODEL_AGE_DAYS
    assert row["training_feature_schema_version"] == row["inference_feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert row["price_adjustment_version"] == PRICE_ADJUSTMENT_VERSION
    assert row["availability_schema_version"] == AVAILABILITY_SCHEMA_VERSION
    assert row["missing_value_policy"] == MISSING_VALUE_POLICY
    assert row["horizon_days"] == 1
    assert row["threshold_used"] == 0.55
    assert row["threshold_source"] == "stored_optimized"
    assert row["prediction_target"] == PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE
    assert row["prediction_semantics_version"] == PREDICTION_SEMANTICS_VERSION

    assert row["api_provenance_status"] == "valid"
    assert row["api_provenance_reason"] == "row_bound_forecast_proof_is_complete_and_bound_to_the_current_contracts"
    assert row["api_prediction_semantics_status"] == "established"
    assert row["api_prediction_semantics_reason"] == (
        f"row_bound_directional_prediction_semantics_are_established_on_prediction_semantics_version:{PREDICTION_SEMANTICS_VERSION}"
    )
    assert row["api_usability_status"] == "current_usable"
    assert row["api_usability_reasons"] == []
