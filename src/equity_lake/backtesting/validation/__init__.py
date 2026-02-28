"""
Validation layer for backtesting.

This package provides walk-forward validation and overfitting detection.
"""

from equity_lake.backtesting.validation.overfitting import OverfittingDetector, OverfittingReport
from equity_lake.backtesting.validation.walk_forward import WalkForwardResult, WalkForwardValidator

__all__ = [
    "WalkForwardValidator",
    "WalkForwardResult",
    "OverfittingDetector",
    "OverfittingReport",
]
