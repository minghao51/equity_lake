"""Canonical filesystem paths for the project.

All constants are computed from ``PROJECT_ROOT`` (derived from ``__file__``).
No filesystem I/O happens at import time — call :func:`ensure_dirs` at
application startup to create directories.

This module is also the single home of the market vocabulary (ADR-0010):
the :data:`PRICE_MARKETS` registry maps each price market to its directory
constant, short alias, exchange MIC codes, and timezone. Every other module
(calendar, ingestion routing, storage views, monitoring, backtesting, CLI)
derives its market metadata from this registry.

Medallion layers
----------------
Storage follows a four-layer medallion architecture:

- **Bronze** (``01_bronze/``) — immutable raw data
- **Silver** (``02_silver/``) — validated, cleaned, deduped
- **Gold** (``03_gold/``) — feature engineering output
- **Platinum** (``04_platinum/``) — ML predictions and signals

Short aliases (``US_NEWS_DIR``, ``US_SOCIAL_SENTIMENT_DIR``,
``SEC_EXTRACTIONS_DIR``, …) point at the same medallion locations and are
used directly at several call sites.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"

DATA_DIR = PROJECT_ROOT / "data"
LAKE_DIR = PROJECT_ROOT / "data" / "lake"
#: Authorized second lake root for real-source rows (ADR-0030). It mirrors the
#: canonical numbered layout, is never cataloged, is never mixed with ``LAKE_DIR``
#: and is only ever selected explicitly (``--lake-dir``). Nothing defaults to it.
REAL_LAKE_DIR = PROJECT_ROOT / "data" / "lake_real"
LOGS_DIR = PROJECT_ROOT / "logs"

# Operator-artifact path contracts shared by writers and readers (the CLI
# ``monitor`` report and the pipeline results the dashboard exporter reads).
# Consumers compose ``LOGS_DIR / HEALTH_REPORT_FILENAME`` at call time so tests
# can monkeypatch ``LOGS_DIR`` on the command module; the pre-joined
# ``HEALTH_REPORT_PATH`` stays exported for callers that need the fixed path.
HEALTH_REPORT_FILENAME = "health-report.json"
HEALTH_REPORT_PATH: Path = LOGS_DIR / HEALTH_REPORT_FILENAME
PIPELINE_RESULTS_NAME_PREFIX = "pipeline_results_"
PIPELINE_RESULTS_GLOB = f"{PIPELINE_RESULTS_NAME_PREFIX}*.json"

MODELS_DIR = PROJECT_ROOT / "data" / "models"

# ---------------------------------------------------------------------------
# Medallion layer roots
# ---------------------------------------------------------------------------
BRONZE_DIR = LAKE_DIR / "01_bronze"
SILVER_DIR = LAKE_DIR / "02_silver"
GOLD_DIR = LAKE_DIR / "03_gold"
PLATINUM_DIR = LAKE_DIR / "04_platinum"

# ---------------------------------------------------------------------------
# Bronze layer (01_bronze/) — immutable raw data
# ---------------------------------------------------------------------------
BRONZE_MARKET_DATA_DIR = BRONZE_DIR / "market_data"
US_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "us_equity"
CN_ASHARE_DIR = BRONZE_MARKET_DATA_DIR / "cn_ashare"
HK_SG_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "hk_sg_equity"
JPX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "jpx_equity"
KRX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "krx_equity"
BRONZE_RAW_ARTICLES_DIR = BRONZE_DIR / "raw_articles"
BRONZE_MACRO_DIR = BRONZE_DIR / "macro"
# Corporate actions (ADR-0011) — market subdirectories nest underneath
# (e.g. .../corporate_actions/us_equity), resolved by Session B's router.
BRONZE_CORPORATE_ACTIONS_DIR = BRONZE_DIR / "corporate_actions"

# ---------------------------------------------------------------------------
# Silver layer (02_silver/) — validated, cleaned, deduped
# ---------------------------------------------------------------------------
SILVER_NEWS_SENTIMENT_DIR = SILVER_DIR / "news_sentiment"
SILVER_SOCIAL_SENTIMENT_DIR = SILVER_DIR / "social_sentiment"
SILVER_PROCESSED_ARTICLES_DIR = SILVER_DIR / "processed_articles"
SILVER_SEC_EXTRACTIONS_DIR = SILVER_DIR / "sec_extractions"
SILVER_ANALYST_RATINGS_DIR = SILVER_DIR / "analyst_ratings"
SILVER_SEC_FINANCIALS_DIR = SILVER_DIR / "sec_financials"
SILVER_CORPORATE_ACTIONS_DIR = SILVER_DIR / "corporate_actions"

# ---------------------------------------------------------------------------
# Gold layer (03_gold/) — feature engineering output
# ---------------------------------------------------------------------------
GOLD_FEATURES_DIR = GOLD_DIR / "features"

# ---------------------------------------------------------------------------
# Platinum layer (04_platinum/) — ML predictions and signals
# ---------------------------------------------------------------------------
PLATINUM_PREDICTIONS_DIR = PLATINUM_DIR / "predictions"

# ---------------------------------------------------------------------------
# Auxiliary tables (outside the medallion lake) — signal history bookkeeping
# ---------------------------------------------------------------------------
SIGNALS_DIR = DATA_DIR / "signals"
# Private tracking state lives outside the medallion lake and generated catalog.
WATCHLISTS_DIR = DATA_DIR / "watchlists"
PRIVATE_WATCHLIST_PATH = WATCHLISTS_DIR / "watchlist.yaml"
TRACKING_DIR = DATA_DIR / "tracking"
TRACKING_DB_PATH = TRACKING_DIR / "tracking.sqlite3"
# Research artifacts (FindingCards, backtest & risk reports) — NOT cataloged.
FINDINGS_DIR = DATA_DIR / "findings"
# Private portfolio ledger (ADR-0024) — auxiliary, NOT cataloged, never under
# data/lake/. Directory 0700 and database 0600 are applied by the writer.
PORTFOLIO_DIR = DATA_DIR / "portfolio"
PORTFOLIO_DB_PATH = PORTFOLIO_DIR / "portfolio.sqlite3"
# Data-quality profiles written by the explicit `equity validate profile` CLI path
# (in-ingest validation profiles stay in memory) — NOT cataloged.
PROFILES_DIR = DATA_DIR / "profiles"
# Default DuckDB scratch database for `equity query` — NOT cataloged.
DUCKDB_DEFAULT_PATH = DATA_DIR / "equity_data.duckdb"

# ---------------------------------------------------------------------------
# Short aliases for silver tables — used directly at their call sites
# (ingest CLI, sentiment signal generator, SEC processor)
# ---------------------------------------------------------------------------
US_NEWS_DIR = SILVER_NEWS_SENTIMENT_DIR
US_SOCIAL_SENTIMENT_DIR = SILVER_SOCIAL_SENTIMENT_DIR
SEC_EXTRACTIONS_DIR = SILVER_SEC_EXTRACTIONS_DIR


# ---------------------------------------------------------------------------
# Market vocabulary registry (ADR-0010)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceMarket:
    """Static metadata for one price market (a :data:`PRICE_MARKETS` entry).

    The bronze directory is stored as the *attribute name* of its paths
    constant and resolved at call time by :func:`market_dir`, so runtime
    patches of the module-level ``*_DIR`` constants keep working.
    """

    market: str  # canonical long key (also the bronze directory name)
    alias: str  # deprecated short key, accepted at boundaries only
    dir_attr: str  # attribute name of the bronze directory constant below
    exchanges: tuple[str, ...]  # exchange_calendars MIC codes
    timezone: str  # IANA timezone


PRICE_MARKETS: dict[str, PriceMarket] = {
    entry.market: entry
    for entry in (
        PriceMarket("us_equity", "us", "US_EQUITY_DIR", ("XNYS",), "America/New_York"),
        PriceMarket("cn_ashare", "cn", "CN_ASHARE_DIR", ("XSHG",), "Asia/Shanghai"),
        PriceMarket("hk_sg_equity", "hk_sg", "HK_SG_EQUITY_DIR", ("XHKG", "XSES"), "Asia/Hong_Kong"),
        PriceMarket("jpx_equity", "jpx", "JPX_EQUITY_DIR", ("JPX",), "Asia/Tokyo"),
        PriceMarket("krx_equity", "krx", "KRX_EQUITY_DIR", ("XKRX",), "Asia/Seoul"),
    )
}

#: The canonical market vocabulary exactly as this module defines it. Consumers that need
#: the DECLARED markets — static contract tables, per-market declarations — read this
#: rather than :data:`PRICE_MARKETS`, which tests legitimately narrow to scope a temporary
#: lake. Reading the patchable global at import time made a module's declarations depend on
#: whether a test had already patched it (a table missing ``cn_ashare`` and raising
#: ``KeyError`` only in a single-module test run).
CANONICAL_PRICE_MARKETS: dict[str, PriceMarket] = dict(PRICE_MARKETS)

# Derived alias maps — never hand-maintained (ADR-0010 Decision 2).
LONG_TO_SHORT: dict[str, str] = {entry.market: entry.alias for entry in PRICE_MARKETS.values()}
SHORT_TO_LONG: dict[str, str] = {entry.alias: entry.market for entry in PRICE_MARKETS.values()}

#: The only price market a real-source root may carry (ADR-0030 decision 5).
#: Single definition: the ``--lake-dir`` scope guard and the health monitor both
#: read it, so a real root can never be judged against a market it may not hold.
REAL_ROOT_PRICE_MARKETS: tuple[str, ...] = ("us_equity",)

# The ten single-form dataset identifiers that share the ingestion market
# vocabulary (VALID_MARKETS) but have no price-market duality (ADR-0010:
# out of scope for aliasing). ``ingestion.types`` re-exports this as
# OPTIONAL_ENRICHMENT_MARKETS; ``core.settings`` validates ``default_markets``
# against it.
OPTIONAL_ENRICHMENT_MARKETS: frozenset[str] = frozenset(
    {
        "macro",
        "us_news",
        "us_social_sentiment",
        "rss_news",
        "reddit_posts",
        "stocktwits_messages",
        "us_earnings_transcripts",
        "us_analyst_ratings",
        "sec_filings_fulltext",
        "us_sec_financials",
    }
)


def canonical_market(market: str) -> str:
    """Return the canonical long key for a price-market identifier.

    Accepts the canonical long form (``us_equity``) and the deprecated short
    alias (``us``). Anything else — including the enrichment dataset
    identifiers — raises ``ValueError``; a typo must fail loudly, never
    silently map to a calendar-less no-op (the ``_subtract_trading_days``
    infinite-loop root cause, ADR-0010).
    """
    if market in PRICE_MARKETS:
        return market
    if market in SHORT_TO_LONG:
        return SHORT_TO_LONG[market]
    raise ValueError(f"Unknown price market: {market!r}. Valid: {', '.join(PRICE_MARKETS)} (aliases: {', '.join(SHORT_TO_LONG)})")


def market_dir(market: str) -> Path:
    """Bronze directory for a price market, resolved at call time.

    The registry stores the *attribute name* of the directory constant, so the
    lookup goes through ``getattr`` on this module — runtime patches of the
    module-level ``*_DIR`` constants (e.g. tests pointing them at tmp dirs)
    are honoured.
    """
    entry = PRICE_MARKETS[canonical_market(market)]
    return cast(Path, getattr(sys.modules[__name__], entry.dir_attr))


#: Lake-relative dataset path of each price market (``01_bronze/market_data/<market>``).
#: Fixed at import time from the directory constants, so a dataset route resolves to a
#: market even when a test has patched those constants to a temporary lake.
PRICE_DATASET_PATHS: dict[str, str] = {
    entry.market: getattr(sys.modules[__name__], entry.dir_attr).relative_to(LAKE_DIR).as_posix() for entry in PRICE_MARKETS.values()
}


def price_dataset_market(dataset: str) -> str | None:
    """Resolve a dataset route to its price market, or ``None`` for a non-price table.

    Accepts the canonical long key, the deprecated short alias and the lake-relative
    medallion path. Returning ``None`` rather than raising keeps the callers (which
    route many dataset kinds through one writer) free of try/except around a normal
    case, while a genuine typo still surfaces from :func:`canonical_market` wherever a
    price market is actually required.
    """
    if dataset in PRICE_MARKETS:
        return dataset
    if dataset in SHORT_TO_LONG:
        return SHORT_TO_LONG[dataset]
    for market, path in PRICE_DATASET_PATHS.items():
        if dataset == path:
            return market
    return None


def ensure_dirs() -> None:
    """Create all required runtime directories.

    Safe to call multiple times. Intended for CLI entry-points only. The
    real-source root (``REAL_LAKE_DIR``) is deliberately not created here: it is
    created only by a write that was explicitly pointed at it (ADR-0030).
    """
    for d in (LAKE_DIR, LOGS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resolve_lake_root(explicit: Path | str | None) -> Path | None:
    """Validate an explicitly selected lake root (ADR-0030).

    ``None`` means "use the canonical ``LAKE_DIR``" and is returned unchanged, so
    every existing caller keeps its behaviour. An explicit root must resolve inside
    ``DATA_DIR`` (``resolve()`` follows symlinks, so an escape is caught), must not
    be ``data/`` itself and must not sit inside the canonical lake; anything else
    raises instead of writing somewhere unexpected. Containment lives at this seam,
    never inside ``delta_table_path``, so storage cannot silently redirect a write.
    """
    if explicit is None:
        return None
    requested = Path(explicit).expanduser()
    root = requested.resolve()
    data_root = DATA_DIR.resolve()
    canonical = LAKE_DIR.resolve()
    if root == data_root:
        raise ValueError("lake root must be a directory inside data/, not data/ itself")
    if not root.is_relative_to(data_root):
        raise ValueError(f"lake root must resolve under {data_root.as_posix()}")
    if root == canonical and requested.absolute() != LAKE_DIR:
        # A symlink (or a `..` path) that lands on the canonical lake would let an
        # explicit second-root write reach the demo root. Only the canonical path
        # itself may name the canonical root.
        raise ValueError("lake root resolves to the canonical lake; a second root must not alias it")
    if root != canonical and root.is_relative_to(canonical):
        # A root nested inside the demo root (`data/lake/01_bronze`) is not a second
        # root: its writes land under `data/lake/` (at `01_bronze/01_bronze/…`), which
        # ADR-0030 decision 10 forbids under any circumstance. Equality is handled
        # above, so only a strict descendant is refused here.
        raise ValueError("lake root resolves inside the canonical lake; the demo root is never written by a second root")
    return root


def _named_lake_root(path: Path) -> Path | None:
    """The lake-root constant *path* names lexically, or ``None`` for any other path."""
    for root in (LAKE_DIR, REAL_LAKE_DIR):
        if path == root or path.is_relative_to(root):
            return root
    return None


def refuse_cross_root_symlink(table_path: Path) -> None:
    """Refuse a lake path that reaches another root through a symlink (ADR-0030, RS-D01).

    The demo root and a real-source root are separate slices: a read or write is scoped
    to the root its caller named. A symlink planted inside one root and pointing at the
    other — ``data/lake_real/01_bronze`` -> ``data/lake/01_bronze``, or the reverse —
    would otherwise let one root serve the other's data while every path still looks
    locally contained. The same rule refuses a link that leaves its own root entirely.

    Refusal only: the path is never rewritten or redirected, so
    ``delta_table_path`` stays a plain join and no implicit redirection can hide in
    storage. A path that is not lexically under a known lake root (a test-lake
    ``--lake-dir``, for example) is returned untouched by the caller.

    Raises:
        ValueError: the path resolves into another lake root, or outside its own root.
    """
    named = _named_lake_root(table_path)
    if named is None:
        return
    root = named.resolve()
    target = table_path.resolve()
    for other in (LAKE_DIR, REAL_LAKE_DIR):
        if other == named:
            continue
        if target.is_relative_to(other.resolve()):
            raise ValueError(f"lake path {table_path} resolves into {other.name}; the demo and real roots are never mixed through a symlink")
    if target != root and not target.is_relative_to(root):
        raise ValueError(f"lake path {table_path} resolves outside its root {named}; a symlink must not redirect a lake root")


def is_real_source_root(lake_dir: Path | str | None) -> bool:
    """True when ``lake_dir`` selects a second, real-source root (ADR-0030).

    ``None`` — and the canonical path itself — is the demo root. Used by the price
    write boundary to decide whether vendor-basis rows are being stored (ADR-0029
    Part I only reconstructs there; the demo root's rows are never re-inflated).
    """
    if lake_dir is None:
        return False
    return Path(lake_dir).expanduser().resolve() != LAKE_DIR.resolve()


def price_markets_for_root(lake_dir: Path | str | None) -> tuple[str, ...]:
    """Price markets a selected root can legitimately hold (ADR-0030 decision 5).

    A real-source root carries the :data:`REAL_ROOT_PRICE_MARKETS` slice only, so a
    reader that iterates price markets — monitoring in particular — must scope to it
    instead of reporting four markets that may never exist there. The canonical root
    keeps the full registry.
    """
    return REAL_ROOT_PRICE_MARKETS if is_real_source_root(lake_dir) else tuple(PRICE_MARKETS)


__all__ = [
    "BRONZE_CORPORATE_ACTIONS_DIR",
    "BRONZE_DIR",
    "BRONZE_MACRO_DIR",
    "BRONZE_MARKET_DATA_DIR",
    "BRONZE_RAW_ARTICLES_DIR",
    "CN_ASHARE_DIR",
    "CONFIG_DIR",
    "DATA_DIR",
    "DUCKDB_DEFAULT_PATH",
    "FINDINGS_DIR",
    "GOLD_DIR",
    "GOLD_FEATURES_DIR",
    "HK_SG_EQUITY_DIR",
    "HEALTH_REPORT_FILENAME",
    "HEALTH_REPORT_PATH",
    "JPX_EQUITY_DIR",
    "KRX_EQUITY_DIR",
    "LAKE_DIR",
    "LONG_TO_SHORT",
    "LOGS_DIR",
    "MODELS_DIR",
    "OPTIONAL_ENRICHMENT_MARKETS",
    "PIPELINE_RESULTS_GLOB",
    "PIPELINE_RESULTS_NAME_PREFIX",
    "PLATINUM_DIR",
    "PLATINUM_PREDICTIONS_DIR",
    "PORTFOLIO_DB_PATH",
    "PORTFOLIO_DIR",
    "PRIVATE_WATCHLIST_PATH",
    "PRICE_DATASET_PATHS",
    "PRICE_MARKETS",
    "PriceMarket",
    "PROJECT_ROOT",
    "PROFILES_DIR",
    "REAL_LAKE_DIR",
    "resolve_lake_root",
    "SEC_EXTRACTIONS_DIR",
    "SHORT_TO_LONG",
    "SIGNALS_DIR",
    "TRACKING_DB_PATH",
    "TRACKING_DIR",
    "WATCHLISTS_DIR",
    "SILVER_ANALYST_RATINGS_DIR",
    "SILVER_CORPORATE_ACTIONS_DIR",
    "SILVER_DIR",
    "SILVER_NEWS_SENTIMENT_DIR",
    "SILVER_PROCESSED_ARTICLES_DIR",
    "SILVER_SEC_EXTRACTIONS_DIR",
    "SILVER_SEC_FINANCIALS_DIR",
    "SILVER_SOCIAL_SENTIMENT_DIR",
    "US_EQUITY_DIR",
    "US_NEWS_DIR",
    "US_SOCIAL_SENTIMENT_DIR",
    "canonical_market",
    "ensure_dirs",
    "is_real_source_root",
    "market_dir",
    "refuse_cross_root_symlink",
    "price_dataset_market",
    "price_markets_for_root",
]
