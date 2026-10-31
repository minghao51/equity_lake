"""Read-only observations for the private tracking preview and doctor command.

This module does not construct a scanner, infer signals, update history, migrate
legacy tables, or create output paths. Ages are descriptive calendar-day
observations; no freshness policy is configured here.
"""

from __future__ import annotations

import json
import math
import stat
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import polars as pl
from deltalake import DeltaTable

from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, InstrumentKey, normalize_identity_frame
from equity_lake.core.paths import (
    BRONZE_DIR,
    BRONZE_MARKET_DATA_DIR,
    DATA_DIR,
    GOLD_DIR,
    GOLD_FEATURES_DIR,
    LAKE_DIR,
    PLATINUM_PREDICTIONS_DIR,
    PRICE_MARKETS,
    SILVER_CORPORATE_ACTIONS_DIR,
    SILVER_DIR,
)
from equity_lake.signals.models import Signal, SignalRecord, SignalScanReport
from equity_lake.signals.tracking import load_private_watchlist
from equity_lake.storage.delta import DeltaError, read_delta, string_view_columns

TableState = Literal["available", "missing", "corrupt"]
HistoryState = Literal["available", "missing", "corrupt", "unknown_identity"]
StorageDirectoryState = Literal["missing", "empty", "present", "corrupt"]
StorageSchemaState = Literal["missing", "clean", "drifted", "unreadable"]
SyntheticProvenanceState = Literal["synthetic", "real", "empty", "unknown"]
SyntheticMarkState = Literal["marked", "unmarked", "empty", "missing", "unreadable"]

#: The only sanctioned synthetic label for corporate-action coverage proofs
#: (ADR-0020); config-resolved price identity uses ``current_ticker_config``, which
#: is not unique to seeded rows. Do not add a third label without an ADR (R7/D3).
SYNTHETIC_MARKER = "synthetic_demo_seed"
#: Canonical Silver dataset written by ``corporate_action_coverage_table`` in
#: ``ingestion/corporate_actions.py``; a contract test pins the two together.
COVERAGE_TABLE_TEMPLATE = f"{SILVER_CORPORATE_ACTIONS_DIR.parent.name}/corporate_action_coverage/{{market}}"
GOLD_FEATURES_TABLE = f"{GOLD_FEATURES_DIR.parent.name}/{GOLD_FEATURES_DIR.name}"
#: Row-only surfaces observed so that ``empty`` cannot hide rows a reset would
#: delete. Bronze price rows carry ``identity_provenance=current_ticker_config``
#: (never a synthetic label); predictions carry no provenance column at all.
BRONZE_PRICE_TABLE_TEMPLATE = f"{BRONZE_MARKET_DATA_DIR.parent.name}/market_data/{{market}}"
PREDICTIONS_TABLE = f"{PLATINUM_PREDICTIONS_DIR.parent.name}/{PLATINUM_PREDICTIONS_DIR.name}"
#: Lake-relative trees whose Delta datasets decide ``empty``: exactly the artifact
#: trees ``devtools/reset_lake.py`` removes (``data/models`` is deliberately absent —
#: it holds derived artifacts, not rows, so provenance cannot judge it).
RESET_DATASET_TREES: tuple[str, ...] = (
    BRONZE_DIR.name,
    SILVER_DIR.name,
    GOLD_DIR.name,
    PLATINUM_PREDICTIONS_DIR.relative_to(LAKE_DIR).as_posix(),
)
#: Cap on reported ``tables`` entries: a deep lake must not flood the report. The
#: row *totals* are always computed over every dataset, never over this slice.
MAX_REPORTED_TABLES = 50
#: Existing Bronze identity label, observed (not added) as the Bronze marker column.
BRONZE_MARKER_COLUMN = "identity_provenance"
#: The value that label already carries for both seeded and real ingestion.
BRONZE_MARKER_VALUE = "current_ticker_config"
_BRONZE_PROVENANCE = (
    "01_bronze price rows carry identity_provenance=current_ticker_config, the same label a real "
    "config-resolved ingestion writes; Bronze rows are not distinguishable from real ingestion."
)
_HISTORY_VALIDATION_SCOPE = (
    "date/ticker/signal_type keys and identity availability are checked before identity filters; "
    "payload fields are validated only for displayed rows; unknown legacy identities remain visible as unavailable "
    "and are never joined to current configuration"
)


@dataclass(frozen=True)
class TableObservation:
    """Read outcome and latest date keyed by canonical market/MIC/symbol."""

    state: TableState
    latest_by_identity: dict[tuple[str, str, str], date]


@dataclass(frozen=True)
class CandidateSignal:
    """A candidate with explicit canonical identity or visible unknown legacy state."""

    ticker: str
    market: str | None
    mic: str | None
    identity_schema_version: int | None
    identity_status: Literal["canonical", "unknown_legacy"]
    date: date
    signal_type: str
    action: str
    confidence: float
    reasoning: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ScanReportObservation:
    """Coverage and candidate signals retained in one persisted scan report."""

    run_id: str
    target_date: date
    watchlist: str
    tickers: list[str]
    instruments: list[InstrumentKey]
    status: str
    outcomes_by_identity: dict[str, list[str]]
    signals: list[CandidateSignal]


@dataclass(frozen=True)
class ScanHistoryObservation:
    """Read-only status for auxiliary structured scan reports."""

    state: HistoryState
    reports: list[ScanReportObservation]
    invalid_reports: int


