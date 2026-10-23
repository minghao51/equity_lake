"""Signal history storage with Delta Lake (ACID merge)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import structlog

from equity_lake.core.paths import DATA_DIR, SIGNALS_DIR
from equity_lake.signals.models import SIGNAL_RECORD_SCHEMA, Signal, SignalRecord, SignalScanReport
from equity_lake.storage.delta import merge_delta, migrate_parquet_to_delta, read_delta

logger = structlog.get_logger(__name__)


def _ensure_delta_table() -> None:
    """One-time migration of legacy Hive-partitioned Parquet to Delta (idempotent)."""
    if SIGNALS_DIR.exists() and not (SIGNALS_DIR / "_delta_log").exists():
        logger.info("signals_migrating_legacy_parquet_to_delta", path=str(SIGNALS_DIR))
        migrate_parquet_to_delta("signals", lake_dir=DATA_DIR)


def save_signals(signals: list[Signal], target_date: date) -> None:
    """Upsert signals into the Delta-backed signal history, keyed by (ticker, date, signal_type).

    Rows pass through the closed :class:`SignalRecord` model at this write
    boundary, so the Delta schema stays stable regardless of generator metadata.
    """
    if not signals:
        return

    _ensure_delta_table()

    records = [SignalRecord.from_signal(signal).model_dump() for signal in signals]
    frame = pl.DataFrame(records, schema=SIGNAL_RECORD_SCHEMA)
    merge_delta(
        frame,
        table="signals",
        key_columns=["market", "mic", "ticker", "date", "signal_type"],
        lake_dir=DATA_DIR,
    )


def save_scan_report(report: SignalScanReport, data_dir: Path | None = None) -> Path:
    """Persist one structured scan report under the auxiliary data boundary.

    The report is revalidated at this write seam; it never enters the cataloged
    medallion lake or the legacy signal-history Delta table.
    """
    validated = SignalScanReport.model_validate(report.model_dump())
    reports_dir = (data_dir or DATA_DIR) / "signal_scans"
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / f"{validated.target_date.isoformat()}-{validated.run_id}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(validated.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def load_signals(target_date: date) -> list[Signal]:
    """Load signals for a target date from the Delta-backed signal history."""
    _ensure_delta_table()
    if not (SIGNALS_DIR / "_delta_log").exists():
        return []

    frame = read_delta("signals", lake_dir=DATA_DIR).filter(pl.col("date") == target_date)
    signals: list[Signal] = []
    record_fields = set(SignalRecord.model_fields)

    for row in frame.iter_rows(named=True):
        # Legacy tables may carry extra/struct columns; only whitelisted scalar
        # fields feed the closed record model.
        data = {key: value for key, value in row.items() if key in record_fields and value is not None}
        try:
            signals.append(SignalRecord.model_validate(data).to_signal())
        except Exception as exc:  # noqa: BLE001 — one bad row must not break the scan
            logger.warning("signal_history_row_invalid", ticker=row.get("ticker"), error=str(exc))

    return signals
