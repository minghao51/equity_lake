"""News sentiment signal generator."""

from datetime import date, timedelta

from equity_lake.sentiment.analyzer import SentimentAnalyzer
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal


class SentimentSignalGenerator(SignalGenerator):
    """Generate signals based on news sentiment analysis.

    Reuses existing sentiment analyzer to fetch news and calculate
    sentiment scores. Generates BUY when sentiment is positive,
    SELL when negative.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.buy_threshold = config.get("buy_threshold", 0.5)
        self.sell_threshold = config.get("sell_threshold", -0.3)
        self.min_articles = config.get("min_articles", 3)
        self.lookback_days = config.get("lookback_days", 7)

        # Initialize sentiment analyzer
        self.analyzer = SentimentAnalyzer()

    def generate(self, ticker: str, target_date: date) -> Signal | None:
        """Generate signal based on news sentiment.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action based on sentiment score
        """
        if not self.is_enabled():
            return None

        # Calculate date range for news lookup
        start_date = target_date - timedelta(days=self.lookback_days)

        try:
            # Fetch news and analyze sentiment
            sentiment_data = self.analyzer.analyze_ticker(
                ticker=ticker, start_date=start_date, end_date=target_date
            )
        except Exception:
            # Sentiment analysis failed
            return None

        if not sentiment_data:
            return None

        # Extract sentiment score and article count
        avg_sentiment = sentiment_data.get("avg_sentiment", 0)
        article_count = sentiment_data.get("article_count", 0)

        # Check minimum article threshold
        if article_count < self.min_articles:
            return None

        # Generate signal based on sentiment
        if avg_sentiment >= self.buy_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="sentiment",
                action="BUY",
                confidence=65.0,
                reasoning=f"Positive sentiment: {avg_sentiment:.2f} from {article_count} articles",
                metadata={
                    "sentiment_score": avg_sentiment,
                    "article_count": article_count,
                    "lookback_days": self.lookback_days,
                },
            )
        elif avg_sentiment <= self.sell_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="sentiment",
                action="SELL",
                confidence=60.0,
                reasoning=f"Negative sentiment: {avg_sentiment:.2f} from {article_count} articles",
                metadata={
                    "sentiment_score": avg_sentiment,
                    "article_count": article_count,
                    "lookback_days": self.lookback_days,
                },
            )

        return None
