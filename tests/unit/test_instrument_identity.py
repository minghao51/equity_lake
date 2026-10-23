"""Synthetic ADR-0016 identity, compatibility, calendar, label, and model-isolation checks."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AvailabilityContractError,
)
from equity_lake.core.calendar import instrument_sessions_between, is_instrument_trading_day, is_trading_day  # type: ignore[import-untyped]
from equity_lake.core.instruments import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_SCHEMA_VERSION,
    InstrumentIdentityError,
    InstrumentKey,
    LegacyIdentityUnavailableError,
    instrument_from_record,
    normalize_identity_frame,
    prepare_source_identity_frame,
)
from equity_lake.features.engineering import FeatureEngineer  # type: ignore[import-untyped]
from equity_lake.features.labels import AVAILABILITY_COLUMN, add_label_availability  # type: ignore[import-untyped]
from equity_lake.ml.feature_loader import FeatureLoader  # type: ignore[import-untyped]
from equity_lake.ml.forecasting import PriceForecaster  # type: ignore[import-untyped]


def test_exchange_mapping_and_historical_legacy_resolution_are_explicit() -> None:
    nyse = InstrumentKey.from_exchange("us_equity", "NYSE", "ABC")
    nasdaq = InstrumentKey.from_exchange("us_equity", "NASDAQ", "ABC")
    assert nyse.tuple == ("us_equity", "XNYS", "ABC")
    assert nasdaq.tuple == ("us_equity", "XNAS", "ABC")
    with pytest.raises(InstrumentIdentityError, match="no configured MIC mapping"):
        InstrumentKey.from_exchange("us_equity", "OTC MAYBE", "ABC")

    legacy = pl.DataFrame({"ticker": ["ABC"], "date": [date(2024, 1, 2)], "value": [1]})
    with pytest.raises(LegacyIdentityUnavailableError):
        normalize_identity_frame(legacy)
    resolved = normalize_identity_frame(legacy, trusted_legacy_provenance={"ABC": [nyse]})
    assert resolved.select("market", "mic", "symbol").rows() == [("us_equity", "XNYS", "ABC")]
    with pytest.raises(LegacyIdentityUnavailableError, match="Ambiguous trusted historical identity"):
        normalize_identity_frame(legacy, trusted_legacy_provenance={"ABC": [nyse, nasdaq]})


def test_trusted_legacy_identity_cannot_override_stored_market() -> None:
    legacy = pl.DataFrame(
        {"market": ["us_equity"], "mic": [None], "ticker": ["ABC"], "date": [date(2024, 1, 2)]},
        schema_overrides={"mic": pl.String},
    )
    foreign = InstrumentKey(market="cn_ashare", mic="XSHG", symbol="ABC")
    with pytest.raises(LegacyIdentityUnavailableError, match="conflicts with stored market"):
        normalize_identity_frame(legacy, trusted_legacy_provenance={"ABC": [foreign]})


def test_partial_identity_conflicts_with_trusted_provenance_are_rejected() -> None:
    legacy = pl.DataFrame({"mic": ["XNAS"], "ticker": ["ABC"], "date": [date(2024, 1, 2)]})
    trusted = InstrumentKey(market="us_equity", mic="XNYS", symbol="ABC")
    with pytest.raises(LegacyIdentityUnavailableError, match="conflicts with stored MIC"):
        normalize_identity_frame(legacy, trusted_legacy_provenance={"ABC": [trusted]})

    contradictory_exchange = pl.DataFrame(
        {"market": ["us_equity"], "mic": ["XNAS"], "exchange": ["NYSE"], "ticker": ["ABC"], "date": [date(2024, 1, 2)]}
    )
    with pytest.raises(InstrumentIdentityError, match="conflicts with stored exchange"):
        normalize_identity_frame(contradictory_exchange)


def test_new_price_identity_requires_explicit_venue_provenance() -> None:
    source = pl.DataFrame({"ticker": ["ABC"], "date": [date(2024, 1, 2)], "close": [10.0], "mic": ["XSHG"]})
    prepared = prepare_source_identity_frame(source, "cn_ashare")
    assert prepared.select("market", "mic", "symbol", "identity_schema_version", "identity_provenance").rows() == [
        ("cn_ashare", "XSHG", "ABC", IDENTITY_SCHEMA_VERSION, "explicit_mic")
    ]
    with pytest.raises(LegacyIdentityUnavailableError, match="no explicit MIC or exchange"):
        prepare_source_identity_frame(source.drop("mic"), "cn_ashare")


def test_price_source_preserves_provider_exchange_and_uses_only_exact_current_config() -> None:
    from equity_lake.core.config import TickerConfig  # type: ignore[import-untyped]
    from equity_lake.core.config_models import MarketConfig, TickerMetadata  # type: ignore[import-untyped]
    from equity_lake.sources.base import STANDARD_COLUMNS, standardize_columns  # type: ignore[import-untyped]
    from equity_lake.sources.us import USEquityFetcher  # type: ignore[import-untyped]

    config = TickerConfig(
        markets={
            "us": MarketConfig(
                currency="USD",
                tickers=[TickerMetadata(symbol="CFG", name="Config", exchange="NASDAQ", sector="Tech")],
            )
        }
    )
    fetcher = USEquityFetcher(tickers=["ABC", "CFG"], ticker_config=config, retry_attempts=1, retry_delay=0.0)
    provider = pl.DataFrame(
        {
            "ticker": ["ABC"],
            "date": [date(2024, 1, 2)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.0],
            "volume": [100],
            "exchange": ["NYSE"],
        }
    )
    standardized = standardize_columns(provider, columns=STANDARD_COLUMNS)
    assert standardized["exchange"].to_list() == ["NYSE"]
    identified = fetcher._attach_source_identity(standardized)
    assert identified.select("market", "mic", "symbol", "identity_provenance").rows() == [("us_equity", "XNYS", "ABC", "provider_exchange")]

    configured = fetcher._attach_source_identity(
        standardized.drop("exchange").with_columns(pl.lit("CFG").alias("ticker"), pl.lit("CFG").alias("symbol"))
    )
    assert configured.select("mic", "identity_provenance").rows() == [("XNAS", "current_ticker_config")]

    unconfigured = USEquityFetcher(tickers=["MISSING"], retry_attempts=1, retry_delay=0.0)
    with pytest.raises(LegacyIdentityUnavailableError, match="omitted MIC/exchange"):
        unconfigured._attach_source_identity(standardized.drop("exchange").with_columns(pl.lit("MISSING").alias("ticker")))


def test_cn_source_szse_alias_uses_xshe_and_unresolved_venues_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import pandas as pd

    from equity_lake.core.config import TickerConfig
    from equity_lake.core.config_models import MarketConfig, TickerMetadata
    from equity_lake.sources.cn import CNAshareFetcher  # type: ignore[import-untyped]

    config = TickerConfig(
        markets={
            "cn": MarketConfig(
                currency="CNY",
                tickers=[TickerMetadata(symbol="000001", name="Ping An", exchange="SZSE", sector="Finance")],
            )
        }
    )
    fetcher = CNAshareFetcher(ticker_config=config, tickers=["000001"], max_workers=1, retry_attempts=1, retry_delay=0)

    def fetch_synthetic_stock(stock_code: str, _date_string: str, trading_date: date) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ticker": [stock_code],
                "date": [trading_date],
                "开盘": [10.0],
                "最高": [11.0],
                "最低": [9.0],
                "收盘": [10.5],
                "成交量": [100],
            }
        )

    monkeypatch.setattr(fetcher, "_fetch_single_stock", fetch_synthetic_stock)
    result = fetcher.fetch(date(2024, 1, 2))
    assert result.select("market", "mic", "symbol", "identity_provenance").rows() == [("cn_ashare", "XSHE", "000001", "current_ticker_config")]

    unknown_venue = pl.DataFrame({"ticker": ["000001"], "date": [date(2024, 1, 2)], "exchange": ["UNKNOWN VENUE"]})
    with pytest.raises(InstrumentIdentityError, match="no configured MIC mapping"):
        fetcher._attach_source_identity(unknown_venue)

    from equity_lake.sources.hk_sg import HKSGEquityFetcher  # type: ignore[import-untyped]

    ambiguous_config = TickerConfig(
        markets={
            "hk": MarketConfig(currency="HKD", tickers=[TickerMetadata(symbol="DUP", name="HK", exchange="HKEX", sector="Tech")]),
            "sg": MarketConfig(currency="SGD", tickers=[TickerMetadata(symbol="DUP", name="SG", exchange="SGX", sector="Tech")]),
        }
    )
    dual_fetcher = HKSGEquityFetcher(hk_tickers=["DUP"], sg_tickers=["DUP"], ticker_config=ambiguous_config)
    with pytest.raises(LegacyIdentityUnavailableError, match="exact current ticker-config identity is ambiguous"):
        dual_fetcher._attach_source_identity(pl.DataFrame({"ticker": ["DUP"], "date": [date(2024, 1, 2)]}))


def test_efinance_and_hybrid_fetches_attach_config_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.core.config import TickerConfig
    from equity_lake.core.config_models import MarketConfig, TickerMetadata
    from equity_lake.sources.cn_efinance import CNEfinanceFetcher  # type: ignore[import-untyped]
    from equity_lake.sources.cn_hybrid import CNHybridFetcher  # type: ignore[import-untyped]

    config = TickerConfig(
        markets={
            "cn": MarketConfig(
                currency="CNY",
                tickers=[TickerMetadata(symbol="000001", name="Ping An", exchange="SZSE", sector="Finance")],
            )
        }
    )
    source_rows = pl.DataFrame(
        {
            "ticker": ["000001"],
            "date": [date(2024, 1, 2)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [100],
        }
    )
    efinance_fetcher = CNEfinanceFetcher(ticker_config=config, tickers=["000001"], retry_attempts=1, retry_delay=0)
    monkeypatch.setattr(efinance_fetcher, "_fetch_history_batch", lambda _tickers, _date: [source_rows])
    efinance_result = efinance_fetcher.fetch(date(2024, 1, 2))
    assert efinance_result.select("market", "mic", "symbol", "identity_provenance").rows() == [
        ("cn_ashare", "XSHE", "000001", "current_ticker_config")
    ]

    hybrid_efinance = CNHybridFetcher(ticker_config=config, tickers=["000001"], enable_efinance=True, enable_akshare=False)
    monkeypatch.setattr(hybrid_efinance, "_fetch_efinance_with_timeout", lambda _date: efinance_result)
    hybrid_efinance_result = hybrid_efinance.fetch(date(2024, 1, 2))
    assert hybrid_efinance_result.select("market", "mic", "symbol", "identity_provenance").rows() == [
        ("cn_ashare", "XSHE", "000001", "current_ticker_config")
    ]

    hybrid_fetcher = CNHybridFetcher(ticker_config=config, tickers=["000001"], enable_efinance=False, enable_akshare=True)
    assert hybrid_fetcher.akshare_fetcher is not None
    monkeypatch.setattr(hybrid_fetcher.akshare_fetcher, "fetch", lambda _date: source_rows)
    hybrid_result = hybrid_fetcher.fetch(date(2024, 1, 2))
    assert hybrid_result.select("market", "mic", "symbol", "identity_provenance").rows() == [("cn_ashare", "XSHE", "000001", "current_ticker_config")]

    monkeypatch.setattr(
        hybrid_fetcher.akshare_fetcher,
        "fetch",
        lambda _date: source_rows.with_columns(pl.lit("UNKNOWN").alias("ticker")),
    )
    with pytest.raises(LegacyIdentityUnavailableError, match="exact current ticker-config identity is unavailable"):
        hybrid_fetcher.fetch(date(2024, 1, 2))


def test_feature_source_keeps_same_symbol_in_distinct_instruments(tmp_path: Path) -> None:
    us = tmp_path / "us_equity"
    cn = tmp_path / "cn_ashare"
    us.mkdir()
    cn.mkdir()
    common = {
        "ticker": ["ABC"],
        "symbol": ["ABC"],
        "date": [date(2024, 1, 2)],
        "open": [10.0],
        "high": [11.0],
        "low": [9.0],
        "close": [10.0],
        "volume": [100],
    }
    pl.DataFrame({**common, "mic": ["XNYS"]}).write_parquet(us / "prices.parquet")
    pl.DataFrame({**common, "mic": ["XSHG"]}).write_parquet(cn / "prices.parquet")

    engineer = FeatureEngineer.__new__(FeatureEngineer)
    engineer.market_sources = [("us_equity", us), ("cn_ashare", cn)]
    rows = engineer._load_price_rows(["ABC"], date(2024, 1, 1), date(2024, 1, 3))
    keys = {instrument_from_record(row).tuple for row in rows.to_dicts()}
    assert keys == {("us_equity", "XNYS", "ABC"), ("cn_ashare", "XSHG", "ABC")}


def test_feature_loader_requires_provenance_and_rejects_legacy_availability_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gold_dir = tmp_path / "03_gold" / "features"
    gold_dir.parent.mkdir(parents=True)
    legacy = pl.DataFrame({"ticker": ["ABC"], "date": [date(2024, 1, 2)], "close": [10.0]})
    write_deltalake(str(gold_dir), legacy.to_arrow(), mode="append", partition_by=["date"])
    monkeypatch.setattr("equity_lake.ml.feature_loader.GOLD_FEATURES_DIR", gold_dir)
    loader = FeatureLoader()
    trusted = InstrumentKey(market="us_equity", mic="XNYS", symbol="ABC")
    try:
        with pytest.raises(LegacyIdentityUnavailableError):
            loader.load_features("ABC", date(2024, 1, 1), date(2024, 1, 3))
        with pytest.raises(AvailabilityContractError, match="source-availability proof is unavailable"):
            loader.load_features(
                "ABC",
                date(2024, 1, 1),
                date(2024, 1, 3),
                trusted_legacy_provenance={"ABC": [trusted]},
            )
    finally:
        loader.close()


def test_feature_source_keeps_cn_krx_and_hk_sg_collisions(tmp_path: Path) -> None:
    sources: list[tuple[str, Path]] = []
    expected = {
        ("cn_ashare", "XSHG", "DUP"),
        ("krx_equity", "XKRX", "DUP"),
        ("hk_sg_equity", "XHKG", "DUP"),
        ("hk_sg_equity", "XSES", "DUP"),
    }
    for index, (market, mic, symbol) in enumerate(sorted(expected)):
        path = tmp_path / market / mic
        path.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            {
                "ticker": [symbol],
                "mic": [mic],
                "date": [date(2024, 1, 2)],
                "open": [10.0 + index],
                "high": [11.0 + index],
                "low": [9.0 + index],
                "close": [10.0 + index],
                "volume": [100],
            }
        ).write_parquet(path / "prices.parquet")
        sources.append((market, path))
    engineer = FeatureEngineer.__new__(FeatureEngineer)
    engineer.market_sources = sources
    rows = engineer._load_price_rows(["DUP"], date(2024, 1, 1), date(2024, 1, 3))
    assert {instrument_from_record(row).tuple for row in rows.to_dicts()} == expected


def test_label_availability_shifts_by_identity_not_symbol() -> None:
    identities = [
        ("hk_sg_equity", "XHKG", date(2024, 8, 8), date(2024, 8, 9)),
        ("hk_sg_equity", "XSES", date(2024, 8, 8), date(2024, 8, 12)),
        ("cn_ashare", "XSHG", date(2024, 8, 8), date(2024, 8, 9)),
        ("krx_equity", "XKRX", date(2024, 8, 8), date(2024, 8, 12)),
    ]
    rows = [
        {
            "market": market,
            "mic": mic,
            "symbol": "ABC",
            "ticker": "ABC",
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "date": day,
            "close": 10.0 + index,
        }
        for index, (market, mic, start, end) in enumerate(identities)
        for day in (start, end)
    ]
    labeled = add_label_availability(pl.DataFrame(rows))
    for market, mic, start, end in identities:
        row = labeled.filter((pl.col("market") == market) & (pl.col("mic") == mic) & (pl.col("date") == start))
        assert row[AVAILABILITY_COLUMN].to_list() == [end]


def test_exchange_specific_calendar_does_not_use_market_union() -> None:
    holiday = date(2024, 8, 9)  # Singapore National Day; Hong Kong trades.
    hk = InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="ABC")
    sg = InstrumentKey(market="hk_sg_equity", mic="XSES", symbol="ABC")
    assert is_trading_day("hk_sg_equity", holiday) is True
    assert is_instrument_trading_day(hk, holiday) is True
    assert is_instrument_trading_day(sg, holiday) is False
    assert holiday in instrument_sessions_between(hk, holiday, holiday)
    assert holiday not in instrument_sessions_between(sg, holiday, holiday)


def test_model_artifact_filenames_and_readers_isolate_cn_krx_and_hk_sg_collisions(tmp_path: Path) -> None:
    instruments = [
        InstrumentKey(market="cn_ashare", mic="XSHG", symbol="ABC"),
        InstrumentKey(market="krx_equity", mic="XKRX", symbol="ABC"),
        InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="ABC"),
        InstrumentKey(market="hk_sg_equity", mic="XSES", symbol="ABC"),
    ]
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    trained_on = date(2024, 1, 2)
    try:
        paths = {instrument.tuple: tmp_path / forecaster._build_model_filename(instrument, trained_on) for instrument in instruments}
        assert len({path.name for path in paths.values()}) == len(instruments)
        for instrument in instruments:
            path = paths[instrument.tuple]
            path.write_bytes(b"synthetic")
            path.with_suffix(".training_metadata.json").write_text(
                json.dumps(
                    {
                        "provenance_version": 3,
                        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                        "instrument": instrument.model_dump(mode="json"),
                        "ticker": instrument.symbol,
                        "model_mode": forecaster.model_mode,
                        "backend": forecaster.backend,
                        "model_file": path.name,
                        "trained_on": trained_on.isoformat(),
                        "refit_boundary": trained_on.isoformat(),
                        "training_start": "2023-12-01",
                        "data_range": {"start": "2023-12-01", "end": trained_on.isoformat()},
                    }
                )
            )
            assert forecaster._resolve_model_path(instrument, trained_on) == path
        with pytest.raises(InstrumentIdentityError, match="ambiguous artifact identity"):
            forecaster._resolve_model_path("ABC", trained_on)
    finally:
        forecaster.close()


def test_candidate_strategies_are_an_explicit_ml_constructor_input(tmp_path: Path) -> None:
    candidates = [{"name": "explicit", "lookback_days": 5, "buy_threshold": 0.01, "sell_threshold": -0.01}]
    forecaster = PriceForecaster(model_dir=str(tmp_path), candidate_strategies=candidates)
    try:
        assert forecaster.candidate_strategies == candidates
    finally:
        forecaster.close()
