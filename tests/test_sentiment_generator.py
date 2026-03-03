"""Test SentimentSignalGenerator."""

import pytest
from datetime import date
from unittest.mock import Mock, patch

from equity_lake.signals.generators.sentiment import SentimentSignalGenerator


def test_sentiment_generator_enabled():
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "buy_threshold": 0.5,
        "sell_threshold": -0.3,
        "min_articles": 3,
    }
    gen = SentimentSignalGenerator(config)
    assert gen.is_enabled() is True


@patch("equity_lake.signals.generators.sentiment.SentimentAnalyzer")
def test_sentiment_generator_buy_signal(mock_analyzer_class):
    """Test BUY signal when sentiment positive."""
    # Mock sentiment analyzer
    mock_analyzer = Mock()
    mock_analyzer.analyze_ticker.return_value = {
        "avg_sentiment": 0.75,  # Above buy_threshold
        "article_count": 5,
    }
    mock_analyzer_class.return_value = mock_analyzer

    config = {"enabled": True, "buy_threshold": 0.5, "min_articles": 3}
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "sentiment"
    assert signal.metadata["sentiment_score"] == 0.75


@patch("equity_lake.signals.generators.sentiment.SentimentAnalyzer")
def test_sentiment_generator_sell_signal(mock_analyzer_class):
    """Test SELL signal when sentiment negative."""
    mock_analyzer = Mock()
    mock_analyzer.analyze_ticker.return_value = {
        "avg_sentiment": -0.5,  # Below sell_threshold
        "article_count": 4,
    }
    mock_analyzer_class.return_value = mock_analyzer

    config = {"enabled": True, "sell_threshold": -0.3, "min_articles": 3}
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("TSLA", date.today())

    assert signal is not None
    assert signal.action == "SELL"


@patch("equity_lake.signals.generators.sentiment.SentimentAnalyzer")
def test_sentiment_generator_no_signal(mock_analyzer_class):
    """Test no signal when sentiment neutral."""
    mock_analyzer = Mock()
    mock_analyzer.analyze_ticker.return_value = {
        "avg_sentiment": 0.1,  # Between thresholds
        "article_count": 5,
    }
    mock_analyzer_class.return_value = mock_analyzer

    config = {
        "enabled": True,
        "buy_threshold": 0.5,
        "sell_threshold": -0.3,
        "min_articles": 3,
    }
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is None
