"""
Configuration models for backtesting.

This module provides Pydantic models for backtesting configuration.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class BacktestConfig(BaseModel):
    """Backtesting configuration."""

    # Strategy settings
    strategy_name: str = Field(..., description="Strategy name")
    strategy_params: dict[str, Any] = Field(
        default_factory=dict, description="Strategy parameters"
    )

    # Data settings
    start_date: date = Field(..., description="Backtest start date")
    end_date: date = Field(..., description="Backtest end date")
    tickers: list[str] = Field(..., description="Ticker symbols")
    markets: list[str] = Field(
        default=["us", "cn", "hk_sg"], description="Markets to query"
    )

    # Execution settings
    initial_cash: float = Field(default=100_000.0, description="Starting capital")
    commission_rate: float = Field(default=0.001, description="Commission rate")
    slippage_rate: float = Field(default=0.0001, description="Slippage rate")

    # Validation settings
    use_walk_forward: bool = Field(
        default=False, description="Use walk-forward validation"
    )
    train_size: int = Field(default=252, description="Training size (days)")
    test_size: int = Field(default=63, description="Test size (days)")

    # Output settings
    output_path: str | None = Field(default=None, description="Output file path")
    verbose: bool = Field(default=False, description="Verbose logging")


__all__ = ["BacktestConfig"]
