"""Reconstruct raw prices from vendor split-adjusted rows at ingest (ADR-0029 Part I).

yfinance returns OHLC **and** volume split-back-adjusted (D01 probe, 2026-11-14): a
10-for-1 split leaves no seam in the vendor series, and only ``adj_close`` carries a
further dividend adjustment. The lake's adjusted-read contract
(:func:`equity_lake.storage.lake_reader.with_price_adjustment`, ADR-0015) assumes stored
prices are *unadjusted* and multiplies them by the stored split ratio, so vendor-basis
rows persisted as fetched would be adjusted a second time — roughly 10x too low for
NVDA across its 2024 split. This module inverts the vendor's back-adjustment before
persistence: OHLC is divided by the cumulative product of the stored split ratios whose
``ex_date`` is after the row's date, and volume is multiplied by the same factor. That
factor is the exact reciprocal of the factor the adjusted read applies, so Bronze holds
raw, split-free prices and the read contract is correct without change.

Inputs fail closed (ADR-0029 decision 2). The transform consumes the *target root's* own
Silver split actions, and a price write refuses rather than guessing when:

``action_store_missing``
    the root has no stored Silver corporate actions (run ``equity corporate-actions``
    against the same ``--lake-dir`` first).
``action_scope_incomplete``
    a ticker in the frame has no stored action at all. A ticker absent from the action
    store is indistinguishable from a ticker whose actions are missing, so it is never
    read as "no split".
``action_invalid``
    a stored split ratio is not a usable ratio (finite, ``> 0`` and not exactly ``1``;
    the lake stores ``old/new``, so a forward split is < 1 and a reverse split > 1).
``action_window_uncovered``
    a ticker's newest stored ``ex_date`` predates the price rows being written, so any
    split above that watermark — and therefore its back-adjustment below it — is
    unproven.

Nothing else about the row moves: ``adj_close`` is stored as fetched (decision 4), the
origin envelope stays ``unknown`` (decision 5), and there is no schema, catalog, reader
or ``price_adjustment_version`` change (decision 6). The transform is a pure function of
``(prices, actions)``, so a re-ingest of the same window is identical (decision 3).

Scope: this runs only for vendor rows landing in a real-source root (ADR-0030). The
canonical root holds demo rows, and decision 8 forbids re-inflating those.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import polars as pl
import structlog

from equity_lake.core.polars_utils import FrameLike, ensure_polars

logger = structlog.get_logger()

__all__ = [
    "SPLIT_ACTION",
    "SplitReconstructionError",
    "load_action_history",
    "reconstruct_vendor_prices",
]

SPLIT_ACTION = "split"
_OHLC_COLUMNS = ("open", "high", "low", "close")


class SplitReconstructionError(RuntimeError):
    """A price frame cannot be re-inflated from provable split actions (ADR-0029).

    ``reason`` is a stable token (see the module docstring) so callers, tests and the
    operator-facing report can distinguish a missing action store from an unproven
    window without parsing prose.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def load_action_history(market: str, *, lake_dir: Path | None = None) -> pl.DataFrame:
    """Read the target root's Silver corporate actions — every kind, not only splits.

    Dividends are included because a ticker's *presence* is the evidence that its
    action history was fetched at all; the transform then uses its splits alone.
    """
    from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs

    actions, _coverage = load_corporate_action_inputs(market, lake_dir=lake_dir)
    return actions


def _proven_empty_tickers(frame: pl.DataFrame, coverage: pl.DataFrame | None) -> set[str]:
    """Tickers whose whole price window is covered by a weak, clockless proof.

    ADR-0033: an empty vendor action list is evidence only as a
    ``vendor_events_no_clock`` proof covering the rows being written. A strict proof
    never proves emptiness (it carries a clock and asserts a different contract), and
    a proof that does not reach the price rows proves nothing about them.
    """
    if coverage is None or coverage.is_empty():
        return set()
    windows = frame.group_by("ticker").agg(
        pl.col("date").cast(pl.Date).min().alias("first"),
        pl.col("date").cast(pl.Date).max().alias("last"),
    )
    proofs = coverage.to_dicts()
    proven: set[str] = set()
    for row in windows.to_dicts():
        ticker = row["ticker"]
        if not isinstance(ticker, str):
            continue
        for proof in proofs:
            if (
                proof.get("symbol") == ticker
                and proof.get("coverage_basis") == "vendor_events_no_clock"
                and isinstance(proof.get("coverage_start"), date)
                and isinstance(proof.get("coverage_end"), date)
                and proof["coverage_start"] <= row["first"]
                and proof["coverage_end"] >= row["last"]
            ):
                proven.add(ticker)
                break
    return proven


