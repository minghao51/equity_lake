"""Smoke tests for the Phase 2B read API (FastAPI TestClient)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from equity_lake.api import deps  # type: ignore[import-untyped]
from equity_lake.api.main import create_app  # type: ignore[import-untyped]
from equity_lake.core.availability import (  # type: ignore[import-untyped]
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
)
from equity_lake.core.dates import resolve_trading_date  # type: ignore[import-untyped]
from equity_lake.core.schemas import (  # type: ignore[import-untyped]
    FEATURE_SCHEMA_VERSION,
    FORECAST_PROVENANCE_COLUMNS,
    FORECAST_PROVENANCE_VERSION,
    PREDICTION_SEMANTICS_VERSION,
    PREDICTION_TARGET_CANDIDATE_EXECUTE,
    PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
)
from equity_lake.findings.models import FindingCard  # type: ignore[import-untyped]
from equity_lake.storage.delta import (  # type: ignore[import-untyped]
    DeltaReadError,
    string_view_columns,
    update_delta_columns,
    write_delta,
)


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


def _prediction_result(rows: list[dict[str, Any]], target_date: date = date(2024, 1, 2)) -> deps.PredictionReadResult:
    return deps.PredictionReadResult(
        rows=rows,
        window_start=target_date,
        window_end=target_date,
        snapshot_version=3,
        processed_rows=len(rows),
        decoded_bytes=128,
        selected_files=1,
        selected_file_bytes=1024,
        scan_status="complete" if rows else "empty",
    )


# --- health + findings --------------------------------------------------------


def test_health_endpoint_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_findings_list_serializes_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "list_findings", lambda: [_fake_card()])
    client = TestClient(create_app())
    response = client.get("/findings")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "demo-card"
    assert data[0]["scope"]["tickers"] == ["AAPL"]


def test_findings_detail_404_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "list_findings", lambda: [])
    client = TestClient(create_app())
    response = client.get("/findings/no-such-card")
    assert response.status_code == 404


# --- signals ------------------------------------------------------------------


def test_signals_endpoint_defaults_to_last_trading_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """No date param resolves to the last completed trading day (calendar-aware), not date.today()."""
    captured: dict[str, date] = {}
    monkeypatch.setattr(deps, "list_signals", lambda d: captured.update(d=d) or [{"ticker": "AAPL", "date": str(d)}])
    client = TestClient(create_app())
    response = client.get("/signals")
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "AAPL"
    assert captured["d"] == resolve_trading_date(None)


def test_signals_endpoint_parses_date_param(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, date] = {}
    monkeypatch.setattr(deps, "list_signals", lambda d: captured.update(d=d) or [])
    client = TestClient(create_app())
    response = client.get("/signals?target_date=2024-01-02")
    assert response.status_code == 200
    assert captured["d"] == date(2024, 1, 2)


def test_signals_maps_delta_read_error_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken signal-history table must surface as 503, not a silent 200 []."""

    def _boom(d: date) -> list[dict[str, Any]]:
        raise DeltaReadError("signals", RuntimeError("corrupt _delta_log"))

    monkeypatch.setattr(deps, "list_signals", _boom)
    client = TestClient(create_app())
    response = client.get("/signals")
    assert response.status_code == 503
    assert "unreadable" in response.json()["detail"]


# --- models / predictions / backtests ----------------------------------------


def test_models_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "list_models", lambda: [{"ticker": "AAPL", "backend": "xgboost"}])
    client = TestClient(create_app())
    response = client.get("/models")
    assert response.status_code == 200
    assert response.json()[0]["backend"] == "xgboost"


def test_predictions_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps,
        "list_predictions",
        lambda **kw: _prediction_result([{"ticker": "AAPL", "p": 0.6, "date": str(kw.get("target_date"))}]),
    )
    client = TestClient(create_app())
    response = client.get("/predictions?ticker=AAPL&limit=5")
    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "AAPL"


def test_predictions_endpoint_passes_date_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """The date query param flows through to deps.list_predictions."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(deps, "list_predictions", lambda **kw: captured.update(kw) or _prediction_result([]))
    client = TestClient(create_app())
    response = client.get("/predictions?target_date=2024-01-02")
    assert response.status_code == 200
    assert captured["target_date"] == date(2024, 1, 2)
    assert response.headers["x-predictions-window-start"] == "2024-01-02"


def test_predictions_maps_delta_read_error_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing/corrupt predictions table must surface as 503, not a silent 200 []."""

    def _boom(**kw: Any) -> list[dict[str, Any]]:
        raise DeltaReadError("04_platinum/predictions", RuntimeError("table missing"))

    monkeypatch.setattr(deps, "list_predictions", _boom)
    client = TestClient(create_app())
    response = client.get("/predictions")
    assert response.status_code == 503
    assert "unreadable" in response.json()["detail"]


