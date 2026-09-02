from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl
import structlog

logger = structlog.get_logger(__name__)

AdjustmentMethod = Literal["split_only", "total_return"]

_OHLC_COLUMNS = ("open", "high", "low", "close")
_METHODS: tuple[str, ...] = ("split_only", "total_return")


def ensure_delta_extension(con: object) -> None:
    """Idempotently install+load the DuckDB delta extension on *con*.

    Single home for the ``INSTALL delta; LOAD delta;`` bootstrap previously
    hand-rolled at ~10 call sites (storage, ingestion, monitoring, backtesting,
    ml/features). Cheap when already loaded; safe to call per-connection.
    """
    con.execute("INSTALL delta; LOAD delta;")  # type: ignore[attr-defined]


def create_market_views(
    con: object,
    *,
    view_prefix: str = "",
    columns: list[str] | None = None,
) -> list[str]:
    """Create one per-market view over the price-market registry (ADR-0010).

    Single home for the per-market ``CREATE OR REPLACE VIEW`` loop previously
    duplicated in ``storage/duckdb.py`` (unified query views, ``view_prefix=""``,
    all columns via ``SELECT *``) and ``backtesting/data_loader.py`` (backtest-
    prefixed OHLCV views, ``view_prefix="backtest_"``, projected to the OHLCV
    columns). ``columns=None`` selects every column; otherwise the view projects
    exactly ``columns``. Returns the names of the views actually created.
    """
    from equity_lake.core.paths import PRICE_MARKETS, market_dir

    ensure_delta_extension(con)
    created: list[str] = []
    for market in PRICE_MARKETS:
        data_dir = market_dir(market)
        if not data_dir.exists():
            logger.warning("Data directory not found, skipping view", market=market, path=str(data_dir))
            continue
        view_name = f"{view_prefix}{market}"
        scan = duckdb_scan_for(data_dir)
        col_expr = "*" if columns is None else ", ".join(columns)
        sql = f"CREATE OR REPLACE VIEW {view_name} AS SELECT {col_expr}, '{market}' as market FROM {scan}"
        try:
            con.execute(sql)  # type: ignore[attr-defined]
            created.append(view_name)
            logger.debug("Created market view", view=view_name)
        except Exception as e:
            logger.error("Failed to create view", view=view_name, error=str(e))
    return created


def duckdb_scan_for(market_path: Path) -> str:
    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(market_path)):
            return f"delta_scan('{market_path}')"
    except (ImportError, OSError, ValueError):
        pass
    return f"read_parquet('{market_path}/**/*.parquet', hive_partitioning=1)"


def _dividend_factor_steps(prices: pl.DataFrame, dividends: pl.DataFrame) -> pl.DataFrame:
    """Factor step per dividend event: ``1 - value / prev_close`` (CRSP-style).

    ``prev_close`` is the last close strictly before the ex-date (the ex-date's
    own close is post-event and must never feed the step). Events without a
    prior close are dropped with a warning rather than guessed.
    """
    prev_close = prices.select(pl.col("ticker"), pl.col("date"), pl.col("close")).sort("date")
    steps = (
        dividends.with_columns((pl.col("ex_date") - pl.duration(days=1)).alias("_asof_key"))
        .sort("_asof_key")
        .join_asof(
            prev_close.rename({"date": "_prev_date"}).sort("_prev_date"),
            left_on="_asof_key",
            right_on="_prev_date",
            by="ticker",
            strategy="backward",
            check_sortedness=False,
        )
        .drop_nulls("close")
        .filter(pl.col("close") > 0)
        .select(pl.col("ticker"), pl.col("ex_date"), (1.0 - pl.col("value") / pl.col("close")).alias("factor_step"))
    )
    dropped = dividends.height - steps.height
    if dropped:
        logger.warning("dividend_events_dropped_no_prior_close", count=dropped)
    return steps


def _factor_on_expr() -> pl.Expr:
    """Expression: product of ``factor_step`` over this-and-later events, per ticker.

    Frame must be sorted by ``(ticker, ex_date)``. The reverse cumulative
    product gives self-and-later. Prices are forward-asof-joined on
    ``date + 1 day`` — the first event strictly after a price row — so the
    matched event's own step is included and an ex-date's own row is never
    adjusted (its close is already in post-event terms).
    """
    return pl.col("factor_step").reverse().cum_prod().reverse().over("ticker").fill_null(1.0).alias("factor_on")


