"""RSS/Atom news feed fetcher for financial media sources."""

from __future__ import annotations

import json
import uuid
from calendar import timegm
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import feedparser
import httpx
import polars as pl
import structlog
import yaml

from equity_lake.core.paths import CONFIG_DIR
from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame

logger = structlog.get_logger()

CONFIG_PATH = CONFIG_DIR / "rss_feeds.yaml"


def _load_feed_config(config_path: Path | None = None) -> list[dict[str, Any]]:
    path = config_path or CONFIG_PATH
    if not path.exists():
        logger.warning("rss_feeds_config_not_found", path=str(path))
        return []
    with path.open() as f:
        data = yaml.safe_load(f)
    return data.get("feeds", []) if data else []


def _parse_published(entry: dict[str, Any], fallback: datetime | None = None) -> datetime:
    published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if published_struct:
        try:
            return datetime.fromtimestamp(timegm(published_struct), tz=UTC).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError):
            pass
    raw = entry.get("published") or entry.get("updated", "")
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
    return fallback or datetime.now(UTC).replace(tzinfo=None)


def _extract_body(entry: dict[str, Any]) -> str:
    for key in ("content", "summary", "description"):
        value = entry.get(key)
        if isinstance(value, list) and value:
            return str(value[0].get("value", "")).strip()
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class RSSNewsFetcher(MarketDataFetcher):
    """Fetch financial news from configured RSS/Atom feeds.

    Each feed is parsed via :mod:`feedparser`. Articles are stored in bronze
    schema for downstream LLM processing.
    """

    market = "rss_news"

    def __init__(
        self,
        feeds_config_path: Path | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.feeds = _load_feed_config(feeds_config_path)
        logger.info("Initialized RSSNewsFetcher", feed_count=len(self.feeds))

    def fetch(self, trading_date: date) -> pl.DataFrame:
        if not self.feeds:
            logger.warning("No RSS feeds configured")
            return _empty_frame()

        logger.info("Fetching RSS feeds", feed_count=len(self.feeds), trading_date=str(trading_date))

        all_articles: list[dict[str, Any]] = []
        for feed_cfg in self.feeds:
            try:
                articles = self._fetch_single_feed(feed_cfg, trading_date)
                all_articles.extend(articles)
            except Exception as exc:
                logger.error("rss_feed_failed", feed=feed_cfg.get("name"), error=str(exc))

        if not all_articles:
            logger.warning("No articles fetched from any RSS feed")
            return _empty_frame()

        df = pl.DataFrame(all_articles)

        for col in BRONZE_ARTICLE_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        df = df.select(BRONZE_ARTICLE_COLUMNS)
        logger.info("Fetched RSS articles", count=df.height)
        return df

    def _fetch_single_feed(self, feed_cfg: dict[str, Any], trading_date: date) -> list[dict[str, Any]]:
        feed_name = feed_cfg["name"]
        feed_url = feed_cfg["url"]
        categories = feed_cfg.get("category", [])
        fallback_dt = datetime.combine(trading_date, datetime.min.time())

        def _parse() -> list[dict[str, Any]]:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            }
            try:
                with httpx.Client(timeout=20, follow_redirects=True) as client:
                    resp = client.get(feed_url, headers=headers)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.content)
            except Exception:
                parsed = feedparser.parse(feed_url)

            if parsed.bozo and parsed.bozo_exception:
                logger.warning("rss_parse_warning", feed=feed_name, error=str(parsed.bozo_exception))

            articles: list[dict[str, Any]] = []
            now = datetime.now(UTC).replace(tzinfo=None)

            for entry in parsed.entries:
                published = _parse_published(entry, fallback=fallback_dt)
                if published.date() < trading_date:
                    continue

                article_id = str(uuid.uuid5(uuid.NAMESPACE_URL, entry.get("link", entry.get("title", ""))))
                body = _extract_body(entry)
                if not body:
                    body = entry.get("title", "")

                metadata = {
                    "categories": categories,
                    "feed_name": feed_name,
                }

                articles.append(
                    {
                        "article_id": article_id,
                        "source_type": "rss",
                        "source_name": feed_name,
                        "source_url": entry.get("link", ""),
                        "title": entry.get("title", "").strip(),
                        "body": body[:5000],
                        "author": entry.get("author", ""),
                        "published_at": published,
                        "fetched_at": now,
                        "source_metadata": json.dumps(metadata),
                        "date": published.date(),
                    }
                )

            logger.debug("rss_feed_parsed", feed=feed_name, articles=len(articles))
            return articles

        return self._retry_on_failure(_parse)  # type: ignore[no-any-return]


__all__ = ["RSSNewsFetcher"]
