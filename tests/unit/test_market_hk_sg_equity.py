"""Offline feasibility pack for ``hk_sg_equity`` (RS-M02).

Proves what the repository already holds for this market — directory route, config
sections, router entry, column contract, both venue calendars, both currencies, catalog
row — and that every §2.9 contract it does not hold fails closed. It also proves the
one property this market exists for: HKEX (``XHKG``/``HKD``) and SGX (``XSES``/``SGD``)
are two venues, never one. Offline only: no network call, no data acquisition, no FX.
Design record: ``docs/developer/architecture/market-hk-sg-equity-feasibility.md``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from runpy import run_path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from equity_lake.core.calendar import (  # type: ignore[import-untyped]
    classify_market_sessions,
    instrument_session_close,
    is_instrument_trading_day,
    is_trading_day,
    market_timezone,
    trading_days_between,
)
from equity_lake.core.config import TickerConfig  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.core.market_contracts import (  # type: ignore[import-untyped]
    CONTRACT_CAPABILITIES,
    MarketContractUnavailable,
    market_contract,
    require_capability,
)
from equity_lake.core.paths import (  # type: ignore[import-untyped]
    PRICE_DATASET_PATHS,
    REAL_ROOT_PRICE_MARKETS,
    canonical_market,
    market_dir,
)
from equity_lake.ingestion.corporate_actions import ingest_corporate_actions  # type: ignore[import-untyped]
from equity_lake.ingestion.router import MARKET_REGISTRY  # type: ignore[import-untyped]
from equity_lake.ingestion.types import MARKET_DIR_MAP, PRICE_MARKET_CONFIG_KEYS, VALID_MARKETS  # type: ignore[import-untyped]
from equity_lake.ingestion.writers import validate_schema  # type: ignore[import-untyped]

_pack = run_path(str(Path(__file__).with_name("_market_fixture_pack.py")))
fixture_digest = _pack["fixture_digest"]
fixture_sessions = _pack["fixture_sessions"]
load_ohlcv = _pack["load_ohlcv"]
round_trip_ohlcv = _pack["round_trip_ohlcv"]
OHLCV_VALUE_COLUMNS = _pack["OHLCV_VALUE_COLUMNS"]

MARKET = "hk_sg_equity"
GOLDEN_SHA256 = "8571b7aac4d19b1d48e096fae6f21a87637123852a7224d552d143ecda59a360"
SESSIONS = (date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5))
IDENTITY_COLUMNS = ("market", "mic", "symbol", "date")
MIC_BY_TICKER = {"0700.HK": "XHKG", "D05.SI": "XSES"}
#: Two dates on which exactly one of the two venues holds a session, so the union
#: answer and the intersection answer must disagree.
HKG_CLOSED_SG_OPEN = date(2024, 6, 10)
SG_CLOSED_HKG_OPEN = date(2024, 6, 17)


def test_pack_is_the_committed_golden_file() -> None:
    """The pack is golden: any unrecorded edit of the CSV fails here."""
    assert fixture_digest(MARKET) == GOLDEN_SHA256


def test_registry_seams_are_present() -> None:
    """Directory, alias, route and fetcher seams all resolve for this market."""
    assert MARKET in VALID_MARKETS
    assert canonical_market("hk_sg") == MARKET
    assert market_dir("hk_sg") == market_dir(MARKET)
    assert market_dir(MARKET).name == MARKET
    assert MARKET_DIR_MAP[MARKET] == PRICE_DATASET_PATHS[MARKET] == "01_bronze/market_data/hk_sg_equity"
    assert MARKET in MARKET_REGISTRY
    # ADR-0030 decision 5 is not amended for this market: the real root stays us_equity-only.
    assert REAL_ROOT_PRICE_MARKETS == ("us_equity",)


def test_both_currencies_are_declared_and_never_converted() -> None:
    """The currency seam is the two existing YAML sections, not a merged market total."""
    assert PRICE_MARKET_CONFIG_KEYS[MARKET] == ("hk", "sg")
    ticker_config = TickerConfig()
    hk = ticker_config.get_market_info("hk")
    sg = ticker_config.get_market_info("sg")
    assert hk is not None and sg is not None
    assert (hk.currency, sg.currency) == ("HKD", "SGD")
    # One section per venue: there is no market-level currency and no FX pair.
    assert hk.currency != sg.currency


def test_fixture_sessions_are_common_venue_sessions() -> None:
    """Calendar, timezone and per-venue session clocks, verified offline."""
    assert tuple(fixture_sessions(MARKET)) == SESSIONS
    assert trading_days_between(MARKET, SESSIONS[0], SESSIONS[-1]) == list(SESSIONS)
    # The registry declares one timezone for the pair; it is the Hong Kong zone.
    assert str(market_timezone(MARKET)) == "Asia/Hong_Kong"

    hong_kong = InstrumentKey(market=MARKET, mic="XHKG", symbol="0700.HK")
    singapore = InstrumentKey(market=MARKET, mic="XSES", symbol="D05.SI")
    closes = []
    for session in SESSIONS:
        assert is_trading_day(MARKET, session) is True
        assert is_instrument_trading_day(hong_kong, session) is True
        assert is_instrument_trading_day(singapore, session) is True
        hk_close = instrument_session_close(hong_kong, session)
        sg_close = instrument_session_close(singapore, session)
        assert hk_close is not None and hk_close.tzinfo is not None
        assert sg_close is not None and sg_close.tzinfo is not None
        closes.append((hk_close, sg_close))
    # Two venues, two clocks: the exact-MIC seam never returns one shared close.
    assert all(hk_close != sg_close for hk_close, sg_close in closes)
    assert instrument_session_close(hong_kong, date(2024, 6, 8)) is None


def test_hk_and_sg_are_never_conflated() -> None:
    """Two venues, two currencies, and intersection ≠ union on single-venue sessions."""
    hong_kong = InstrumentKey(market=MARKET, mic="XHKG", symbol="0700.HK")
    singapore = InstrumentKey(market=MARKET, mic="XSES", symbol="D05.SI")
    contract = market_contract(MARKET)
    assert contract.calendar_exchanges == ("XHKG", "XSES")
    assert contract.mics == frozenset({"XHKG", "XSES"})

    # XHKG closed / XSES open.
    assert is_instrument_trading_day(hong_kong, HKG_CLOSED_SG_OPEN) is False
    assert is_instrument_trading_day(singapore, HKG_CLOSED_SG_OPEN) is True
    # XSES closed / XHKG open.
    assert is_instrument_trading_day(hong_kong, SG_CLOSED_HKG_OPEN) is True
    assert is_instrument_trading_day(singapore, SG_CLOSED_HKG_OPEN) is False

    # Union semantics: the market is active on both dates.
    scheduled, not_scheduled = classify_market_sessions([MARKET], HKG_CLOSED_SG_OPEN)
    assert (scheduled, not_scheduled) == ([MARKET], [])
    assert is_trading_day(MARKET, HKG_CLOSED_SG_OPEN) is True
    assert is_trading_day(MARKET, SG_CLOSED_HKG_OPEN) is True

    # Intersection semantics: neither single-venue session is expected for every ticker,
    # while the two sessions that both venues share still are.
    union_expected = [day for day in (HKG_CLOSED_SG_OPEN, SG_CLOSED_HKG_OPEN) if is_trading_day(MARKET, day)]
    assert union_expected == [HKG_CLOSED_SG_OPEN, SG_CLOSED_HKG_OPEN]
    common = trading_days_between(MARKET, HKG_CLOSED_SG_OPEN, SG_CLOSED_HKG_OPEN)
    assert set(union_expected).isdisjoint(common)
    assert common, "the two venues share sessions in this window, so the intersection is not empty"


def test_fixture_round_trips_through_the_production_write_path(tmp_path: Path) -> None:
    """read fixture → validate → write to a temporary lake → read back → identity + values equal."""
    frame = load_ohlcv(MARKET)
    stored = round_trip_ohlcv(frame, MARKET, tmp_path)

    assert stored.height == frame.height
    assert set(stored["market"]) == {MARKET}
    # The fixture states venue labels only; MIC is derived by the identity seam.
    assert set(stored["mic"]) == {"XHKG", "XSES"}
    assert set(stored["symbol"]) == set(frame["ticker"])
    assert stored["identity_schema_version"].null_count() == 0

    expected = frame.with_columns(
        pl.lit(MARKET).alias("market"),
        pl.col("ticker").replace_strict(MIC_BY_TICKER, return_dtype=pl.String).alias("mic"),
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
