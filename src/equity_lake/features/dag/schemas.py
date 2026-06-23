"""Pydantic models for medallion layer boundary validation.

These models define the expected schema at each layer transition.
They are used for row-level sampling validation in addition to the
Hamilton ``@check_output`` decorator's built-in validators.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class OHLCVCleanModel(BaseModel):
    """Silver boundary: validated OHLCV row."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticker: str
    date: date | datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class FeatureModel(BaseModel):
    """Gold boundary: validated feature row (key indicators)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticker: str
    date: date | datetime
    close: float
    rsi_14: float = Field(ge=0, le=100)
    macd: float
    volume: float = Field(ge=0)


class PredictionModel(BaseModel):
    """Platinum boundary: validated prediction output."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticker: str
    date: date
    direction: str = Field(description="up | down")
    probability: float = Field(ge=0.0, le=1.0)
    model_version: str
    model_mode: str = ""
    feature_schema_version: int = 3
