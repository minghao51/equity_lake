"""Reddit JSON API fetcher for financial subreddits.

Uses the public ``.json`` endpoint (10 req/min, IP-based). Includes
inter-request delay and ``X-Ratelimit-Remaining`` header inspection to
stay within Reddit's unauthenticated rate budget.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import structlog
import yaml

from equity_lake.core.paths import CONFIG_DIR
from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame

logger = structlog.get_logger()

CONFIG_PATH = CONFIG_DIR / "social_sources.yaml"
REDDIT_BASE_URL = "https://www.reddit.com"


def _build_user_agent() -> str:
    """Build a Reddit-compliant User-Agent string.

    Required format: ``<platform>:<app ID>:<version> (by u/<username>)``
    Raises ValueError if REDDIT_USER_AGENT is unset or malformed.
    """
    user_agent = os.getenv("REDDIT_USER_AGENT")
    if not user_agent:
        raise ValueError(
            "REDDIT_USER_AGENT environment variable is required. Set it to '<platform>:<app-id>:<version> (by u/<username>)' per Reddit API policy."
        )
    if not user_agent.startswith(("linux", "macos", "windows")):
        raise ValueError(f"REDDIT_USER_AGENT must start with 'linux', 'macos', or 'windows'. Got: {user_agent!r}")
    return user_agent


def _load_social_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or CONFIG_PATH
    if not path.exists():
        logger.warning("social_sources_config_not_found", path=str(path))
        return {}
    with path.open() as f:
        data = yaml.safe_load(f)
    return data if data else {}


def _to_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC).replace(tzinfo=None)


class RedditFetcher(MarketDataFetcher):
    """Fetch posts from financial subreddits via Reddit JSON API.

    Uses the public ``.json`` endpoint — no OAuth required for public
    subreddit data. Rate limiting is handled via inter-request delay
    (~7s between requests, staying within the 10 req/min budget) and
    ``X-Ratelimit-Remaining`` header inspection.

    Rate budget: 6 subreddits × 1 request each = 6 requests per run.
    At 10 req/min unauthenticated limit, this fits comfortably with
    inter-request pacing.
    """

    market = "reddit_posts"

    def __init__(
        self,
        config_path: Path | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 2.0,
    ):
        super().__init__(retry_attempts, retry_delay)
        config = _load_social_config(config_path)
        self.subreddits: list[dict[str, Any]] = config.get("reddit", {}).get("subreddits", [])
        self.user_agent = _build_user_agent()
        logger.info("Initialized RedditFetcher", subreddit_count=len(self.subreddits))

    def fetch(self, trading_date: date) -> pl.DataFrame:
        if not self.subreddits:
            logger.warning("No subreddits configured")
            return _empty_frame()

        logger.info("Fetching Reddit posts", subreddit_count=len(self.subreddits), trading_date=str(trading_date))

        all_posts: list[dict[str, Any]] = []
        for i, sub_cfg in enumerate(self.subreddits):
            try:
                posts = self._fetch_subreddit(sub_cfg, trading_date)
                all_posts.extend(posts)
            except Exception as exc:
                logger.error("reddit_subreddit_failed", subreddit=sub_cfg.get("name"), error=str(exc))
            if i < len(self.subreddits) - 1:
                time.sleep(7)

        if not all_posts:
            logger.warning("No Reddit posts fetched")
            return _empty_frame()

        df = pl.DataFrame(all_posts)

        for col in BRONZE_ARTICLE_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        df = df.select(BRONZE_ARTICLE_COLUMNS)
        logger.info("Fetched Reddit posts", count=df.height)
        return df

    def _fetch_subreddit(self, sub_cfg: dict[str, Any], trading_date: date) -> list[dict[str, Any]]:
        sub_name = sub_cfg["name"]
        post_limit = sub_cfg.get("post_limit", 30)

        def _fetch() -> list[dict[str, Any]]:
            url = f"{REDDIT_BASE_URL}/r/{sub_name}/hot.json"
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            }
            params = {"limit": min(post_limit, 100)}

            with httpx.Client(timeout=15, follow_redirects=True) as client:
                response = client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data: dict[str, Any] = response.json()

            remaining = response.headers.get("X-Ratelimit-Remaining")
            if remaining is not None:
                try:
                    rem_float = float(remaining)
                    if rem_float < 2:
                        reset = response.headers.get("X-Ratelimit-Reset", "60")
                        wait = max(float(reset), 10)
                        logger.warning("reddit_rate_limit_low", remaining=rem_float, wait_seconds=wait)
                        time.sleep(wait)
                except ValueError:
                    pass

            posts: list[dict[str, Any]] = []
            children = data.get("data", {}).get("children", [])

            for child in children[:post_limit]:
                post_data = child.get("data", {})
                created_utc = post_data.get("created_utc", 0)
                published = _to_datetime(created_utc) if created_utc else datetime.now(UTC).replace(tzinfo=None)

                if published.date() < trading_date:
                    continue

                permalink = post_data.get("permalink", "")
                full_url = f"{REDDIT_BASE_URL}{permalink}" if permalink else ""
                article_id = str(uuid.uuid5(uuid.NAMESPACE_URL, full_url or str(created_utc)))

                title = post_data.get("title", "").strip()
                selftext = post_data.get("selftext", "").strip()

                metadata = {
                    "score": post_data.get("score", 0),
                    "upvote_ratio": post_data.get("upvote_ratio", 0),
                    "num_comments": post_data.get("num_comments", 0),
                    "subreddit": sub_name,
                    "permalink": permalink,
                    "flair": post_data.get("link_flair_text"),
                }

                posts.append(
                    {
                        "article_id": article_id,
                        "source_type": "reddit",
                        "source_name": f"r/{sub_name}",
                        "source_url": full_url,
                        "title": title,
                        "body": f"{title}\n\n{selftext}"[:5000] if selftext else title[:5000],
                        "author": post_data.get("author", ""),
                        "published_at": published,
                        "fetched_at": datetime.now(UTC).replace(tzinfo=None),
                        "source_metadata": json.dumps(metadata),
                        "date": published.date(),
                    }
                )

            logger.debug("reddit_subreddit_parsed", subreddit=sub_name, posts=len(posts))
            return posts

        return self._retry_on_failure(_fetch)  # type: ignore[no-any-return]


__all__ = ["RedditFetcher"]
