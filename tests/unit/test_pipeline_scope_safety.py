"""Regression tests for default ticker resolution and HK/SG pipeline planning."""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from equity_lake import pipeline  # type: ignore[import-untyped]
from equity_lake.core.config import MarketConfig, TickerConfig, TickerMetadata  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentIdentityError, InstrumentKey  # type: ignore[import-untyped]
from equity_lake.ingestion.types import SourceOutcome, SourceStatus  # type: ignore[import-untyped]
from equity_lake.pipeline import _resolve_configured_tickers, execute_eod_pipeline  # type: ignore[import-untyped]

_HK_SG_TICKERS = ["0700.HK", "0005.HK", "C6L.SI"]


def _ticker_config() -> TickerConfig:
    """Build a small config with separate HK/SG sections and a cross-section duplicate."""
    return TickerConfig(
        markets={
            "hk": MarketConfig(
                currency="HKD",
                tickers=[
                    TickerMetadata(symbol="0700.HK", name="Tencent", exchange="XHKG", sector="Tech", priority=10),
                    TickerMetadata(symbol="0005.HK", name="HSBC", exchange="XHKG", sector="Finance", priority=8),
                    TickerMetadata(symbol="INACTIVE.HK", name="Inactive", exchange="XHKG", sector="Other", active=False),
                ],
            ),
            "sg": MarketConfig(
                currency="SGD",
                tickers=[
                    TickerMetadata(symbol="C6L.SI", name="Singapore Airlines", exchange="XSES", sector="Transport", priority=9),
                    TickerMetadata(symbol="0700.HK", name="Duplicate fixture", exchange="XSES", sector="Other", priority=7),
                ],
            ),
        }
    )


def test_configured_hk_sg_symbol_projection_preserves_cross_exchange_collision() -> None:
    config = _ticker_config()

    assert _resolve_configured_tickers(["hk_sg_equity"], config) == [*_HK_SG_TICKERS, "0700.HK"]


def test_hk_sg_plan_uses_each_instrument_exchange_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """An XHKG-only session excludes configured XSES instruments from features and ML."""
    config = _ticker_config()
    feature_calls: list[dict[str, Any]] = []
    ingestion_calls: list[dict[str, object]] = []
    ml_calls: list[dict[str, Any]] = []

    def fake_ingestion(**kwargs: Any) -> dict[str, SourceOutcome]:
        ingestion_calls.append(kwargs)
        return {"hk_sg_equity": SourceOutcome(SourceStatus.WRITTEN)}

    def fake_features(**kwargs: Any) -> pl.DataFrame:
        feature_calls.append(kwargs)
        instruments = kwargs["instruments"]
        return pl.DataFrame([instrument.as_columns() for instrument in instruments])

    def fake_ml(**kwargs: Any) -> tuple[bool, dict[str, object]]:
        ml_calls.append(kwargs)
        return True, {}

    monkeypatch.setattr("equity_lake.pipeline.run_daily_ingestion", fake_ingestion)
    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", fake_features)
    monkeypatch.setattr("equity_lake.pipeline.run_prediction_job", fake_ml)
    monkeypatch.setattr(
        "equity_lake.pipeline.backfill_date_range",
        lambda **_: (_ for _ in ()).throw(AssertionError("history backfill must not run")),
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 8, 9),  # Singapore holiday; Hong Kong is open.
        markets=["hk_sg_equity"],
        ticker_config=config,
    )

    assert results["scope"]["scheduled"] == ["hk_sg_equity"]
    assert results["scope"]["not_scheduled"] == []
    assert results["scope"]["tickers"] == ["0700.HK", "0005.HK"]
    assert ingestion_calls[0]["markets"] == ["hk_sg_equity"]
    assert feature_calls[0]["tickers"] == ["0700.HK", "0005.HK"]
    assert [instrument.mic for instrument in feature_calls[0]["instruments"]] == ["XHKG", "XHKG"]
    assert results["features"]["success"] is True
    assert len(ml_calls) == 1
    assert {instrument.tuple for instrument in ml_calls[0]["tickers"]} == {instrument.tuple for instrument in feature_calls[0]["instruments"]}
    assert ml_calls[0]["candidate_strategies"]
    assert results["ml"]["success"] is True


