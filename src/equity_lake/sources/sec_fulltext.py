"""SEC EDGAR full-text filing fetcher for US equities.

Fetches recent 10-K and 10-Q filings from SEC EDGAR, extracts clean text
using ``readability-lxml``, segments into standard sections (Item 1A Risk
Factors, Item 7 MD&A), and stores each section as a bronze article row
(``source_type="sec_filing"``).

Uses the SEC submissions API to discover filings, then downloads the
primary document for full-text extraction. SEC rate limit: 10 req/s.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import polars as pl
import structlog

from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame

logger = structlog.get_logger()

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dash}/{doc}"
SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"

TARGET_FORM_TYPES = {"10-K", "10-Q"}

SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("risk_factors", re.compile(r"\bitem\s*1a\.?\s*risk\s*factors\b", re.IGNORECASE)),
    ("mda", re.compile(r"\bitem\s*7\.?\s*management[']?s?\s*discussion\s*and\s*analysis\b", re.IGNORECASE)),
]

MAX_SECTION_CHARS = 8000
MAX_FILING_LOOKBACK_DAYS = 120


class SECFilingFetcher(MarketDataFetcher):
    """Fetch and segment SEC 10-K/10-Q filings into bronze article rows.

    Each filing section becomes one row in ``BRONZE_ARTICLE_COLUMNS`` format
    with ``source_type="sec_filing"``. Section metadata (filing type, section
    name, accession number) is stored in ``source_metadata`` as JSON.
    """

    market = "sec_filings_fulltext"

    def __init__(
        self,
        tickers: list[str] | None = None,
        user_agent: str | None = None,
        lookback_days: int = MAX_FILING_LOOKBACK_DAYS,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.tickers = tickers or []
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT", "Equity Lake contact@example.com")
        self.lookback_days = lookback_days
        self._ticker_cik_cache: dict[str, int] = {}
        logger.info("Initialized SECFilingFetcher", ticker_count=len(self.tickers), lookback_days=lookback_days)

    def fetch(self, trading_date: date) -> pl.DataFrame:
        if not self.tickers:
            logger.warning("No tickers configured for SEC filings")
            return _empty_frame()

        logger.info("Fetching SEC filings", ticker_count=len(self.tickers), trading_date=str(trading_date))

        cutoff_date = trading_date - timedelta(days=self.lookback_days)
        self._load_ticker_cik_map()

        all_articles: list[dict[str, Any]] = []
        for ticker in self.tickers:
            try:
                articles = self._fetch_ticker(ticker, trading_date, cutoff_date)
                all_articles.extend(articles)
            except Exception as exc:
                logger.error("sec_filing_fetch_failed", ticker=ticker, error=str(exc))

        if not all_articles:
            logger.info("No SEC filings found in lookback window")
            return _empty_frame()

        df = pl.DataFrame(all_articles)
        for col in BRONZE_ARTICLE_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        df = df.select(BRONZE_ARTICLE_COLUMNS)
        logger.info("Fetched SEC filing sections", count=df.height)
        return df

    def _load_ticker_cik_map(self) -> None:
        if self._ticker_cik_cache:
            return

        def _fetch() -> dict[str, int]:
            headers = {"User-Agent": self.user_agent}
            with httpx.Client(timeout=15) as client:
                resp = client.get(SEC_TICKER_URL, headers=headers)
                resp.raise_for_status()
                payload: Any = resp.json()

            return {entry["ticker"].upper(): int(entry["cik_str"]) for entry in payload.values()}

        self._ticker_cik_cache = self._retry_on_failure(_fetch)

    def _fetch_ticker(self, ticker: str, trading_date: date, cutoff_date: date) -> list[dict[str, Any]]:
        cik = self._ticker_cik_cache.get(ticker.upper())
        if cik is None:
            logger.warning("Unknown SEC ticker", ticker=ticker)
            return []

        filings = self._get_recent_filings(cik, cutoff_date, trading_date)
        if not filings:
            return []

        articles: list[dict[str, Any]] = []
        for filing in filings:
            try:
                sections = self._download_and_extract(ticker, cik, filing)
                articles.extend(sections)
            except Exception as exc:
                logger.error("sec_filing_parse_failed", ticker=ticker, accession=filing.get("accession"), error=str(exc))

        return articles

    def _get_recent_filings(self, cik: int, cutoff_date: date, end_date: date) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            url = SEC_SUBMISSIONS_URL.format(cik=f"{cik:010d}")
            headers = {"User-Agent": self.user_agent}
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                payload: Any = resp.json()

            recent = payload.get("filings", {}).get("recent", {})
            filing_dates: list[str] = recent.get("filingDate", [])
            forms: list[str] = recent.get("form", [])
            accessions: list[str] = recent.get("accessionNumber", [])
            primary_docs: list[str] = recent.get("primaryDocument", [])

            results: list[dict[str, Any]] = []
            for fd, form, acc, doc in zip(filing_dates, forms, accessions, primary_docs, strict=False):
                if form not in TARGET_FORM_TYPES:
                    continue
                filing_day = date.fromisoformat(fd)
                if not (cutoff_date <= filing_day <= end_date):
                    continue
                results.append(
                    {
                        "filing_date": filing_day,
                        "form_type": form,
                        "accession": acc,
                        "primary_doc": doc,
                    }
                )
            return results

        return self._retry_on_failure(_fetch)  # type: ignore[no-any-return]

    def _download_and_extract(
        self,
        ticker: str,
        cik: int,
        filing: dict[str, Any],
    ) -> list[dict[str, Any]]:
        accession = filing["accession"]
        acc_no_dash = accession.replace("-", "")
        doc_url = SEC_ARCHIVES_URL.format(cik=cik, acc_no_dash=acc_no_dash, doc=filing["primary_doc"])

        def _download() -> list[dict[str, Any]]:
            headers = {"User-Agent": self.user_agent}
            with httpx.Client(timeout=30) as client:
                resp = client.get(doc_url, headers=headers)
                resp.raise_for_status()
                html = resp.text

            sections = self._extract_sections(html)
            if not sections:
                logger.debug("sec_filing_no_sections_found", ticker=ticker, url=doc_url)
                return []

            now = datetime.now()
            filing_date = filing["filing_date"]
            results: list[dict[str, Any]] = []

            for section_type, body in sections:
                article_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sec_{accession}_{section_type}"))
                metadata = {
                    "ticker": ticker,
                    "cik": cik,
                    "filing_type": filing["form_type"],
                    "accession": accession,
                    "section": section_type,
                    "document_url": doc_url,
                }
                results.append(
                    {
                        "article_id": article_id,
                        "source_type": "sec_filing",
                        "source_name": "sec_edgar",
                        "source_url": doc_url,
                        "title": f"{ticker} {filing['form_type']} — {section_type.replace('_', ' ').title()}",
                        "body": body[:MAX_SECTION_CHARS],
                        "author": "",
                        "published_at": datetime.combine(filing_date, datetime.min.time()),
                        "fetched_at": now,
                        "source_metadata": json.dumps(metadata),
                        "date": filing_date,
                    }
                )
            return results

        result = self._retry_on_failure(_download)
        time.sleep(0.15)
        return result  # type: ignore[no-any-return]

    def _extract_sections(self, html: str) -> list[tuple[str, str]]:
        try:
            from readability import Document

            doc = Document(html)
            clean_text = doc.summary()
            text = _strip_html_tags(clean_text)
        except Exception as exc:
            logger.debug("readability_extraction_failed", error=str(exc))
            text = _strip_html_tags(html)

        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            return []

        boundaries: list[tuple[str, int]] = []
        for section_name, pattern in SECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                boundaries.append((section_name, match.start()))

        boundaries.sort(key=lambda x: x[1])

        if not boundaries:
            return []

        sections: list[tuple[str, str]] = []
        for i, (section_name, start) in enumerate(boundaries):
            end = boundaries[i + 1][1] if i + 1 < len(boundaries) else len(text)
            section_text = text[start:end].strip()
            if len(section_text) > 50:
                sections.append((section_name, section_text))

        return sections


def _strip_html_tags(html: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"&nbsp;|&#160;", " ", clean)
    clean = re.sub(r"&amp;", "&", clean)
    clean = re.sub(r"&lt;", "<", clean)
    clean = re.sub(r"&gt;", ">", clean)
    clean = re.sub(r"&quot;", '"', clean)
    clean = re.sub(r"&#\d+;", "", clean)
    return clean


__all__ = ["SECFilingFetcher"]
