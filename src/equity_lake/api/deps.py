"""Thin dependency getters for the read API over the equity data lake.

Each getter wraps an existing reader (the findings writer, the signal history,
the Delta readers, or a filesystem listing) so routers stay thin and reuse the
canonical lake accessors — no duplicated I/O. FastAPI-free; routers call these
directly (patchable via ``monkeypatch.setattr(deps, ...)``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import isfinite
from numbers import Real
from pathlib import Path
from typing import Any

from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, MARKET_MICS, InstrumentKey
from equity_lake.core.paths import FINDINGS_DIR, MODELS_DIR, canonical_market
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION
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
    semantics_reason = "row_bound_directional_prediction_semantics_are_not_persisted"
    if row.get("model_mode") == "v2_meta_label":
        semantics_reason = "stored_v2_meta_label_mode_maps_a_binary_meta_label_to_up_down;direction_is_not_established"
    usability_reasons = ["row_bound_forecast_provenance_is_not_persisted", "prediction_semantics_are_unverified"]
    if identity_status != "valid":
        usability_reasons.append(f"identity_{identity_status}")
    if schema_status != "current":
        usability_reasons.append(f"feature_schema_{schema_status}")
    if validity_status != "valid":
        usability_reasons.append(f"prediction_values_{validity_status}")
    safe_row = {key: _json_safe_value(value) for key, value in row.items()}
    return {
        **safe_row,
        "api_identity_status": identity_status,
        "api_identity_reason": identity_reason,
        "api_provenance_status": "unknown",
        "api_provenance_reason": "no_supported_row_bound_forecast_proof_is_persisted",
        "api_schema_status": schema_status,
        "api_schema_reason": schema_reason,
        "api_validity_status": validity_status,
        "api_validity_reasons": validity_reasons,
        "api_prediction_semantics_status": "unknown",
        "api_prediction_semantics_reason": semantics_reason,
        "api_usability_status": "not_current_usable",
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
    ``target_date`` selects exactly that date, including older history. Delta
    partition selection and Arrow row filters run before Python row conversion;
    selected file bytes and decoded batches have explicit fail-closed budgets.
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
        predicates: list[Any] = []
        for column, value in (("market", normalized_market), ("mic", normalized_mic), ("symbol", normalized_symbol)):
            if value is not None:
                if column not in available_columns:
                    return _empty_prediction_result(
                        window_start=window_start,
                        window_end=window_end,
                        snapshot_version=snapshot_version,
                        selected_files=selected_files,
                        selected_file_bytes=selected_file_bytes,
                    )
                predicates.append(arrow_dataset.field(column) == value)
        if normalized_ticker is not None:
            ticker_predicates = [arrow_dataset.field(column) == normalized_ticker for column in ("ticker", "symbol") if column in available_columns]
            if not ticker_predicates:
                return _empty_prediction_result(
                    window_start=window_start,
                    window_end=window_end,
                    snapshot_version=snapshot_version,
                    selected_files=selected_files,
                    selected_file_bytes=selected_file_bytes,
                )
            alias_predicate = ticker_predicates[0]
            for predicate in ticker_predicates[1:]:
                alias_predicate = alias_predicate | predicate
            predicates.append(alias_predicate)

        filter_expression = None
        for predicate in predicates:
            filter_expression = predicate if filter_expression is None else filter_expression & predicate
        scanner = dataset.scanner(
            columns=dataset.schema.names,
            filter=filter_expression,
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
