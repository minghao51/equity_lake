"""Sentiment analysis module for financial news and social media."""

from equity_lake.sentiment.analyzer import (
    SentimentAnalyzer,
    analyze_sentiment_scores,
)

__all__ = ["SentimentAnalyzer", "analyze_sentiment_scores"]