@pytest.mark.parametrize("limit", [0, 1001])
def test_predictions_rejects_out_of_range_limit(limit: int) -> None:
    client = TestClient(create_app())
    response = client.get(f"/predictions?limit={limit}")
    assert response.status_code == 422


def test_backtests_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "list_backtests", lambda: [{"slug": "momentum__zero"}])
    client = TestClient(create_app())
    response = client.get("/backtests")
    assert response.status_code == 200
    assert response.json()[0]["slug"] == "momentum__zero"


# --- bounded predictions reads and row status -------------------------------


def _prediction_row(
    symbol: str = "AAPL",
    *,
    target_date: date = date(2024, 1, 2),
    market: str | None = "us_equity",
    mic: str | None = "XNAS",
    **overrides: Any,
) -> dict[str, Any]:
    return {
        "market": market,
        "mic": mic,
        "symbol": symbol,
        "ticker": symbol,
        "identity_schema_version": 1,
        "date": target_date,
        "direction": "up",
        "probability": 0.6,
        "model_mode": "v1_direction",
        "model_version": "artifact-token-is-not-proof",
        "feature_schema_version": 7,
        **overrides,
    }


def _write_predictions_table(
    lake_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]] | None = None,
    *,
    schema_overrides: dict[str, pl.DataType] | None = None,
) -> None:
    """Write a synthetic date-partitioned Delta table and point the lake at it."""
    if rows is None:
        rows = [
            {"ticker": "AAPL", "date": date(2024, 1, 2), "direction": "up", "probability": 0.6},
            {"ticker": "AAPL", "date": date(2024, 1, 1), "direction": "down", "probability": 0.55},
            {"ticker": "MSFT", "date": date(2024, 1, 2), "direction": "up", "probability": 0.7},
        ]
    frame = pl.DataFrame(rows, infer_schema_length=None, schema_overrides=schema_overrides)
    assert write_delta(frame, "04_platinum/predictions", lake_dir=lake_dir)
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", lake_dir)


# ADR-0021 §2.2 dtypes; an all-null proof column needs an explicit type because
# Delta rejects a parquet ``Null`` column.
_FORECAST_PROOF_DTYPES: dict[str, pl.DataType] = {
    "forecast_provenance_version": pl.Int16(),
    "model_provenance_version": pl.Int16(),
    "model_artifact_sha256": pl.String(),
    "model_trained_at": pl.Date(),
    "model_age_days": pl.Int16(),
    "model_max_age_days": pl.Int16(),
    "training_feature_schema_version": pl.Int16(),
    "inference_feature_schema_version": pl.Int16(),
    "price_adjustment_version": pl.String(),
    "availability_schema_version": pl.Int16(),
    "missing_value_policy": pl.String(),
    "candidate_strategy_fingerprint": pl.String(),
    "horizon_days": pl.Int16(),
    "threshold_used": pl.Float64(),
    "threshold_source": pl.String(),
    "prediction_target": pl.String(),
    "prediction_semantics_version": pl.Int16(),
}


