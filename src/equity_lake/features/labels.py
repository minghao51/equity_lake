"""Label lifecycle: availability metadata and bounded label-only finalization.

Implements ADR-0014. The Gold label columns (``next_day_return`` and the v2
meta-label bookkeeping) keep their names and semantics; this module only adds
the ``label_available_date`` availability marker and the bounded update path
that finalizes the preceding observed session's label on a later session.

Invariants enforced here:

- The label for a row becomes computable on the next **observed** session for
  that instrument (next exchange session with a valid bar), never a calendar
  day and never across a missing bar.
- Finalization is a matched-only, label-column-only Delta update: it never
  inserts keys, evolves the schema, or touches unrelated feature columns.
- As-of training/backtest eligibility excludes labels whose availability
  postdates the cutoff, and legacy rows with unknown availability are
  strict-excluded by default.

Broad historical repair is report-first and separately authorized (D09);
:func:`scan_label_repair` only inventories scope and :func:`apply_label_repair`
performs the bounded update the caller explicitly requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import structlog

from equity_lake.core.paths import LAKE_DIR
from equity_lake.storage.delta import DeltaError, read_delta, update_delta_columns

logger = structlog.get_logger(__name__)

GOLD_FEATURES_TABLE = "03_gold/features"
TARGET_COLUMN = "next_day_return"
AVAILABILITY_COLUMN = "label_available_date"
#: Columns a label-only update is allowed to touch, in order.
LABEL_COLUMNS: tuple[str, ...] = (TARGET_COLUMN, AVAILABILITY_COLUMN)
KEY_COLUMNS: tuple[str, ...] = ("ticker", "date")


def add_label_availability(frame: pl.DataFrame) -> pl.DataFrame:
    """Add ``label_available_date`` = the next observed session per ticker.

    The frame is ordered by ``(ticker, date)`` so the following row's date is
    the session on which the target became computable. The final row of each
    ticker has no next observation yet and keeps a null availability (pending);
    a timestamp is never fabricated.
    """
    if frame.is_empty():
        return frame
    ordered = frame.sort(list(KEY_COLUMNS))
    return ordered.with_columns(pl.col("date").shift(-1).over("ticker").alias(AVAILABILITY_COLUMN))


def filter_as_of_available(frame: pl.DataFrame, cutoff: date, *, strict: bool = True) -> pl.DataFrame:
    """Keep rows whose label availability is known and on/before *cutoff*.

    ADR-0014 §5: training/backtests must not consume a label finalized after
    the cutoff. Legacy rows with unknown availability (the column is absent, or
    null) are strict-excluded by default; ``strict=False`` is the documented
    tolerance hook and is never used by the default training/backtest path.
    """
    if frame.is_empty():
        return frame
    if AVAILABILITY_COLUMN not in frame.columns:
        return frame.head(0) if strict else frame
    available = pl.col(AVAILABILITY_COLUMN)
    if strict:
        return frame.filter(available.is_not_null() & (available <= pl.lit(cutoff)))
    # Tolerance hook: unknown availability is KEPT under strict=False, matching
    # the absent-column branch above (review warning 2).
    return frame.filter(available.is_null() | (available <= pl.lit(cutoff)))


def preserve_finalized_labels(frame: pl.DataFrame, *, lake_dir: Path | None = None) -> pl.DataFrame:
    """Carry already-finalized label values across a normal rewrite of their row.

    Replaying an older output session recomputes that row with a null target
    (the next observation is outside the window); without this step the
    all-column upsert would erase a label finalized by a later session. Only
    label columns are carried; feature columns keep the freshly computed values.
    """
    if frame.is_empty() or TARGET_COLUMN not in frame.columns or "date" not in frame.columns:
        return frame
    dates = frame.select("date").drop_nulls().unique().to_series().to_list()
    if not dates:
        return frame
    try:
        stored = read_delta(
            GOLD_FEATURES_TABLE,
            lake_dir=lake_dir,
            filters=[("date", ">=", min(dates)), ("date", "<=", max(dates))],
        )
    except DeltaError:
        return frame
    if stored.is_empty() or TARGET_COLUMN not in stored.columns:
        return frame
    stored = stored.with_columns(pl.col("date").cast(pl.Date))
    available = [column for column in LABEL_COLUMNS if column in frame.columns and column in stored.columns]
    if not available:
        return frame
    renamed = stored.select(
        pl.col("ticker"),
        pl.col("date"),
        *[pl.col(column).alias(f"__stored_{column}") for column in available],
    ).unique(subset=list(KEY_COLUMNS))
    joined = frame.join(renamed, on=list(KEY_COLUMNS), how="left")
    if TARGET_COLUMN in available:
        # A replay that recomputes a DIFFERENT non-null finalized label would
        # silently overwrite it through the normal all-column upsert, outside
        # the report-first repair path (review warning 3). Surface it loudly;
        # the rewrite itself stays (feature columns must keep fresh values).
        divergent = joined.filter(
            pl.col(TARGET_COLUMN).is_not_null()
            & pl.col(f"__stored_{TARGET_COLUMN}").is_not_null()
            & (pl.col(TARGET_COLUMN) != pl.col(f"__stored_{TARGET_COLUMN}"))
        )
        if divergent.height > 0:
            logger.warning(
                "label_replay_divergence",
                count=divergent.height,
                sample=[{"ticker": row["ticker"], "date": str(row["date"])} for row in divergent.head(5).iter_rows(named=True)],
                message="Replay recomputed finalized labels differently; the rewrite overwrites them outside the report-first repair path.",
            )
    joined = joined.with_columns(
        [pl.when(pl.col(column).is_null()).then(pl.col(f"__stored_{column}")).otherwise(pl.col(column)).alias(column) for column in available]
    )
    return joined.drop([f"__stored_{column}" for column in available])


@dataclass(frozen=True)
class LabelFinalizationResult:
    """Outcome of one preceding-session label finalization attempt."""

    eligible: int = 0
    finalized: int = 0
    pending: int = 0
    unmatched: int = 0
    pending_keys: tuple[tuple[str, date], ...] = ()
    unmatched_keys: tuple[tuple[str, date], ...] = ()
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "eligible": self.eligible,
            "finalized": self.finalized,
            "pending": self.pending,
            "unmatched": self.unmatched,
            "pending_keys": [{"ticker": ticker, "date": str(day)} for ticker, day in self.pending_keys],
            "unmatched_keys": [{"ticker": ticker, "date": str(day)} for ticker, day in self.unmatched_keys],
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


def _key_tuples(frame: pl.DataFrame) -> tuple[tuple[str, date], ...]:
    if frame.is_empty():
        return ()
    rows = frame.select("ticker", "date").unique(maintain_order=True).iter_rows(named=True)
    return tuple((str(row["ticker"]), row["date"]) for row in rows)


def preceding_label_frame(features_df: pl.DataFrame, output_start_date: date) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split the preceding observed session's rows into (eligible, pending)."""
    if features_df.is_empty() or TARGET_COLUMN not in features_df.columns:
        empty = features_df.head(0) if not features_df.is_empty() else features_df
        return empty, empty
    frame = features_df if AVAILABILITY_COLUMN in features_df.columns else add_label_availability(features_df)
    ordered = frame.sort(list(KEY_COLUMNS))
    prior = ordered.filter(pl.col("date") < pl.lit(output_start_date)).unique(subset=["ticker"], keep="last", maintain_order=True)
    if prior.is_empty():
        return prior, prior
    pending = prior.filter(pl.col(TARGET_COLUMN).is_null() | pl.col(AVAILABILITY_COLUMN).is_null())
    eligible = prior.filter(pl.col(TARGET_COLUMN).is_not_null() & pl.col(AVAILABILITY_COLUMN).is_not_null())
    return eligible, pending


