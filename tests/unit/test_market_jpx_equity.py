"""Offline feasibility pack for ``jpx_equity`` (RS-M03).

Proves what the repository already holds for this market — directory route, config
section, router entry, column contract, venue calendar/timezone, currency, catalog row
— and that every §2.9 contract it does not hold fails closed. It also pins the
``JPX``/``XTKS`` vocabulary split to a single venue, so a second exchange cannot be
added to this market silently. Offline only: no network call, no data acquisition,
no FX. Design record:
``docs/developer/architecture/market-jpx-equity-feasibility.md``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from runpy import run_path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from equity_lake.core.calendar import (
    classify_market_sessions,
    instrument_session_close,
    is_instrument_trading_day,
    is_trading_day,
    market_timezone,
    trading_days_between,
)
from equity_lake.core.config import TickerConfig
from equity_lake.core.instruments import InstrumentKey
from equity_lake.core.market_contracts import (
    CONTRACT_CAPABILITIES,
    MarketContractUnavailable,
    market_contract,
    require_capability,
)
from equity_lake.core.paths import (
    PRICE_DATASET_PATHS,
    REAL_ROOT_PRICE_MARKETS,
    canonical_market,
    market_dir,
)
from equity_lake.ingestion.corporate_actions import ingest_corporate_actions
from equity_lake.ingestion.router import MARKET_REGISTRY
from equity_lake.ingestion.types import MARKET_DIR_MAP, PRICE_MARKET_CONFIG_KEYS, VALID_MARKETS
from equity_lake.ingestion.writers import validate_schema

_pack = run_path(str(Path(__file__).with_name("_market_fixture_pack.py")))
fixture_digest = _pack["fixture_digest"]
fixture_sessions = _pack["fixture_sessions"]
load_ohlcv = _pack["load_ohlcv"]
round_trip_ohlcv = _pack["round_trip_ohlcv"]
OHLCV_VALUE_COLUMNS = _pack["OHLCV_VALUE_COLUMNS"]

MARKET = "jpx_equity"
GOLDEN_SHA256 = "e6691d0074a60ab4d0ceb2c4757280e9d5863d3873f23f41746d50ba4c76dfbc"
SESSIONS = (date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5))
IDENTITY_COLUMNS = ("market", "mic", "symbol", "date")
MIC = "XTKS"


def test_pack_is_the_committed_golden_file() -> None:
    """The pack is golden: any unrecorded edit of the CSV fails here."""
    assert fixture_digest(MARKET) == GOLDEN_SHA256


def test_registry_seams_are_present() -> None:
    """Directory, alias, route and fetcher seams all resolve for this market."""
    assert MARKET in VALID_MARKETS
    assert canonical_market("jpx") == MARKET
    assert market_dir("jpx") == market_dir(MARKET)
    assert market_dir(MARKET).name == MARKET
    assert MARKET_DIR_MAP[MARKET] == PRICE_DATASET_PATHS[MARKET] == "01_bronze/market_data/jpx_equity"
    assert MARKET in MARKET_REGISTRY
    # ADR-0030 decision 5 is not amended for this market: the real root stays us_equity-only.
    assert REAL_ROOT_PRICE_MARKETS == ("us_equity",)


def test_currency_is_declared_in_the_ticker_config() -> None:
    """The currency seam is the existing YAML section, not a new constant."""
    assert PRICE_MARKET_CONFIG_KEYS[MARKET] == ("jpx",)
    market_config = TickerConfig().get_market_info("jpx")
    assert market_config is not None
    assert market_config.currency == "JPY"


def test_fixture_sessions_are_exact_venue_sessions() -> None:
    """Calendar, timezone and session close clock, verified offline."""
    assert tuple(fixture_sessions(MARKET)) == SESSIONS
    assert trading_days_between(MARKET, SESSIONS[0], SESSIONS[-1]) == list(SESSIONS)
    assert str(market_timezone(MARKET)) == "Asia/Tokyo"

    for symbol in ("7203.T", "6758.T"):
        instrument = InstrumentKey(market=MARKET, mic=MIC, symbol=symbol)
        for session in SESSIONS:
            assert is_trading_day(MARKET, session) is True
            assert is_instrument_trading_day(instrument, session) is True
            close = instrument_session_close(instrument, session)
            assert close is not None and close.tzinfo is not None
            # The exact TSE session close, taken from the venue calendar — not a
            # weekday or a fixed UTC convention, and not another venue's close.
            assert close == datetime(2024, 6, session.day, 6, 0, tzinfo=UTC)
        assert instrument_session_close(instrument, date(2024, 6, 8)) is None


def test_jpx_and_xtks_are_one_venue_in_two_vocabularies() -> None:
    """The registry exchange is a calendar id, the MIC is identity, and one calendar serves both."""
    contract = market_contract(MARKET)
    # Registry vocabulary: a calendar identifier ("JPX", core/paths.py:158).
    assert contract.calendar_exchanges == ("JPX",)
    # Identity vocabulary: the venue MIC (core/instruments.py:63).
    assert contract.mics == frozenset({MIC})

    # One exchange means exclusion and union semantics cannot disagree.
    for session in SESSIONS:
        scheduled, not_scheduled = classify_market_sessions([MARKET], session)
        assert (scheduled, not_scheduled) == ([MARKET], [])
        assert trading_days_between(MARKET, session, session) == [session]

    # A non-session is a non-session for the market and for the instrument alike.
    holiday = date(2024, 6, 8)
    assert is_trading_day(MARKET, holiday) is False
    assert is_instrument_trading_day(InstrumentKey(market=MARKET, mic=MIC, symbol="7203.T"), holiday) is False
    assert trading_days_between(MARKET, holiday, holiday) == []


def test_fixture_round_trips_through_the_production_write_path(tmp_path: Path) -> None:
    """read fixture → validate → write to a temporary lake → read back → identity + values equal."""
    frame = load_ohlcv(MARKET)
    stored = round_trip_ohlcv(frame, MARKET, tmp_path)

    assert stored.height == frame.height
    assert set(stored["market"]) == {MARKET}
    # The fixture states the venue label only; MIC is derived by the identity seam.
    assert set(stored["mic"]) == {MIC}
    assert set(stored["symbol"]) == set(frame["ticker"])
    assert stored["identity_schema_version"].null_count() == 0

    expected = frame.with_columns(
        pl.lit(MARKET).alias("market"),
        pl.lit(MIC).alias("mic"),
        pl.col("ticker").alias("symbol"),
    ).select(*IDENTITY_COLUMNS, *OHLCV_VALUE_COLUMNS)
    actual = stored.select(*IDENTITY_COLUMNS, *OHLCV_VALUE_COLUMNS)

    assert_frame_equal(
        actual.sort(IDENTITY_COLUMNS),
        expected.sort(IDENTITY_COLUMNS),
        check_dtypes=False,
        check_column_order=False,
    )


def test_column_contract_fails_closed_on_a_missing_required_field() -> None:
    """Dropping any required OHLCV field refuses the write path's schema check."""
    frame = load_ohlcv(MARKET)
    assert validate_schema(frame, MARKET) is True
    for missing in ("ticker", "date", "open", "high", "low", "close", "volume"):
        assert validate_schema(frame.drop(missing), MARKET) is False


def test_contracts_the_repository_does_not_hold_fail_closed() -> None:
    """Volume/lot, price limits, settlement, rights and actions are declared ``unknown``."""
    contract = market_contract(MARKET)
    assert set(contract.capabilities) == set(CONTRACT_CAPABILITIES)
    unknown = {name for name, capability in contract.capabilities.items() if capability.state == "unknown"}
    assert unknown == set(CONTRACT_CAPABILITIES)
    for name, capability in contract.capabilities.items():
        assert capability.evidence, name
        assert capability.input_packet, name
        with pytest.raises(MarketContractUnavailable):
            require_capability(MARKET, name)


def test_corporate_action_ingestion_refuses_without_fetching_or_writing(tmp_path: Path) -> None:
    """ADR-0011 v1 is us_equity-only; the refusal precedes any fetcher construction."""
    assert market_contract(MARKET).capability("corporate_actions").state == "unknown"
    result = ingest_corporate_actions(MARKET, lake_dir=tmp_path)
    assert result == {"ok": False, "fetched": 0, "since": None, "market": MARKET}
    assert not (tmp_path / "01_bronze" / "corporate_actions").exists()