def _write_proof_table(lake_dir: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    """Write proof rows with the §2.2 dtypes (Delta rejects an untyped all-null column)."""
    _write_predictions_table(lake_dir, monkeypatch, rows, schema_overrides=_FORECAST_PROOF_DTYPES)


def _forecast_proof(**overrides: Any) -> dict[str, Any]:
    """Return a complete, current, self-consistent v1 proof (ADR-0021 §2.2)."""
    proof: dict[str, Any] = {
        "forecast_provenance_version": FORECAST_PROVENANCE_VERSION,
        # E1 evidence; the stored-row reader requires presence and an integer
        # type. The writer copies the sidecar's provenance_version verbatim.
        "model_provenance_version": 1,
        "model_artifact_sha256": "0" * 64,
        "model_trained_at": date(2023, 12, 29),
        "model_age_days": 4,
        "model_max_age_days": 30,
        "training_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "inference_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "missing_value_policy": "median_impute",
        "candidate_strategy_fingerprint": None,
        "horizon_days": 1,
        "threshold_used": 0.5,
        "threshold_source": "config_default",
        "prediction_target": PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
        "prediction_semantics_version": PREDICTION_SEMANTICS_VERSION,
    }
    proof.update(overrides)
    return proof


def _forbid_prediction_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if file-budget tests progress far enough to create an Arrow scanner."""
    original_open = deps._open_predictions_snapshot

    class DatasetProbe:
        def __init__(self, dataset: Any) -> None:
            self.dataset = dataset
            self.schema = dataset.schema

        def scanner(self, **kwargs: Any) -> Any:
            pytest.fail("Arrow scanner was created before selected-file budgets were checked")

    class SnapshotProbe:
        def __init__(self, snapshot: Any) -> None:
            self.snapshot = snapshot

        def __getattr__(self, name: str) -> Any:
            return getattr(self.snapshot, name)

        def to_pyarrow_dataset(self, *, partitions: Any) -> DatasetProbe:
            return DatasetProbe(self.snapshot.to_pyarrow_dataset(partitions=partitions))

    monkeypatch.setattr(deps, "_open_predictions_snapshot", lambda path: SnapshotProbe(original_open(path)))


def _forbid_prediction_batch_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if an over-budget Arrow batch is converted to Python rows."""
    original_open = deps._open_predictions_snapshot

    class BatchProbe:
        def __init__(self, batch: Any) -> None:
            self.batch = batch

        @property
        def num_rows(self) -> int:
            return int(self.batch.num_rows)

        @property
        def nbytes(self) -> int:
            return int(self.batch.nbytes)

        def to_pylist(self) -> list[dict[str, Any]]:
            pytest.fail("Over-budget Arrow batch was converted to Python rows")

    class ScannerProbe:
        def __init__(self, scanner: Any) -> None:
            self.scanner = scanner

        def to_batches(self) -> Any:
            return (BatchProbe(batch) for batch in self.scanner.to_batches())

    class DatasetProbe:
        def __init__(self, dataset: Any) -> None:
            self.dataset = dataset
            self.schema = dataset.schema

        def scanner(self, **kwargs: Any) -> ScannerProbe:
            return ScannerProbe(self.dataset.scanner(**kwargs))

    class SnapshotProbe:
        def __init__(self, snapshot: Any) -> None:
            self.snapshot = snapshot

        def __getattr__(self, name: str) -> Any:
            return getattr(self.snapshot, name)

        def to_pyarrow_dataset(self, *, partitions: Any) -> DatasetProbe:
            return DatasetProbe(self.snapshot.to_pyarrow_dataset(partitions=partitions))

    monkeypatch.setattr(deps, "_open_predictions_snapshot", lambda path: SnapshotProbe(original_open(path)))


def test_list_predictions_prunes_date_partition_and_returns_sorted_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch)
    result = deps.list_predictions(target_date=date(2024, 1, 2))
    assert {row["ticker"] for row in result.rows} == {"AAPL", "MSFT"}
    assert all(row["date"] == date(2024, 1, 2) for row in result.rows)
    assert result.selected_files == 1
    assert result.snapshot_version == 0


def test_default_window_is_31_utc_calendar_days_and_explicit_date_reaches_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(
        tmp_path,
        monkeypatch,
        [
            _prediction_row("OLD", target_date=date(2023, 12, 31)),
            _prediction_row("START", target_date=date(2024, 1, 1)),
            _prediction_row("END", target_date=date(2024, 1, 31)),
            _prediction_row("FUTURE", target_date=date(2024, 2, 1)),
        ],
    )
    monkeypatch.setattr(deps, "_utc_today", lambda: date(2024, 1, 31))

    recent = deps.list_predictions()
    assert recent.window_start == date(2024, 1, 1)
    assert recent.window_end == date(2024, 1, 31)
    assert {row["symbol"] for row in recent.rows} == {"START", "END"}

    historical = deps.list_predictions(target_date=date(2023, 12, 31))
    assert [row["symbol"] for row in historical.rows] == ["OLD"]
    assert historical.window_start == historical.window_end == date(2023, 12, 31)


def test_empty_result_retains_window_and_snapshot_coverage_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch)
    client = TestClient(create_app())
    response = client.get("/predictions?target_date=2024-01-03")
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["x-predictions-window-start"] == "2024-01-03"
    assert response.headers["x-predictions-window-end"] == "2024-01-03"
    assert response.headers["x-predictions-snapshot-version"] == "0"
    assert response.headers["x-predictions-scan-status"] == "empty"
    assert response.headers["x-predictions-returned-rows"] == "0"