@dataclass(frozen=True)
class SignalHistoryObservation:
    """Read-only status and bounded rows from the existing signal history.

    Required keys are checked across all loaded rows; other payload fields are
    checked only for selected rows, so ``available`` is not a full-corpus
    payload-integrity claim.
    """

    state: HistoryState
    signals: list[CandidateSignal]
    total_rows: int
    invalid_rows: int
    count_by_identity: dict[str, int]
    latest_by_identity: dict[str, date]
    unknown_identity_rows: int
    truncated: bool
    validation_scope: str = _HISTORY_VALIDATION_SCOPE


@dataclass(frozen=True)
class TickerReadiness:
    """Observed source, feature, scan, and history coverage for one identity."""

    market: str
    mic: str
    ticker: str
    price_status: str
    latest_price_session: date | None
    price_age_days: int | None
    feature_status: str
    latest_feature_session: date | None
    feature_age_days: int | None
    latest_scan_target_date: date | None
    latest_scan_report_statuses: list[str]
    latest_scan_outcomes: list[str]
    signal_history_rows: int
    latest_signal_history_date: date | None


@dataclass(frozen=True)
class StorageSchemaObservation:
    """Arrow ``string_view`` drift for one table (metadata only, read-only).

    Merge rewrites (label finalization, upserts) store string columns as Arrow
    ``string_view`` while the Delta log still declares ``string``; PyArrow has no
    comparison kernels for that type, so readers post-filter those columns. This
    observation makes the drift visible instead of leaving it to an exception.
    """

    table: str
    state: StorageSchemaState
    scanned_files: int
    string_view_columns: list[str]


@dataclass(frozen=True)
class SyntheticTableMark:
    """Rows and marker counts for one observed lake table.

    ``column`` names the marker column consulted; it is ``""`` on a row-only surface
    (``04_platinum/predictions``, and every generically discovered Delta dataset under
    the reset targets), which has no marker and can only be ``empty`` or
    ``unmarked``. ``earliest_marked_date``/``latest_marked_date`` are the affected
    window of the marked rows (the coverage window for proofs, the session for
    features), never an ingestion timestamp.
    """

    table: str
    column: str
    state: SyntheticMarkState
    rows: int
    marked_rows: int
    earliest_marked_date: date | None
    latest_marked_date: date | None


@dataclass(frozen=True)
class SyntheticProvenanceObservation:
    """Read-only answer to "is the canonical lake synthetic?" (R7/D3, visibility only).

    Derived only from markers that already exist: ``02_silver``
    ``corporate_action_coverage.source`` and Gold ``features.action_coverage_source``
    equal to :data:`SYNTHETIC_MARKER`. Nothing is written and no new provenance label
    exists.

    ``empty`` means no rows in *any* Delta dataset under the reset targets
    (:data:`RESET_DATASET_TREES`): the marker tables and every dataset found by
    walking those trees, not a hand-enumerated subset of them. A lake holding rows
    only in ``02_silver/news_sentiment`` or ``01_bronze/macro`` is therefore never
    ``empty``, and a reset can never delete rows this observation did not count.

    ``real`` belongs to the state vocabulary so callers can refuse it, but this
    observation never reports it: the markers cannot prove real ingestion. A lake
    that holds rows and no marker is ``unknown``, because Bronze price rows are
    indistinguishable from real config-resolved ingestion.

    ``tables`` is capped at :data:`MAX_REPORTED_TABLES` entries (``tables_truncated``
    says so); ``rows``/``marked_rows`` always total every observed dataset.
    """

    state: SyntheticProvenanceState
    tables: list[SyntheticTableMark]
    tables_truncated: bool
    rows: int
    marked_rows: int
    earliest_marked_date: date | None
    latest_marked_date: date | None
    bronze_provenance: str
    reason: str


