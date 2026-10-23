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
from typing import Any, cast

import polars as pl
import structlog

from equity_lake.core.availability import AvailabilityContractError  # pyright: ignore[reportMissingImports] -- source import verified by uv
from equity_lake.core.paths import LAKE_DIR
from equity_lake.storage.delta import DeltaError, read_delta, update_delta_columns

from ..core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_COLUMNS,
    IDENTITY_DATE_COLUMNS,
    IDENTITY_SCHEMA_VERSION,
    LegacyIdentityUnavailableError,
    instrument_from_record,
    normalize_identity_frame,
)

logger = structlog.get_logger(__name__)

GOLD_FEATURES_TABLE = "03_gold/features"
TARGET_COLUMN = "next_day_return"
AVAILABILITY_COLUMN = "label_available_date"
#: Columns a label-only update is allowed to touch, in order.
LABEL_COLUMNS: tuple[str, ...] = (TARGET_COLUMN, AVAILABILITY_COLUMN)
KEY_COLUMNS: tuple[str, ...] = IDENTITY_DATE_COLUMNS


def _require_identity_schema(frame: pl.DataFrame) -> None:
    if "identity_schema_version" not in frame.columns:
        raise LegacyIdentityUnavailableError("Identity schema version is absent; legacy shared rows are unavailable")
    versions = frame["identity_schema_version"].drop_nulls().unique().to_list()
    if not versions or versions != [IDENTITY_SCHEMA_VERSION]:
        raise LegacyIdentityUnavailableError(f"Unsupported or unknown identity schema versions: {versions}")