def test_list_predictions_classifies_identity_schema_validity_and_unproven_trust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = _prediction_row("LEGACY", market=None, mic=None, identity_schema_version=None, feature_schema_version=None)
    legacy.update({"market": None, "mic": None, "symbol": None})
    rows = [
        _prediction_row("CANONICAL"),
        legacy,
        _prediction_row("BAD_ALIAS", ticker="OTHER"),
        _prediction_row("HISTORICAL", feature_schema_version=4),
        _prediction_row("MISSING_SCHEMA", feature_schema_version=None),
        _prediction_row("INVALID_SCHEMA", feature_schema_version=-1),
        _prediction_row("FIXTURE", model_mode="unknown", model_version="fake"),
        _prediction_row("NAN", probability=float("nan")),
        _prediction_row("INFINITY", probability=float("inf")),
        _prediction_row("OUT_OF_RANGE", probability=1.2),
        _prediction_row("MISSING_PROBABILITY", probability=None),
        _prediction_row("META", model_mode="v2_meta_label"),
    ]
    _write_predictions_table(tmp_path, monkeypatch, rows)
    result = deps.list_predictions(target_date=date(2024, 1, 2), limit=100)
    by_symbol = {row.get("symbol") or row["ticker"]: row for row in result.rows}

    canonical = by_symbol["CANONICAL"]
    assert canonical["api_identity_status"] == "valid"
    assert canonical["api_schema_status"] == "current"
    assert canonical["api_validity_status"] == "valid"
    assert canonical["api_provenance_status"] == "unknown"
    assert canonical["api_provenance_reason"] == "no_supported_row_bound_forecast_proof_is_persisted"
    assert canonical["api_usability_status"] == "not_current_usable"
    assert canonical["model_version"] == "artifact-token-is-not-proof"

    assert by_symbol["LEGACY"]["api_identity_status"] == "unknown"
    assert by_symbol["LEGACY"]["api_schema_status"] == "unknown"
    assert by_symbol["BAD_ALIAS"]["api_identity_status"] == "invalid"
    assert by_symbol["HISTORICAL"]["api_schema_status"] == "historical"
    assert by_symbol["MISSING_SCHEMA"]["api_schema_status"] == "unknown"
    assert by_symbol["INVALID_SCHEMA"]["api_schema_status"] == "invalid"
    assert by_symbol["FIXTURE"]["api_provenance_status"] == "unknown"
    assert by_symbol["FIXTURE"]["api_usability_status"] == "not_current_usable"
    assert by_symbol["NAN"]["api_validity_status"] == "invalid"
    assert by_symbol["INFINITY"]["api_validity_status"] == "invalid"
    assert by_symbol["OUT_OF_RANGE"]["api_validity_status"] == "invalid"
    assert by_symbol["MISSING_PROBABILITY"]["api_validity_status"] == "unknown"
    assert by_symbol["META"]["api_prediction_semantics_status"] == "unknown"
    assert "meta_label" in by_symbol["META"]["api_prediction_semantics_reason"]


def test_predictions_report_usable_row_bound_forecast_for_a_current_v1_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete v1 proof on every current contract is the only ``current_usable`` shape."""
    _write_proof_table(tmp_path, monkeypatch, [_prediction_row("USABLE", **_forecast_proof())])

    row = deps.list_predictions(target_date=date(2024, 1, 2)).rows[0]

    assert row["api_provenance_status"] == "valid"
    assert row["api_provenance_reason"] == "row_bound_forecast_proof_is_complete_and_bound_to_the_current_contracts"
    assert row["api_prediction_semantics_status"] == "established"
    assert row["api_prediction_semantics_reason"] == (
        f"row_bound_directional_prediction_semantics_are_established_on_prediction_semantics_version:{PREDICTION_SEMANTICS_VERSION}"
    )
    assert row["api_usability_status"] == "current_usable"
    assert row["api_usability_reasons"] == []


def test_predictions_report_historical_provenance_on_a_superseded_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rules hold, but the row is bound to an older price-adjustment contract."""
    proof = _forecast_proof(price_adjustment_version="split-only-v0")
    _write_proof_table(tmp_path, monkeypatch, [_prediction_row("HISTORICAL", **proof)])

    result = deps.list_predictions(target_date=date(2024, 1, 2))
    # Rows are never filtered by status (ADR-0021 §2.4).
    assert [row["symbol"] for row in result.rows] == ["HISTORICAL"]
    row = result.rows[0]
    assert row["api_provenance_status"] == "historical"
    assert row["api_provenance_reason"] == (
        f"row_bound_forecast_proof_is_bound_to_a_superseded_contract:price_adjustment_version:split-only-v0;current:{PRICE_ADJUSTMENT_VERSION}"
    )
    assert row["api_prediction_semantics_status"] == "established"
    assert row["api_usability_status"] == "not_current_usable"
    assert row["api_usability_reasons"] == ["forecast_provenance_historical"]