@dataclass(frozen=True)
class TrackingReadinessReport:
    """A policy-neutral snapshot; it is not a health or trading decision."""

    as_of: date
    support_scope: str
    watchlist: str
    tickers: list[TickerReadiness]
    price_table_status: TableState
    feature_table_status: TableState
    scan_history_status: HistoryState
    scan_history_invalid_reports: int
    scan_reports: list[ScanReportObservation]
    signal_history_status: HistoryState
    signal_history_invalid_rows: int
    signal_history_unknown_identity_rows: int
    signal_history: list[CandidateSignal]
    signal_history_truncated: bool
    freshness_limit: str
    inference_status: str
    delivery_status: str
    storage_schema: list[StorageSchemaObservation]
    synthetic_provenance: SyntheticProvenanceObservation
    signal_history_validation_scope: str = _HISTORY_VALIDATION_SCOPE

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-ready primitive data without writing an artifact."""
        return asdict(self)


def _candidate(signal: Signal) -> CandidateSignal:
    """Copy only known explanation fields; never expose arbitrary metadata."""
    known_metadata = {
        "strategy",
        "lookback_days",
        "pct_from_sma",
        "price",
        "sma",
        "sentiment_score",
        "article_count",
        "prediction",
        "probability",
        "horizon_days",
        "model_mode",
        "model_version",
        "execution_probability",
        "candidate_action",
        "candidate_source",
        "meta_label_threshold",
        "threshold_used",
        "threshold_source",
        "barrier_settings",
    }
    identity_known = signal.market is not None and signal.mic is not None and signal.identity_schema_version == IDENTITY_SCHEMA_VERSION
    return CandidateSignal(
        ticker=signal.ticker,
        market=signal.market if identity_known else None,
        mic=signal.mic if identity_known else None,
        identity_schema_version=IDENTITY_SCHEMA_VERSION if identity_known else None,
        identity_status="canonical" if identity_known else "unknown_legacy",
        date=signal.date,
        signal_type=signal.signal_type,
        action=signal.action,
        confidence=signal.confidence,
        reasoning=signal.reasoning,
        metadata={key: value for key, value in signal.metadata.items() if key in known_metadata},
    )


def _inspect_storage_directory(path: Path) -> tuple[StorageDirectoryState, list[Path]]:
    """Classify a storage path without following symlinks or special entries."""
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            return "corrupt", []
    except FileNotFoundError:
        return "missing", []
    except OSError:
        return "corrupt", []

    entries: list[Path] = []
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except OSError:
            return "corrupt", []
        for child in children:
            try:
                mode = child.lstat().st_mode
            except OSError:
                return "corrupt", []
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                return "corrupt", []
            entries.append(child)
            if stat.S_ISDIR(mode):
                pending.append(child)

    return ("empty" if not entries else "present"), entries


def _recognized_table_layout(path: Path, entries: list[Path], *, is_delta: bool) -> bool:
    """Accept only known table files, partition directories, and Delta metadata.

    Unknown visible entries fail closed even when valid data files are present.
    Accepted Delta internals are log JSON/checkpoints/CRC, ``_last_checkpoint``,
    ``_started_*``/``_committed_*`` markers, ``_sidecars`` Parquet,
    ``_staged_commits`` JSON, and ``_change_data``. Newer
    or otherwise unrecognized Delta artifacts need an explicit allowlist entry.
    Hidden library files are ignored after inspection rejects symlinks/special files.
    """
    has_parquet = False
    has_delta_log = False
    root_metadata = {"_metadata", "_common_metadata", "_SUCCESS"}
    delta_log_directories = {"_sidecars", "_staged_commits"}

    for entry in entries:
        parts = entry.relative_to(path).parts
        if any(part.startswith(".") for part in parts):
            continue

        is_directory = entry.is_dir()
        if is_delta and parts[0] == "_delta_log":
            has_delta_log = True
            if len(parts) == 1:
                if not is_directory:
                    return False
                continue
            log_child = parts[1]
            if log_child in delta_log_directories:
                if len(parts) == 2 and is_directory:
                    continue
                if len(parts) == 3 and not is_directory:
                    suffix = entry.suffix
                    if (log_child == "_sidecars" and suffix == ".parquet") or (log_child == "_staged_commits" and suffix == ".json"):
                        continue
                return False
            is_log_file = (
                entry.name == "_last_checkpoint"
                or entry.name.startswith(("_started_", "_committed_"))
                or entry.name.endswith((".json", ".json.gz", ".parquet", ".crc"))
            )
            if len(parts) == 2 and not is_directory and is_log_file:
                continue
            return False

        if is_delta and parts[0] == "_change_data":
            if is_directory:
                if len(parts) == 1 or all("=" in part and part.partition("=")[0] for part in parts[1:]):
                    continue
                return False
            if entry.suffix == ".parquet" and all("=" in part and part.partition("=")[0] for part in parts[1:-1]):
                continue
            return False

        if len(parts) == 1 and not is_directory and entry.name in root_metadata:
            continue
        if is_directory:
            if all("=" in part and part.partition("=")[0] for part in parts):
                continue
            return False
        if entry.suffix == ".parquet" and all("=" in part and part.partition("=")[0] for part in parts[:-1]):
            has_parquet = True
            continue
        return False

    return (not is_delta or has_delta_log) and (is_delta or has_parquet)


def _read_table_dates(path: Path, table: str, as_of: date, *, lake_dir: Path | None = None) -> TableObservation:
    """Read latest dates only for rows with explicit canonical instrument identity."""
    directory_state, entries = _inspect_storage_directory(path)
    if directory_state == "missing":
        return TableObservation("missing", {})
    if directory_state == "corrupt":
        return TableObservation("corrupt", {})

    try:
        is_delta = DeltaTable.is_deltatable(str(path))
        if is_delta:
            if not _recognized_table_layout(path, entries, is_delta=True):
                return TableObservation("corrupt", {})
            frame = read_delta(table, lake_dir=lake_dir or LAKE_DIR, columns=["market", "mic", "symbol", "date"])
        elif directory_state == "empty":
            return TableObservation("missing", {})
        elif path / "_delta_log" in entries or not _recognized_table_layout(path, entries, is_delta=False):
            return TableObservation("corrupt", {})
        else:
            parquet_files = [
                item for item in entries if item.suffix == ".parquet" and not any(part.startswith(".") for part in item.relative_to(path).parts)
            ]
            if not parquet_files:
                return TableObservation("corrupt", {})
            frame = pl.scan_parquet([str(item) for item in parquet_files], hive_partitioning=True).select("market", "mic", "symbol", "date").collect()
        if not {"market", "mic", "symbol", "date"}.issubset(frame.columns):
            return TableObservation("corrupt", {})
        normalized = normalize_identity_frame(frame).with_columns(pl.col("date").cast(pl.Date, strict=False).alias("_observed_date"))
        if normalized["_observed_date"].null_count():
            return TableObservation("corrupt", {})
        latest_rows = (
            normalized.filter(pl.col("_observed_date") <= as_of)
            .group_by("market", "mic", "symbol")
            .agg(pl.col("_observed_date").max().alias("latest"))
            .iter_rows(named=True)
        )
        latest_by_identity = {
            (str(row["market"]), str(row["mic"]), str(row["symbol"])): row["latest"] for row in latest_rows if row["latest"] is not None
        }
        return TableObservation("available", latest_by_identity)
    except Exception:
        # Missing/ambiguous historical identity is unavailable, never current-US inferred.
        return TableObservation("corrupt", {})


def _read_scan_reports(as_of: date, data_dir: Path) -> ScanHistoryObservation:
    """Read structured scan outcomes, retaining corruption as an explicit state."""
    reports_dir = data_dir / "signal_scans"
    directory_state, entries = _inspect_storage_directory(reports_dir)
    if directory_state == "missing":
        return ScanHistoryObservation("missing", [], 0)
    if directory_state == "corrupt":
        return ScanHistoryObservation("corrupt", [], 1)
    if directory_state == "empty":
        return ScanHistoryObservation("missing", [], 0)

    report_paths = [
        entry for entry in entries if entry.parent == reports_dir and entry.is_file() and entry.suffix == ".json" and not entry.name.startswith(".")
    ]
    invalid_reports = 1 if any(entry not in report_paths for entry in entries) else 0
    reports: list[ScanReportObservation] = []
    for report_path in sorted(report_paths):
        try:
            report = SignalScanReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        except Exception:
            invalid_reports += 1
            continue
        if report.target_date > as_of:
            continue
        outcomes: dict[str, list[str]] = {}
        for outcome in report.outcomes:
            key = InstrumentKey(market=outcome.market, mic=outcome.mic, symbol=outcome.ticker).display_key
            outcomes.setdefault(key, []).append(outcome.status)
        signals = [_candidate(signal) for signal in report.signals if signal.date <= as_of]
        reports.append(
            ScanReportObservation(
                run_id=report.run_id,
                target_date=report.target_date,
                watchlist=report.watchlist,
                tickers=list(report.tickers),
                instruments=list(report.instruments),
                status=report.status,
                outcomes_by_identity=outcomes,
                signals=signals,
            )
        )
    reports.sort(key=lambda item: (item.target_date, item.run_id), reverse=True)
    state: HistoryState = "corrupt" if invalid_reports else "available"
    return ScanHistoryObservation(state, reports, invalid_reports)


def _read_history_frame(data_dir: Path) -> tuple[HistoryState, pl.DataFrame | None]:
    """Read current Delta or legacy Parquet history without the migration helper."""
    history_path = data_dir / "signals"
    directory_state, entries = _inspect_storage_directory(history_path)
    if directory_state == "missing":
        return "missing", None
    if directory_state == "corrupt":
        return "corrupt", None
    try:
        is_delta = DeltaTable.is_deltatable(str(history_path))
        if is_delta:
            if not _recognized_table_layout(history_path, entries, is_delta=True):
                return "corrupt", None
            return "available", read_delta("signals", lake_dir=data_dir)
        if directory_state == "empty":
            return "missing", None
        if history_path / "_delta_log" in entries or not _recognized_table_layout(history_path, entries, is_delta=False):
            return "corrupt", None
        parquet_files = [
            item for item in entries if item.suffix == ".parquet" and not any(part.startswith(".") for part in item.relative_to(history_path).parts)
        ]
        if not parquet_files:
            return "corrupt", None
        return "available", pl.scan_parquet([str(item) for item in parquet_files], hive_partitioning=True).collect()
    except Exception:
        return "corrupt", None


def read_signal_history_readonly(
    *,
    target_date: date | None = None,
    as_of: date | None = None,
    limit: int = 100,
    tickers: set[str] | None = None,
    identities: set[tuple[str, str, str]] | None = None,
    data_dir: Path | None = None,
) -> SignalHistoryObservation:
    """Read history without migration; unknown legacy identities are never joined."""
    if limit < 1:
        raise ValueError("history limit must be at least 1")
    cutoff = target_date or as_of or date.today()
    selected_data_dir = data_dir or DATA_DIR
    state, frame = _read_history_frame(selected_data_dir)
    if frame is None:
        return SignalHistoryObservation(state, [], 0, 0, {}, {}, 0, False)
    required = {"ticker", "date", "signal_type", "action", "confidence", "reasoning"}
    if not required.issubset(frame.columns):
        return SignalHistoryObservation("corrupt", [], 0, frame.height, {}, {}, 0, False)

    for column in ("market", "mic", "identity_schema_version"):
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias(column))
    normalized = frame.with_columns(
        pl.col("ticker").cast(pl.Utf8, strict=False),
        pl.col("signal_type").cast(pl.Utf8, strict=False),
        pl.col("date").cast(pl.Date, strict=False).alias("_observed_date"),
    )
    invalid_ticker = pl.col("ticker").is_null() | (pl.col("ticker").str.strip_chars() == "").fill_null(True)
    invalid_signal_type = ~pl.col("signal_type").is_in(["backtest", "sentiment", "ml"]).fill_null(False)
    invalid_required_key = pl.col("_observed_date").is_null() | invalid_ticker | invalid_signal_type
    invalid_rows = normalized.filter(invalid_required_key).height
    eligible = normalized.filter(~invalid_required_key & (pl.col("_observed_date") <= cutoff))
    if target_date is not None:
        eligible = eligible.filter(pl.col("_observed_date") == target_date)
    unknown_identity_rows = sum(_history_identity(row) is None for row in eligible.iter_rows(named=True))
    rows = eligible.sort(["_observed_date", "ticker", "signal_type"], descending=[True, False, False]).iter_rows(named=True)

    signals: list[CandidateSignal] = []
    count_by_identity: dict[str, int] = {}
    latest_by_identity: dict[str, date] = {}
    total_rows = 0
    for row in rows:
        symbol = row["ticker"]
        if tickers is not None and symbol not in tickers:
            continue
        identity = _history_identity(row)
        if identities is not None and identity is not None and identity.tuple not in identities:
            continue
        action = row.get("action")
        confidence = row.get("confidence")
        reasoning = row.get("reasoning")
        if action not in {"BUY", "SELL", "HOLD"} or not isinstance(confidence, int | float) or isinstance(confidence, bool):
            invalid_rows += 1
            continue
        score = float(confidence)
        if not math.isfinite(score) or not 0 <= score <= 100 or not isinstance(reasoning, str):
            invalid_rows += 1
            continue
        total_rows += 1
        if identity is None:
            candidate = CandidateSignal(
                ticker=symbol,
                market=None,
                mic=None,
                identity_schema_version=None,
                identity_status="unknown_legacy",
                date=row["_observed_date"],
                signal_type=str(row["signal_type"]),
                action=action,
                confidence=score,
                reasoning=reasoning,
                metadata={},
            )
        else:
            data = {key: value for key, value in row.items() if key in SignalRecord.model_fields and value is not None}
            data["date"] = row["_observed_date"]
            try:
                signal = SignalRecord.model_validate(data).to_signal()
            except Exception:
                invalid_rows += 1
                continue
            candidate = _candidate(signal)
            key = identity.display_key
            count_by_identity[key] = count_by_identity.get(key, 0) + 1
            latest_by_identity[key] = max(signal.date, latest_by_identity.get(key, signal.date))
        if len(signals) < limit:
            signals.append(candidate)
    result_state: HistoryState = "corrupt" if invalid_rows else "unknown_identity" if unknown_identity_rows else state
    return SignalHistoryObservation(
        state=result_state,
        signals=signals,
        total_rows=total_rows,
        invalid_rows=invalid_rows,
        count_by_identity=count_by_identity,
        latest_by_identity=latest_by_identity,
        unknown_identity_rows=unknown_identity_rows,
        truncated=total_rows > limit,
    )


def _history_identity(row: dict[str, Any]) -> InstrumentKey | None:
    """Return canonical history identity only when the stored schema proves it."""
    market, mic, symbol, version = row.get("market"), row.get("mic"), row.get("ticker"), row.get("identity_schema_version")
    if (
        not isinstance(market, str)
        or not market.strip()
        or not isinstance(mic, str)
        or not mic.strip()
        or not isinstance(symbol, str)
        or not symbol.strip()
    ):
        return None
    if not isinstance(version, int) or isinstance(version, bool) or version != IDENTITY_SCHEMA_VERSION:
        return None
    try:
        return InstrumentKey(market=market, mic=mic, symbol=symbol)
    except (ValueError, TypeError):
        return None


def _observe_storage_schema(table: str, path: Path, *, lake_dir: Path | None = None) -> StorageSchemaObservation:
    """Report Arrow string_view drift for one table without reading data pages."""
    directory_state, _entries = _inspect_storage_directory(path)
    if directory_state == "missing":
        return StorageSchemaObservation(table=table, state="missing", scanned_files=0, string_view_columns=[])
    try:
        drift = string_view_columns(table, lake_dir=lake_dir or LAKE_DIR)
    except DeltaError:
        return StorageSchemaObservation(table=table, state="unreadable", scanned_files=0, string_view_columns=[])
    return StorageSchemaObservation(
        table=table,
        state="drifted" if drift.drifted else "clean",
        scanned_files=drift.scanned_files,
        string_view_columns=drift.columns,
    )


def _observe_synthetic_mark(
    table: str,
    path: Path,
    *,
    marker_column: str | None,
    first_date_column: str,
    last_date_column: str,
    lake_dir: Path,
) -> SyntheticTableMark:
    """Count rows and marker rows in one table without writing, migrating, or inferring.

    ``marker_column=None`` observes a row-only surface: rows are counted, no marker
    is invented, and the surface can only be ``empty`` or ``unmarked``.
    """

    def mark(
        state: SyntheticMarkState,
        rows: int = 0,
        marked_rows: int = 0,
        earliest: date | None = None,
        latest: date | None = None,
    ) -> SyntheticTableMark:
        return SyntheticTableMark(
            table=table,
            column=marker_column or "",
            state=state,
            rows=rows,
            marked_rows=marked_rows,
            earliest_marked_date=earliest,
            latest_marked_date=latest,
        )

    directory_state, _entries = _inspect_storage_directory(path)
    if directory_state == "missing":
        return mark("missing")
    if directory_state == "empty":
        # An existing directory with no entries holds no rows; nothing to read.
        return mark("empty")
    if directory_state == "corrupt":
        return mark("unreadable")
    if marker_column is None:
        # Row-only surface: only the row count is needed, so no row payload is
        # materialized and no marker column is invented. The reader contract is the
        # same: an unreadable table is `unreadable` (unknown state), never `empty`.
        try:
            counted = DeltaTable(str(path)).to_pyarrow_dataset().count_rows()
        except Exception:
            return mark("unreadable")
        return mark("empty") if counted == 0 else mark("unmarked", rows=counted)
    projection = [marker_column, first_date_column, last_date_column]
    try:
        frame = read_delta(table, lake_dir=lake_dir, columns=list(dict.fromkeys(projection)))
    except Exception:
        return mark("unreadable")
    if frame.height == 0:
        return mark("empty")
    missing_columns = [column for column in (marker_column, first_date_column, last_date_column) if column not in frame.columns]
    if missing_columns:
        # A table without the marker column cannot be classified: unknown, not real.
        return mark("unreadable", rows=frame.height)
    try:
        marked = frame.filter(pl.col(marker_column) == SYNTHETIC_MARKER)
        earliest = marked[first_date_column].min() if marked.height else None
        latest = marked[last_date_column].max() if marked.height else None
    except Exception:
        return mark("unreadable", rows=frame.height)
    return mark(
        "marked" if marked.height else "unmarked",
        rows=frame.height,
        marked_rows=marked.height,
        earliest=earliest if isinstance(earliest, date) else None,
        latest=latest if isinstance(latest, date) else None,
    )


def _discover_delta_datasets(lake_dir: Path) -> list[tuple[str, Path]]:
    """Find every Delta dataset under the reset targets as ``(lake-relative name, path)``.

    Any directory holding a ``_delta_log`` is a dataset, so a table only one writer
    knows about still contributes its rows to the ``empty`` judgement — a reset deletes
    whole trees, so enumeration by hand is what let real rows be reported ``empty``.
    The walk never follows symlinks (nothing outside the lake is inspected) and stops
    at a dataset, so nested directories are counted once, by the outer dataset.
    """
    found: list[tuple[str, Path]] = []
    for tree in RESET_DATASET_TREES:
        pending = [lake_dir / tree]
        while pending:
            directory = pending.pop()
            try:
                if not stat.S_ISDIR(directory.lstat().st_mode):
                    continue
            except OSError:
                continue
            if (directory / "_delta_log").is_dir():
                found.append((directory.relative_to(lake_dir).as_posix(), directory))
                continue
            try:
                children = list(directory.iterdir())
            except OSError:
                continue
            for child in children:
                try:
                    if stat.S_ISDIR(child.lstat().st_mode):
                        pending.append(child)
                except OSError:
                    continue
    return sorted(found)


def _listed(names: Sequence[str], *, limit: int = 8) -> str:
    """Render names for a reason string without letting a large lake flood it."""
    shown = ", ".join(names[:limit])
    return shown if len(names) <= limit else f"{shown}, and {len(names) - limit} more"


def _residual_data_files(lake_dir: Path) -> list[str]:
    """Files under the reset targets that belong to no Delta dataset.

    A reset deletes whole trees, so a stray bare-Parquet (or JSONL) table under
    ``02_silver`` that carries no ``_delta_log`` is still rows the reset would
    destroy. It cannot be counted, so it must never be reported ``empty``.
    ``_delta_log`` internals and dotfiles (``.DS_Store``, ``.gitkeep``) are not data.
    """
    residuals: list[str] = []
    for tree in RESET_DATASET_TREES:
        root = lake_dir / tree
        try:
            if not stat.S_ISDIR(root.lstat().st_mode):
                continue
        except OSError:
            continue
        for path in root.rglob("*"):
            if path.name.startswith(".") or "_delta_log" in path.parts:
                continue
            try:
                if stat.S_ISREG(path.lstat().st_mode):
                    residuals.append(path.relative_to(lake_dir).as_posix())
            except OSError:
                continue
    return sorted(residuals)


def observe_synthetic_provenance(*, lake_dir: Path | None = None) -> SyntheticProvenanceObservation:
    """Observe whether the lake holds synthetic rows, using only existing markers.

    States, over every Delta dataset a reset would delete (marked and row-only):

    - ``synthetic`` — at least one row carries :data:`SYNTHETIC_MARKER` (this wins even
      when unmarked datasets coexist, so a seeded-then-partially-ingested lake stays
      resettable).
    - ``empty`` — no Delta dataset under the reset targets holds any row, and no
      non-Delta data file sits under those trees.
    - ``unknown`` — rows exist without a marker (including Bronze-only lakes, or rows
      only in a dataset no marker table covers), a dataset cannot be read, or
      non-Delta data files exist whose rows cannot be counted.
    - ``real`` — reserved for a positive proof of real ingestion; today's markers
      cannot produce it, so an unmarked lake is never reported ``real``.
    """
    selected_lake_dir = lake_dir or LAKE_DIR
    tables = [
        _observe_synthetic_mark(
            COVERAGE_TABLE_TEMPLATE.format(market=market),
            selected_lake_dir / COVERAGE_TABLE_TEMPLATE.format(market=market),
            marker_column="source",
            first_date_column="coverage_start",
            last_date_column="coverage_end",
            lake_dir=selected_lake_dir,
        )
        for market in sorted(PRICE_MARKETS)
    ]
    tables.append(
        _observe_synthetic_mark(
            GOLD_FEATURES_TABLE,
            selected_lake_dir / GOLD_FEATURES_TABLE,
            marker_column="action_coverage_source",
            first_date_column="date",
            last_date_column="date",
            lake_dir=selected_lake_dir,
        )
    )
    # Row-only surfaces a reset deletes: rows here must never be reported ``empty``.
    # Bronze observes the existing identity label (never the synthetic marker), which
    # documents the indistinguishable case instead of inferring provenance.
    tables.extend(
        _observe_synthetic_mark(
            BRONZE_PRICE_TABLE_TEMPLATE.format(market=market),
            selected_lake_dir / BRONZE_PRICE_TABLE_TEMPLATE.format(market=market),
            marker_column=BRONZE_MARKER_COLUMN,
            first_date_column="date",
            last_date_column="date",
            lake_dir=selected_lake_dir,
        )
        for market in sorted(PRICE_MARKETS)
    )
    tables.append(
        _observe_synthetic_mark(
            PREDICTIONS_TABLE,
            selected_lake_dir / PREDICTIONS_TABLE,
            marker_column=None,
            first_date_column="date",
            last_date_column="date",
            lake_dir=selected_lake_dir,
        )
    )

    # Every remaining Delta dataset under the reset targets: unenumerated tables
    # (news sentiment, macro, SEC extractions, raw articles, …) are still rows a
    # reset would delete, so ``empty`` is decided over them too. Row-only: no marker
    # column exists on them, and none is invented.
    observed_names = {table.table for table in tables}
    tables.extend(
        _observe_synthetic_mark(
            table,
            path,
            marker_column=None,
            first_date_column="date",
            last_date_column="date",
            lake_dir=selected_lake_dir,
        )
        for table, path in _discover_delta_datasets(selected_lake_dir)
        if table not in observed_names
    )

    rows = sum(table.rows for table in tables)
    marked_rows = sum(table.marked_rows for table in tables)
    residuals = [] if rows else _residual_data_files(selected_lake_dir)
    earliest = min((table.earliest_marked_date for table in tables if table.earliest_marked_date is not None), default=None)
    latest = max((table.latest_marked_date for table in tables if table.latest_marked_date is not None), default=None)
    unreadable = [table.table for table in tables if table.state == "unreadable"]
    held = [table.table for table in tables if table.rows]
    # Only the Bronze *price* tables carry that identity label; macro/news/raw
    # datasets must not be described as carrying it.
    bronze_price_names = {BRONZE_PRICE_TABLE_TEMPLATE.format(market=market) for market in PRICE_MARKETS}
    bronze_held = [name for name in held if name in bronze_price_names]
    # Totals above are exact; only the reported per-table slice is capped.
    reported = tables[:MAX_REPORTED_TABLES]
    truncated = len(tables) > len(reported)
    truncation_note = f" {len(tables) - len(reported)} further dataset(s) are not listed." if truncated else ""

    if marked_rows:
        state: SyntheticProvenanceState = "synthetic"
        reason = (
            f"{marked_rows} of {rows} row(s) in the datasets under the reset targets carry {SYNTHETIC_MARKER!r}; "
            f"the lake holds synthetic rows.{truncation_note}"
        )
    elif unreadable:
        state = "unknown"
        reason = f"cannot read {len(unreadable)} observed table(s) ({_listed(unreadable)}); the absence of a marker is not proven."
    elif residuals:
        state = "unknown"
        reason = (
            f"no Delta dataset under the reset targets holds a row, but {len(residuals)} non-Delta data file(s) remain "
            f"({_listed(residuals)}); their rows cannot be counted, so the lake is not proven empty."
        )
    elif rows == 0:
        state = "empty"
        reason = (
            "no rows in any Delta dataset under the reset targets "
            f"({', '.join(RESET_DATASET_TREES)}); data/models holds derived artifacts and no rows."
        )
    else:
        # Deliberately not "real": no-marker-plus-data is the indistinguishable case (R7/D3).
        state = "unknown"
        bronze_note = (
            f" The Bronze row-only table(s) holding rows ({_listed(bronze_held)}) carry "
            f"{BRONZE_MARKER_COLUMN}={BRONZE_MARKER_VALUE}, the same label a real config-resolved ingestion writes."
            if bronze_held
            else ""
        )
        reason = (
            f"{rows} row(s) in the datasets under the reset targets ({_listed(held)}) carry no {SYNTHETIC_MARKER!r} marker; "
            f"this is unknown rather than real because the existing markers cannot prove real ingestion.{bronze_note}{truncation_note}"
        )
    return SyntheticProvenanceObservation(
        state=state,
        tables=reported,
        tables_truncated=truncated,
        rows=rows,
        marked_rows=marked_rows,
        earliest_marked_date=earliest,
        latest_marked_date=latest,
        bronze_provenance=_BRONZE_PROVENANCE,
        reason=reason,
    )


def build_tracking_readiness(
    *,
    watchlist_path: Path | None = None,
    ticker_config_path: Path | None = None,
    as_of: date | None = None,
    data_dir: Path | None = None,
    lake_dir: Path | None = None,
) -> TrackingReadinessReport:
    """Build read-only readiness for the canonical private managed watchlist."""
    cutoff = as_of or date.today()
    watchlist = load_private_watchlist(watchlist_path, ticker_config_path)
    selected_data_dir = data_dir or DATA_DIR
    selected_lake_dir = lake_dir or LAKE_DIR
    identities = {item.instrument.tuple for item in watchlist.instruments}
    price_tables = {
        market: _read_table_dates(
            selected_lake_dir / f"01_bronze/market_data/{market}",
            f"01_bronze/market_data/{market}",
            cutoff,
            lake_dir=selected_lake_dir,
        )
        for market in sorted({item.instrument.market for item in watchlist.instruments})
    }
    feature_table = _read_table_dates(selected_lake_dir / "03_gold/features", "03_gold/features", cutoff, lake_dir=selected_lake_dir)
    storage_schema = [
        _observe_storage_schema(f"01_bronze/market_data/{market}", selected_lake_dir / f"01_bronze/market_data/{market}", lake_dir=selected_lake_dir)
        for market in sorted({item.instrument.market for item in watchlist.instruments})
    ]
    storage_schema.extend(
        [
            _observe_storage_schema("03_gold/features", selected_lake_dir / "03_gold/features", lake_dir=selected_lake_dir),
            _observe_storage_schema("04_platinum/predictions", selected_lake_dir / "04_platinum/predictions", lake_dir=selected_lake_dir),
        ]
    )
    scan_history = _read_scan_reports(cutoff, selected_data_dir)
    scan_history = ScanHistoryObservation(
        scan_history.state,
        [
            report
            for report in scan_history.reports
            if report.watchlist == watchlist.name and {item.tuple for item in report.instruments} == identities
        ],
        scan_history.invalid_reports,
    )
    signal_history = read_signal_history_readonly(as_of=cutoff, limit=100, identities=identities, data_dir=selected_data_dir)

    ticker_rows: list[TickerReadiness] = []
    for tracked in watchlist.instruments:
        instrument = tracked.instrument
        identity_key = instrument.display_key
        price_table = price_tables.get(instrument.market, TableObservation("missing", {}))
        latest_price = price_table.latest_by_identity.get(instrument.tuple)
        latest_feature = feature_table.latest_by_identity.get(instrument.tuple)
        ticker_scans = [report for report in scan_history.reports if instrument.tuple in {item.tuple for item in report.instruments}]
        latest_scan_date = max((report.target_date for report in ticker_scans), default=None)
        latest_runs = [report for report in ticker_scans if report.target_date == latest_scan_date]
        latest_scan_statuses = [report.status for report in latest_runs]
        latest_outcomes = [status for report in latest_runs for status in report.outcomes_by_identity.get(identity_key, [])]
        ticker_rows.append(
            TickerReadiness(
                market=instrument.market,
                mic=instrument.mic,
                ticker=instrument.symbol,
                price_status=_ticker_table_status(price_table, instrument.tuple),
                latest_price_session=latest_price,
                price_age_days=(cutoff - latest_price).days if latest_price else None,
                feature_status=_ticker_table_status(feature_table, instrument.tuple),
                latest_feature_session=latest_feature,
                feature_age_days=(cutoff - latest_feature).days if latest_feature else None,
                latest_scan_target_date=latest_scan_date,
                latest_scan_report_statuses=latest_scan_statuses,
                latest_scan_outcomes=latest_outcomes,
                signal_history_rows=signal_history.count_by_identity.get(identity_key, 0),
                latest_signal_history_date=signal_history.latest_by_identity.get(identity_key),
            )
        )

    table_states = list(price_tables.values())
    price_status: TableState = (
        "corrupt"
        if any(table.state == "corrupt" for table in table_states)
        else ("available" if any(table.state == "available" for table in table_states) else "missing")
    )
    return TrackingReadinessReport(
        as_of=cutoff,
        support_scope="Canonical managed watchlist identities across all five supported markets; unknown historical identities remain unavailable.",
        watchlist=watchlist.name,
        tickers=ticker_rows,
        price_table_status=price_status,
        feature_table_status=feature_table.state,
        scan_history_status=scan_history.state,
        scan_history_invalid_reports=scan_history.invalid_reports,
        scan_reports=scan_history.reports,
        signal_history_status=signal_history.state,
        signal_history_invalid_rows=signal_history.invalid_rows,
        signal_history_unknown_identity_rows=signal_history.unknown_identity_rows,
        signal_history=signal_history.signals,
        signal_history_truncated=signal_history.truncated,
        freshness_limit="unset/unknown; ages are descriptive calendar days only",
        inference_status="not evaluated; readiness observation only",
        delivery_status="unavailable/not configured; no delivery state exists",
        storage_schema=storage_schema,
        synthetic_provenance=observe_synthetic_provenance(lake_dir=selected_lake_dir),
        signal_history_validation_scope=_HISTORY_VALIDATION_SCOPE,
    )


def _ticker_table_status(table: TableObservation, identity: tuple[str, str, str]) -> str:
    if table.state != "available":
        return table.state
    return "observed" if identity in table.latest_by_identity else "no_rows_as_of"


def conflicts_by_ticker_date(signals: list[CandidateSignal]) -> list[list[CandidateSignal]]:
    """Return conflicts only within one known canonical identity and session."""
    grouped: dict[tuple[tuple[str, str, str], date], list[CandidateSignal]] = {}
    for signal in signals:
        if signal.action in {"BUY", "SELL"} and signal.market is not None and signal.mic is not None:
            key = (signal.market, signal.mic, signal.ticker)
            grouped.setdefault((key, signal.date), []).append(signal)
    return [values for values in grouped.values() if {signal.action for signal in values} == {"BUY", "SELL"}]


def render_json(report: TrackingReadinessReport | SignalHistoryObservation) -> str:
    """Serialize an observation for terminal display only."""
    return json.dumps(asdict(report), indent=2, default=str)
