"""Signal generators for different data sources."""

from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator

__all__ = [
    "SignalGenerator",
    "BacktestSignalGenerator",
    "SentimentSignalGenerator",
    "MLPredictionSignalGenerator",
]
