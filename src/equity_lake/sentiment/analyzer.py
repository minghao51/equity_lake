"""Sentiment analysis for financial text using VADER and FinBERT."""

from enum import Enum
from typing import Literal

import pandas as pd  # type: ignore[import-untyped]
import structlog

# Try to import VADER
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

logger = structlog.get_logger()


class SentimentMethod(str, Enum):
    """Supported sentiment analysis methods."""

    VADER = "vader"
    FINBERT = "finbert"


class SentimentLabel(str, Enum):
    """Standardized sentiment labels."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SentimentAnalyzer:
    """
    Analyze sentiment of financial text using VADER or FinBERT.

    VADER (Valence Aware Dictionary and sEntiment Reasoner) is a fast,
    rule-based sentiment analysis tool optimized for social media text.
    It's suitable for MVP and real-time applications.

    FinBERT is a transformer model fine-tuned on financial communications
    for superior accuracy on financial text. Use for batch processing.

    Attributes:
        method: Sentiment analysis method ("vader" or "finbert")
        analyzer: The underlying analyzer instance
    """

    def __init__(self, method: Literal["vader", "finbert"] = "vader"):
        """
        Initialize the sentiment analyzer.

        Args:
            method: Sentiment analysis method (default: "vader")

        Raises:
            ImportError: If required dependencies are not installed
        """
        self.method = method

        if method == "vader":
            if not VADER_AVAILABLE:
                raise ImportError(
                    "vaderSentiment is required for VADER method. "
                    "Install with: uv pip install vaderSentiment"
                )
            self.analyzer = SentimentIntensityAnalyzer()
            logger.info("Initialized VADER sentiment analyzer")
        elif method == "finbert":
            # FinBERT support planned for Phase 5
            raise NotImplementedError(
                "FinBERT method not yet implemented. "
                "Use method='vader' for now."
            )
        else:
            raise ValueError(f"Unknown method: {method}. Use 'vader' or 'finbert'")

    def analyze(self, text: str) -> dict:
        """
        Analyze sentiment of a single text string.

        Args:
            text: Text to analyze

        Returns:
            Dictionary with:
                - compound: Compound sentiment score (-1.0 to 1.0)
                - label: Sentiment label ('positive', 'negative', 'neutral')
                - scores: Individual scores (neg, neu, pos) for VADER
        """
        if not text or not isinstance(text, str):
            return self._neutral_result()

        text = text.strip()
        if not text:
            return self._neutral_result()

        if self.method == "vader":
            return self._analyze_vader(text)

        raise NotImplementedError(f"analyze() not implemented for method={self.method}")

    def analyze_batch(self, texts: list[str]) -> pd.DataFrame:
        """
        Analyze sentiment for multiple texts.

        Args:
            texts: List of text strings to analyze

        Returns:
            DataFrame with columns:
                - text: Original text
                - compound: Compound sentiment score
                - label: Sentiment label
                - neg: Negative score (VADER only)
                - neu: Neutral score (VADER only)
                - pos: Positive score (VADER only)
        """
        if not texts:
            return pd.DataFrame()

        results = []
        for text in texts:
            result = self.analyze(text)
            result["text"] = text
            results.append(result)

        df = pd.DataFrame(results)
        # Reorder columns
        cols = ["text", "compound", "label"]
        if self.method == "vader":
            cols.extend(["neg", "neu", "pos"])
        df = df[cols]

        return df

    def _analyze_vader(self, text: str) -> dict:
        """Analyze sentiment using VADER."""
        scores = self.analyzer.polarity_scores(text)

        # Determine label based on compound score
        # Standard thresholds: positive >= 0.05, negative <= -0.05, else neutral
        compound = scores["compound"]
        if compound >= 0.05:
            label = SentimentLabel.POSITIVE
        elif compound <= -0.05:
            label = SentimentLabel.NEGATIVE
        else:
            label = SentimentLabel.NEUTRAL

        return {
            "compound": compound,
            "label": label.value,
            "scores": {
                "neg": scores["neg"],
                "neu": scores["neu"],
                "pos": scores["pos"],
            },
            "neg": scores["neg"],
            "neu": scores["neu"],
            "pos": scores["pos"],
        }

    def _neutral_result(self) -> dict:
        """Return neutral sentiment for empty/invalid text."""
        return {
            "compound": 0.0,
            "label": SentimentLabel.NEUTRAL.value,
            "scores": {"neg": 0.0, "neu": 1.0, "pos": 0.0},
            "neg": 0.0,
            "neu": 1.0,
            "pos": 0.0,
        }


def analyze_sentiment_scores(
    df: pd.DataFrame,
    text_column: str = "headline",
    method: Literal["vader", "finbert"] = "vader",
) -> pd.DataFrame:
    """
    Add sentiment scores to a DataFrame with text data.

    Args:
        df: Input DataFrame with text column
        text_column: Name of column containing text
        method: Sentiment analysis method

    Returns:
        DataFrame with additional columns:
            - sentiment_score: Compound sentiment score
            - sentiment_label: Sentiment label
            - neg: Negative score (VADER only)
            - neu: Neutral score (VADER only)
            - pos: Positive score (VADER only)
    """
    if text_column not in df.columns:
        raise ValueError(f"Column '{text_column}' not found in DataFrame")

    analyzer = SentimentAnalyzer(method=method)
    texts = df[text_column].fillna("").tolist()

    results_df = analyzer.analyze_batch(texts)

    # Add results to original DataFrame
    df = df.copy()
    df["sentiment_score"] = results_df["compound"].values
    df["sentiment_label"] = results_df["label"].values

    if method == "vader":
        df["neg"] = results_df["neg"].values
        df["neu"] = results_df["neu"].values
        df["pos"] = results_df["pos"].values

    return df


__all__ = [
    "SentimentAnalyzer",
    "SentimentMethod",
    "SentimentLabel",
    "analyze_sentiment_scores",
]
