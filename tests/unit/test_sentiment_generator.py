"""Offline contract tests for sentiment identity and score handling."""

from datetime import date
from typing import Any
from unittest.mock import patch

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator  # type: ignore[import-untyped]


def _instrument(symbol: str) -> InstrumentKey:
    return InstrumentKey(market="us_equity", mic="XNAS", symbol=symbol)


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
def test_sentiment_generator_enabled(_mock_setup_view: Any) -> None:
    generator = SentimentSignalGenerator({"enabled": True, "buy_threshold": 0.5, "sell_threshold": -0.3, "min_articles": 3})
    assert generator.is_enabled() is True


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
@patch.object(SentimentSignalGenerator, "_load_sentiment_summary", autospec=True)
def test_sentiment_generator_buy_signal(mock_load_sentiment: Any, _mock_setup_view: Any) -> None:
    mock_load_sentiment.return_value = {"avg_sentiment": 0.75, "article_count": 5}
    generator = SentimentSignalGenerator({"enabled": True, "buy_threshold": 0.5, "min_articles": 3})

    signal = generator.generate(_instrument("AAPL"), date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "sentiment"
    assert signal.metadata["sentiment_score"] == 0.75
    assert (signal.market, signal.mic) == ("us_equity", "XNAS")


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
@patch.object(SentimentSignalGenerator, "_load_sentiment_summary", autospec=True)
def test_sentiment_generator_sell_signal(mock_load_sentiment: Any, _mock_setup_view: Any) -> None:
    mock_load_sentiment.return_value = {"avg_sentiment": -0.5, "article_count": 4}
    generator = SentimentSignalGenerator({"enabled": True, "sell_threshold": -0.3, "min_articles": 3})

    signal = generator.generate(_instrument("TSLA"), date.today())

    assert signal is not None
    assert signal.action == "SELL"


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
@patch.object(SentimentSignalGenerator, "_load_sentiment_summary", autospec=True)
def test_sentiment_generator_no_signal(mock_load_sentiment: Any, _mock_setup_view: Any) -> None:
    mock_load_sentiment.return_value = {"avg_sentiment": 0.1, "article_count": 5}
    generator = SentimentSignalGenerator({"enabled": True, "buy_threshold": 0.5, "sell_threshold": -0.3, "min_articles": 3})

    assert generator.generate(_instrument("AAPL"), date.today()) is None