def test_predictions_report_invalid_provenance_when_the_row_contradicts_its_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An observed age beyond the applied bound is a contradiction, not history."""
    proof = _forecast_proof(model_age_days=31, model_max_age_days=30)
    _write_proof_table(tmp_path, monkeypatch, [_prediction_row("INVALID", **proof)])

    result = deps.list_predictions(target_date=date(2024, 1, 2))
    assert [row["symbol"] for row in result.rows] == ["INVALID"]
    row = result.rows[0]
    assert row["api_provenance_status"] == "invalid"
    assert row["api_provenance_reason"] == "row_bound_forecast_proof_contradicts_the_row:model_age_days_exceeds_the_applied_model_max_age_days"
    assert row["api_prediction_semantics_status"] == "established"
    assert row["api_usability_status"] == "not_current_usable"
    assert row["api_usability_reasons"] == ["forecast_provenance_invalid"]


def test_predictions_report_unknown_provenance_without_a_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent proof column set and an all-null one are both ``unknown``."""
    _write_predictions_table(tmp_path / "absent", monkeypatch, [_prediction_row("ABSENT")])
    absent = deps.list_predictions(target_date=date(2024, 1, 2)).rows[0]
    assert "forecast_provenance_version" not in absent
    assert absent["api_provenance_status"] == "unknown"
    assert absent["api_provenance_reason"] == "no_supported_row_bound_forecast_proof_is_persisted"
    assert absent["api_prediction_semantics_status"] == "unknown"
    assert absent["api_usability_status"] == "not_current_usable"
    assert absent["api_usability_reasons"] == ["forecast_provenance_unknown", "prediction_semantics_unknown"]

    no_proof: dict[str, Any] = {column: None for column in FORECAST_PROVENANCE_COLUMNS}
    _write_proof_table(tmp_path / "null", monkeypatch, [_prediction_row("NULL", **no_proof)])
    null_row = deps.list_predictions(target_date=date(2024, 1, 2)).rows[0]
    assert null_row["forecast_provenance_version"] is None
    assert null_row["api_provenance_status"] == "unknown"
    assert null_row["api_provenance_reason"] == "no_supported_row_bound_forecast_proof_is_persisted"
    assert null_row["api_usability_reasons"] == ["forecast_provenance_unknown", "prediction_semantics_unknown"]


def test_v2_meta_label_keeps_valid_provenance_but_unknown_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0021 Decision 3: E1-E3 + candidate target, never a directional forecast."""
    proof = _forecast_proof(
        model_mode="v2_meta_label",
        prediction_target=PREDICTION_TARGET_CANDIDATE_EXECUTE,
        candidate_strategy_fingerprint="momentum__zero__v3",
    )
    _write_proof_table(tmp_path, monkeypatch, [_prediction_row("META", **proof)])

    row = deps.list_predictions(target_date=date(2024, 1, 2)).rows[0]

    assert row["api_provenance_status"] == "valid"
    assert row["api_provenance_reason"] == "row_bound_forecast_proof_is_complete_and_bound_to_the_current_contracts"
    assert row["api_prediction_semantics_status"] == "unknown"
    assert row["api_prediction_semantics_reason"] == ("stored_v2_meta_label_mode_maps_a_binary_meta_label_to_up_down;direction_is_not_established")
    assert row["api_usability_status"] == "not_current_usable"
    assert row["api_usability_reasons"] == ["prediction_semantics_unknown"]


def test_superseded_prediction_semantics_are_reported_as_superseded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A v1 row on an older semantics version is honest history, not current."""
    older = PREDICTION_SEMANTICS_VERSION - 1
    _write_proof_table(tmp_path, monkeypatch, [_prediction_row("OLDER", **_forecast_proof(prediction_semantics_version=older))])

    row = deps.list_predictions(target_date=date(2024, 1, 2)).rows[0]

    assert row["api_provenance_status"] == "historical"
    assert row["api_prediction_semantics_status"] == "superseded"
    assert row["api_prediction_semantics_reason"] == f"prediction_semantics_version:{older};current:{PREDICTION_SEMANTICS_VERSION}"
    assert row["api_usability_status"] == "not_current_usable"
    assert row["api_usability_reasons"] == ["forecast_provenance_historical", "prediction_semantics_superseded"]


