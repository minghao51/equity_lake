"""Helpers for working across pandas and Polars during migration."""

from __future__ import annotations

import pandas as pd
import polars as pl

type FrameLike = pd.DataFrame | pl.DataFrame


def ensure_polars(df: FrameLike) -> pl.DataFrame:
    """Return a Polars DataFrame regardless of the caller's frame type."""
    if isinstance(df, pl.DataFrame):
        return df
    if isinstance(df, pd.DataFrame):
        return pl.from_pandas(df)
    raise TypeError(f"Unsupported dataframe type: {type(df)!r}")


def normalize_temporal_columns(df: FrameLike, *, date_columns: tuple[str, ...] = (), datetime_columns: tuple[str, ...] = ()) -> pl.DataFrame:
    """Parse string temporal columns while preserving already-typed values."""
    frame = ensure_polars(df)
    expressions: list[pl.Expr] = []

    for column in date_columns:
        if column in frame.columns and frame.schema[column] == pl.Utf8:
            expressions.append(pl.col(column).str.to_date(strict=False).alias(column))

    for column in datetime_columns:
        if column in frame.columns and frame.schema[column] == pl.Utf8:
            expressions.append(pl.col(column).str.to_datetime(strict=False).alias(column))

    if expressions:
        return frame.with_columns(expressions)
    return frame


def frame_is_empty(df: FrameLike | None) -> bool:
    """Return whether a pandas or Polars frame has no rows."""
    if df is None:
        return True
    if isinstance(df, pd.DataFrame):
        return bool(df.empty)
    if isinstance(df, pl.DataFrame):
        return bool(df.is_empty())
    raise TypeError(f"Unsupported dataframe type: {type(df)!r}")
