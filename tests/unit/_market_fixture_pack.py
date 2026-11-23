"""Golden fixture-pack harness for the per-market feasibility packs (RS-M01..RS-M04).

Each non-US price market has a committed pack under
``tests/fixtures/markets/<market>/ohlcv.csv``. The rows were generated once by the
existing deterministic seeder (:func:`equity_lake.devtools.seeding.synthetic_ohlcv`,
documented in ``tests/fixtures/markets/README.md``) and are then frozen: the
digest assertion in each market's test makes an unrecorded edit fail, so the file
is golden rather than a sample.

The round-trip below is the production path, not a reimplementation: column
contract (``ingestion.writers.validate_schema``) → directory route
(``MARKET_DIR_MAP``) → merge write into a temporary Delta root
(``ingestion.writers.upsert_dataset``) → read back (``storage.delta.read_delta``).
Nothing here touches the canonical lake, the real root, the network or FX.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import cast

import polars as pl

from equity_lake.ingestion.types import MARKET_DIR_MAP  # type: ignore[import-untyped]
from equity_lake.ingestion.writers import upsert_dataset, validate_schema  # type: ignore[import-untyped]
from equity_lake.storage.delta import read_delta  # type: ignore[import-untyped]

#: Committed pack root (``tests/fixtures/markets``).
PACK_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "markets"

#: The one committed file per market, and its explicit schema. ``exchange`` is the
#: venue label each source supplies; MIC is derived by the production identity
#: seam, never stated by the fixture.
OHLCV_FILENAME = "ohlcv.csv"
OHLCV_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.String(),
    "exchange": pl.String(),
    "date": pl.Date(),
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),
    "volume": pl.Int64(),
    "adj_close": pl.Float64(),
}
#: Value columns compared row-for-row after the round trip.
OHLCV_VALUE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume", "adj_close")


def fixture_path(market: str, filename: str = OHLCV_FILENAME) -> Path:
    """Path of a committed fixture file for *market*."""
    return PACK_ROOT / market / filename


def fixture_digest(market: str, filename: str = OHLCV_FILENAME) -> str:
    """SHA-256 of a committed fixture file (the golden-pack digest)."""
    return hashlib.sha256(fixture_path(market, filename).read_bytes()).hexdigest()


def load_ohlcv(market: str) -> pl.DataFrame:
    """Load a market's fixture with the explicit pack schema (dates parsed as ``Date``)."""
    return pl.read_csv(fixture_path(market), schema_overrides=OHLCV_SCHEMA)


def fixture_sessions(market: str) -> list[date]:
    """Distinct fixture dates, ascending — the sessions the pack exercises."""
    return sorted(set(load_ohlcv(market)["date"].to_list()))


def round_trip_ohlcv(frame: pl.DataFrame, market: str, lake_dir: Path) -> pl.DataFrame:
    """Validate, write into ``lake_dir`` through the market route, and read back.

    Args:
        frame: Fixture frame (``ticker, exchange, date`` + OHLCV values).
        market: Canonical price-market key; its ``MARKET_DIR_MAP`` route is the
            table path, so a wrong route cannot pass silently.
        lake_dir: A temporary lake root. Never the canonical or real root.

    Returns:
        The stored frame as read back from Delta.
    """
    assert validate_schema(frame, market) is True, f"fixture does not satisfy the {market} column contract"
    table = MARKET_DIR_MAP[market]
    trading_date = cast(date, frame["date"].max())
    assert upsert_dataset(frame, table, trading_date, lake_dir=lake_dir) is True
    return cast(pl.DataFrame, read_delta(table, lake_dir=lake_dir))
