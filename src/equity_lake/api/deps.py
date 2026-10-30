"""Thin dependency getters for the read API over the equity data lake.

Each getter wraps an existing reader (the findings writer, the signal history,
the Delta readers, or a filesystem listing) so routers stay thin and reuse the
canonical lake accessors — no duplicated I/O. FastAPI-free; routers call these
directly (patchable via ``monkeypatch.setattr(deps, ...)``).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import isfinite
from numbers import Real
from pathlib import Path
from typing import Any

from equity_lake.core.availability import AVAILABILITY_SCHEMA_VERSION, PRICE_ADJUSTMENT_VERSION
from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, MARKET_MICS, InstrumentKey
from equity_lake.core.paths import FINDINGS_DIR, MODELS_DIR, canonical_market
from equity_lake.core.schemas import (
    FEATURE_SCHEMA_VERSION,
    FORECAST_PROVENANCE_COLUMNS,
    FORECAST_PROVENANCE_VERSION,
    PREDICTION_SEMANTICS_VERSION,
    PREDICTION_TARGET_CANDIDATE_EXECUTE,
    PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
)
from equity_lake.findings.models import FindingCard
from equity_lake.findings.writer import load_finding_cards
from equity_lake.signals.history import load_signals
from equity_lake.storage.delta import DeltaReadError, delta_table_path


def list_findings() -> list[FindingCard]:
    """Load every serialized FindingCard under ``data/findings/``."""
    return load_finding_cards()


def list_signals(target_date: date) -> list[dict[str, Any]]:
    """Load buy/sell/hold signals for one date (empty list if none on disk)."""
    return [asdict(signal) for signal in load_signals(target_date)]


def list_models() -> list[dict[str, Any]]:
    """List trained-model summaries (one entry per ``*.training_summary.json``)."""
    summaries: list[dict[str, Any]] = []
    for path in sorted(MODELS_DIR.glob("*.training_summary.json")):
        try:
            summaries.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return summaries


DEFAULT_PREDICTIONS_WINDOW_DAYS = 31
MAX_PREDICTIONS_SCAN_FILES = 4096
MAX_PREDICTIONS_SCAN_FILE_BYTES = 256 * 1024 * 1024
MAX_PREDICTIONS_SCAN_ROWS = 50_000
MAX_PREDICTIONS_DECODED_BYTES = 64 * 1024 * 1024
PREDICTIONS_SCAN_BATCH_ROWS = 256


class PredictionQueryError(ValueError):
    """A market/MIC/symbol filter is invalid or internally inconsistent."""


class PredictionScanBudgetExceededError(RuntimeError):
    """A bounded predictions read exceeded a declared resource budget."""

    def __init__(
        self,
        reason: str,
        *,
        window_start: date,
        window_end: date,
        snapshot_version: int,
        selected_files: int,
        selected_file_bytes: int,
        processed_rows: int,
        decoded_bytes: int,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.window_start = window_start
        self.window_end = window_end
        self.snapshot_version = snapshot_version
        self.selected_files = selected_files
        self.selected_file_bytes = selected_file_bytes
        self.processed_rows = processed_rows
        self.decoded_bytes = decoded_bytes


@dataclass(frozen=True, slots=True)
class PredictionReadResult:
    """JSON-list rows plus coverage metadata for response headers."""

    rows: list[dict[str, Any]]
    window_start: date
    window_end: date
    snapshot_version: int
    processed_rows: int
    decoded_bytes: int
    selected_files: int
    selected_file_bytes: int
    scan_status: str


def _utc_today() -> date:
    """Use the UTC calendar date as the stable anchor for the default window."""
    return datetime.now(UTC).date()


def _prediction_window(target_date: date | None) -> tuple[date, date]:
    if target_date is not None:
        return target_date, target_date
    end = _utc_today()
    return end - timedelta(days=DEFAULT_PREDICTIONS_WINDOW_DAYS - 1), end


def _validate_prediction_filters(
    *, market: str | None, mic: str | None, symbol: str | None, ticker: str | None
) -> tuple[str | None, str | None, str | None, str | None]:
    """Validate partial identity filters without inferring a missing identity component."""
    normalized_market: str | None = None
    if market is not None:
        try:
            normalized_market = canonical_market(market.strip())
        except ValueError as exc:
            raise PredictionQueryError(str(exc)) from exc

    normalized_mic = mic.strip().upper() if mic is not None else None
    known_mics = {venue for venues in MARKET_MICS.values() for venue in venues}
    if normalized_mic is not None and normalized_mic not in known_mics:
        raise PredictionQueryError(f"Unknown MIC {normalized_mic!r}; valid MICs: {', '.join(sorted(known_mics))}")
    if normalized_market is not None and normalized_mic is not None and normalized_mic not in MARKET_MICS[normalized_market]:
        raise PredictionQueryError(f"MIC {normalized_mic!r} is not supported for market {normalized_market!r}")

    normalized_symbol = symbol.strip() if symbol is not None else None
    normalized_ticker = ticker.strip() if ticker is not None else None
    for name, value in (("symbol", normalized_symbol), ("ticker", normalized_ticker)):
        if value is not None and not value:
            raise PredictionQueryError(f"{name} must not be empty")
    if normalized_symbol is not None and normalized_ticker is not None and normalized_symbol != normalized_ticker:
        raise PredictionQueryError("ticker and symbol filters must agree when both are supplied")
    return normalized_market, normalized_mic, normalized_symbol, normalized_ticker


def _open_predictions_snapshot(table_path: Path) -> Any:
    """Open one Delta snapshot; subsequent reads stay pinned to this instance."""
    from deltalake import DeltaTable

    return DeltaTable(str(table_path))


def _raise_budget_exceeded(
    reason: str,
    *,
    window_start: date,
    window_end: date,
    snapshot_version: int,
    selected_files: int,
    selected_file_bytes: int,
    processed_rows: int = 0,
    decoded_bytes: int = 0,
) -> None:
    raise PredictionScanBudgetExceededError(
        reason,
        window_start=window_start,
        window_end=window_end,
        snapshot_version=snapshot_version,
        selected_files=selected_files,
        selected_file_bytes=selected_file_bytes,
        processed_rows=processed_rows,
        decoded_bytes=decoded_bytes,
    )


def _sort_date_ordinal(value: Any) -> int:
    if isinstance(value, datetime):
        return value.date().toordinal()
    if isinstance(value, date):
        return value.toordinal()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).toordinal()
        except ValueError:
            pass
    return -1


def _stable_row_key(row: dict[str, Any]) -> tuple[int, str, str, str, str]:
    tie_breaker = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str, allow_nan=True)
    return (
        -_sort_date_ordinal(row.get("date")),
        str(row.get("market") or ""),
        str(row.get("mic") or ""),
        str(row.get("symbol") or row.get("ticker") or ""),
        tie_breaker,
    )


def _identity_status(row: dict[str, Any]) -> tuple[str, str]:
    market = row.get("market")
    mic = row.get("mic")
    symbol = row.get("symbol")
    if market is None or mic is None or symbol is None:
        return "unknown", "missing_explicit_market_mic_or_symbol"
    if not all(isinstance(value, str) for value in (market, mic, symbol)):
        return "invalid", "market_mic_and_symbol_must_be_strings"
    if not market or market != market.strip() or not mic.strip() or not symbol.strip() or symbol != symbol.strip():
        return "invalid", "identity_fields_are_empty_or_not_canonical"
    if row.get("ticker") is not None and (not isinstance(row["ticker"], str) or row["ticker"] != symbol):
        return "invalid", "ticker_alias_differs_from_symbol"
    try:
        identity = InstrumentKey(market=market, mic=mic, symbol=symbol)
    except (TypeError, ValueError):
        return "invalid", "market_mic_symbol_combination_is_unsupported"
    if identity.market != market:
        return "invalid", "market_is_not_canonical"
    if identity.mic != mic:
        return "invalid", "mic_is_not_canonical"
    if identity.symbol != symbol:
        return "invalid", "symbol_is_not_canonical"
    identity_version = row.get("identity_schema_version")
    if identity_version is None:
        return "unknown", "missing_identity_schema_version"
    if isinstance(identity_version, bool) or not isinstance(identity_version, int):
        return "invalid", "identity_schema_version_is_not_an_integer"
    if identity_version != IDENTITY_SCHEMA_VERSION:
        return "unknown", f"unsupported_identity_schema_version:{identity_version}"
    return "valid", "explicit_composite_identity_is_structurally_valid"


def _feature_schema_status(row: dict[str, Any]) -> tuple[str, str]:
    version = row.get("feature_schema_version")
    if version is None:
        return "unknown", "missing_feature_schema_version"
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        return "invalid", "feature_schema_version_is_not_a_positive_integer"
    if version == FEATURE_SCHEMA_VERSION:
        return "current", f"feature_schema_version:{version}"
    if version < FEATURE_SCHEMA_VERSION:
        return "historical", f"feature_schema_version:{version};current:{FEATURE_SCHEMA_VERSION}"
    return "unknown", f"unsupported_future_feature_schema_version:{version}"


def _prediction_value_status(row: dict[str, Any]) -> tuple[str, list[str]]:
    invalid: list[str] = []
    unknown: list[str] = []
    target_date = row.get("date")
    if target_date is None:
        unknown.append("missing_date")
    elif not isinstance(target_date, date) or isinstance(target_date, datetime):
        invalid.append("date_is_not_a_calendar_date")

    direction = row.get("direction")
    if direction is None:
        unknown.append("missing_direction")
    elif not isinstance(direction, str) or direction not in {"up", "down"}:
        invalid.append("direction_is_not_up_or_down")

    probability = row.get("probability")
    if probability is None:
        unknown.append("missing_probability")
    elif isinstance(probability, bool) or not isinstance(probability, (Real, Decimal)):
        invalid.append("probability_is_not_numeric")
    else:
        try:
            finite = probability.is_finite() if isinstance(probability, Decimal) else isfinite(float(probability))
            in_range = 0.0 <= float(probability) <= 1.0
        except (OverflowError, TypeError, ValueError):
            finite = False
            in_range = False
        if not finite:
            invalid.append("probability_is_not_finite")
        elif not in_range:
            invalid.append("probability_is_out_of_range")

    if invalid:
        return "invalid", invalid + unknown
    if unknown:
        return "unknown", unknown
    return "valid", []


# --- row-bound forecast provenance (ADR-0021 §2.4) ---------------------------

# The reader re-derives the proof from the row alone, so it mirrors the
# write-boundary rule set (``ml/__init__.py::_forecast_provenance_violated``)
# instead of trusting the writer.
_PREDICTION_TARGET_BY_MODEL_MODE: dict[str, str] = {
    "v1_direction": PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
    "v2_meta_label": PREDICTION_TARGET_CANDIDATE_EXECUTE,
}
_E4_COLUMNS = ("horizon_days", "threshold_used", "threshold_source", "prediction_target", "prediction_semantics_version")
_FORECAST_PROOF_REQUIRED_COLUMNS = tuple(column for column in FORECAST_PROVENANCE_COLUMNS if column != "candidate_strategy_fingerprint")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_FORECAST_PROOF_UNKNOWN_REASON = "no_supported_row_bound_forecast_proof_is_persisted"
_V2_META_LABEL_SEMANTICS_REASON = "stored_v2_meta_label_mode_maps_a_binary_meta_label_to_up_down;direction_is_not_established"


def _row_int(row: dict[str, Any], column: str) -> int | None:
    """Return a stored integer column value, or None when absent or mistyped."""
    value = row.get(column)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _row_float(row: dict[str, Any], column: str) -> float | None:
    """Return a stored finite numeric column value, or None when absent or mistyped."""
    value = row.get(column)
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _row_date(row: dict[str, Any], column: str) -> date | None:
    """Return a stored calendar date, or None when absent or mistyped."""
    value = row.get(column)
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _row_text(row: dict[str, Any], column: str) -> str | None:
    """Return a stored string column value, or None when absent or mistyped."""
    value = row.get(column)
    return value if isinstance(value, str) else None


def _forecast_provenance_rule_violations(row: dict[str, Any]) -> list[str]:
    """Return the ADR-0021 §2.3 rule violations for one stored row.

    The contract-version currency checks are deliberately excluded: they
    separate ``valid`` from ``historical`` instead of deciding ``invalid``.
    """
    violations: list[str] = []
    model_mode = row.get("model_mode")
    fingerprint = _row_text(row, "candidate_strategy_fingerprint")
    missing = [column for column in _FORECAST_PROOF_REQUIRED_COLUMNS if row.get(column) is None]
    if model_mode == "v2_meta_label":
        if fingerprint is None:
            missing.append("candidate_strategy_fingerprint")
    elif fingerprint is not None:
        violations.append("candidate_strategy_fingerprint_is_present_outside_the_meta_label_contract")
    if missing:
        return [f"missing_forecast_proof_columns:{','.join(missing)}", *violations]

    digest = _row_text(row, "model_artifact_sha256")
    if digest is None or _SHA256_HEX.fullmatch(digest) is None:
        violations.append("model_artifact_sha256_is_not_64_lowercase_hex_characters")
    if _row_int(row, "model_provenance_version") is None:
        violations.append("model_provenance_version_is_not_an_integer")
    if _row_text(row, "price_adjustment_version") is None:
        violations.append("price_adjustment_version_is_not_a_string")
    if _row_int(row, "availability_schema_version") is None:
        violations.append("availability_schema_version_is_not_an_integer")
    if _row_text(row, "missing_value_policy") is None:
        violations.append("missing_value_policy_is_not_a_string")
    if _row_text(row, "threshold_source") is None:
        violations.append("threshold_source_is_not_a_string")
    # Type errors are reported as type errors: a mistyped date is not "after the
    # prediction date", and a mistyped contract version is not contract drift.
    trained_at = _row_date(row, "model_trained_at")
    prediction_date = _row_date(row, "date")
    if trained_at is None:
        violations.append("model_trained_at_is_not_a_date")
    if prediction_date is None:
        violations.append("date_is_not_a_date")
    elif trained_at is not None and trained_at > prediction_date:
        violations.append("model_trained_at_is_after_the_prediction_date")
    age = _row_int(row, "model_age_days")
    max_age = _row_int(row, "model_max_age_days")
    if age is None:
        violations.append("model_age_days_is_not_an_integer")
    elif age < 0:
        violations.append("model_age_days_is_negative")
    if max_age is None:
        violations.append("model_max_age_days_is_not_an_integer")
    elif age is not None and age > max_age:
        violations.append("model_age_days_exceeds_the_applied_model_max_age_days")
    training_version = _row_int(row, "training_feature_schema_version")
    inference_version = _row_int(row, "inference_feature_schema_version")
    stored_version = _row_int(row, "feature_schema_version")
    if training_version is None or inference_version is None:
        violations.append("feature_schema_versions_are_not_integers")
    elif training_version != inference_version:
        violations.append("training_and_inference_feature_schema_versions_differ")
    if inference_version is not None and stored_version is not None and inference_version != stored_version:
        violations.append("inference_feature_schema_version_differs_from_the_stored_feature_schema_version")
    threshold = _row_float(row, "threshold_used")
    if threshold is None or not 0.0 <= threshold <= 1.0:
        violations.append("threshold_used_is_outside_the_unit_interval")
    horizon = _row_int(row, "horizon_days")
    if horizon is None:
        violations.append("horizon_days_is_not_an_integer")
    elif horizon < 1:
        violations.append("horizon_days_is_below_one")
    expected_target = _PREDICTION_TARGET_BY_MODEL_MODE.get(model_mode) if isinstance(model_mode, str) else None
    if expected_target is None or _row_text(row, "prediction_target") != expected_target:
        violations.append("prediction_target_is_inconsistent_with_model_mode")
    return violations


def _forecast_contract_version_drift(row: dict[str, Any]) -> list[str]:
    """Return the envelope/E3 contract versions that are not the current ones."""
    checks: tuple[tuple[str, object, object], ...] = (
        ("forecast_provenance_version", _row_int(row, "forecast_provenance_version"), FORECAST_PROVENANCE_VERSION),
        ("price_adjustment_version", _row_text(row, "price_adjustment_version"), PRICE_ADJUSTMENT_VERSION),
        ("availability_schema_version", _row_int(row, "availability_schema_version"), AVAILABILITY_SCHEMA_VERSION),
        ("prediction_semantics_version", _row_int(row, "prediction_semantics_version"), PREDICTION_SEMANTICS_VERSION),
    )
    return [f"{column}:{observed};current:{expected}" for column, observed, expected in checks if observed != expected]


def _forecast_provenance_status(row: dict[str, Any]) -> tuple[str, str]:
    """Derive ``api_provenance_status`` from the stored row (ADR-0021 §2.4).

    ``unknown`` is decided by the envelope version being absent or null — the
    documented "no proof" written by inference paths that cannot supply the
    evidence. A claiming row is ``invalid`` when it contradicts itself, and
    ``historical`` when every rule holds but a contract version is not current:
    a version this reader does not know is not current either, so it is never
    ``valid`` and is never reported as a contradiction.
    """
    if row.get("forecast_provenance_version") is None:
        return "unknown", _FORECAST_PROOF_UNKNOWN_REASON
    violations = _forecast_provenance_rule_violations(row)
    envelope = _row_int(row, "forecast_provenance_version")
    if envelope is None:
        violations.append("forecast_provenance_version_is_not_an_integer")
    if violations:
        return "invalid", "row_bound_forecast_proof_contradicts_the_row:" + ",".join(violations)
    if envelope is not None and envelope > FORECAST_PROVENANCE_VERSION:
        # A version this reader does not know is not superseded history: it is an
        # unknown contract this reader cannot judge (ADR-0021 does not define it;
        # treating it as "historical" would misreport a newer contract as old).
        return "unknown", f"forecast_provenance_version_is_unsupported_by_this_reader:{envelope}"
    drift = _forecast_contract_version_drift(row)
    if drift:
        return "historical", "row_bound_forecast_proof_is_bound_to_a_superseded_contract:" + ",".join(drift)
    return "valid", "row_bound_forecast_proof_is_complete_and_bound_to_the_current_contracts"


def _prediction_semantics_rule_violations(row: dict[str, Any]) -> list[str]:
    """Return the E4 completeness/consistency violations for a ``v1_direction`` row."""
    missing = [column for column in _E4_COLUMNS if row.get(column) is None]
    if missing:
        return [f"missing_decision_semantics_columns:{','.join(missing)}"]
    violations: list[str] = []
    horizon = _row_int(row, "horizon_days")
    if horizon is None or horizon < 1:
        violations.append("horizon_days_is_below_one")
    threshold = _row_float(row, "threshold_used")
    if threshold is None or not 0.0 <= threshold <= 1.0:
        violations.append("threshold_used_is_outside_the_unit_interval")
    if _row_text(row, "threshold_source") is None:
        violations.append("threshold_source_is_not_a_string")
    if _row_text(row, "prediction_target") != PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE:
        violations.append("prediction_target_is_not_next_session_return_positive")
    if _row_int(row, "prediction_semantics_version") is None:
        violations.append("prediction_semantics_version_is_not_an_integer")
    return violations


def _prediction_semantics_status(row: dict[str, Any]) -> tuple[str, str]:
    """Derive ``api_prediction_semantics_status`` (ADR-0021 §2.4).

    Only ``v1_direction`` can establish a directional forecast. Every
    ``v2_meta_label`` row stays ``unknown`` until a separate record defines
    meta-label usability, and a later semantics version this reader does not
    know is never ``established``.
    """
    model_mode = row.get("model_mode")
    if model_mode == "v2_meta_label":
        return "unknown", _V2_META_LABEL_SEMANTICS_REASON
    if model_mode != "v1_direction":
        return "unknown", f"stored_model_mode_does_not_establish_directional_semantics:{model_mode}"
    version = _row_int(row, "prediction_semantics_version")
    violations = _prediction_semantics_rule_violations(row)
    if version is not None and version > PREDICTION_SEMANTICS_VERSION:
        violations.append(f"unsupported_future_prediction_semantics_version:{version}")
    if violations:
        return "unknown", "row_bound_directional_prediction_semantics_are_not_established:" + ",".join(violations)
    if version is None:  # unreachable: the rule above already reports a non-integer version
        return "unknown", "row_bound_directional_prediction_semantics_are_not_persisted"
    if version < PREDICTION_SEMANTICS_VERSION:
        return "superseded", f"prediction_semantics_version:{version};current:{PREDICTION_SEMANTICS_VERSION}"
    return "established", f"row_bound_directional_prediction_semantics_are_established_on_prediction_semantics_version:{version}"


def _json_safe_value(value: Any) -> Any:
    """Convert non-finite numbers to explicit strings for standards-compliant JSON."""
    if isinstance(value, float) and not isfinite(value):
        if value != value:
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, Decimal) and not value.is_finite():
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


def _annotate_prediction(row: dict[str, Any]) -> dict[str, Any]:
    identity_status, identity_reason = _identity_status(row)
    schema_status, schema_reason = _feature_schema_status(row)
    validity_status, validity_reasons = _prediction_value_status(row)
    provenance_status, provenance_reason = _forecast_provenance_status(row)
    semantics_status, semantics_reason = _prediction_semantics_status(row)
    # Rows are never filtered by status (ADR-0021 §2.4): a row that is not
    # current stays visible with its stored fields and its reasons.
    usability_reasons: list[str] = []
    if identity_status != "valid":
        usability_reasons.append(f"identity_{identity_status}")
    if schema_status != "current":
        usability_reasons.append(f"feature_schema_{schema_status}")
    if validity_status != "valid":
        usability_reasons.append(f"prediction_values_{validity_status}")
    if provenance_status != "valid":
        usability_reasons.append(f"forecast_provenance_{provenance_status}")
    if semantics_status != "established":
        usability_reasons.append(f"prediction_semantics_{semantics_status}")
    safe_row = {key: _json_safe_value(value) for key, value in row.items()}
    return {
        **safe_row,
        "api_identity_status": identity_status,
        "api_identity_reason": identity_reason,
        "api_provenance_status": provenance_status,
        "api_provenance_reason": provenance_reason,
        "api_schema_status": schema_status,
        "api_schema_reason": schema_reason,
        "api_validity_status": validity_status,
        "api_validity_reasons": validity_reasons,
        "api_prediction_semantics_status": semantics_status,
        "api_prediction_semantics_reason": semantics_reason,
        "api_usability_status": "not_current_usable" if usability_reasons else "current_usable",
        "api_usability_reasons": usability_reasons,
    }


def _empty_prediction_result(
    *,
    window_start: date,
    window_end: date,
    snapshot_version: int,
    selected_files: int = 0,
    selected_file_bytes: int = 0,
) -> PredictionReadResult:
    return PredictionReadResult(
        rows=[],
        window_start=window_start,
        window_end=window_end,
        snapshot_version=snapshot_version,
        processed_rows=0,
        decoded_bytes=0,
        selected_files=selected_files,
        selected_file_bytes=selected_file_bytes,
        scan_status="empty",
    )


def list_predictions(
    *,
    target_date: date | None = None,
    ticker: str | None = None,
    market: str | None = None,
    mic: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> PredictionReadResult:
    """Read bounded historical predictions from one pinned Delta snapshot.

    The default range is 31 inclusive UTC calendar dates. An explicit
    ``target_date`` selects exactly that date, including older history. Delta date
    partition selection runs before Python row conversion; identity/ticker equality
    filters are applied to the decoded rows because PyArrow has no comparison
    kernels for Arrow ``string_view`` columns (which merge rewrites introduce), and
    the selected-file/row/byte budgets bound what is decoded.
    """
    normalized_market, normalized_mic, normalized_symbol, normalized_ticker = _validate_prediction_filters(
        market=market, mic=mic, symbol=symbol, ticker=ticker
    )
    window_start, window_end = _prediction_window(target_date)
    window_dates = [window_start + timedelta(days=offset) for offset in range((window_end - window_start).days + 1)]
    partition_filters: list[tuple[str, str, Any]] = [("date", "in", [day.isoformat() for day in window_dates])]
    table_name = "04_platinum/predictions"

    try:
        snapshot = _open_predictions_snapshot(delta_table_path(table_name))
        snapshot_version = int(snapshot.version())
        if "date" not in snapshot.metadata().partition_columns:
            raise ValueError("predictions table is not partitioned by date; bounded reads are unavailable")

        selected_uris = snapshot.file_uris(partition_filters=partition_filters)
        selected_files = len(selected_uris)
        if selected_files > MAX_PREDICTIONS_SCAN_FILES:
            _raise_budget_exceeded(
                f"selected file count {selected_files} exceeds {MAX_PREDICTIONS_SCAN_FILES}",
                window_start=window_start,
                window_end=window_end,
                snapshot_version=snapshot_version,
                selected_files=selected_files,
                selected_file_bytes=0,
            )

        selected_file_bytes = 0
        for uri in selected_uris:
            selected_file_bytes += Path(uri).stat().st_size
            if selected_file_bytes > MAX_PREDICTIONS_SCAN_FILE_BYTES:
                _raise_budget_exceeded(
                    f"selected file bytes exceed {MAX_PREDICTIONS_SCAN_FILE_BYTES}",
                    window_start=window_start,
                    window_end=window_end,
                    snapshot_version=snapshot_version,
                    selected_files=selected_files,
                    selected_file_bytes=selected_file_bytes,
                )
        if not selected_uris:
            return _empty_prediction_result(window_start=window_start, window_end=window_end, snapshot_version=snapshot_version)

        import pyarrow.dataset as arrow_dataset

        dataset = snapshot.to_pyarrow_dataset(partitions=partition_filters)
        available_columns = set(dataset.schema.names)

        def empty_result() -> PredictionReadResult:
            return _empty_prediction_result(
                window_start=window_start,
                window_end=window_end,
                snapshot_version=snapshot_version,
                selected_files=selected_files,
                selected_file_bytes=selected_file_bytes,
            )

        identity_filters = {"market": normalized_market, "mic": normalized_mic, "symbol": normalized_symbol}
        for column, value in identity_filters.items():
            if value is not None and column not in available_columns:
                return empty_result()
        if normalized_ticker is not None and not ({"ticker", "symbol"} & available_columns):
            return empty_result()

        def row_matches(row: dict[str, Any]) -> bool:
            """Identity/ticker equality after decoding (Arrow cannot compare string_view)."""
            for column, value in identity_filters.items():
                if value is not None and row.get(column) != value:
                    return False
            if normalized_ticker is not None:
                return row.get("ticker") == normalized_ticker or row.get("symbol") == normalized_ticker
            return True

        scanner = dataset.scanner(
            columns=dataset.schema.names,
            batch_size=PREDICTIONS_SCAN_BATCH_ROWS,
            batch_readahead=0,
            fragment_readahead=1,
            fragment_scan_options=arrow_dataset.ParquetFragmentScanOptions(pre_buffer=False),
            use_threads=False,
            cache_metadata=False,
        )
        rows: list[dict[str, Any]] = []
        processed_rows = 0
        decoded_bytes = 0
        for batch in scanner.to_batches():
            processed_rows += batch.num_rows
            decoded_bytes += batch.nbytes
            if processed_rows > MAX_PREDICTIONS_SCAN_ROWS:
                _raise_budget_exceeded(
                    f"processed row count {processed_rows} exceeds {MAX_PREDICTIONS_SCAN_ROWS}",
                    window_start=window_start,
                    window_end=window_end,
                    snapshot_version=snapshot_version,
                    selected_files=selected_files,
                    selected_file_bytes=selected_file_bytes,
                    processed_rows=processed_rows,
                    decoded_bytes=decoded_bytes,
                )
            if decoded_bytes > MAX_PREDICTIONS_DECODED_BYTES:
                _raise_budget_exceeded(
                    f"decoded Arrow bytes {decoded_bytes} exceeds {MAX_PREDICTIONS_DECODED_BYTES}",
                    window_start=window_start,
                    window_end=window_end,
                    snapshot_version=snapshot_version,
                    selected_files=selected_files,
                    selected_file_bytes=selected_file_bytes,
                    processed_rows=processed_rows,
                    decoded_bytes=decoded_bytes,
                )
            rows.extend(batch.to_pylist())

        if normalized_market is not None or normalized_mic is not None or normalized_symbol is not None or normalized_ticker is not None:
            rows = [row for row in rows if row_matches(row)]
        rows.sort(key=_stable_row_key)
        selected_rows = [_annotate_prediction(row) for row in rows[:limit]]
        return PredictionReadResult(
            rows=selected_rows,
            window_start=window_start,
            window_end=window_end,
            snapshot_version=snapshot_version,
            processed_rows=processed_rows,
            decoded_bytes=decoded_bytes,
            selected_files=selected_files,
            selected_file_bytes=selected_file_bytes,
            scan_status="complete" if selected_rows else "empty",
        )
    except PredictionScanBudgetExceededError:
        raise
    except DeltaReadError:
        raise
    except Exception as exc:
        raise DeltaReadError(table_name, exc) from exc


def list_backtests() -> list[dict[str, Any]]:
    """List arena/backtest run reports (``data/findings/<slug>/metrics.json``)."""
    runs: list[dict[str, Any]] = []
    for metrics_path in sorted(FINDINGS_DIR.glob("*/metrics.json")):
        try:
            data: dict[str, Any] = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["slug"] = metrics_path.parent.name
        runs.append(data)
    return runs


__all__ = [
    "list_backtests",
    "list_findings",
    "list_models",
    "list_predictions",
    "list_signals",
]
