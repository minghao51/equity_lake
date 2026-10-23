"""Deterministic per-generator/ticker scan coverage tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import pytest

import equity_lake.signals.generators.sentiment as sentiment_module  # type: ignore[import-untyped]
import equity_lake.signals.scanner as scanner_module  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.generators.base import GenerationOutcome  # type: ignore[import-untyped]
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator
from equity_lake.signals.models import Signal, SignalConfig, Watchlist  # type: ignore[import-untyped]
from equity_lake.signals.scanner import SignalScanner

TARGET = date(2024, 12, 2)


class _StubGenerator:
    def __init__(self, evaluator: Callable[[InstrumentKey, date], GenerationOutcome | Exception]) -> None:
        self.evaluator = evaluator
        self.calls: list[InstrumentKey] = []

    def generate_outcome(self, instrument: InstrumentKey, target_date: date) -> GenerationOutcome:
        self.calls.append(instrument)
        result = self.evaluator(instrument, target_date)
        if isinstance(result, Exception):
            raise result
        return result


def _signal(instrument: InstrumentKey) -> Signal:
    return Signal(
        instrument.symbol,
        TARGET,
        "backtest",
        "BUY",
        70.0,
        "synthetic condition",
        {},
        market=instrument.market,
        mic=instrument.mic,
    )


def _scanner(
    monkeypatch: pytest.MonkeyPatch,
    factories: dict[str, _StubGenerator],
    *,
    enabled: tuple[str, ...] = ("backtest", "sentiment"),
) -> SignalScanner:
    for name, attribute in (
        ("backtest", "BacktestSignalGenerator"),
        ("sentiment", "SentimentSignalGenerator"),
        ("ml", "MLPredictionSignalGenerator"),
    ):
        stub = factories.get(name)
        if stub is not None:
            monkeypatch.setattr(scanner_module, attribute, lambda _config, stub=stub: stub)
    monkeypatch.setattr(scanner_module, "is_instrument_trading_day", lambda _instrument, _date: True)
    config = SignalConfig(
        backtest={"enabled": "backtest" in enabled},
        sentiment={"enabled": "sentiment" in enabled},
        ml={"enabled": "ml" in enabled},
    )
    return SignalScanner(
        config,
        Watchlist(name="Synthetic", tickers=["AAPL", "MSFT"]),
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker) for ticker in ("AAPL", "MSFT")],
    )


def test_scan_report_preserves_signals_and_partial_generator_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    backtest = _StubGenerator(
        lambda instrument, _date: GenerationOutcome(status="signal", signal=_signal(instrument))
        if instrument.symbol == "AAPL"
        else GenerationOutcome(status="no_condition")
    )
    sentiment = _StubGenerator(lambda _ticker, _date: GenerationOutcome(status="unavailable", detail="no sentiment rows"))
    scanner = _scanner(monkeypatch, {"backtest": backtest, "sentiment": sentiment})

    report = scanner.scan_report(TARGET)

    assert report.status == "partial"
    assert [(outcome.ticker, outcome.generator, outcome.status) for outcome in report.outcomes] == [
        ("AAPL", "backtest", "signal"),
        ("AAPL", "sentiment", "unavailable"),
        ("MSFT", "backtest", "no_condition"),
        ("MSFT", "sentiment", "unavailable"),
    ]
    assert [(signal.ticker, signal.action) for signal in report.signals] == [("AAPL", "BUY")]
    assert report.outcome_counts == {"signal": 1, "unavailable": 2, "no_condition": 1}


def test_legacy_scan_api_returns_signal_list_and_retains_structured_report(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = _StubGenerator(lambda instrument, _date: GenerationOutcome(status="signal", signal=_signal(instrument)))
    scanner = _scanner(monkeypatch, {"backtest": generator}, enabled=("backtest",))

    signals = scanner.scan(TARGET)

    assert [signal.ticker for signal in signals] == ["AAPL", "MSFT"]
    assert scanner.last_report is not None
    assert scanner.last_report.status == "success"


def test_all_required_evaluations_failed_report_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    backtest = _StubGenerator(lambda _ticker, _date: GenerationOutcome(status="unavailable", detail="no prices"))
    sentiment = _StubGenerator(lambda _ticker, _date: RuntimeError("query failed"))
    scanner = _scanner(monkeypatch, {"backtest": backtest, "sentiment": sentiment})

    report = scanner.scan_report(TARGET)

    assert report.status == "failed"
    assert [outcome.status for outcome in report.outcomes] == ["unavailable", "error", "unavailable", "error"]
    assert report.outcomes[1].detail == "query failed"


def test_missing_model_is_not_reported_as_a_neutral_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    backtest = _StubGenerator(lambda _ticker, _date: GenerationOutcome(status="no_condition"))
    ml = _StubGenerator(lambda _ticker, _date: GenerationOutcome(status="missing_model", detail="no trained model"))
    scanner = _scanner(monkeypatch, {"backtest": backtest, "ml": ml}, enabled=("backtest", "ml"))

    report = scanner.scan_report(TARGET)

    assert report.status == "partial"
    assert [outcome.status for outcome in report.outcomes] == ["no_condition", "missing_model", "no_condition", "missing_model"]


def test_closed_us_session_is_explicit_and_does_not_call_generators(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = _StubGenerator(lambda _ticker, _date: pytest.fail("not-scheduled evaluation must not run"))
    scanner = _scanner(monkeypatch, {"backtest": generator}, enabled=("backtest",))
    monkeypatch.setattr(scanner_module, "is_instrument_trading_day", lambda _instrument, _date: False)

    report = scanner.scan_report(TARGET)

    assert report.status == "not_scheduled"
    assert [outcome.status for outcome in report.outcomes] == ["not_scheduled", "not_scheduled"]
    assert generator.calls == []


def test_no_condition_is_a_successful_zero_signal_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = _StubGenerator(lambda _ticker, _date: GenerationOutcome(status="no_condition"))
    scanner = _scanner(monkeypatch, {"backtest": generator}, enabled=("backtest",))

    report = scanner.scan_report(TARGET)

    assert report.status == "success"
    assert report.signals == []
    assert [outcome.status for outcome in report.outcomes] == ["no_condition", "no_condition"]


def _temporary_duckdb_sentiment_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scores: list[float | None]) -> GenerationOutcome:
    """Exercise the production parquet-view/query path against isolated local data."""
    news_dir = tmp_path / "news"
    news_dir.mkdir()
    parquet_path = news_dir / "sentiment.parquet"
    writer = duckdb.connect(str(tmp_path / "fixture.duckdb"))
    writer.execute("CREATE TABLE sentiment_fixture (ticker VARCHAR, market VARCHAR, mic VARCHAR, date DATE, sentiment_score DOUBLE)")
    writer.executemany(
        "INSERT INTO sentiment_fixture VALUES (?, ?, ?, ?, ?)",
        [("AAPL", "us_equity", "XNAS", TARGET, score) for score in scores],
    )
    escaped_path = parquet_path.as_posix().replace("'", "''")
    writer.execute(f"COPY sentiment_fixture TO '{escaped_path}' (FORMAT PARQUET)")
    writer.close()
    monkeypatch.setattr(sentiment_module, "US_NEWS_DIR", news_dir)

    generator = SentimentSignalGenerator({"min_articles": 3, "lookback_days": 7})
    try:
        return generator.generate_outcome(InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"), TARGET)
    finally:
        generator.con.close()


@pytest.mark.parametrize(
    ("scores", "expected_action"),
    [
        ([0.8, 0.7, 0.9, None, None], "BUY"),
        ([-0.8, -0.7, -0.9, None, None], "SELL"),
    ],
)
def test_sentiment_signals_count_only_finite_scored_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scores: list[float | None], expected_action: str
) -> None:
    outcome = _temporary_duckdb_sentiment_outcome(tmp_path, monkeypatch, scores)

    assert outcome.status == "signal"
    assert outcome.signal is not None
    assert outcome.signal.action == expected_action
    assert outcome.signal.metadata["article_count"] == 5  # Preserve the legacy total-row history count.
    assert outcome.detail == "sentiment coverage: 3 usable scored rows of 5 rows"


@pytest.mark.parametrize(
    ("scores", "detail"),
    [
        ([0.8, None, None], "insufficient sentiment coverage: sentiment coverage: 1 usable scored rows of 3 rows; need 3 usable scored rows"),
        ([None, None, None], "no usable finite sentiment scores; sentiment coverage: 0 usable scored rows of 3 rows"),
        (
            [float("nan"), float("inf"), float("-inf")],
            "no usable finite sentiment scores; sentiment coverage: 0 usable scored rows of 3 rows",
        ),
    ],
)
def test_sentiment_unavailable_when_history_has_too_few_finite_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scores: list[float | None], detail: str
) -> None:
    outcome = _temporary_duckdb_sentiment_outcome(tmp_path, monkeypatch, scores)

    assert outcome.status == "unavailable"
    assert outcome.signal is None
    assert outcome.detail == detail


def test_sentiment_neutral_outcome_retains_raw_and_usable_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = _temporary_duckdb_sentiment_outcome(tmp_path, monkeypatch, [0.1, 0.2, 0.1, None])

    assert outcome.status == "no_condition"
    assert outcome.detail == "sentiment coverage: 3 usable scored rows of 4 rows"