def _stored_keys(update_df: pl.DataFrame, lake_dir: Path | None) -> pl.DataFrame | None:
    """Bounded read of the stored ``(ticker, date)`` keys for the update's dates.

    Returns ``None`` when the Gold table does not exist yet (nothing persisted
    to finalize). Only identity columns are materialized; the date filter keeps
    the read partition-pruned even as Gold history grows.
    """
    dates = update_df.select("date").drop_nulls().unique().to_series().to_list()
    if not dates:
        return None
    filters: list[tuple[str, str, Any]] = [("date", ">=", min(dates)), ("date", "<=", max(dates))]
    try:
        stored = read_delta(GOLD_FEATURES_TABLE, lake_dir=lake_dir, filters=filters, columns=["ticker", "date"])
    except DeltaError:
        return None
    return stored.with_columns(pl.col("date").cast(pl.Date)).select("ticker", "date").unique()


def _finalized_divergences(matched: pl.DataFrame, lake_dir: Path | None) -> int:
    """Count matched keys whose recomputed non-null label differs from stored (W03 review).

    Finalization recomputes the label from current source data; when a source
    revision changes an already-finalized value, the rewrite happens outside the
    report-first repair path, so it is surfaced loudly here instead of silently.
    """
    if matched.is_empty() or TARGET_COLUMN not in matched.columns:
        return 0
    dates = matched.select("date").drop_nulls().unique().to_series().to_list()
    if not dates:
        return 0
    filters: list[tuple[str, str, Any]] = [("date", ">=", min(dates)), ("date", "<=", max(dates))]
    try:
        stored = read_delta(GOLD_FEATURES_TABLE, lake_dir=lake_dir, filters=filters, columns=["ticker", "date", TARGET_COLUMN])
    except DeltaError:
        return 0
    if stored.is_empty() or TARGET_COLUMN not in stored.columns:
        return 0
    stored = stored.with_columns(pl.col("date").cast(pl.Date)).unique(subset=["ticker", "date"])
    joined = matched.select([*KEY_COLUMNS, TARGET_COLUMN]).join(stored, on=list(KEY_COLUMNS), how="inner", suffix="__stored")
    divergent = joined.filter(
        pl.col(TARGET_COLUMN).is_not_null()
        & pl.col(f"{TARGET_COLUMN}__stored").is_not_null()
        & (pl.col(TARGET_COLUMN) != pl.col(f"{TARGET_COLUMN}__stored"))
    )
    return divergent.height


