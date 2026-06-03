"""Test SentimentSignalGenerator."""

from datetime import date
from unittest.mock import patch

from equity_lake.signals.generators.sentiment import SentimentSignalGenerator


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
def test_sentiment_generator_enabled(_mock_setup_view):
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "buy_threshold": 0.5,
        "sell_threshold": -0.3,
        "min_articles": 3,
    }
    gen = SentimentSignalGenerator(config)
    assert gen.is_enabled() is True


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
@patch.object(SentimentSignalGenerator, "_load_sentiment_summary", autospec=True)
def test_sentiment_generator_buy_signal(mock_load_sentiment, _mock_setup_view):
    """Test BUY signal when sentiment positive."""
    mock_load_sentiment.return_value = {
        "avg_sentiment": 0.75,  # Above buy_threshold
        "article_count": 5,
    }

    config = {"enabled": True, "buy_threshold": 0.5, "min_articles": 3}
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "sentiment"
    assert signal.metadata["sentiment_score"] == 0.75


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
@patch.object(SentimentSignalGenerator, "_load_sentiment_summary", autospec=True)
def test_sentiment_generator_sell_signal(mock_load_sentiment, _mock_setup_view):
    """Test SELL signal when sentiment negative."""
    mock_load_sentiment.return_value = {
        "avg_sentiment": -0.5,  # Below sell_threshold
        "article_count": 4,
    }

    config = {"enabled": True, "sell_threshold": -0.3, "min_articles": 3}
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("TSLA", date.today())

    assert signal is not None
    assert signal.action == "SELL"


@patch.object(SentimentSignalGenerator, "_setup_view", autospec=True)
@patch.object(SentimentSignalGenerator, "_load_sentiment_summary", autospec=True)
def test_sentiment_generator_no_signal(mock_load_sentiment, _mock_setup_view):
    """Test no signal when sentiment neutral."""
    mock_load_sentiment.return_value = {
        "avg_sentiment": 0.1,  # Between thresholds
        "article_count": 5,
    }

    config = {
        "enabled": True,
        "buy_threshold": 0.5,
        "sell_threshold": -0.3,
        "min_articles": 3,
    }
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is None
