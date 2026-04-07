"""Reddit sentiment loader."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from equity_lake.loaders.base import BaseDataLoader, LoaderMetadata, LoadResult


class RedditSentimentLoader(BaseDataLoader):
    """Fetch Reddit posts and score them with VADER."""

    metadata = LoaderMetadata(
        name="reddit_sentiment",
        description="Reddit sentiment loader for stock tickers.",
        supported_markets=["US"],
        requires_auth=True,
        data_types=["sentiment"],
    )

    def _validate_config(self) -> None:
        required = ["client_id", "client_secret", "user_agent"]
        missing = [key for key in required if key not in self.config]
        if missing:
            raise ValueError(f"Missing required config: {missing}")
        self.subreddits = self.config.get("subreddits", ["wallstreetbets", "stocks", "investing"])
        self.posts_per_symbol = int(self.config.get("posts_per_symbol", 25))

    def load(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> LoadResult:
        import praw
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        reddit = praw.Reddit(
            client_id=self.config["client_id"],
            client_secret=self.config["client_secret"],
            user_agent=self.config["user_agent"],
        )
        analyzer = SentimentIntensityAnalyzer()
        records: list[dict[str, object]] = []

        for symbol in symbols:
            query = f"${symbol} OR {symbol}"
            for subreddit_name in self.subreddits:
                subreddit = reddit.subreddit(subreddit_name)
                for post in subreddit.search(
                    query,
                    sort="new",
                    time_filter="month",
                    limit=self.posts_per_symbol,
                ):
                    post_date = datetime.fromtimestamp(post.created_utc).date()
                    if not (start_date <= post_date <= end_date):
                        continue
                    scores = analyzer.polarity_scores(f"{post.title}\n{post.selftext or ''}")
                    records.append(
                        {
                            "ticker": symbol,
                            "date": post_date,
                            "subreddit": subreddit_name,
                            "title": post.title[:200],
                            "score": post.score,
                            "num_comments": post.num_comments,
                            "compound_sentiment": scores["compound"],
                            "positive_sentiment": scores["pos"],
                            "negative_sentiment": scores["neg"],
                            "neutral_sentiment": scores["neu"],
                            "url": f"https://reddit.com{post.permalink}",
                        }
                    )

        frame = pd.DataFrame.from_records(records)
        return LoadResult(
            success=not frame.empty,
            data=frame,
            records_count=len(frame),
            errors=[] if not frame.empty else ["No Reddit posts matched the request"],
        )

    def get_available_symbols(self) -> list[str]:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "META"]

    def validate_connection(self) -> bool:
        import praw

        try:
            reddit = praw.Reddit(
                client_id=self.config["client_id"],
                client_secret=self.config["client_secret"],
                user_agent=self.config["user_agent"],
            )
            _ = reddit.read_only
        except Exception:
            return False
        return True


__all__ = ["RedditSentimentLoader"]
