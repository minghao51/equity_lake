"""Hamilton-backed feature pipeline."""

from __future__ import annotations

from typing import Any

import pandas as pd


class FeaturePipeline:
    """Declarative feature pipeline powered by Hamilton."""

    DEFAULT_FEATURES = [
        "ticker",
        "date",
        "open_price",
        "high",
        "low",
        "close",
        "volume",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_histogram",
        "bb_upper",
        "bb_middle",
        "bb_lower",
        "bb_width",
        "bb_pct",
        "atr_14",
        "roc_5",
        "roc_10",
        "roc_20",
        "return_1d",
        "return_5d",
        "return_10d",
        "return_20d",
        "overnight_return",
        "intraday_return",
        "hl_range",
        "volume_ma_20",
        "volume_roc_5",
        "obv",
        "volume_ratio",
        "day_of_week",
        "day_of_month",
        "month",
        "quarter",
        "days_to_month_end",
        "trading_day_of_month",
        "volatility_20",
        "next_day_return",
    ]

    def __init__(self, enable_cache: bool = False):
        self.enable_cache = enable_cache
        self._driver = self._build_driver()

    def _build_driver(self) -> Any:
        from hamilton import driver

        from equity_lake.features import hamilton_features

        builder = driver.Builder().with_modules(hamilton_features)
        if self.enable_cache:
            builder = builder.with_cache()
        return builder.build()

    def compute(
        self,
        price_data: pd.DataFrame,
        features: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """Compute a feature frame for one ticker's price history."""
        requested = features or self.DEFAULT_FEATURES
        execution_inputs = {"price_data": price_data}
        if inputs:
            execution_inputs.update(inputs)

        result = self._driver.execute(requested, inputs=execution_inputs)
        frame = pd.DataFrame(result)
        if "open_price" in frame.columns:
            frame = frame.rename(columns={"open_price": "open"})
        return frame


def compute_features(
    price_data: pd.DataFrame,
    features: list[str] | None = None,
) -> pd.DataFrame:
    """Compute features from price data using the default pipeline."""
    return FeaturePipeline().compute(price_data=price_data, features=features)


__all__ = ["FeaturePipeline", "compute_features"]
