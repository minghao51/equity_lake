"""Hamilton-backed feature pipeline (medallion-layered DAG).

The driver assembles four layered modules into a single DAG:

- ``dag.raw_01``       — Bronze: OHLCV column extraction
- ``dag.clean_02``     — Silver: basic transforms (returns)
- ``dag.features_03``  — Gold: technical indicators
- ``dag.enrichments_04`` — Gold: external data joins

Callers use :meth:`compute_technical` for per-ticker indicators and
:meth:`compute_enriched` for batch external-data merges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
from hamilton import base
from hamilton.plugins import h_polars

from equity_lake.core.polars_utils import FrameLike, ensure_polars

FEATURE_SCHEMA_VERSION = 3


class FeaturePipeline:
    """Declarative feature pipeline powered by Hamilton."""

    TECHNICAL_FEATURES = [
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
    ]

    TARGET_FEATURES = ["next_day_return"]

    DEFAULT_FEATURES = TECHNICAL_FEATURES

    def __init__(self, enable_cache: bool = False):
        self.enable_cache = enable_cache
        self._driver = self._build_driver()

    def _build_driver(self) -> Any:
        from hamilton import driver

        from equity_lake.features.dag import (
            clean_02,
            enrichments_04,
            features_03,
            raw_01,
        )

        adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
        builder = driver.Builder().with_modules(raw_01, clean_02, features_03, enrichments_04).with_adapter(adapter)
        if self.enable_cache:
            builder = builder.with_cache()
        return builder.build()

    def compute_technical(
        self,
        price_data: FrameLike,
        features: list[str] | None = None,
        include_target: bool = False,
    ) -> pl.DataFrame:
        """Compute technical indicator features for one ticker's price history.

        This is Phase 1 of the two-phase execution model — runs per-ticker.

        Args:
            price_data: OHLCV DataFrame for a single ticker.
            features: Override list of feature node names to compute.
                Defaults to :attr:`TECHNICAL_FEATURES`.
            include_target: If True, append :attr:`TARGET_FEATURES`
                (``next_day_return``) to the requested features.
        """
        requested = list(features or self.TECHNICAL_FEATURES)
        if include_target:
            requested = requested + self.TARGET_FEATURES
        execution_inputs: dict[str, Any] = {"price_data": ensure_polars(price_data)}

        result = self._driver.execute(requested, inputs=execution_inputs)
        frame = result if isinstance(result, pl.DataFrame) else pl.DataFrame(result)
        if "open_price" in frame.columns:
            frame = frame.rename({"open_price": "open"})
        frame = frame.with_columns(pl.lit(FEATURE_SCHEMA_VERSION).alias("feature_schema_version"))
        return frame

    def compute_enriched(
        self,
        features_df: pl.DataFrame,
        *,
        duckdb_conn: Any,
        start_date: Any,
        end_date: Any,
        enable_news_sentiment: bool = False,
        enable_social_sentiment: bool = False,
        enable_enriched_sentiment: bool = False,
        enable_analyst_ratings: bool = False,
        enable_sec_features: bool = False,
        enable_macro: bool = True,
    ) -> pl.DataFrame:
        """Apply external data enrichments to a batch feature frame.

        This is Phase 2 of the two-phase execution model — runs once for
        all tickers after technical indicators have been computed.
        """
        result = self._driver.execute(
            ["enriched_features"],
            inputs={
                "features_df": features_df,
                "duckdb_conn": duckdb_conn,
                "start_date": start_date,
                "end_date": end_date,
                "enable_news_sentiment": enable_news_sentiment,
                "enable_social_sentiment": enable_social_sentiment,
                "enable_enriched_sentiment": enable_enriched_sentiment,
                "enable_analyst_ratings": enable_analyst_ratings,
                "enable_sec_features": enable_sec_features,
                "enable_macro": enable_macro,
            },
        )
        return result if isinstance(result, pl.DataFrame) else pl.DataFrame(result)

    def compute(
        self,
        price_data: FrameLike,
        features: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> pl.DataFrame:
        """Backward-compatible alias for :meth:`compute_technical`."""
        return self.compute_technical(price_data=price_data, features=features)

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
    return FeaturePipeline().compute_technical(price_data=price_data, features=features)


__all__ = ["FeaturePipeline", "compute_features"]
