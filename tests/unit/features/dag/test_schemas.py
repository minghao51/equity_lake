"""Tests for boundary validation schemas and pointblank Platinum validation."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from pydantic import ValidationError

from equity_lake.features.dag.schemas import FeatureModel, OHLCVCleanModel, PredictionModel
from equity_lake.ml import validate_predictions

# ---------------------------------------------------------------------------
# OHLCVCleanModel (Silver boundary)
# ---------------------------------------------------------------------------


def test_ohlcv_clean_valid() -> None:
    model = OHLCVCleanModel(
        ticker="AAPL",
        date=date(2024, 1, 1),
        open=150.0,
        high=155.0,
        low=148.0,
        close=152.0,
        volume=1_000_000.0,
    )
    assert model.ticker == "AAPL"


def test_ohlcv_clean_rejects_negative_close() -> None:
    with pytest.raises(ValidationError):
        OHLCVCleanModel(
            ticker="AAPL",
            date=date(2024, 1, 1),
            open=150.0,
            high=155.0,
            low=148.0,
            close=-1.0,
            volume=1_000_000.0,
        )


def test_ohlcv_clean_rejects_negative_volume() -> None:
    with pytest.raises(ValidationError):
        OHLCVCleanModel(
            ticker="AAPL",
            date=date(2024, 1, 1),
            open=150.0,
            high=155.0,
            low=148.0,
            close=152.0,
            volume=-100.0,
        )


# ---------------------------------------------------------------------------
# FeatureModel (Gold boundary)
# ---------------------------------------------------------------------------


def test_feature_model_valid() -> None:
    model = FeatureModel(
        ticker="AAPL",
        date=date(2024, 1, 1),
        close=152.0,
        rsi_14=55.5,
        macd=0.25,
        volume=1_000_000.0,
    )
    assert model.rsi_14 == 55.5


def test_feature_model_rejects_rsi_out_of_range() -> None:
    with pytest.raises(ValidationError):
        FeatureModel(
            ticker="AAPL",
            date=date(2024, 1, 1),
            close=152.0,
            rsi_14=150.0,
            macd=0.25,
            volume=1_000_000.0,
        )


# ---------------------------------------------------------------------------
# PredictionModel (Platinum boundary)
# ---------------------------------------------------------------------------


def test_prediction_model_valid() -> None:
    model = PredictionModel(
        ticker="AAPL",
        date=date(2024, 1, 1),
        direction="up",
        probability=0.65,
        model_version="v1",
    )
    assert model.direction == "up"


def test_prediction_model_rejects_invalid_probability() -> None:
    with pytest.raises(ValidationError):
        PredictionModel(
            ticker="AAPL",
            date=date(2024, 1, 1),
            direction="up",
            probability=1.5,
            model_version="v1",
        )


# ---------------------------------------------------------------------------
# pointblank Platinum validation
# ---------------------------------------------------------------------------


def test_validate_predictions_valid() -> None:
    df = pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 1, 1), date(2024, 1, 1)],
            "direction": ["up", "down"],
            "probability": [0.65, 0.30],
        }
    )
    assert validate_predictions(df) is True


def test_validate_predictions_catches_bad_probability() -> None:
    df = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "direction": ["up"],
            "probability": [1.5],
        }
    )
    assert validate_predictions(df) is False


def test_validate_predictions_catches_bad_direction() -> None:
    df = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "direction": ["sideways"],
            "probability": [0.5],
        }
    )
    assert validate_predictions(df) is False