@pytest.mark.parametrize(
    ("overrides", "expected_violation"),
    [
        ({"model_artifact_sha256": "0" * 63}, "model_artifact_sha256_is_not_64_lowercase_hex_characters"),
        ({"model_artifact_sha256": "A" * 64}, "model_artifact_sha256_is_not_64_lowercase_hex_characters"),
        ({"model_trained_at": date(2024, 1, 3)}, "model_trained_at_is_after_the_prediction_date"),
        ({"model_age_days": -1}, "model_age_days_is_negative"),
        ({"threshold_used": 1.5}, "threshold_used_is_outside_the_unit_interval"),
        ({"horizon_days": 0}, "horizon_days_is_below_one"),
        ({"candidate_strategy_fingerprint": "leak"}, "candidate_strategy_fingerprint_is_present_outside_the_meta_label_contract"),
        ({"prediction_target": PREDICTION_TARGET_CANDIDATE_EXECUTE}, "prediction_target_is_inconsistent_with_model_mode"),
        (
            {"training_feature_schema_version": FEATURE_SCHEMA_VERSION - 1, "inference_feature_schema_version": FEATURE_SCHEMA_VERSION - 1},
            "inference_feature_schema_version_differs_from_the_stored_feature_schema_version",
        ),
        ({"availability_schema_version": AVAILABILITY_SCHEMA_VERSION - 1}, "row_bound_forecast_proof_is_bound_to_a_superseded_contract"),
    ],
)
def test_forecast_proof_rules_are_reported_individually(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    expected_violation: str,
) -> None:
    """Each §2.3/§2.4 rule is pinned on its own so one rule cannot hide another."""
    _write_proof_table(tmp_path, monkeypatch, [_prediction_row("RULE", **_forecast_proof(**overrides))])

    row = deps.list_predictions(target_date=date(2024, 1, 2)).rows[0]

    expected_status = "historical" if expected_violation.startswith("row_bound_forecast_proof_is_bound") else "invalid"
    assert row["api_provenance_status"] == expected_status
    assert expected_violation in row["api_provenance_reason"]
    assert row["api_usability_status"] == "not_current_usable"
    assert f"forecast_provenance_{expected_status}" in row["api_usability_reasons"]


def test_unsupported_future_envelope_version_is_unknown_not_historical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A proof version this reader does not know is unknown, never "old" and never usable."""
    future = FORECAST_PROVENANCE_VERSION + 1
    _write_proof_table(tmp_path, monkeypatch, [_prediction_row("FUTURE", **_forecast_proof(forecast_provenance_version=future))])

    row = deps.list_predictions(target_date=date(2024, 1, 2)).rows[0]

    assert row["api_provenance_status"] == "unknown"
    assert row["api_provenance_reason"] == f"forecast_provenance_version_is_unsupported_by_this_reader:{future}"
    assert row["api_usability_status"] == "not_current_usable"
    assert row["api_usability_reasons"] == ["forecast_provenance_unknown"]


def test_forecast_proof_statuses_never_filter_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Usable, historical, invalid, and unknown rows are all returned together."""
    rows = [
        _prediction_row("USABLE", **_forecast_proof()),
        _prediction_row("HISTORICAL", **_forecast_proof(price_adjustment_version="split-only-v0")),
        _prediction_row("INVALID", **_forecast_proof(model_age_days=31, model_max_age_days=30)),
        _prediction_row("UNKNOWN"),
    ]
    _write_proof_table(tmp_path, monkeypatch, rows)

    result = deps.list_predictions(target_date=date(2024, 1, 2), limit=100)

    assert {row["symbol"] for row in result.rows} == {"USABLE", "HISTORICAL", "INVALID", "UNKNOWN"}
    usability = {row["symbol"]: row["api_usability_status"] for row in result.rows}
    assert usability["USABLE"] == "current_usable"
    assert {status for symbol, status in usability.items() if symbol != "USABLE"} == {"not_current_usable"}


@pytest.mark.parametrize("stored_mic", ["xnas", " XNAS "])
def test_noncanonical_stored_mic_is_invalid_without_relabeling_and_exact_filter_remains_coherent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored_mic: str
) -> None:
    rows = [
        _prediction_row("SAME", model_version="canonical"),
        _prediction_row("SAME", mic=stored_mic, model_version="noncanonical"),
        _prediction_row("SAME", mic=stored_mic, model_version="noncanonical-second"),
    ]
    _write_predictions_table(tmp_path, monkeypatch, rows)

    history = deps.list_predictions(target_date=date(2024, 1, 2))
    by_version = {row["model_version"]: row for row in history.rows}
    assert len(history.rows) == 3
    assert by_version["canonical"]["api_identity_status"] == "valid"
    for version in ("noncanonical", "noncanonical-second"):
        assert by_version[version]["mic"] == stored_mic
        assert by_version[version]["api_identity_status"] == "invalid"
        assert by_version[version]["api_identity_reason"] == "mic_is_not_canonical"

    venue_history = deps.list_predictions(target_date=date(2024, 1, 2), market="us_equity", mic="XNAS")
    assert [row["model_version"] for row in venue_history.rows] == ["canonical"]


def test_non_numeric_probability_is_explicitly_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch, [_prediction_row("TEXT", probability="0.6")])
    result = deps.list_predictions(target_date=date(2024, 1, 2))
    assert result.rows[0]["api_validity_status"] == "invalid"
    assert "probability_is_not_numeric" in result.rows[0]["api_validity_reasons"]