def test_fully_closed_hk_sg_session_skips_features_and_ml(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_work(**kwargs: Any) -> None:
        raise AssertionError("closed session must not compute, infer, or backfill")

    monkeypatch.setattr(
        "equity_lake.pipeline.run_daily_ingestion",
        lambda **_: {"hk_sg_equity": SourceOutcome(SourceStatus.NOT_SCHEDULED)},
    )
    for name in ("run_feature_job", "run_prediction_job", "backfill_date_range"):
        monkeypatch.setattr(f"equity_lake.pipeline.{name}", forbid_work)
    results = execute_eod_pipeline(
        trading_date=date(2024, 3, 29),
        markets=["hk_sg_equity"],
        ticker_config=_ticker_config(),
    )
    assert results["scope"]["scheduled"] == []
    assert results["scope"]["not_scheduled"] == ["hk_sg_equity"]
    assert results["scope"]["tickers"] == []
    assert results["ingestion"]["required_failures"] == []
    for stage in ("features", "ml"):
        assert results[stage]["success"] is True
        assert results[stage]["skipped"] is True
        assert results[stage]["reason"] == "not_scheduled_scope"


def test_pipeline_passes_explicit_source_date_market_instrument_scopes_to_both_processors(monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.ingestion import bronze_silver, sec_processor  # type: ignore[import-untyped]

    captured_articles: list[Any] = []
    captured_sec: list[Any] = []

    def fake_ingestion(**kwargs: Any) -> dict[str, SourceOutcome]:
        return {market: SourceOutcome(SourceStatus.NO_DATA) for market in kwargs["markets"]}

    def fake_article(trading_date: date, *, scope: Any, diagnostics: dict[str, Any] | None = None) -> bool:
        captured_articles.append(scope)
        if diagnostics is not None:
            diagnostics.update(mode="paid_processing_disabled")
        return False

    def fake_sec(trading_date: date, *, scope: Any, diagnostics: dict[str, Any] | None = None) -> bool:
        captured_sec.append(scope)
        if diagnostics is not None:
            diagnostics.update(mode="paid_processing_disabled")
        return False

    monkeypatch.setattr(pipeline, "run_daily_ingestion", fake_ingestion)
    monkeypatch.setattr(bronze_silver, "process_bronze_to_silver", fake_article)
    monkeypatch.setattr(sec_processor, "process_sec_bronze_to_silver", fake_sec)

    stage, _ = pipeline._run_ingestion_stage(
        trading_date=date(2026, 10, 17),
        markets=["rss_news", "stocktwits_messages", "sec_filings_fulltext"],
        dry_run=False,
        ticker_config=_ticker_config(),
        filters=None,
        explicit_tickers=["AAPL"],
    )

    assert [scope.source for scope in captured_articles] == ["rss", "stocktwits"]
    for scope in (*captured_articles, *captured_sec):
        assert scope.trading_date == date(2026, 10, 17)
        assert scope.market == "us_equity"
        assert "AAPL" in scope.instruments
    assert captured_sec[0].source == "sec_filing"
    assert stage["bronze_to_silver"]["success"] is True
    assert stage["bronze_to_silver"]["skipped"] is True
    assert stage["bronze_to_silver"]["reason"] == "paid_processing_disabled"
    assert stage["sec_to_silver"]["reason"] == "paid_processing_disabled"


def test_explicit_hk_sg_tickers_remain_explicit_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit overrides are not replaced or expanded by configured defaults."""
    feature_calls: list[dict[str, Any]] = []
    custom = InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="CUSTOM.HK")

    def fake_features(**kwargs: Any) -> pl.DataFrame:
        feature_calls.append(kwargs)
        return pl.DataFrame([custom.as_columns()])

    monkeypatch.setattr("equity_lake.pipeline.run_feature_job", fake_features)
    monkeypatch.setattr("equity_lake.pipeline.run_prediction_job", lambda **_: (True, {}))

    results = execute_eod_pipeline(
        trading_date=date(2024, 8, 9),
        markets=["hk_sg_equity"],
        instruments=[custom],
        ticker_config=_ticker_config(),
        skip_ingestion=True,
    )

    assert results["scope"]["explicit_tickers"] is True
    assert results["scope"]["tickers"] == ["CUSTOM.HK"]
    assert feature_calls[0]["tickers"] == ["CUSTOM.HK"]
    assert feature_calls[0]["instruments"] == [custom]
    assert results["features"]["success"] is True


def _multi_market_config() -> TickerConfig:
    return TickerConfig(
        markets={
            "cn": MarketConfig(currency="CNY", tickers=[TickerMetadata(symbol="DUP", name="CN", exchange="SZSE", sector="Tech")]),
            "krx": MarketConfig(currency="KRW", tickers=[TickerMetadata(symbol="DUP", name="KR", exchange="KRX", sector="Tech")]),
            "hk": MarketConfig(currency="HKD", tickers=[TickerMetadata(symbol="DUP", name="HK", exchange="HKEX", sector="Tech")]),
            "sg": MarketConfig(currency="SGD", tickers=[TickerMetadata(symbol="DUP", name="SG", exchange="SGX", sector="Tech")]),
            "jpx": MarketConfig(currency="JPY", tickers=[TickerMetadata(symbol="JP1", name="JP", exchange="TSE", sector="Tech")]),
        }
    )


def test_pipeline_preserves_same_symbol_identities_across_markets_and_ml(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _multi_market_config()
    keys = [
        InstrumentKey(market="cn_ashare", mic="XSHE", symbol="DUP"),
        InstrumentKey(market="krx_equity", mic="XKRX", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XSES", symbol="DUP"),
        InstrumentKey(market="jpx_equity", mic="XTKS", symbol="JP1"),
    ]
    ingestion_calls: list[dict[str, Any]] = []
    feature_calls: list[dict[str, Any]] = []
    ml_calls: list[dict[str, Any]] = []

    def fake_ingestion(**kwargs: Any) -> dict[str, SourceOutcome]:
        ingestion_calls.append(kwargs)
        return {market: SourceOutcome(SourceStatus.WRITTEN) for market in kwargs["markets"]}

    def fake_features(**kwargs: Any) -> pl.DataFrame:
        feature_calls.append(kwargs)
        return pl.DataFrame([instrument.as_columns() for instrument in kwargs["instruments"]])

    def fake_ml(**kwargs: Any) -> tuple[bool, dict[str, object]]:
        ml_calls.append(kwargs)
        return True, {}

    monkeypatch.setattr(pipeline, "run_daily_ingestion", fake_ingestion)
    monkeypatch.setattr(pipeline, "run_feature_job", fake_features)
    monkeypatch.setattr(pipeline, "run_prediction_job", fake_ml)

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 4),
        markets=["cn_ashare", "krx_equity", "hk_sg_equity", "jpx_equity"],
        instruments=keys,
        ticker_config=config,
    )

    assert [call["markets"] for call in ingestion_calls] == [["cn_ashare"], ["krx_equity"], ["hk_sg_equity"], ["jpx_equity"]]
    assert results["scope"]["tickers"] == ["DUP", "DUP", "DUP", "DUP", "JP1"]
    assert feature_calls[0]["instruments"] == keys
    produced = results["features"]["coverage"]["produced_instruments"]
    assert {(item["market"], item["mic"], item["symbol"]) for item in produced} == {item.tuple for item in keys}
    assert {instrument.tuple for instrument in ml_calls[0]["tickers"]} == {item.tuple for item in keys}
    assert results["features"]["success"] is True
    assert results["ml"]["success"] is True


def test_synthetic_normal_pipeline_preserves_provider_identity_through_ml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exercise every price route through identity writes, features, and ML selectors."""
    import math

    import polars as pl
    from deltalake import DeltaTable

    from equity_lake.core.instruments import InstrumentKey
    from equity_lake.ingestion import router
    from equity_lake.sources.base import MarketDataFetcher  # type: ignore[import-untyped]

    target = date(2024, 1, 4)
    config = TickerConfig(
        markets={
            "us": MarketConfig(currency="USD", tickers=[TickerMetadata(symbol="US1", name="US", exchange="NASDAQ", sector="Tech")]),
            "cn": MarketConfig(currency="CNY", tickers=[TickerMetadata(symbol="DUP", name="CN", exchange="SZSE", sector="Tech")]),
            "krx": MarketConfig(currency="KRW", tickers=[TickerMetadata(symbol="DUP", name="KR", exchange="KRX", sector="Tech")]),
            "hk": MarketConfig(currency="HKD", tickers=[TickerMetadata(symbol="DUP", name="HK", exchange="HKEX", sector="Tech")]),
            "sg": MarketConfig(currency="SGD", tickers=[TickerMetadata(symbol="SGDUP", name="SG", exchange="SGX", sector="Tech")]),
            "jpx": MarketConfig(currency="JPY", tickers=[TickerMetadata(symbol="JP1", name="JP", exchange="TSE", sector="Tech")]),
        }
    )
    identities = [
        InstrumentKey(market="us_equity", mic="XNAS", symbol="US1"),
        InstrumentKey(market="cn_ashare", mic="XSHE", symbol="DUP"),
        InstrumentKey(market="krx_equity", mic="XKRX", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XSES", symbol="SGDUP"),
        InstrumentKey(market="jpx_equity", mic="XTKS", symbol="JP1"),
    ]
    exchange_by_key = {
        identities[0].tuple: "NASDAQ",
        identities[1].tuple: "SZSE",
        identities[2].tuple: "KRX",
        identities[3].tuple: "HKEX",
        identities[4].tuple: "SGX",
        identities[5].tuple: "TSE",
    }
    days: list[date] = []
    current = target - timedelta(days=140)
    while current <= target:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    provider_frames: dict[str, pl.DataFrame] = {}
    for market in ("us_equity", "cn_ashare", "krx_equity", "hk_sg_equity", "jpx_equity"):
        rows: list[dict[str, Any]] = []
        for offset, instrument in enumerate(item for item in identities if item.market == market):
            exchange = exchange_by_key[instrument.tuple]
            for index, day in enumerate(days):
                close = 100.0 + offset * 10 + 8 * math.sin(index / 2.5) + index * 0.05
                rows.append(
                    {
                        "ticker": instrument.symbol,
                        "date": day,
                        "open": close - 0.5,
                        "high": close + 1.0,
                        "low": close - 1.0,
                        "close": close,
                        "volume": 100_000 + index,
                        "exchange": exchange,
                    }
                )
        provider_frames[market] = pl.DataFrame(rows)

    class SyntheticPriceFetcher(MarketDataFetcher):
        def __init__(self, market: str, frame: pl.DataFrame, ticker_config: TickerConfig | None) -> None:
            super().__init__(retry_attempts=1, retry_delay=0, ticker_config=ticker_config)
            self.market = market
            self.frame = frame

        def fetch(self, trading_date: date) -> pl.DataFrame:
            return cast(pl.DataFrame, self._attach_source_identity(self.frame))

    source_alias = {"us_equity": "us", "cn_ashare": "cn", "krx_equity": "krx", "hk_sg_equity": "hk_sg", "jpx_equity": "jpx"}
    for market, frame in provider_frames.items():

        def make_factory(route: str, rows: pl.DataFrame) -> Any:
            def factory(*, ticker_config: TickerConfig | None = None, **_kwargs: Any) -> SyntheticPriceFetcher:
                return SyntheticPriceFetcher(source_alias[route], rows, ticker_config)

            return factory

        monkeypatch.setitem(router.MARKET_REGISTRY, market, make_factory(market, frame))

    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    monkeypatch.setattr("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path)
    monkeypatch.setattr("equity_lake.features.engineering.LAKE_DIR", tmp_path)
    from deltalake import write_deltalake

    from equity_lake.core.availability import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
        ActionCoverageProof,
    )
    from equity_lake.core.schemas import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
        CORPORATE_ACTION_SCHEMA,
    )

    source_time = datetime.combine(days[0] - timedelta(days=30), time(12, 0), tzinfo=UTC)
    for market in ("us_equity", "cn_ashare", "krx_equity", "hk_sg_equity", "jpx_equity"):
        proofs = [
            ActionCoverageProof(
                market=instrument.market,
                mic=instrument.mic,
                symbol=instrument.symbol,
                source="synthetic-complete-window",
                coverage_start=days[0],
                coverage_end=target,
                source_available_at=source_time,
                source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
            ).model_dump()
            for instrument in identities
            if instrument.market == market
        ]
        write_deltalake(str(tmp_path / "02_silver" / "corporate_action_coverage" / market), pl.DataFrame(proofs).to_arrow(), mode="overwrite")
        empty_actions = pl.DataFrame(schema=CORPORATE_ACTION_SCHEMA)
        write_deltalake(str(tmp_path / "02_silver" / "corporate_actions" / market), empty_actions.to_arrow(), mode="overwrite")
    ml_calls: list[dict[str, Any]] = []
    pointblank_market_batches: list[set[str]] = []

    def fake_ml(**kwargs: Any) -> tuple[bool, dict[str, object]]:
        ml_calls.append(kwargs)
        return True, {}

    from equity_lake.validation.schemas import PriceDataSchema  # type: ignore[import-untyped]

    actual_price_validation = PriceDataSchema.validate

    def record_price_validation(frame: pl.DataFrame) -> pl.DataFrame:
        validated = cast(pl.DataFrame, actual_price_validation(frame))
        pointblank_market_batches.append(set(validated["market"].unique().to_list()))
        return validated

    monkeypatch.setattr(PriceDataSchema, "validate", staticmethod(record_price_validation))
    monkeypatch.setattr(pipeline, "run_prediction_job", fake_ml)
    results = execute_eod_pipeline(
        trading_date=target,
        markets=["us_equity", "cn_ashare", "krx_equity", "hk_sg_equity", "jpx_equity"],
        instruments=identities,
        ticker_config=config,
    )

    assert results["ingestion"]["success"] is True
    assert len(pointblank_market_batches) == 5
    assert set().union(*pointblank_market_batches) == {"us_equity", "cn_ashare", "hk_sg_equity", "jpx_equity", "krx_equity"}
    assert all(len(batch) == 1 for batch in pointblank_market_batches)
    assert results["features"]["success"] is True
    assert results["features"]["coverage"]["missing_instruments"] == {}
    expected = {instrument.tuple for instrument in identities}
    for market in ("us_equity", "cn_ashare", "krx_equity", "hk_sg_equity", "jpx_equity"):
        bronze_path = tmp_path / "01_bronze" / "market_data" / market
        bronze = cast(pl.DataFrame, pl.from_arrow(DeltaTable(str(bronze_path)).to_pyarrow_table()))
        assert {tuple(row) for row in bronze.select("market", "mic", "symbol").unique().iter_rows()} == {
            identity for identity in expected if identity[0] == market
        }
        assert set(bronze["identity_provenance"].unique().to_list()) == {"provider_exchange"}
    gold = cast(pl.DataFrame, pl.from_arrow(DeltaTable(str(tmp_path / "03_gold" / "features")).to_pyarrow_table()))
    assert {tuple(row) for row in gold.select("market", "mic", "symbol").unique().iter_rows()} == expected
    assert {
        "action_coverage_source",
        "action_coverage_start",
        "action_coverage_end",
        "action_source_available_at",
        "action_source_valid_until",
        "action_coverage_status",
        "action_coverage_schema_version",
    }.issubset(gold.columns)
    assert gold["action_coverage_source"].unique().to_list() == ["synthetic-complete-window"]
    assert gold["action_source_available_at"].unique().to_list() == [source_time]
    assert gold["action_source_valid_until"].unique().to_list() == [datetime(2030, 1, 1, tzinfo=UTC)]
    assert gold["action_coverage_start"].unique().to_list() == [days[0]]
    assert gold["action_coverage_end"].unique().to_list() == [target]
    assert gold["action_coverage_status"].unique().to_list() == ["complete"]
    assert gold["action_coverage_schema_version"].unique().to_list() == [2]
    assert min(cast(list[int], gold["action_source_age_days"].to_list())) > 7
    assert {instrument.tuple for instrument in ml_calls[0]["tickers"]} == expected


def test_missing_duplicate_symbol_coverage_is_reported_by_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _multi_market_config()
    keys = [
        InstrumentKey(market="cn_ashare", mic="XSHE", symbol="DUP"),
        InstrumentKey(market="krx_equity", mic="XKRX", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XSES", symbol="DUP"),
    ]
    monkeypatch.setattr(
        pipeline,
        "run_daily_ingestion",
        lambda **kwargs: {market: SourceOutcome(SourceStatus.WRITTEN) for market in kwargs["markets"]},
    )
    monkeypatch.setattr(pipeline, "run_feature_job", lambda **_: pl.DataFrame([key.as_columns() for key in keys[:-1]]))
    monkeypatch.setattr(pipeline, "run_prediction_job", lambda **_: (_ for _ in ()).throw(AssertionError("ML ran with incomplete identity coverage")))

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 4),
        markets=["cn_ashare", "krx_equity", "hk_sg_equity"],
        instruments=keys,
        ticker_config=config,
    )

    missing_key = keys[-1].display_key
    assert results["features"]["success"] is False
    assert results["features"]["coverage"]["missing"] == {missing_key: "missing_output_bar"}
    assert results["features"]["coverage"]["missing_instruments"] == {missing_key: "missing_output_bar"}
    assert missing_key in results["features"]["error"]
    assert results["ml"]["skipped"] is True


def test_bare_explicit_symbol_with_multiple_market_owners_fails_closed() -> None:
    with pytest.raises(InstrumentIdentityError, match="ambiguous across configured instruments"):
        execute_eod_pipeline(
            trading_date=date(2024, 1, 4),
            markets=["cn_ashare", "krx_equity", "hk_sg_equity"],
            tickers=["DUP"],
            ticker_config=_multi_market_config(),
            skip_ingestion=True,
        )