def _require_scope_and_window(frame: pl.DataFrame, actions: pl.DataFrame, coverage: pl.DataFrame | None, market: str, lake_dir: Path | None) -> None:
    """Refuse unless every ticker's action evidence is present and reaches its prices.

    A ticker absent from the action store passes only when a weak proof covers its
    whole price window (ADR-0033); it has no splits, so the factor is 1.
    """
    root = "the canonical lake" if lake_dir is None else Path(lake_dir).as_posix()
    requested = sorted(ticker for ticker in frame["ticker"].unique().to_list() if isinstance(ticker, str))
    stored = set(actions["ticker"].to_list()) if "ticker" in actions.columns else set()
    proven = _proven_empty_tickers(frame, coverage)
    missing = [ticker for ticker in requested if ticker not in stored and ticker not in proven]
    if missing:
        raise SplitReconstructionError(
            "action_scope_incomplete",
            f"no stored corporate actions or proven-empty coverage for {', '.join(missing)} in {root}; a ticker absent "
            "from the action store cannot be told apart from one whose actions are missing, so no price row is written. "
            f"Run `equity corporate-actions --market {market} --tickers {','.join(missing)} --lake-dir <root>` first, "
            "or use `equity widen` (ADR-0033) which proves an empty vendor list before landing such an instrument.",
        )
    if actions.is_empty():
        return

    watermarks = actions.group_by("ticker").agg(pl.col("ex_date").max().alias("action_watermark"))
    priced = frame.filter(pl.col("ticker").is_in(sorted(stored)))
    last_rows = priced.group_by("ticker").agg(pl.col("date").cast(pl.Date).max().alias("price_end")) if not priced.is_empty() else None
    uncovered: list[str] = []
    for row in last_rows.join(watermarks, on="ticker", how="left").sort("ticker").to_dicts() if last_rows is not None else []:
        watermark = row["action_watermark"]
        if not isinstance(watermark, date) or watermark < row["price_end"]:
            uncovered.append(f"{row['ticker']} (prices to {row['price_end']}, actions to {watermark})")
    if uncovered:
        raise SplitReconstructionError(
            "action_window_uncovered",
            f"the action store in {root} does not reach the price window: {'; '.join(uncovered)}. "
            "A split after the stored watermark is undetectable, so its back-adjustment would silently stay "
            f"applied. Re-run `equity corporate-actions --market {market} --lake-dir <root>` to refresh it.",
        )


def _validate_splits(splits: pl.DataFrame) -> None:
    """Refuse a stored split ratio that cannot be inverted (ADR-0029 decision 2)."""
    for row in splits.select("ticker", "ex_date", "value").to_dicts():
        value = row["value"]
        usable = isinstance(value, (int, float)) and isinstance(row["ex_date"], date) and math.isfinite(float(value))
        if not usable or float(value) <= 0.0 or float(value) == 1.0:
            raise SplitReconstructionError(
                "action_invalid",
                f"{row['ticker']} on {row['ex_date']}: stored split ratio {value!r} is not a usable old/new ratio "
                "(expected a finite positive ratio other than 1)",
            )


