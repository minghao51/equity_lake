"""Sentiment analysis for financial text using VADER and FinBERT."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

import polars as pl
import structlog

from equity_lake.core.polars_utils import FrameLike, ensure_polars

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

logger = structlog.get_logger()


class SentimentMethod(StrEnum):
    """Supported sentiment analysis methods."""

    VADER = "vader"
    FINBERT = "finbert"


class SentimentLabel(StrEnum):
    """Standardized sentiment labels."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SentimentAnalyzer:
    """Analyze sentiment of financial text using VADER or FinBERT."""

    def __init__(self, method: Literal["vader", "finbert"] = "vader"):
        self.method = method

        if method == "vader":
            if not VADER_AVAILABLE:
                raise ImportError("vaderSentiment is required for VADER method. Install with: uv pip install vaderSentiment")
            self.analyzer = SentimentIntensityAnalyzer()
            logger.info("Initialized VADER sentiment analyzer")
        elif method == "finbert":
            raise NotImplementedError("FinBERT method not yet implemented. Use method='vader' for now.")
        else:
            raise ValueError(f"Unknown method: {method}. Use 'vader' or 'finbert'")

    def analyze(self, text: str) -> dict[str, object]:
        """Analyze sentiment of a single text string."""
        if not text or not isinstance(text, str):
            return self._neutral_result()

        text = text.strip()
        if not text:
            return self._neutral_result()

        if self.method == "vader":
            return self._analyze_vader(text)
        raise NotImplementedError(f"analyze() not implemented for method={self.method}")

    def analyze_batch(self, texts: list[str]) -> pl.DataFrame:
        """Analyze sentiment for multiple texts."""
        if not texts:
            return pl.DataFrame()

        results = []
        for text in texts:
            result = self.analyze(text)
            result["text"] = text
            results.append(result)

        columns = ["text", "compound", "label"]
        if self.method == "vader":
            columns.extend(["neg", "neu", "pos"])
        return pl.DataFrame(results).select(columns)

    def _analyze_vader(self, text: str) -> dict[str, object]:
        scores = self.analyzer.polarity_scores(text)
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
            "scores": {"neg": scores["neg"], "neu": scores["neu"], "pos": scores["pos"]},
            "neg": scores["neg"],
            "neu": scores["neu"],
            "pos": scores["pos"],
        }

    def _neutral_result(self) -> dict[str, object]:
        return {
            "compound": 0.0,
            "label": SentimentLabel.NEUTRAL.value,
            "scores": {"neg": 0.0, "neu": 1.0, "pos": 0.0},
            "neg": 0.0,
            "neu": 1.0,
            "pos": 0.0,
        }


def analyze_sentiment_scores(
    df: FrameLike,
    text_column: str = "headline",
    method: Literal["vader", "finbert"] = "vader",
) -> pl.DataFrame:
    """Add sentiment scores to a frame with text data."""
    frame = ensure_polars(df)
    if text_column not in frame.columns:
        raise ValueError(f"Column '{text_column}' not found in DataFrame")

    analyzer = SentimentAnalyzer(method=method)
    texts = frame[text_column].fill_null("").cast(pl.Utf8).to_list()
    results_df = analyzer.analyze_batch(texts)

    enriched = frame.with_columns(
        pl.Series("sentiment_score", results_df["compound"].to_list()),
        pl.Series("sentiment_label", results_df["label"].to_list()),
    )
    if method == "vader":
        enriched = enriched.with_columns(
            pl.Series("neg", results_df["neg"].to_list()),
            pl.Series("neu", results_df["neu"].to_list()),
            pl.Series("pos", results_df["pos"].to_list()),
        )
    return enriched


__all__ = [
    "SentimentAnalyzer",
    "SentimentMethod",
    "SentimentLabel",
    "analyze_sentiment_scores",
]