def with_price_adjustment(
    prices: pl.DataFrame,
    actions: pl.DataFrame,
    *,
    method: AdjustmentMethod = "split_only",
    as_of: date | None = None,
) -> pl.DataFrame:
    """Back-adjust OHLC prices for corporate actions at read time (ADR-0011).

    Stored prices stay raw; adjustment is applied per row from the event
    history. ``split_only`` (default) fixes split discontinuities only;
    ``total_return`` additionally reinvests dividends via
    ``1 - dividend / prev_close`` steps. ``as_of`` restricts adjustment to
    events knowable by that date (point-in-time reads).

    Args:
        prices: OHLCV frame with ``ticker`` and ``date`` columns; must not
            already contain an ``ex_date`` column. ``volume`` and
            ``adj_close`` are never modified.
        actions: Event frame with ``ticker``, ``ex_date``, ``action``
            (``dividend`` | ``split``), ``value`` (cash dividend per share or
            split ratio, e.g. ``0.5`` = 2-for-1).
        method: ``split_only`` or ``total_return``.
        as_of: Drop events with ``ex_date`` after this date when given.

    Returns:
        A new frame in the original row order with OHLC columns scaled by the
        cumulative adjustment factor. Frames without any OHLC column, and
        frames with no applicable events, are returned unchanged.
    """
    if method not in _METHODS:
        raise ValueError(f"Unknown adjustment method: {method!r} (expected one of {_METHODS})")
    out = prices.clone()
    for col in ("ticker", "date"):
        if col not in out.columns:
            raise ValueError(f"prices frame missing required column: {col}")
    scale_cols = [c for c in _OHLC_COLUMNS if c in out.columns]
    if out.is_empty() or not scale_cols:
        return out

    for col in ("ticker", "ex_date", "action", "value"):
        if col not in actions.columns:
            raise ValueError(f"actions frame missing required column: {col}")
    events = actions.clone()
    if as_of is not None:
        events = events.filter(pl.col("ex_date") <= as_of)
    events = events.filter(pl.col("action").is_in(["split", "dividend"]))
    if events.is_empty():
        return out

    splits = events.filter(pl.col("action") == "split").select(pl.col("ticker"), pl.col("ex_date"), pl.col("value").alias("factor_step"))
    steps = splits
    if method == "total_return":
        dividends = events.filter(pl.col("action") == "dividend").select(pl.col("ticker"), pl.col("ex_date"), pl.col("value"))
        if not dividends.is_empty():
            steps = pl.concat([splits, _dividend_factor_steps(out, dividends)], how="vertical")
    if steps.is_empty():
        return out

    # Collapse same-day events (e.g. split + dividend on one ex-date) so the
    # asof join can never silently drop one of their steps.
    steps = steps.group_by(["ticker", "ex_date"]).agg(pl.col("factor_step").product()).sort(["ticker", "ex_date"]).with_columns(_factor_on_expr())
    indexed = (
        out.with_row_index("_adjust_row_idx").with_columns((pl.col("date") + pl.duration(days=1)).alias("_asof_key")).sort(["ticker", "_asof_key"])
    )
    adjusted = (
        indexed.join_asof(steps, by="ticker", left_on="_asof_key", right_on="ex_date", strategy="forward", check_sortedness=False)
        .with_columns(pl.col("factor_on").fill_null(1.0))
        .with_columns([pl.col(c) * pl.col("factor_on") for c in scale_cols])
    )
    drop_cols = ["_adjust_row_idx", "factor_on", "_asof_key"]
    if "ex_date" not in out.columns:
        drop_cols.append("ex_date")
    return adjusted.sort("_adjust_row_idx").drop(drop_cols)


def factor_snapshot(actions: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """Cumulative split factor per ticker as of *as_of* (split-only).

    Dividend factors need price context, so the snapshot covers splits only —
    the deterministic, assumption-free adjustment (ADR-0011 default).
    """
    for col in ("ticker", "ex_date", "action", "value"):
        if col not in actions.columns:
            raise ValueError(f"actions frame missing required column: {col}")
    return (
        actions.filter((pl.col("ex_date") <= as_of) & (pl.col("action") == "split"))
        .group_by("ticker")
        .agg(pl.col("value").product().alias("factor"))
        .sort("ticker")
    )
