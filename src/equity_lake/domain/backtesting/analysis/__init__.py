"""
Analysis layer for backtesting.

This package provides performance metrics, attribution analysis,
and report generation.
"""

from equity_lake.domain.backtesting.analysis.attribution import AttributionAnalyzer
from equity_lake.domain.backtesting.analysis.metrics import (
    PerformanceMetrics,
    compute_quick_metrics,
)
from equity_lake.domain.backtesting.analysis.reports import ReportGenerator

__all__ = [
    "AttributionAnalyzer",
    "PerformanceMetrics",
    "compute_quick_metrics",
    "ReportGenerator",
]
