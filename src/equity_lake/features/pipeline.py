"""Hamilton-backed feature pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
from hamilton import base
from hamilton.plugins import h_polars

from equity_lake.core.polars_utils import FrameLike, ensure_polars

FEATURE_SCHEMA_VERSION = 2


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

        adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
        builder = driver.Builder().with_modules(hamilton_features).with_adapter(adapter)
        if self.enable_cache:
            builder = builder.with_cache()
        return builder.build()

    def compute(
        self,
        price_data: FrameLike,
        features: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> pl.DataFrame:
        """Compute a feature frame for one ticker's price history."""
        requested = features or self.DEFAULT_FEATURES
        execution_inputs = {"price_data": ensure_polars(price_data)}
        if inputs:
            execution_inputs.update(inputs)

        result = self._driver.execute(requested, inputs=execution_inputs)
        frame = result if isinstance(result, pl.DataFrame) else pl.DataFrame(result)
        if "open_price" in frame.columns:
            frame = frame.rename({"open_price": "open"})
        frame = frame.with_columns(pl.lit(FEATURE_SCHEMA_VERSION).alias("feature_schema_version"))
        return frame

    def export_lineage(self, output_path: str | Path | None = None) -> str | None:
        """Export the DAG lineage as a PNG image.

        Requires ``graphviz`` to be installed on the system.
        Returns the output path, or None if export failed.
        """
        if output_path is None:
            output_path = "docs/architecture/pipeline_lineage.png"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._driver.display_all_functions(str(output_path))
            return str(output_path)
        except Exception as exc:
            import structlog

            structlog.get_logger().warning("lineage_export_failed", error=str(exc))
            return None


def compute_features(
    price_data: FrameLike,
    features: list[str] | None = None,
) -> pl.DataFrame:
    """Compute features from price data using the default pipeline."""
    return FeaturePipeline().compute(price_data=price_data, features=features)


__all__ = ["FeaturePipeline", "compute_features"]