def add_label_availability(frame: pl.DataFrame) -> pl.DataFrame:
    """Add ``label_available_date`` = the next observed session per ticker.

    The frame is ordered by ``(ticker, date)`` so the following row's date is
    the session on which the target became computable. The final row of each
    ticker has no next observation yet and keeps a null availability (pending);
    a timestamp is never fabricated.
    """
    if frame.is_empty():
        return frame
    identified = normalize_identity_frame(frame)
    _require_identity_schema(identified)
    ordered = identified.sort(list(KEY_COLUMNS))
    return ordered.with_columns(pl.col("date").shift(-1).over(list(IDENTITY_COLUMNS)).alias(AVAILABILITY_COLUMN))


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
    frame = normalize_identity_frame(frame)
    _require_identity_schema(frame)
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
    stored = normalize_identity_frame(stored)
    _require_identity_schema(stored)
    available = [column for column in LABEL_COLUMNS if column in frame.columns and column in stored.columns]
    if not available:
        return frame
    renamed = stored.select(
        *[pl.col(column) for column in KEY_COLUMNS],
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
    pending_keys: tuple[tuple[str, str, str, date], ...] = ()
    unmatched_keys: tuple[tuple[str, str, str, date], ...] = ()
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "eligible": self.eligible,
            "finalized": self.finalized,
            "pending": self.pending,
            "unmatched": self.unmatched,
            "pending_keys": [
                {"market": market, "mic": mic, "symbol": symbol, "ticker": symbol, "date": str(day)} for market, mic, symbol, day in self.pending_keys
            ],
            "unmatched_keys": [
                {"market": market, "mic": mic, "symbol": symbol, "ticker": symbol, "date": str(day)}
                for market, mic, symbol, day in self.unmatched_keys
            ],
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


def _key_tuples(frame: pl.DataFrame) -> tuple[tuple[str, str, str, date], ...]:
    if frame.is_empty():
        return ()
    identified = normalize_identity_frame(frame)
    rows = identified.select(*KEY_COLUMNS).unique(maintain_order=True).iter_rows(named=True)
    return tuple((str(row["market"]), str(row["mic"]), str(row["symbol"]), row["date"]) for row in rows)


def preceding_label_frame(features_df: pl.DataFrame, output_start_date: date) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split the preceding observed session's rows into (eligible, pending)."""
    if features_df.is_empty() or TARGET_COLUMN not in features_df.columns:
        empty = features_df.head(0) if not features_df.is_empty() else features_df
        return empty, empty
    identified = normalize_identity_frame(features_df)
    _require_identity_schema(identified)
    frame = identified if AVAILABILITY_COLUMN in identified.columns else add_label_availability(identified)
    ordered = frame.sort(list(KEY_COLUMNS))
    prior = ordered.filter(pl.col("date") < pl.lit(output_start_date)).unique(subset=list(IDENTITY_COLUMNS), keep="last", maintain_order=True)
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
        stored = read_delta(GOLD_FEATURES_TABLE, lake_dir=lake_dir, filters=filters, columns=[*KEY_COLUMNS, "identity_schema_version"])
    except DeltaError:
        return None
    stored = normalize_identity_frame(stored)
    _require_identity_schema(stored)
    return stored.with_columns(pl.col("date").cast(pl.Date)).select(list(KEY_COLUMNS)).unique()


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
        stored = read_delta(
            GOLD_FEATURES_TABLE,
            lake_dir=lake_dir,
            filters=filters,
            columns=[*KEY_COLUMNS, "identity_schema_version", TARGET_COLUMN],
        )
    except DeltaError:
        return 0
    if stored.is_empty() or TARGET_COLUMN not in stored.columns:
        return 0
    stored = normalize_identity_frame(stored)
    _require_identity_schema(stored)
    stored = stored.with_columns(pl.col("date").cast(pl.Date)).unique(subset=list(KEY_COLUMNS))
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
        metrics = update_delta_columns(
            matched,
            GOLD_FEATURES_TABLE,
            update_columns=list(LABEL_COLUMNS),
            key_columns=list(KEY_COLUMNS),
            lake_dir=lake_dir,
        )
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
            return [
                {
                    "market": row["market"],
                    "mic": row["mic"],
                    "symbol": row["symbol"],
                    "ticker": row["symbol"],
                    "date": str(row["date"]),
                }
                for row in frame.select(*KEY_COLUMNS).head(50).iter_rows(named=True)
            ]

        return {
            "start": str(self.start),
            "end": str(self.end),
            "finalizable": self.finalizable_count,
            "pending": self.pending_count,
            "finalizable_keys": key_rows(self.finalizable),
            "pending_keys": key_rows(self.pending),
        }


def _load_price_bars(tickers: list[str] | None, start: date, end: date, lake_dir: Path | None) -> pl.DataFrame:
    """Load only price bars with explicit MIC/exchange identity provenance."""
    from deltalake import DeltaTable

    from equity_lake.core.paths import PRICE_MARKETS
    from equity_lake.ingestion.types import MARKET_DIR_MAP

    lake = lake_dir or LAKE_DIR
    parts: list[pl.DataFrame] = []
    for market in PRICE_MARKETS:
        path = lake / MARKET_DIR_MAP[market]
        if not path.exists():
            continue
        if DeltaTable.is_deltatable(str(path)):
            table = DeltaTable(str(path))
            available = {field.name for field in table.schema().fields}
            selected = [name for name in ("ticker", "symbol", "mic", "exchange", "date", "close") if name in available]
            raw = cast(pl.DataFrame, pl.from_arrow(table.to_pyarrow_table(columns=selected, filters=[("date", ">=", start), ("date", "<=", end)])))
        else:
            parquet = pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=True)
            available = set(parquet.collect_schema().names())
            selected = [name for name in ("ticker", "symbol", "mic", "exchange", "date", "close") if name in available]
            raw = parquet.select(selected).filter(pl.col("date").cast(pl.Date).is_between(start, end, closed="both")).collect()
        if raw.is_empty():
            continue
        symbol_column = "symbol" if "symbol" in raw.columns else "ticker"
        if symbol_column not in raw.columns or "date" not in raw.columns or "close" not in raw.columns:
            continue
        expressions: list[pl.Expr] = [pl.lit(market).alias("market"), pl.col(symbol_column).alias("symbol"), pl.col(symbol_column).alias("ticker")]
        expressions.extend([pl.col(name) if name in raw.columns else pl.lit(None, dtype=pl.String).alias(name) for name in ("mic", "exchange")])
        expressions.extend([pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64)])
        selected_bars = raw.select(expressions)
        if tickers:
            selected_bars = selected_bars.filter(pl.col("symbol").is_in(tickers))
        parts.append(selected_bars)
    if not parts:
        return pl.DataFrame(
            schema={"market": pl.String, "mic": pl.String, "symbol": pl.String, "ticker": pl.String, "date": pl.Date, "close": pl.Float64}
        )
    bars = pl.concat(parts, how="diagonal_relaxed")
    identified: list[dict[str, Any]] = []
    for row in bars.to_dicts():
        try:
            key = instrument_from_record(row)
        except LegacyIdentityUnavailableError:
            continue
        row.update(key.as_columns())
        identified.append(row)
    return (
        pl.DataFrame(identified, infer_schema_length=None)
        if identified
        else pl.DataFrame(
            schema={"market": pl.String, "mic": pl.String, "symbol": pl.String, "ticker": pl.String, "date": pl.Date, "close": pl.Float64}
        )
    )


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
    gold = normalize_identity_frame(gold)
    _require_identity_schema(gold)
    if tickers:
        gold = gold.filter(pl.col("symbol").is_in(tickers))
    gold = gold.with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64))
    if TARGET_COLUMN not in gold.columns:
        gold = gold.with_columns(pl.lit(None).cast(pl.Float64).alias(TARGET_COLUMN))
    unlabeled = gold.filter(pl.col(TARGET_COLUMN).is_null()).select([*KEY_COLUMNS, "close"])
    if AVAILABILITY_COLUMN in gold.columns:
        availability_unknown = (
            gold.filter(pl.col(TARGET_COLUMN).is_not_null() & pl.col(AVAILABILITY_COLUMN).is_null())
            .select(list(KEY_COLUMNS))
            .with_columns(pl.col("symbol").alias("ticker"))
        )
    else:
        availability_unknown = (
            gold.filter(pl.col(TARGET_COLUMN).is_not_null()).select(list(KEY_COLUMNS)).with_columns(pl.col("symbol").alias("ticker"))
        )
    if unlabeled.is_empty():
        return LabelRepairPlan(start=start, end=end, availability_unknown=availability_unknown)

    bars = _load_price_bars(tickers, start, end + _availability_lookahead(), lake_dir).sort(list(KEY_COLUMNS))
    adjusted_pairs: list[dict[str, Any]] = []
    try:
        from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs
        from equity_lake.storage.lake_reader import with_price_adjustment

        for identity_row in bars.select(*IDENTITY_COLUMNS).unique().to_dicts():
            instrument = instrument_from_record(identity_row)
            actions, coverage = load_corporate_action_inputs(instrument.market, lake_dir=lake_dir)
            identity_bars = bars.filter(
                (pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol)
            ).sort("date")
            rows = identity_bars.to_dicts()
            for current, following in zip(rows, rows[1:], strict=False):
                pair = pl.DataFrame([current, following])
                adjusted = with_price_adjustment(pair, actions, coverage=coverage, as_of=following["date"])
                adjusted = adjusted.sort("date")
                adjusted_close = adjusted["close"].to_list()
                adjusted_pairs.append(
                    {
                        **{column: current[column] for column in IDENTITY_COLUMNS},
                        "ticker": current["ticker"],
                        "date": current["date"],
                        "_entry_close": adjusted_close[0],
                        "_next_close": adjusted_close[1],
                        "_next_date": following["date"],
                    }
                )
    except AvailabilityContractError as exc:
        logger.warning("label_repair_action_coverage_unavailable", error=str(exc))
        pending = unlabeled.select(*KEY_COLUMNS).with_columns(pl.col("symbol").alias("ticker"))
        return LabelRepairPlan(start=start, end=end, pending=pending, availability_unknown=availability_unknown)

    pair_schema = {
        **{column: pl.String for column in IDENTITY_COLUMNS},
        "ticker": pl.String,
        "date": pl.Date,
        "_entry_close": pl.Float64,
        "_next_close": pl.Float64,
        "_next_date": pl.Date,
    }
    pairs = pl.DataFrame(adjusted_pairs, schema=pair_schema, strict=False) if adjusted_pairs else pl.DataFrame(schema=pair_schema)
    joined = unlabeled.join(pairs.select(*KEY_COLUMNS, "_entry_close", "_next_close", "_next_date"), on=list(KEY_COLUMNS), how="left")

    candidate = (
        pl.when((pl.col("_entry_close") != 0.0) & pl.col("_next_close").is_not_null())
        .then(pl.col("_next_close") / pl.col("_entry_close") - 1.0)
        .otherwise(None)
        .alias(TARGET_COLUMN)
    )
    joined = joined.with_columns(candidate, pl.col("_next_date").alias(AVAILABILITY_COLUMN))
    finalizable = (
        joined.filter(pl.col(TARGET_COLUMN).is_not_null())
        .select([*KEY_COLUMNS, TARGET_COLUMN, AVAILABILITY_COLUMN])
        .with_columns(pl.col("symbol").alias("ticker"))
    )
    pending = joined.filter(pl.col(TARGET_COLUMN).is_null()).select(list(KEY_COLUMNS)).with_columns(pl.col("symbol").alias("ticker"))
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
    metrics = update_delta_columns(
        matched,
        GOLD_FEATURES_TABLE,
        update_columns=list(LABEL_COLUMNS),
        key_columns=list(KEY_COLUMNS),
        lake_dir=lake_dir,
    )
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