def _split_factor_frame(frame: pl.DataFrame, splits: pl.DataFrame) -> pl.DataFrame:
    """Scale each row into raw basis; the factor is the product of later split ratios.

    A row dated ``D`` is back-adjusted by the vendor for every split with
    ``ex_date > D``, so the recovering factor is the product of those ratios' stored
    ``old/new`` values — exactly the reciprocal of what the adjusted read multiplies by.
    """
    steps = (
        splits.group_by(["ticker", "ex_date"])
        .agg(pl.col("value").cast(pl.Float64).product().alias("__split_step"))
        .sort(["ticker", "ex_date"])
        .with_columns(pl.col("__split_step").reverse().cum_prod().reverse().over("ticker").alias("__split_factor"))
    )
    keyed = frame.with_row_index("__row_idx").with_columns((pl.col("date").cast(pl.Date) + pl.duration(days=1)).alias("__split_asof"))
    joined = (
        keyed.sort(["ticker", "__split_asof"])
        .join_asof(steps, left_on="__split_asof", right_on="ex_date", by="ticker", strategy="forward", check_sortedness=False)
        .with_columns(pl.col("__split_factor").fill_null(1.0))
        .sort("__row_idx")
    )
    volume_dtype = frame.schema.get("volume")
    scaling = [(pl.col(column) / pl.col("__split_factor")).alias(column) for column in _OHLC_COLUMNS if column in frame.columns]
    if volume_dtype is not None:
        volume = pl.col("volume") * pl.col("__split_factor")
        if volume_dtype.is_integer():
            # Vendor back-adjusted volumes are fractional; a raw share count is not.
            volume = volume.round().cast(volume_dtype)
        scaling.append(volume.alias("volume"))
    scaled = joined.with_columns(scaling)
    return scaled.select(frame.columns)


def reconstruct_vendor_prices(
    df: FrameLike,
    *,
    market: str = "us_equity",
    lake_dir: Path | None = None,
    actions: pl.DataFrame | None = None,
    coverage: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Return *df* in raw basis, or refuse when its splits cannot be proven (ADR-0029).

    Args:
        df: The frame fetched for one trading date (or a multi-date range).
        market: Price market whose Silver actions are the transform input.
        lake_dir: Target root; its own action store is read, never the canonical one.
        actions: Pre-read action frame (tests, or a caller that already has one).
        coverage: Pre-read coverage frame; loaded with the actions when omitted. A weak
            proof covering the price rows lets an action-free instrument pass (ADR-0033).

    Returns:
        The same rows with OHLC divided by, and volume multiplied by, the cumulative
        ratio of splits after each row's date. ``adj_close`` and every other column are
        untouched, and the column order is preserved.

    Raises:
        SplitReconstructionError: Any of the fail-closed reasons in the module docstring.
    """
    frame = ensure_polars(df)
    if frame.is_empty():
        return frame
    if "ticker" not in frame.columns or "date" not in frame.columns:
        raise SplitReconstructionError("frame_incomplete", "a price frame without ticker/date columns cannot be reconstructed")

    if actions is None:
        from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs

        history, stored_coverage = load_corporate_action_inputs(market, lake_dir=lake_dir)
        if coverage is None:
            coverage = stored_coverage
    else:
        history = actions
    if history.is_empty():
        requested = {ticker for ticker in frame["ticker"].unique().to_list() if isinstance(ticker, str)}
        proven = _proven_empty_tickers(frame, coverage)
        if requested - proven:
            root = "the canonical lake" if lake_dir is None else Path(lake_dir).as_posix()
            raise SplitReconstructionError(
                "action_store_missing",
                f"no stored corporate actions for {market} in {root}; vendor prices are split-back-adjusted and must not "
                f"be written unlabelled. Run `equity corporate-actions --market {market} --lake-dir <root>` first.",
            )
    _require_scope_and_window(frame, history, coverage, market, lake_dir)
    splits = history.filter(pl.col("action") == SPLIT_ACTION)
    _validate_splits(splits)
    if splits.is_empty():
        logger.info("split_reconstruction_no_splits", market=market, rows=frame.height, tickers=frame["ticker"].n_unique())
        return frame

    reconstructed = _split_factor_frame(frame, splits)
    logger.info(
        "split_reconstruction_applied",
        market=market,
        rows=frame.height,
        tickers=frame["ticker"].n_unique(),
        splits=splits.height,
        root="canonical" if lake_dir is None else Path(lake_dir).as_posix(),
    )
    return reconstructed
