"""Populate a demo US universe lake for the Strategy Lab.

Offline-safe by design: generates deterministic synthetic OHLCV by default, so the
whole showcase pipeline (arena -> FindingCards) runs with **no network or API
keys**. With ``real=True`` it attempts a live ``yfinance`` history pull and falls
back to synthetic on any failure.

Safety rails: the default target is the auxiliary sample lake
(``data/sample/``), mirroring ``cli/bootstrap.py cmd_sample``. Writing to the
canonical lake (``LAKE_DIR``) requires an explicit ``lake_dir`` targeting it
**plus** ``overwrite_production_lake=True`` — the CLI additionally enforces an
interactive confirmation (or the ``--overwrite-production-lake`` flag) before
passing that authorization through. Idempotent via ``mode="overwrite"``.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, cast

import polars as pl
import structlog

from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, InstrumentKey
from equity_lake.devtools.seeding import DEMO_UNIVERSE, OhlcvProfile, configured_identities, synthetic_ohlcv, trailing_business_days

logger = structlog.get_logger(__name__)

# The built-in 50-symbol demo universe lives in devtools/seeding.py (shared with
# ``equity bootstrap sample``) and is re-exported here for callers/tests.

US_EQUITY_MARKET = "01_bronze/market_data/us_equity"
US_EQUITY_SOURCE_MARKET = "us_equity"
# Provenance label for demo coverage proofs. Free-form in ``ActionCoverageProof``
# (unlike price ``identity_provenance``, which has a closed vocabulary).
COVERAGE_SOURCE = "synthetic_demo_seed"

# Demo-lake synthetic tuning; the random walk itself is shared.
DEMO_PROFILE = OhlcvProfile(
    price_range=(50.0, 500.0),
    drift=0.0002,
    volatility=0.015,
    range_scale=0.008,
    volume_mu=16.0,
    include_adj_close=False,
)


def _same_path(a: Path, b: Path) -> bool:
    """Path equality robust to case-insensitive filesystems (``DATA/lake`` vs ``data/lake``).

    When both paths exist, ``os.path.samefile`` compares the underlying store,
    so differently-cased spellings of the same directory compare equal. Otherwise
    fall back to a ``normcase`` string comparison (identity on case-sensitive
    platforms, case/slash-folded on case-insensitive ones).
    """
    if a.exists() and b.exists():
        try:
            return os.path.samefile(a, b)
        except OSError:
            pass
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _targets_production_lake(target_root: Path) -> bool:
    """True when the target root is the canonical lake or lives underneath it."""
    from equity_lake.core.paths import LAKE_DIR

    resolved = target_root.resolve()
    lake = LAKE_DIR.resolve()
    if _same_path(resolved, lake):
        return True
    return any(_same_path(lake, parent) for parent in resolved.parents)


def _try_real_fetch(tickers: list[str], years: float) -> pl.DataFrame | None:
    """Attempt a live yfinance history pull; return None on any failure."""
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_yfinance_unavailable", error=str(exc))
        return None

    frames: list[pl.DataFrame] = []
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period=f"{int(round(years))}y", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed_ticker_failed", ticker=t, error=str(exc))
            continue
        if hist is None or hist.empty:
            continue
        frames.append(
            pl.DataFrame(
                {
                    "ticker": t,
                    "date": [idx.date() for idx in hist.index],
                    "open": hist["Open"].to_numpy(),
                    "high": hist["High"].to_numpy(),
                    "low": hist["Low"].to_numpy(),
                    "close": hist["Close"].to_numpy(),
                    "volume": hist["Volume"].to_numpy(),
                }
            )
        )
    if not frames:
        logger.warning("seed_real_fetch_empty")
        return None
    return pl.concat(frames).drop_nulls(subset=["close"])


def resolve_universe(tickers: list[str] | None) -> list[str]:
    """Resolve the demo universe: explicit > config `demo` group > built-in default."""
    if tickers:
        return [t.strip().upper() for t in tickers if t.strip()]
    try:
        from equity_lake.core.config import TickerConfig

        group = TickerConfig().get_tickers_by_group("demo", active_only=True)
        if group:
            return group
    except Exception as exc:  # noqa: BLE001
        logger.debug("seed_demo_group_unavailable", error=str(exc))
    return list(DEMO_UNIVERSE)


def stamp_identity(df: pl.DataFrame, identities: dict[str, InstrumentKey]) -> pl.DataFrame:
    """Stamp the canonical composite identity (ADR-0016) on seeded price rows.

    Seeded rows land in the same Bronze table real ingestion writes, so they must
    carry the same identity contract; the MIC is config-derived, and the honest
    provenance label for that is ``current_ticker_config``.
    """
    by_symbol = {symbol: key.mic for symbol, key in identities.items()}
    return df.with_columns(
        pl.col("ticker").replace_strict({symbol: key.market for symbol, key in identities.items()}, return_dtype=pl.String).alias("market"),
        pl.col("ticker").replace_strict(by_symbol, return_dtype=pl.String).alias("mic"),
        pl.col("ticker").alias("symbol"),
        pl.lit(IDENTITY_SCHEMA_VERSION, dtype=pl.Int64).alias("identity_schema_version"),
        pl.lit("current_ticker_config", dtype=pl.String).alias("identity_provenance"),
    )


def coverage_proofs(df: pl.DataFrame, identities: dict[str, InstrumentKey]) -> pl.DataFrame:
    """Build the source-scoped coverage proof the point-in-time gate requires.

    ADR-0015 refuses to treat empty action data as proof of "no actions".
    Synthetic prices are split-free by construction, so the generator itself is
    the source: one complete, empty-interval proof per instrument, observable
    from the first seeded session and fresh through the last one.
    """
    from equity_lake.core.availability import ActionCoverageProof

    coverage_start = cast(date, df["date"].min())
    coverage_end = cast(date, df["date"].max())
    proofs = [
        ActionCoverageProof(
            market=identities[symbol].market,
            mic=identities[symbol].mic,
            symbol=identities[symbol].symbol,
            source=COVERAGE_SOURCE,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            source_available_at=datetime.combine(coverage_start, time.min, tzinfo=UTC),
            source_valid_until=datetime.combine(coverage_end, time.max, tzinfo=UTC),
        )
        for symbol in sorted(df["ticker"].unique().to_list())
    ]
    return pl.DataFrame([proof.as_row() for proof in proofs])


def seed_demo(
    *,
    years: float = 5.0,
    tickers: list[str] | None = None,
    real: bool = False,
    seed: int = 42,
    verbose: bool = False,
    lake_dir: str | Path | None = None,
    overwrite_production_lake: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seed a demo US universe into a lake.

    Args:
        years: Years of history to generate/fetch.
        tickers: Explicit ticker list; ``None`` resolves the `demo` config group
            (falling back to :data:`DEMO_UNIVERSE`).
        real: Attempt a live yfinance pull; fall back to synthetic on failure.
        seed: Deterministic seed for synthetic generation.
        verbose: Debug logging.
        lake_dir: Lake root override. Defaults to the auxiliary sample lake
            (``data/sample/``); targeting the canonical ``LAKE_DIR`` (or a path
            under it) requires ``overwrite_production_lake=True``.
        overwrite_production_lake: Explicit authorization to overwrite the
            canonical lake. Without it, a production-lake target raises
            :class:`ValueError` instead of writing.
        dry_run: Preview the seed summary without writing anything.

    Returns:
        Summary dict: ``{tickers, rows, days, source, path, coverage_proofs, dry_run}``.
    """
    from equity_lake.core.logging import setup_structured_logging
    from equity_lake.core.paths import DATA_DIR
    from equity_lake.storage.delta import write_delta

    setup_structured_logging(level="DEBUG" if verbose else "INFO")

    universe = resolve_universe(tickers)
    if not universe:
        raise ValueError("No tickers resolved for demo seed.")

    target_root = Path(lake_dir) if lake_dir is not None else DATA_DIR / "sample"
    production_target = _targets_production_lake(target_root)
    if production_target and not overwrite_production_lake:
        raise ValueError(
            f"Refusing to overwrite the canonical lake at {target_root} without an explicit override. "
            f"Seed the default sample lake ({DATA_DIR / 'sample'}) instead, or pass --lake together with "
            "--overwrite-production-lake (or confirm interactively) to authorize the bronze overwrite."
        )

    source = "synthetic"
    df: pl.DataFrame | None = None
    if real:
        logger.info("seed_demo_real_fetch", tickers=len(universe), years=years)
        df = _try_real_fetch(universe, years)
        if df is not None:
            source = "real"

    if df is None:
        days = trailing_business_days(int(years * 365))
        df = synthetic_ohlcv(universe, days, profile=DEMO_PROFILE, seed=seed)
        logger.info("seed_demo_synthetic", tickers=len(universe), days=len(days))

    if df.is_empty():
        raise ValueError("Seed produced no rows.")

    # Bare tickers are not identity (ADR-0016): stamp the same composite identity
    # the real ingestion path writes, resolved from the source config.
    identities = configured_identities(sorted(df["ticker"].unique().to_list()))
    df = stamp_identity(df, identities)
    proofs = coverage_proofs(df, identities)

    if dry_run:
        summary: dict[str, Any] = {
            "tickers": df["ticker"].n_unique(),
            "rows": df.height,
            "days": df["date"].n_unique(),
            "source": source,
            "path": str(target_root),
            "coverage_proofs": proofs.height,
            "dry_run": True,
        }
        logger.info("seed_demo_dry_run", **summary)
        return summary

    if production_target:
        logger.warning(
            "seed_demo_overwrite_production_lake",
            path=str(target_root),
            table=US_EQUITY_MARKET,
            mode="overwrite",
        )

    write_delta(df, US_EQUITY_MARKET, mode="overwrite", lake_dir=target_root)
    # Point-in-time consumers (arena, features, labels) require a source-scoped
    # coverage proof; without it they fail closed on every decision date.
    from equity_lake.ingestion.corporate_actions import corporate_action_coverage_table

    coverage_table = corporate_action_coverage_table(US_EQUITY_SOURCE_MARKET)
    write_delta(proofs, coverage_table, mode="overwrite", lake_dir=target_root, partition_by=["coverage_end"])

    summary = {
        "tickers": df["ticker"].n_unique(),
        "rows": df.height,
        "days": df["date"].n_unique(),
        "source": source,
        "path": str(target_root),
        "coverage_proofs": proofs.height,
        "dry_run": False,
    }
    logger.info("seed_demo_complete", **summary)
    return summary


__all__ = ["COVERAGE_SOURCE", "DEMO_UNIVERSE", "coverage_proofs", "resolve_universe", "seed_demo", "stamp_identity"]
