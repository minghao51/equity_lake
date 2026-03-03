"""Signal scanner orchestrator."""

from datetime import date, timedelta
from typing import List, Optional

from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.formatters.markdown import MarkdownFormatter
from equity_lake.signals.formatters.terminal import TerminalFormatter
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator
from equity_lake.signals.history import save_signals_to_parquet
from equity_lake.signals.models import Signal, SignalConfig, Watchlist


class SignalScanner:
    """Main orchestrator for scanning watchlist and generating signals."""

    def __init__(self, config: SignalConfig, watchlist: Watchlist):
        """Initialize scanner with config and watchlist.

        Args:
            config: Signal configuration
            watchlist: Tickers to scan
        """
        self.config = config
        self.watchlist = watchlist

        # Initialize generators
        self.generators: List[SignalGenerator] = []
        if config.is_generator_enabled("backtest"):
            self.generators.append(BacktestSignalGenerator(config.backtest))
        if config.is_generator_enabled("sentiment"):
            self.generators.append(SentimentSignalGenerator(config.sentiment))
        if config.is_generator_enabled("ml"):
            self.generators.append(MLPredictionSignalGenerator(config.ml))

        # Initialize formatters
        self.formatters = {
            "json": JSONFormatter(),
            "md": MarkdownFormatter(),
            "table": TerminalFormatter(),
        }

    def scan(self, target_date: Optional[date] = None) -> List[Signal]:
        """Scan all tickers and return aggregated signals.

        Args:
            target_date: Date to generate signals for (default: yesterday)

        Returns:
            List of Signal objects
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)

        all_signals = []

        # Scan each ticker
        for ticker in self.watchlist.tickers:
            ticker_signals = self._scan_ticker(ticker, target_date)
            if ticker_signals:
                all_signals.extend(ticker_signals)

        return all_signals

    def _scan_ticker(self, ticker: str, target_date: date) -> List[Signal]:
        """Scan a single ticker with all generators.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signals for

        Returns:
            List of Signal objects for this ticker
        """
        signals = []

        for generator in self.generators:
            try:
                signal = generator.generate(ticker, target_date)
                if signal:
                    signals.append(signal)
            except Exception as e:
                # Log but continue with other generators
                print(f"Warning: {generator.__class__.__name__} failed for {ticker}: {e}")
                continue

        return signals

    def format_signals(self, signals: List[Signal], format: str = "table") -> str:
        """Format signals for output.

        Args:
            signals: List of Signal objects
            format: Output format (json, md, table)

        Returns:
            Formatted string
        """
        formatter = self.formatters.get(format)
        if not formatter:
            raise ValueError(f"Unknown format: {format}. Use: json, md, table")

        return formatter.format(signals)

    def save_history(self, signals: List[Signal]):
        """Save signals to Parquet history.

        Args:
            signals: List of Signal objects to save
        """
        if not signals:
            return

        target_date = signals[0].date
        save_signals_to_parquet(signals, target_date)