def test_non_finite_probability_remains_json_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(
        tmp_path,
        monkeypatch,
        [_prediction_row("NAN", probability=float("nan")), _prediction_row("INF", probability=float("inf"))],
    )
    response = TestClient(create_app()).get("/predictions?target_date=2024-01-02")
    assert response.status_code == 200
    rows = {row["symbol"]: row for row in response.json()}
    assert rows["NAN"]["probability"] == "NaN"
    assert rows["INF"]["probability"] == "Infinity"
    assert all(row["api_validity_status"] == "invalid" for row in rows.values())


def test_ticker_alias_keeps_same_symbol_distinct_across_all_venues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venue_rows = [
        _prediction_row("SAME", market="us_equity", mic="XNAS"),
        _prediction_row("SAME", market="cn_ashare", mic="XSHG"),
        _prediction_row("SAME", market="hk_sg_equity", mic="XHKG"),
        _prediction_row("SAME", market="hk_sg_equity", mic="XSES"),
        _prediction_row("SAME", market="jpx_equity", mic="XTKS"),
        _prediction_row("SAME", market="krx_equity", mic="XKRX"),
    ]
    _write_predictions_table(tmp_path, monkeypatch, venue_rows)

    broad_alias = deps.list_predictions(target_date=date(2024, 1, 2), ticker="SAME")
    assert {(row["market"], row["mic"], row["symbol"]) for row in broad_alias.rows} == {
        (row["market"], row["mic"], row["symbol"]) for row in venue_rows
    }
    venue = deps.list_predictions(target_date=date(2024, 1, 2), ticker="SAME", market="hk_sg", mic="XSES")
    assert [(row["market"], row["mic"]) for row in venue.rows] == [("hk_sg_equity", "XSES")]


@pytest.mark.parametrize(
    "query",
    [
        "market=not-a-market",
        "market=cn_ashare&mic=XNAS",
        "mic=NOT_A_MIC",
        "ticker=AAPL&symbol=MSFT",
    ],
)
def test_predictions_reject_inconsistent_identity_filters_before_read(query: str) -> None:
    response = TestClient(create_app()).get(f"/predictions?{query}")
    assert response.status_code == 422


def test_arrow_reader_pushes_date_partition_and_filters_identity_after_decode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(
        tmp_path,
        monkeypatch,
        [
            _prediction_row("AAPL", target_date=date(2024, 1, 2)),
            _prediction_row("MSFT", target_date=date(2024, 1, 2)),
            _prediction_row("AAPL", target_date=date(2024, 1, 1)),
        ],
    )
    original_open = deps._open_predictions_snapshot
    observed: dict[str, Any] = {}

    class DatasetProbe:
        def __init__(self, dataset: Any) -> None:
            self.dataset = dataset
            self.schema = dataset.schema

        def scanner(self, **kwargs: Any) -> Any:
            # Identity predicates are NOT pushed into Arrow: PyArrow has no
            # comparison kernels for Arrow string_view columns, which merge
            # rewrites introduce (see deps.list_predictions).
            observed["arrow_filter"] = kwargs.get("filter")
            return self.dataset.scanner(**kwargs)

    class SnapshotProbe:
        def __init__(self, snapshot: Any) -> None:
            self.snapshot = snapshot

        def __getattr__(self, name: str) -> Any:
            return getattr(self.snapshot, name)

        def to_pyarrow_dataset(self, *, partitions: Any) -> DatasetProbe:
            observed["partition_filter"] = partitions
            dataset = self.snapshot.to_pyarrow_dataset(partitions=partitions)
            observed["selected_fragments"] = [fragment.path for fragment in dataset.get_fragments()]
            return DatasetProbe(dataset)

    monkeypatch.setattr(deps, "_open_predictions_snapshot", lambda path: SnapshotProbe(original_open(path)))
    result = deps.list_predictions(target_date=date(2024, 1, 2), ticker="AAPL", market="us_equity", mic="XNAS")

    assert len(observed["partition_filter"][0][2]) == 1
    assert "date=2024-01-02" in observed["selected_fragments"][0]
    assert observed["arrow_filter"] is None
    # Identity/date scoping still holds after the decode-time filter.
    assert [(row["ticker"], row["date"]) for row in result.rows] == [("AAPL", date(2024, 1, 2))]