def finalize_preceding_labels(
    features_df: pl.DataFrame,
    output_start_date: date,
    *,
    lake_dir: Path | None = None,
    dry_run: bool = False,
) -> LabelFinalizationResult:
    """Finalize the preceding observed session's label fields for each ticker.

    Uses only information available by the output session (the computed frame
    already contains the next session's bar). The update is matched-only and
    label-column-only; keys that are not persisted are reported, never created.
    """
    eligible, pending = preceding_label_frame(features_df, output_start_date)
    pending_keys = _key_tuples(pending)
    if eligible.is_empty():
        return LabelFinalizationResult(eligible=0, pending=len(pending_keys), pending_keys=pending_keys)

    update_df = eligible.unique(subset=list(KEY_COLUMNS), keep="last").select([*KEY_COLUMNS, *LABEL_COLUMNS])
    if dry_run:
        return LabelFinalizationResult(eligible=update_df.height, pending=len(pending_keys), pending_keys=pending_keys)

    stored = _stored_keys(update_df, lake_dir)
    if stored is None:
        return LabelFinalizationResult(
            eligible=update_df.height,
            pending=len(pending_keys),
            pending_keys=pending_keys,
            unmatched=update_df.height,
            unmatched_keys=_key_tuples(update_df),
        )

    matched = update_df.join(stored, on=list(KEY_COLUMNS), how="semi")
    unmatched = update_df.join(stored, on=list(KEY_COLUMNS), how="anti")
    divergences = _finalized_divergences(matched, lake_dir)
    if divergences > 0:
        logger.warning(
            "label_finalization_divergence",
            count=divergences,
            message=(
                "Finalization recomputed already-finalized labels to different values "
                "(possible source-data revision); the rewrite happens outside the report-first repair path."
            ),
        )
    result = LabelFinalizationResult(
        eligible=update_df.height,
        pending=len(pending_keys),
        pending_keys=pending_keys,
        unmatched=unmatched.height,
        unmatched_keys=_key_tuples(unmatched),
    )
    if matched.is_empty():
        return result

    try:
        metrics = update_delta_columns(matched, GOLD_FEATURES_TABLE, update_columns=list(LABEL_COLUMNS), lake_dir=lake_dir)
    except DeltaError as exc:
        logger.error("label_finalization_update_failed", error=str(exc))
        return LabelFinalizationResult(
            eligible=result.eligible,
            pending=result.pending,
            pending_keys=result.pending_keys,
            unmatched=result.unmatched,
            unmatched_keys=result.unmatched_keys,
            error=str(exc),
        )
    return LabelFinalizationResult(
        eligible=result.eligible,
        finalized=int(metrics.get("num_target_rows_updated", 0)),
        pending=result.pending,
        pending_keys=result.pending_keys,
        unmatched=result.unmatched,
        unmatched_keys=result.unmatched_keys,
    )


