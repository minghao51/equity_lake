"""R3: training summaries must not report unmeasured validation as a zero.

``_build_training_summary`` conflated "trained" (an artifact exists) with
"validated" (purged walk-forward metrics exist); both CLI formatters then
rendered the missing metrics as ``0.000``. These tests pin the separation and
the two render paths.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from equity_lake.cli.commands.intelligence import _format_training_summary  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.ml.forecasting import PriceForecaster  # type: ignore[import-untyped]


@pytest.fixture()
def forecaster(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[PriceForecaster]:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    instance = PriceForecaster(model_dir=str(tmp_path))
    try:
        yield instance
    finally:
        instance.close()


def _summary(forecaster: PriceForecaster, tmp_path: Path, *, validation_metrics: dict[str, Any] | None, labelled_rows: int) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        forecaster._build_training_summary(
            ticker="AAPL",
            instrument=InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"),
            trained_on=date(2024, 4, 30),
            model_path=tmp_path / "model.pkl",
            training_start=date(2024, 1, 2),
            train_rows=40,
            val_rows=10,
            labelled_rows=labelled_rows,
            validation_metrics=validation_metrics,
        ),
    )


def test_zero_fold_summary_withholds_metrics_and_reports_unavailable(forecaster: PriceForecaster, tmp_path: Path) -> None:
    summary = _summary(forecaster, tmp_path, validation_metrics={"folds": 0}, labelled_rows=60)

    assert summary["status"] == "trained"
    assert summary["validation_fold_count"] == 0
    assert summary["validation_status"] == "unavailable"
    assert "mean_accuracy" not in summary
    assert "mean_precision" not in summary
    assert "mean_recall" not in summary
    reason = summary["validation_unavailable_reason"]
    assert "no purged walk-forward fold" in reason
    assert "60 labelled rows" in reason


def test_validation_not_requested_does_not_blame_the_folds(forecaster: PriceForecaster, tmp_path: Path) -> None:
    """``validate=False`` never attempts a fold, so the reason must not imply one failed to fit."""
    summary = _summary(forecaster, tmp_path, validation_metrics=None, labelled_rows=60)

    assert summary["validation_status"] == "unavailable"
    assert "mean_accuracy" not in summary
    reason = summary["validation_unavailable_reason"]
    assert "not requested" in reason
    assert "fold fits" not in reason


def test_verified_summary_carries_measured_means(forecaster: PriceForecaster, tmp_path: Path) -> None:
    measured = {"folds": 2, "mean_accuracy": 0.61, "mean_precision": 0.58, "mean_recall": 0.63}

    summary = _summary(forecaster, tmp_path, validation_metrics=measured, labelled_rows=60)

    assert summary["status"] == "trained"
    assert summary["validation_fold_count"] == 2
    assert summary["validation_status"] == "verified"
    assert summary["validation_unavailable_reason"] is None
    assert summary["mean_accuracy"] == measured["mean_accuracy"]
    assert summary["mean_precision"] == measured["mean_precision"]
    assert summary["mean_recall"] == measured["mean_recall"]


def test_format_training_summary_renders_unavailable_without_metrics() -> None:
    rendered = _format_training_summary(
        {
            "ticker": "AAPL",
            "trained_on": "2024-04-30",
            "model_mode": "v1_direction",
            "status": "trained",
            "train_rows": 40,
            "validation_rows": 10,
            "validation_fold_count": 0,
            "validation_status": "unavailable",
            "validation_unavailable_reason": "no purged walk-forward fold fits in 60 labelled rows",
        }
    )

    assert "validation: unavailable (no purged walk-forward fold fits in 60 labelled rows)" in rendered
    assert "Mean accuracy" not in rendered
    assert "Mean precision" not in rendered
    assert "Mean recall" not in rendered


def test_format_training_summary_renders_metrics_when_verified() -> None:
    rendered = _format_training_summary(
        {
            "ticker": "AAPL",
            "trained_on": "2024-04-30",
            "model_mode": "v1_direction",
            "status": "trained",
            "train_rows": 40,
            "validation_rows": 10,
            "validation_fold_count": 2,
            "validation_status": "verified",
            "validation_unavailable_reason": None,
            "mean_accuracy": 0.61,
            "mean_precision": 0.58,
            "mean_recall": 0.63,
        }
    )

    assert "Mean accuracy: 0.610" in rendered
    assert "Mean precision: 0.580" in rendered
    assert "Mean recall: 0.630" in rendered
    assert "unavailable" not in rendered
