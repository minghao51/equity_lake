"""Signal scanner orchestrator."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.formatters.markdown import MarkdownFormatter
from equity_lake.signals.formatters.terminal import TerminalFormatter
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.generators.meta_label import MetaLabelSignalGenerator
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator
from equity_lake.signals.history import save_signals
from equity_lake.signals.models import Signal, SignalConfig, Watchlist


class SignalScanner:
    """Main orchestrator for scanning watchlist and generating signals."""

    def __init__(self, config: SignalConfig, watchlist: Watchlist, max_workers: int = 4):
        """Initialize scanner with config and watchlist.

        Args:
            config: Signal configuration
            watchlist: Tickers to scan
            max_workers: Thread pool size for parallel scanning
        """
        self.config = config
        self.watchlist = watchlist
        self.max_workers = max_workers

        # Initialize generators
        self.generators: list[SignalGenerator] = []
        if config.is_generator_enabled("backtest"):
            self.generators.append(BacktestSignalGenerator(config.backtest))
        if config.is_generator_enabled("sentiment"):
            self.generators.append(SentimentSignalGenerator(config.sentiment))
        if config.is_generator_enabled("ml"):
            if config.ml.get("mode") == "v2_meta_label":
                self.generators.append(MetaLabelSignalGenerator(config.ml))
            else:
                self.generators.append(MLPredictionSignalGenerator(config.ml))

        # Initialize formatters
        self.formatters = {
            "json": JSONFormatter(),
            "md": MarkdownFormatter(),
            "table": TerminalFormatter(),
        }

    def _should_scan_sequentially(self) -> bool:
        """Avoid concurrent ML scans because the forecaster stack is not thread-safe."""
        return any(isinstance(generator, MLPredictionSignalGenerator | MetaLabelSignalGenerator) for generator in self.generators)

    def scan(self, target_date: date | None = None) -> list[Signal]:
        """Scan all tickers and return aggregated signals.

        Args:
            target_date: Date to generate signals for (default: yesterday)

        Returns:
            List of Signal objects
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)

        all_signals = []

        if len(self.watchlist.tickers) <= 1 or self._should_scan_sequentially():
            for ticker in self.watchlist.tickers:
                ticker_signals = self._scan_ticker(ticker, target_date)
                if ticker_signals:
                    all_signals.extend(ticker_signals)
            return all_signals

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self._scan_ticker, ticker, target_date): ticker for ticker in self.watchlist.tickers}
            for future in as_completed(futures):
                try:
                    ticker_signals = future.result()
                    if ticker_signals:
                        all_signals.extend(ticker_signals)
                except Exception as exc:
                    ticker = futures[future]
                    print(f"Warning: scan failed for {ticker}: {exc}")

        return all_signals

    def _scan_ticker(self, ticker: str, target_date: date) -> list[Signal]:
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

    def format_signals(self, signals: list[Signal], format: str = "table") -> str:
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

    def save_history(self, signals: list[Signal]) -> None:
        """Save signals to Parquet history.

        Args:
            signals: List of Signal objects to save
        """
        if not signals:
            return

        target_date = signals[0].date
        save_signals(signals, target_date)
