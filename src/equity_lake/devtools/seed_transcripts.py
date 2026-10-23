"""Seed the earnings-call transcript corpus from a HuggingFace dataset.

Loads the full S&P 500 earnings-transcripts dataset
(``kurry/sp500_earnings_transcripts`` — ~33k transcripts, 2005-2025) into bronze
``01_bronze/raw_articles`` as an expandable base, and can preview a ticker-scoped subset for silver ``02_silver/processed_articles``.
Paid enrichment is fail-closed until a bounded provider adapter reports actual cost.

Why a devtools script (not a production source): the dataset is a static snapshot,
not a live feed. Bronze holds the full base for future ticker expansion; silver
enrichment is deliberately disabled until a bounded provider actual-cost adapter
exists. The script mirrors the SEC bronze schema
exactly (ticker in ``source_metadata``; ``published_at``/``fetched_at`` cast to
microseconds to match the existing table) — no schema, catalog, CLI, or boundary
changes.

Re-runnable: deterministic ``article_id`` (``uuid5`` over ``symbol/year/quarter``)
keeps bronze merges idempotent. Real Silver writes are currently rejected.

Usage::

    uv run python -m equity_lake.devtools.seed_transcripts --skip-silver
    uv run python -m equity_lake.devtools.seed_transcripts --dry-run  # preview: no lake writes or LLM tokens

Exit code: 0 only when every requested step (bronze/silver) reports ``ok=True``;
any failure exits 1.

Non-dry-run executions that request Silver enrichment exit before download or
bronze writes. Dry-run previews the requested scope; ``--skip-silver`` permits
bronze-only loading.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import structlog

from equity_lake.core.paths import DATA_DIR
from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS

logger = structlog.get_logger(__name__)

HF_DATASET = "kurry/sp500_earnings_transcripts"
HF_PARQUET_URL = f"https://huggingface.co/datasets/{HF_DATASET}/resolve/main/parquet_files/part-0.parquet"
HF_DATASET_URL = f"https://huggingface.co/datasets/{HF_DATASET}"
BRONZE_MARKET = "01_bronze/raw_articles"
SOURCE_TYPE = "earnings_transcript"
SOURCE_NAME = "huggingface:kurry/sp500_earnings_transcripts"
CACHE_PATH = DATA_DIR / ".cache" / "hf_sp500_earnings.parquet"

# Only the columns we need from the HF parquet (drop structured_content/company_id to bound memory).
_HF_COLUMNS = ["symbol", "quarter", "year", "date", "content", "company_name"]


def _ensure_cached(*, force: bool) -> Path:
    """Download the HF parquet to a local cache (idempotent across re-runs)."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists() and not force:
        logger.info("seed_transcripts_cache_hit", path=str(CACHE_PATH), size_mb=round(CACHE_PATH.stat().st_size / 1e6, 1))
        return CACHE_PATH
    logger.info("seed_transcripts_download_start", url=HF_PARQUET_URL)
    with httpx.stream("GET", HF_PARQUET_URL, follow_redirects=True, timeout=httpx.Timeout(30.0, read=None)) as resp:
        resp.raise_for_status()
        try:
            with open(CACHE_PATH, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
        except OSError as exc:
            CACHE_PATH.unlink(missing_ok=True)
            raise RuntimeError(f"Unable to cache the transcript dataset at {CACHE_PATH}") from exc
    logger.info("seed_transcripts_download_done", size_mb=round(CACHE_PATH.stat().st_size / 1e6, 1))
    return CACHE_PATH


def _load_hf(path: Path) -> pl.DataFrame:
    """Read the cached HF parquet (selected columns only)."""
    return pl.read_parquet(path, columns=_HF_COLUMNS)


def _to_bronze(hf: pl.DataFrame, fetch_ts: datetime) -> pl.DataFrame:
    """Map HF transcript rows onto the canonical ``BRONZE_ARTICLE_COLUMNS`` schema.

    Deterministic ``article_id`` (uuid5 over ``symbol/year/quarter``) makes the
    upsert idempotent. Ticker lives in ``source_metadata`` (mirrors SEC rows);
    temporal columns are cast to microseconds to match the existing table.
    """
    df = hf.rename({"content": "body"})
    df = df.with_columns(
        pl.col("date").str.strptime(pl.Datetime("us"), "%Y-%m-%d %H:%M:%S", strict=False).alias("published_at"),
    ).with_columns(pl.col("published_at").dt.date().alias("date"))

    tickers = df["symbol"].str.to_uppercase().to_list()
    years = df["year"].to_list()
    quarters = df["quarter"].to_list()
    cnames = df["company_name"].fill_null("").to_list()

    article_ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"hf_kurry_{t}_{y}_{q}")) for t, y, q in zip(tickers, years, quarters, strict=True)]
    metas = [
        json.dumps({"ticker": t, "quarter": q, "year": y, "company_name": c}) for t, q, y, c in zip(tickers, quarters, years, cnames, strict=True)
    ]
    titles = [f"{t} {c} Q{q} {y} Earnings Call".strip() for t, c, q, y in zip(tickers, cnames, quarters, years, strict=True)]
    urls = [f"{HF_DATASET_URL}#{t}_{y}_Q{q}" for t, y, q in zip(tickers, years, quarters, strict=True)]

    df = df.with_columns(
        pl.Series("article_id", article_ids),
        pl.Series("source_metadata", metas),
        pl.Series("title", titles),
        pl.Series("source_url", urls),
        pl.Series("author", cnames),
        pl.lit(SOURCE_TYPE).alias("source_type"),
        pl.lit(SOURCE_NAME).alias("source_name"),
        pl.lit(fetch_ts).cast(pl.Datetime("us")).alias("fetched_at"),
    )
    df = df.drop_nulls(subset=["date"])

    for col in BRONZE_ARTICLE_COLUMNS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))
    return df.select(BRONZE_ARTICLE_COLUMNS)


