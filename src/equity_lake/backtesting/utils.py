"""Utilities for backtesting operations."""

import pandas as pd


def extract_field_from_maybe_multiindex(
    data: pd.DataFrame,
    field: str = "close",
) -> pd.DataFrame:
    """
    Extract field column from DataFrame that may or may not be MultiIndex.

    This utility handles the common pattern of extracting price fields
    from DataFrames that may have a MultiIndex structure (ticker, field)
    or a simple single-level column structure.

    Args:
        data: DataFrame with potential MultiIndex columns
        field: Field name to extract (default: "close")

    Returns:
        DataFrame with the specified field extracted

    Examples:
        >>> # MultiIndex DataFrame
        >>> df = pd.DataFrame(...)
        >>> close_df = extract_field_from_maybe_multiindex(df, "close")
        >>>
        >>> # Simple DataFrame
        >>> simple_df = extract_field_from_maybe_multiindex(df, "close")
    """
    return data.xs(field, level="field", axis=1) if isinstance(data.columns, pd.MultiIndex) else data


__all__ = ["extract_field_from_maybe_multiindex"]
