"""Library-facing orchestration helpers used by the CLI wrappers."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from equity_lake.core.paths import LAKE_DIR
from equity_lake.features import run_feature_job
from equity_lake.ingestion.orchestrator import run_daily_ingestion
from equity_lake.ml import run_prediction_job


def run_ingestion_stage(
    trading_date: date,
    markets: list[str],
    dry_run: bool = False,
) -> dict[str, bool]:
    """Run the ingestion stage directly in-process."""
    return run_daily_ingestion(
        trading_date=trading_date,
        markets=markets,
        dry_run=dry_run,
        parallel=True,
    )


def run_feature_stage(
    trading_date: date,
    tickers: list[str],
    output_dir: Path | None = None,
    compute_target: bool = True,
) -> pd.DataFrame:
    """Generate and persist features for the requested trading date."""
    output_path = output_dir or (LAKE_DIR / "features")
    return run_feature_job(
        tickers=tickers,
        output_start_date=trading_date,
        output_end_date=trading_date,
        output_dir=output_path,
        compute_target=compute_target,
    )


def run_ml_inference_stage(
    trading_date: date,
    tickers: list[str],
    model_dir: str | None = None,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Run price-forecast inference for each ticker."""
    return run_prediction_job(
        trading_date=trading_date,
        tickers=tickers,
        model_dir=model_dir,
    )