# ---------------------------------------------------------------------------
# Bounded, report-first repair path (D09)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelRepairPlan:
    """Inventory of unlabeled Gold rows in a bounded date range.

    ``availability_unknown`` rows carry a stored target but no availability
    metadata (legacy rows): they are strict-excluded from training and reported
    here so an availability-only fix can be explicitly authorized (review
    warning 4); they are never touched by the bounded label repair itself.
    """

    start: date
    end: date
    finalizable: pl.DataFrame = field(default_factory=pl.DataFrame)
    pending: pl.DataFrame = field(default_factory=pl.DataFrame)
    availability_unknown: pl.DataFrame = field(default_factory=pl.DataFrame)

    @property
    def finalizable_count(self) -> int:
        return self.finalizable.height

    @property
    def pending_count(self) -> int:
        return self.pending.height

    @property
    def availability_unknown_count(self) -> int:
        return self.availability_unknown.height

    def to_payload(self) -> dict[str, Any]:
        def key_rows(frame: pl.DataFrame) -> list[dict[str, str]]:
            return [{"ticker": row["ticker"], "date": str(row["date"])} for row in frame.select("ticker", "date").head(50).iter_rows(named=True)]

        return {
            "start": str(self.start),
            "end": str(self.end),
            "finalizable": self.finalizable_count,
            "pending": self.pending_count,
            "finalizable_keys": key_rows(self.finalizable),
            "pending_keys": key_rows(self.pending),
        }


def _load_price_bars(tickers: list[str] | None, start: date, end: date, lake_dir: Path | None) -> pl.DataFrame:
    """Load ``(ticker, date, close)`` bars over the five price markets."""
    import duckdb

    from equity_lake.core.paths import PRICE_MARKETS
    from equity_lake.ingestion.types import MARKET_DIR_MAP
    from equity_lake.storage.lake_reader import duckdb_scan_for, ensure_delta_extension

    lake = lake_dir or LAKE_DIR
    con = duckdb.connect(":memory:")
    try:
        ensure_delta_extension(con)
        parts: list[str] = []
        for market in PRICE_MARKETS:
            path = lake / MARKET_DIR_MAP[market]
            if path.exists():
                parts.append(f"SELECT ticker, date, close FROM {duckdb_scan_for(path)}")
        if not parts:
            return pl.DataFrame(schema={"ticker": pl.Utf8, "date": pl.Date, "close": pl.Float64})
        union = " UNION ALL ".join(parts)
        if tickers:
            query = (
                f"SELECT ticker, date, close FROM ({union}) "
                "WHERE ticker IN (SELECT unnest($1::VARCHAR[])) AND date BETWEEN $2 AND $3 "
                "ORDER BY ticker, date"
            )
            bars = con.execute(query, [tickers, start, end]).pl()
        else:
            query = f"SELECT ticker, date, close FROM ({union}) WHERE date BETWEEN $1 AND $2 ORDER BY ticker, date"
            bars = con.execute(query, [start, end]).pl()
        return bars.with_columns(pl.col("date").cast(pl.Date))
    finally:
        con.close()


