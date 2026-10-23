"""Signal scanner orchestrator with explicit per-generator coverage outcomes."""

from copy import deepcopy
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

import structlog

from equity_lake.core.calendar import is_instrument_trading_day
from equity_lake.core.instruments import InstrumentKey
from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.formatters.markdown import MarkdownFormatter
from equity_lake.signals.formatters.terminal import TerminalFormatter
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.generators.base import GenerationOutcome, SignalGenerator
from equity_lake.signals.generators.meta_label import MetaLabelSignalGenerator
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator
from equity_lake.signals.history import save_signals
from equity_lake.signals.models import Signal, SignalConfig, SignalScanOutcome, SignalScanReport, Watchlist

logger = structlog.get_logger(__name__)


class SignalScanner:
    """Main orchestrator for scanning a watchlist sequentially."""

    def __init__(
        self,
        config: SignalConfig,
        watchlist: Watchlist,
        max_workers: int = 4,
        *,
        instruments: list[InstrumentKey],
    ):
        """Initialize generators for a canonical, market-aware scan scope.

        Args:
            config: Validated signal configuration.
            watchlist: Display name and legacy ticker labels.
            max_workers: Retained for API compatibility; scans remain sequential.
            instruments: Exact configured canonical identities; bare symbols are never inferred.
        """
        if not instruments:
            raise ValueError("signal scans require at least one canonical instrument identity")
        if len({instrument.tuple for instrument in instruments}) != len(instruments):
            raise ValueError("signal scan instrument identities must be unique")
        self.config = config
        self.watchlist = watchlist
        self.instruments = list(instruments)
        self.max_workers = max_workers
        self.generators: list[SignalGenerator] = []
        self._generators_by_name: dict[str, SignalGenerator] = {}
        self._initialization_failures: dict[str, tuple[Literal["missing_model", "error"], str]] = {}
        self._enabled_generator_names = [name for name in ("backtest", "sentiment", "ml") if config.is_generator_enabled(name)]
        self._current_outcomes: list[SignalScanOutcome] = []
        self.last_report: SignalScanReport | None = None

        generators: list[tuple[str, type[SignalGenerator], dict]] = []
        if config.is_generator_enabled("backtest"):
            generators.append(("backtest", BacktestSignalGenerator, config.backtest))
        if config.is_generator_enabled("sentiment"):
            generators.append(("sentiment", SentimentSignalGenerator, config.sentiment))
        if config.is_generator_enabled("ml"):
            if config.ml.get("mode") == "v2_meta_label":
                ml_config = deepcopy(config.ml)
                candidate_strategies = ml_config.get("candidate_strategies")
                if candidate_strategies is None:
                    candidate_strategies = config.backtest.get("strategies")
                # Explicit injection prevents PriceForecaster's legacy YAML/default fallback.
                ml_config["candidate_strategies"] = deepcopy(candidate_strategies)
                generators.append(("ml", MetaLabelSignalGenerator, ml_config))
            else:
                generators.append(("ml", MLPredictionSignalGenerator, config.ml))

        for name, generator_type, generator_config in generators:
            try:
                generator = generator_type(generator_config)
            except Exception as exc:
                status: Literal["missing_model", "error"] = "missing_model" if isinstance(exc, FileNotFoundError) else "error"
                self._initialization_failures[name] = (status, str(exc))
                logger.warning("signal_generator_initialization_failed", generator=name, status=status, error=str(exc))
                continue
            self.generators.append(generator)
            self._generators_by_name[name] = generator

        self.formatters = {
            "json": JSONFormatter(),
            "md": MarkdownFormatter(),
            "table": TerminalFormatter(),
        }

    def scan(self, target_date: date | None = None) -> list[Signal]:
        """Scan and return signals, preserving the legacy list-returning API."""
        return self.scan_report(target_date).signals

    def scan_report(self, target_date: date | None = None) -> SignalScanReport:
        """Scan and return structured signal and coverage outcomes."""
        if target_date is None:
            target_date = date.today() - timedelta(days=1)

        issues: list[str] = []
        all_signals: list[Signal] = []
        self._current_outcomes = []
        if not self._enabled_generator_names:
            issues.append("no signal generators are enabled")

        for instrument in self.instruments:
            try:
                scheduled = is_instrument_trading_day(instrument, target_date)
                schedule_error = None
            except Exception as exc:
                scheduled = False
                schedule_error = str(exc)
                issues.append(f"{instrument.display_key} session classification failed: {schedule_error}")
            all_signals.extend(self._scan_ticker(instrument, target_date, scheduled=scheduled, schedule_error=schedule_error))

        statuses = [outcome.status for outcome in self._current_outcomes]
        success_count = sum(status in {"signal", "no_condition"} for status in statuses)
        issue_count = sum(status in {"unavailable", "missing_model", "error"} for status in statuses)
        if statuses and all(status == "not_scheduled" for status in statuses):
            report_status: Literal["success", "partial", "failed", "not_scheduled"] = "not_scheduled"
        elif not statuses or (issue_count > 0 and success_count == 0):
            report_status = "failed"
        elif issue_count:
            report_status = "partial"
        else:
            report_status = "success"

        report = SignalScanReport(
            run_id=uuid4().hex,
            target_date=target_date,
            watchlist=self.watchlist.name,
            tickers=[instrument.symbol for instrument in self.instruments],
            instruments=list(self.instruments),
            generators=list(self._enabled_generator_names),
            status=report_status,
            outcomes=list(self._current_outcomes),
            signals=all_signals,
            issues=issues,
        )
        self.last_report = report
        return report

    def _scan_ticker(
        self,
        instrument: InstrumentKey,
        target_date: date,
        *,
        scheduled: bool = True,
        schedule_error: str | None = None,
    ) -> list[Signal]:
        """Scan one ticker, recording an outcome for every enabled generator."""
        signals: list[Signal] = []
        for name in self._enabled_generator_names:
            if schedule_error is not None:
                outcome = SignalScanOutcome(
                    ticker=instrument.symbol,
                    market=instrument.market,
                    mic=instrument.mic,
                    identity_schema_version=1,
                    generator=name,
                    status="error",
                    detail=f"session classification failed: {schedule_error}",
                )
            elif not scheduled:
                outcome = SignalScanOutcome(
                    ticker=instrument.symbol,
                    market=instrument.market,
                    mic=instrument.mic,
                    identity_schema_version=1,
                    generator=name,
                    status="not_scheduled",
                    detail="instrument venue is closed on target date",
                )
            elif name in self._initialization_failures:
                status, detail = self._initialization_failures[name]
                outcome = SignalScanOutcome(
                    ticker=instrument.symbol,
                    market=instrument.market,
                    mic=instrument.mic,
                    identity_schema_version=1,
                    generator=name,
                    status=status,
                    detail=detail,
                )
            else:
                generator = self._generators_by_name[name]
                try:
                    result = generator.generate_outcome(instrument, target_date)
                    if not isinstance(result, GenerationOutcome):
                        raise TypeError(f"{name} generator returned an invalid outcome")
                    if result.status == "signal" and result.signal is None:
                        raise ValueError(f"{name} generator reported signal without a Signal")
                    if result.status != "signal" and result.signal is not None:
                        raise ValueError(f"{name} generator returned a Signal with status {result.status!r}")
                    if result.signal is not None:
                        if (
                            result.signal.ticker != instrument.symbol
                            or result.signal.date != target_date
                            or result.signal.market != instrument.market
                            or result.signal.mic != instrument.mic
                        ):
                            raise ValueError(f"{name} generator returned a signal for a different canonical instrument or date")
                        signals.append(result.signal)
                    outcome = SignalScanOutcome(
                        ticker=instrument.symbol,
                        market=instrument.market,
                        mic=instrument.mic,
                        identity_schema_version=1,
                        generator=name,
                        status=result.status,
                        detail=result.detail,
                    )
                except Exception as exc:
                    logger.warning("signal_generator_failed", generator=name, instrument=instrument.display_key, error=str(exc))
                    outcome = SignalScanOutcome(
                        ticker=instrument.symbol,
                        market=instrument.market,
                        mic=instrument.mic,
                        identity_schema_version=1,
                        generator=name,
                        status="error",
                        detail=str(exc),
                    )
            self._current_outcomes.append(outcome)
        return signals

    def format_signals(self, signals: list[Signal], format: str = "table") -> str:
        """Format signals in the requested legacy output format."""
        formatter = self.formatters.get(format)
        if not formatter:
            raise ValueError(f"Unknown format: {format}. Use: json, md, table")
        return formatter.format(signals)

    def save_history(self, signals: list[Signal]) -> None:
        """Save nonempty signals through the existing Delta history boundary."""
        if not signals:
            return
        save_signals(signals, signals[0].date)