def seed_transcripts_bronze(hf: pl.DataFrame, fetch_ts: datetime, *, dry_run: bool = False) -> dict[str, Any]:
    """Write the full transcript base into bronze ``01_bronze/raw_articles``.

    Idempotent: merges on ``article_id`` (deterministic), so re-runs upsert
    without duplicating. With ``dry_run=True`` nothing is written; the summary
    previews the rows that a real run would merge.
    """
    from equity_lake.storage.delta import DeltaMergeError, merge_delta

    df = _to_bronze(hf, fetch_ts)
    try:
        n_tickers = df["source_metadata"].map_elements(lambda value: json.loads(value).get("ticker"), return_dtype=pl.Utf8).n_unique()
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise ValueError("generated transcript metadata contains an invalid ticker record") from exc
    if dry_run:
        summary: dict[str, Any] = {"rows": df.height, "tickers": int(n_tickers), "ok": True, "dry_run": True}
        logger.info("seed_transcripts_bronze_dry_run", **summary)
        return summary
    try:
        ok = merge_delta(df, BRONZE_MARKET, key_columns=["article_id"])
    except DeltaMergeError:
        ok = False
    summary = {"rows": df.height if ok else 0, "tickers": int(n_tickers) if ok else 0, "ok": ok}
    logger.info("seed_transcripts_bronze_complete", **summary)
    return summary


def seed_transcripts_silver(hf: pl.DataFrame, tickers: list[str], fetch_ts: datetime, *, dry_run: bool = False) -> dict[str, Any]:
    """Preview a ticker scope; paid Silver enrichment fails closed until bounded cost reporting exists.

    Filtering happens on the in-memory HF frame because ticker is not a bronze
    column. ``dry_run=True`` previews the scope without paid processing or writes.
    A real run is rejected rather than calling the unbounded legacy LLM helper.
    """

    wanted = {t.strip().upper() for t in tickers if t.strip()}
    scoped = hf.filter(pl.col("symbol").str.to_uppercase().is_in(sorted(wanted)))
    if scoped.is_empty():
        logger.warning("seed_transcripts_silver_empty", tickers=sorted(wanted))
        return {"rows": 0, "tickers": 0, "ok": False}

    bronze = _to_bronze(scoped, fetch_ts)
    logger.info("seed_transcripts_silver_processing", rows=bronze.height, tickers=sorted(wanted), dry_run=dry_run)
    if dry_run:
        summary: dict[str, Any] = {
            "rows": bronze.height,
            "tickers": int(scoped["symbol"].str.to_uppercase().n_unique()),
            "ok": True,
            "dry_run": True,
        }
        logger.info("seed_transcripts_silver_dry_run", **summary)
        return summary

    logger.error(
        "seed_transcripts_silver_disabled",
        reason="no bounded provider actual-cost adapter is available; no LLM call or Silver write was attempted",
    )
    return {
        "rows": 0,
        "tickers": 0,
        "ok": False,
        "mode": "paid_processing_disabled",
        "error": "transcript LLM processing is disabled until bounded provider actual-cost reporting is available",
    }


def main() -> int:
    import argparse

    from equity_lake.core.logging import setup_structured_logging

    parser = argparse.ArgumentParser(description="Seed earnings-call transcript corpus from HuggingFace.")
    parser.add_argument("--tickers", default="AAPL,MSFT,GOOGL", help="Comma-separated tickers for scoped silver enrichment")
    parser.add_argument("--skip-bronze", action="store_true", help="Skip loading the full bronze base")
    parser.add_argument("--skip-silver", action="store_true", help="Skip scoped silver enrichment")
    parser.add_argument("--force-download", action="store_true", help="Re-download the HF parquet even if cached")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview bronze/silver scope with no lake writes or LLM tokens (a cold cache still downloads the source parquet)",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    setup_structured_logging(level="DEBUG" if args.verbose else "INFO")
    if not args.skip_silver and not args.dry_run:
        logger.error(
            "seed_transcripts_paid_processing_disabled",
            reason="no bounded provider actual-cost adapter is available; no download, bronze write, or LLM call was attempted",
        )
        return 1

    fetch_ts = datetime.now(UTC).replace(tzinfo=None)

    hf: pl.DataFrame | None = None
    if not args.skip_bronze or not args.skip_silver:
        path = _ensure_cached(force=args.force_download)
        hf = _load_hf(path)
        logger.info("seed_transcripts_hf_loaded", rows=hf.height, path=str(path))

    bronze_summary: dict[str, Any] = {}
    if not args.skip_bronze and hf is not None:
        bronze_summary = seed_transcripts_bronze(hf, fetch_ts, dry_run=args.dry_run)

    silver_summary: dict[str, Any] = {}
    if not args.skip_silver and hf is not None:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        silver_summary = seed_transcripts_silver(hf, tickers, fetch_ts, dry_run=args.dry_run)

    logger.info("seed_transcripts_done", bronze=bronze_summary, silver=silver_summary, dry_run=args.dry_run)

    # A requested step reporting ok=False (merge failure, empty scope, or no LLM
    # output) must surface as a non-zero exit, not a silent success.
    failures = [name for name, s in (("bronze", bronze_summary), ("silver", silver_summary)) if s and not s.get("ok")]
    if failures:
        logger.error("seed_transcripts_failed", failed_steps=failures)
        return 1
    return 0


__all__ = [
    "seed_transcripts_bronze",
    "seed_transcripts_silver",
]


if __name__ == "__main__":
    raise SystemExit(main())