def scan_label_repair(
    *,
    start: date,
    end: date,
    tickers: list[str] | None = None,
    lake_dir: Path | None = None,
) -> LabelRepairPlan:
    """Inventory unlabeled Gold rows in ``[start, end]`` (report-only, no writes).

    A row is "finalizable" when its next observed session's bar exists (the
    label can be computed with known availability); otherwise it stays pending
    because the next-session bar is missing.
    """
    filters: list[tuple[str, str, Any]] = [("date", ">=", start), ("date", "<=", end)]
    try:
        gold = read_delta(GOLD_FEATURES_TABLE, lake_dir=lake_dir, filters=filters)
    except DeltaError:
        return LabelRepairPlan(start=start, end=end)
    if gold.is_empty():
        return LabelRepairPlan(start=start, end=end)
    if tickers:
        gold = gold.filter(pl.col("ticker").is_in(tickers))
    gold = gold.with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64))
    if TARGET_COLUMN not in gold.columns:
        gold = gold.with_columns(pl.lit(None).cast(pl.Float64).alias(TARGET_COLUMN))
    unlabeled = gold.filter(pl.col(TARGET_COLUMN).is_null()).select("ticker", "date", "close")
    if AVAILABILITY_COLUMN in gold.columns:
        availability_unknown = gold.filter(pl.col(TARGET_COLUMN).is_not_null() & pl.col(AVAILABILITY_COLUMN).is_null()).select(list(KEY_COLUMNS))
    else:
        availability_unknown = gold.filter(pl.col(TARGET_COLUMN).is_not_null()).select(list(KEY_COLUMNS))
    if unlabeled.is_empty():
        return LabelRepairPlan(start=start, end=end, availability_unknown=availability_unknown)

    bars = _load_price_bars(tickers, start, end + _availability_lookahead(), lake_dir)
    bars = bars.sort(list(KEY_COLUMNS)).with_columns(
        pl.col("close").shift(-1).over("ticker").alias("_next_close"),
        pl.col("date").shift(-1).over("ticker").alias("_next_date"),
    )
    joined = unlabeled.join(bars.select("ticker", "date", "_next_close", "_next_date"), on=list(KEY_COLUMNS), how="left")

    candidate = (
        pl.when((pl.col("close") != 0.0) & pl.col("_next_close").is_not_null())
        .then(pl.col("_next_close") / pl.col("close") - 1.0)
        .otherwise(None)
        .alias(TARGET_COLUMN)
    )
    joined = joined.with_columns(candidate, pl.col("_next_date").alias(AVAILABILITY_COLUMN))
    finalizable = joined.filter(pl.col(TARGET_COLUMN).is_not_null()).select([*KEY_COLUMNS, TARGET_COLUMN, AVAILABILITY_COLUMN])
    pending = joined.filter(pl.col(TARGET_COLUMN).is_null()).select(list(KEY_COLUMNS))
    return LabelRepairPlan(start=start, end=end, finalizable=finalizable, pending=pending, availability_unknown=availability_unknown)


def _availability_lookahead() -> Any:
    """Buffer added past ``end`` so the next session's bar can be found."""
    return timedelta(days=30)


def apply_label_repair(plan: LabelRepairPlan, *, lake_dir: Path | None = None) -> int:
    """Apply the plan's finalizable label updates; returns the updated row count."""
    if plan.finalizable.is_empty():
        return 0
    stored = _stored_keys(plan.finalizable, lake_dir)
    if stored is None:
        return 0
    matched = plan.finalizable.join(stored, on=list(KEY_COLUMNS), how="semi")
    if matched.is_empty():
        return 0
    metrics = update_delta_columns(matched, GOLD_FEATURES_TABLE, update_columns=list(LABEL_COLUMNS), lake_dir=lake_dir)
    return int(metrics.get("num_target_rows_updated", 0))


__all__ = [
    "AVAILABILITY_COLUMN",
    "GOLD_FEATURES_TABLE",
    "KEY_COLUMNS",
    "LABEL_COLUMNS",
    "TARGET_COLUMN",
    "LabelFinalizationResult",
    "LabelRepairPlan",
    "add_label_availability",
    "apply_label_repair",
    "filter_as_of_available",
    "finalize_preceding_labels",
    "preceding_label_frame",
    "preserve_finalized_labels",
    "scan_label_repair",
]