def test_filtered_predictions_read_survives_a_merge_rewrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F10: identity filters must work on a table whose strings became string_view."""
    _write_predictions_table(
        tmp_path,
        monkeypatch,
        [
            _prediction_row("AAPL", target_date=date(2024, 1, 2)),
            _prediction_row("MSFT", target_date=date(2024, 1, 2)),
        ],
    )
    update_delta_columns(
        pl.DataFrame(
            [{"market": "us_equity", "mic": "XNAS", "symbol": "AAPL", "date": date(2024, 1, 2), "probability": 0.61}],
            schema_overrides={"probability": pl.Float64},
        ),
        "04_platinum/predictions",
        update_columns=["probability"],
        key_columns=["market", "mic", "symbol", "date"],
        lake_dir=tmp_path,
    )
    assert string_view_columns("04_platinum/predictions", lake_dir=tmp_path).drifted

    result = deps.list_predictions(target_date=date(2024, 1, 2), ticker="AAPL")

    assert [row["ticker"] for row in result.rows] == ["AAPL"]


def test_predictions_read_uses_a_pinned_delta_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch, [_prediction_row("BEFORE")])
    original_open = deps._open_predictions_snapshot

    def open_then_append(path: Path) -> Any:
        snapshot = original_open(path)
        assert snapshot.version() == 0
        assert write_delta(
            pl.DataFrame([_prediction_row("AFTER")]),
            "04_platinum/predictions",
            lake_dir=tmp_path,
        )
        return snapshot

    monkeypatch.setattr(deps, "_open_predictions_snapshot", open_then_append)
    result = deps.list_predictions(target_date=date(2024, 1, 2))
    assert result.snapshot_version == 0
    assert [row["symbol"] for row in result.rows] == ["BEFORE"]


def test_prediction_ties_have_deterministic_order_across_physical_row_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _prediction_row("SAME", probability=0.8, direction="up", model_version="z-model"),
        _prediction_row("SAME", probability=0.2, direction="down", model_version="a-model"),
    ]
    _write_predictions_table(tmp_path / "first", monkeypatch, rows)
    first = deps.list_predictions(target_date=date(2024, 1, 2))

    _write_predictions_table(tmp_path / "reversed", monkeypatch, list(reversed(rows)))
    reversed_rows = deps.list_predictions(target_date=date(2024, 1, 2))
    assert first.rows == reversed_rows.rows


def test_scan_budget_exhaustion_fails_closed_and_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch, [_prediction_row("A"), _prediction_row("B")])
    monkeypatch.setattr(deps, "MAX_PREDICTIONS_SCAN_ROWS", 1)

    with pytest.raises(deps.PredictionScanBudgetExceededError) as exc_info:
        deps.list_predictions(target_date=date(2024, 1, 2))
    assert "processed row count" in exc_info.value.reason
    assert exc_info.value.processed_rows == 2

    response = TestClient(create_app()).get("/predictions?target_date=2024-01-02")
    assert response.status_code == 503
    assert "resource budget exceeded" in response.json()["detail"]
    assert response.headers["x-predictions-scan-status"] == "budget_exceeded"
    assert response.headers["x-predictions-snapshot-version"] == "0"


def test_selected_file_count_budget_is_checked_before_scanner_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch, [_prediction_row("A")])
    monkeypatch.setattr(deps, "MAX_PREDICTIONS_SCAN_FILES", 0)
    _forbid_prediction_scanner(monkeypatch)
    with pytest.raises(deps.PredictionScanBudgetExceededError, match="selected file count"):
        deps.list_predictions(target_date=date(2024, 1, 2))


def test_selected_file_byte_budget_is_checked_before_scanner_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch, [_prediction_row("A")])
    monkeypatch.setattr(deps, "MAX_PREDICTIONS_SCAN_FILE_BYTES", 1)
    _forbid_prediction_scanner(monkeypatch)
    with pytest.raises(deps.PredictionScanBudgetExceededError, match="selected file bytes"):
        deps.list_predictions(target_date=date(2024, 1, 2))


def test_decoded_byte_budget_is_checked_before_row_conversion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch, [_prediction_row("A")])
    monkeypatch.setattr(deps, "MAX_PREDICTIONS_DECODED_BYTES", 0)
    _forbid_prediction_batch_conversion(monkeypatch)
    with pytest.raises(deps.PredictionScanBudgetExceededError, match="decoded Arrow bytes"):
        deps.list_predictions(target_date=date(2024, 1, 2))


def test_predictions_limit_bounds_returned_rows_only_with_explicit_scan_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_predictions_table(tmp_path, monkeypatch)
    result = deps.list_predictions(target_date=date(2024, 1, 2), limit=1)
    assert len(result.rows) == 1
    assert result.processed_rows == 2
    assert result.scan_status == "complete"


def test_list_predictions_missing_table_raises_delta_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    with pytest.raises(DeltaReadError):
        deps.list_predictions()
