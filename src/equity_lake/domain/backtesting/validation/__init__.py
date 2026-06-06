"""
Validation layer for backtesting.

This package provides walk-forward validation and overfitting detection.
"""

from equity_lake.domain.backtesting.validation.overfitting import (
    OverfittingDetector,
    OverfittingReport,
)
from equity_lake.domain.backtesting.validation.walk_forward import (
    WalkForwardResult,
    WalkForwardValidator,
)

__all__ = [
    "WalkForwardValidator",
    "WalkForwardResult",
    "OverfittingDetector",
    "OverfittingReport",
]
