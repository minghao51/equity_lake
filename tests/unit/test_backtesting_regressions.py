"""Regression tests for backtesting data/signal handling."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from equity_lake.backtesting.data_loader import BacktestDataLoader  # type: ignore[import-untyped]
from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE, VectorBacktestEngine, _join_strategy_weights  # type: ignore[import-untyped]
from equity_lake.backtesting.strategy.base import BaseStrategy  # type: ignore[import-untyped]
from equity_lake.backtesting.strategy.momentum import CrossSectionalMomentumStrategy  # type: ignore[import-untyped]
from equity_lake.core.availability import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ActionCoverageProof,
    AvailabilityContractError,
)
from equity_lake.core.instruments import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    InstrumentIdentityError,
    InstrumentKey,
    LegacyIdentityUnavailableError,
)


def _covered_loader(*instruments: InstrumentKey) -> BacktestDataLoader:
    """Test-only loader with explicit, complete no-action coverage proofs."""
    actions = pl.DataFrame(schema={"ticker": pl.String, "ex_date": pl.Date, "action": pl.String, "value": pl.Float64})
    action_inputs: dict[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]] = {}
    for instrument in instruments:
        proof = ActionCoverageProof(
            market=instrument.market,
            mic=instrument.mic,
            symbol=instrument.symbol,
            source="test-complete-window",
            coverage_start=date(2026, 5, 1),
            coverage_end=date(2026, 7, 1),
            source_available_at=datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
            source_valid_until=datetime(2026, 7, 1, 23, 59, tzinfo=UTC),
        )
        action_inputs[instrument.tuple] = (actions, pl.DataFrame([proof.model_dump()]))
    return BacktestDataLoader(action_inputs=action_inputs)


class _PerTickerStrategy(BaseStrategy):
    def initialize(self, data: pl.DataFrame) -> None:
        self._data = data

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        aapl_rows = data.filter(pl.col("ticker") == "AAPL")
        first_date = aapl_rows["date"].min()
        last_date = aapl_rows["date"].max()

        return data.with_columns(
            pl.when((pl.col("ticker") == "AAPL") & ((pl.col("date") == first_date) | (pl.col("date") == last_date)))
            .then(1.0)
            .otherwise(0.0)
            .alias("weight")
        ).select("date", "ticker", "weight")

    def finalize(self) -> None:
        return None


@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")
def test_backtest_engine_excludes_unverified_preloaded_data() -> None:
    data = pl.DataFrame(
        {
            "date": [date(2026, 6, 1), date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 2)],
            "ticker": ["AAPL", "MSFT", "AAPL", "MSFT"],
            "open": [10.0, 50.0, 12.0, 55.0],
            "high": [10.5, 50.5, 12.5, 55.5],
            "low": [9.5, 49.5, 11.5, 54.5],
            "close": [10.0, 50.0, 12.0, 55.0],
            "volume": [100, 100, 100, 100],
        }
    )

    engine = VectorBacktestEngine(
        strategy=_PerTickerStrategy(),
        tickers=["AAPL", "MSFT"],
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2),
        initial_cash=1_000,
        preloaded_data=data,
    )

    with pytest.raises(AvailabilityContractError, match="lacks verified point-in-time action context"):
        engine.run()


def test_backtest_data_loader_filters_unioned_markets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    us_dir = tmp_path / "us_equity"
    cn_dir = tmp_path / "cn_ashare"
    for market_dir in [us_dir, cn_dir]:
        partition = market_dir / "date=2026-06-01"
        partition.mkdir(parents=True)

    pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNYS"],
            "symbol": ["AAPL"],
            "ticker": ["AAPL"],
            "date": ["2026-06-01"],
            "open": [1],
            "high": [1],
            "low": [1],
            "close": [1],
            "volume": [1],
        },
    ).write_parquet(us_dir / "date=2026-06-01" / "2026-06-01.parquet")
    pl.DataFrame(
        {
            "market": ["cn_ashare"],
            "mic": ["XSHE"],
            "symbol": ["OTHER"],
            "ticker": ["OTHER"],
            "date": ["2026-06-01"],
            "open": [2],
            "high": [2],
            "low": [2],
            "close": [2],
            "volume": [2],
        },
    ).write_parquet(cn_dir / "date=2026-06-01" / "2026-06-01.parquet")

    # The loader resolves registry dirs via core.paths at call time (ADR-0010).
    monkeypatch.setattr("equity_lake.core.paths.US_EQUITY_DIR", us_dir)
    monkeypatch.setattr("equity_lake.core.paths.CN_ASHARE_DIR", cn_dir)

    loader = _covered_loader(InstrumentKey(market="us_equity", mic="XNYS", symbol="AAPL"))
    data = loader.load(
        tickers=["AAPL"],
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        markets=["us_equity", "cn_ashare"],
    )

    assert set(data["ticker"].to_list()) == {"AAPL"}


def test_backtest_loader_disambiguates_cross_market_symbol_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    us_dir = tmp_path / "us_equity"
    cn_dir = tmp_path / "cn_ashare"
    for root, market, mic, close in [(us_dir, "us_equity", "XNYS", 10.0), (cn_dir, "cn_ashare", "XSHG", 20.0)]:
        partition = root / "date=2026-06-01"
        partition.mkdir(parents=True)
        pl.DataFrame(
            {
                "market": [market],
                "mic": [mic],
                "symbol": ["ABC"],
                "ticker": ["ABC"],
                "date": ["2026-06-01"],
                "open": [close],
                "high": [close],
                "low": [close],
                "close": [close],
                "volume": [100],
            }
        ).write_parquet(partition / "2026-06-01.parquet")

    monkeypatch.setattr("equity_lake.core.paths.US_EQUITY_DIR", us_dir)
    monkeypatch.setattr("equity_lake.core.paths.CN_ASHARE_DIR", cn_dir)
    loader = BacktestDataLoader()
    request_date = date(2026, 6, 1)

    with pytest.raises(InstrumentIdentityError, match="Bare symbol"):
        loader.load(["ABC"], request_date, request_date, markets=["us_equity", "cn_ashare"])

    selected = _covered_loader(InstrumentKey(market="us_equity", mic="XNYS", symbol="ABC")).load(
        [InstrumentKey(market="us_equity", mic="XNYS", symbol="ABC")],
        request_date,
        request_date,
        markets=["us_equity", "cn_ashare"],
    )
    assert selected.select("market", "mic", "symbol").unique().rows() == [("us_equity", "XNYS", "ABC")]
    assert selected["close"].to_list() == [10.0]


def test_backtest_loader_keeps_cn_krx_and_hk_sg_collisions_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prices = {
        "cn_ashare": [("XSHG", 10.0)],
        "krx_equity": [("XKRX", 20.0)],
        "hk_sg_equity": [("XHKG", 30.0), ("XSES", 40.0)],
    }
    instrument_keys = [InstrumentKey(market=market, mic=mic, symbol="DUP") for market, venues in prices.items() for mic, _close in venues]
    for market, venues in prices.items():
        root = tmp_path / market
        partition = root / "date=2026-06-01"
        partition.mkdir(parents=True)
        rows = [
            {
                "market": market,
                "mic": mic,
                "symbol": "DUP",
                "ticker": "DUP",
                "date": "2026-06-01",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100,
            }
            for mic, close in venues
        ]
        pl.DataFrame(rows).write_parquet(partition / "2026-06-01.parquet")
        path_attr = {"cn_ashare": "CN_ASHARE_DIR", "krx_equity": "KRX_EQUITY_DIR", "hk_sg_equity": "HK_SG_EQUITY_DIR"}[market]
        monkeypatch.setattr(f"equity_lake.core.paths.{path_attr}", root)

    loaded = _covered_loader(*instrument_keys).load(
        instrument_keys,
        date(2026, 6, 1),
        date(2026, 6, 1),
        markets=list(prices),
    )
    assert set(loaded.select("market", "mic", "symbol").unique().iter_rows()) == {key.tuple for key in instrument_keys}
    assert set(loaded["close"].to_list()) == {10.0, 20.0, 30.0, 40.0}


def test_strategy_weight_state_is_grouped_by_market_mic_and_symbol() -> None:
    identities = [
        ("cn_ashare", "XSHG"),
        ("krx_equity", "XKRX"),
        ("hk_sg_equity", "XHKG"),
        ("hk_sg_equity", "XSES"),
    ]
    rows = []
    for index, (market, mic) in enumerate(identities):
        for day, close in ((date(2026, 6, 1), 10.0), (date(2026, 6, 2), 10.0 + index)):
            rows.append(
                {
                    "market": market,
                    "mic": mic,
                    "symbol": "DUP",
                    "ticker": "DUP",
                    "date": day,
                    "close": close,
                }
            )
    data = pl.DataFrame(rows)
    strategy = CrossSectionalMomentumStrategy(params={"lookback_days": 1, "skip_days": 0, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})
    strategy.initialize(data)
    weights = strategy.generate_weights(data)
    assert weights.select("market", "mic", "symbol").unique().height == 4
    assert weights.filter(pl.col("date") == date(2026, 6, 2)).select("market", "mic", "symbol").unique().height == 4
    assert weights.filter(pl.col("date") == date(2026, 6, 2))["weight"].sum() == pytest.approx(1.0)


def test_engine_weight_join_uses_full_composite_identity() -> None:
    data = pl.DataFrame(
        {
            "market": ["cn_ashare", "krx_equity", "hk_sg_equity", "hk_sg_equity"],
            "mic": ["XSHG", "XKRX", "XHKG", "XSES"],
            "symbol": ["DUP"] * 4,
            "ticker": ["DUP"] * 4,
            "date": [date(2026, 6, 1)] * 4,
            "close": [10.0, 20.0, 30.0, 40.0],
        }
    )
    weights = data.select("market", "mic", "symbol", "date").with_columns(pl.col("mic").is_in(["XSHG", "XHKG"]).cast(pl.Float64).alias("weight"))
    joined = _join_strategy_weights(data, weights)
    assert joined.sort("mic")["weight"].to_list() == [1.0, 0.0, 0.0, 1.0]
    with pytest.raises(InstrumentIdentityError, match="dropped composite"):
        _join_strategy_weights(data, data.select("date", "ticker").with_columns(pl.lit(1.0).alias("weight")))


def test_backtest_loader_deduplicates_unique_legacy_canonical_dual_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "cn_ashare"
    partition = root / "date=2026-06-01"
    partition.mkdir(parents=True)
    pl.DataFrame(
        {
            "market": ["cn_ashare", "cn_ashare"],
            "mic": [None, "XSHG"],
            "symbol": ["DUP", "DUP"],
            "ticker": ["DUP", "DUP"],
            "date": ["2026-06-01", "2026-06-01"],
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "volume": [100, 200],
        }
    ).write_parquet(partition / "2026-06-01.parquet")
    monkeypatch.setattr("equity_lake.core.paths.CN_ASHARE_DIR", root)
    instrument = InstrumentKey(market="cn_ashare", mic="XSHG", symbol="DUP")
    loaded = _covered_loader(instrument).load(
        [instrument],
        date(2026, 6, 1),
        date(2026, 6, 1),
        markets=["cn_ashare"],
        trusted_legacy_provenance={"DUP": [instrument]},
    )
    assert loaded.height == 1
    assert loaded["close"].to_list() == [2.0]


def test_symbol_change_is_not_guessed_from_text() -> None:
    frame = pl.DataFrame(
        {
            "market": ["krx_equity", "krx_equity"],
            "mic": ["XKRX", "XKRX"],
            "symbol": ["OLD", "NEW"],
            "ticker": ["OLD", "NEW"],
            "date": [date(2026, 6, 1), date(2026, 6, 1)],
        }
    )
    from equity_lake.core.instruments import normalize_identity_frame

    normalized = normalize_identity_frame(frame, deduplicate=True)
    assert normalized.select("market", "mic", "symbol").unique().height == 2
    assert set(normalized["symbol"].to_list()) == {"OLD", "NEW"}


def test_backtest_loader_requires_trusted_legacy_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    us_dir = tmp_path / "us_equity"
    partition = us_dir / "date=2026-06-01"
    partition.mkdir(parents=True)
    pl.DataFrame(
        {"ticker": ["ABC"], "date": ["2026-06-01"], "close": [10.0], "open": [10.0], "high": [10.0], "low": [10.0], "volume": [100]}
    ).write_parquet(partition / "2026-06-01.parquet")
    monkeypatch.setattr("equity_lake.core.paths.US_EQUITY_DIR", us_dir)
    loader = BacktestDataLoader()
    request_date = date(2026, 6, 1)
    trusted = InstrumentKey(market="us_equity", mic="XNYS", symbol="ABC")

    with pytest.raises(LegacyIdentityUnavailableError):
        loader.load(["ABC"], request_date, request_date, markets=["us_equity"])

    result = _covered_loader(trusted).load(
        ["ABC"],
        request_date,
        request_date,
        markets=["us_equity"],
        trusted_legacy_provenance={"ABC": [trusted]},
    )
    assert result.select("market", "mic", "symbol").unique().rows() == [("us_equity", "XNYS", "ABC")]


def test_backtest_data_loader_forward_fills_gaps_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B4: gaps are forward-filled (past prices carried forward); bfill is gone."""
    us_dir = tmp_path / "us_equity"
    for day, close in [("2026-06-01", 10.0), ("2026-06-02", None), ("2026-06-03", 12.0)]:
        partition = us_dir / f"date={day}"
        partition.mkdir(parents=True)
        pl.DataFrame(
            {
                "market": ["us_equity"],
                "mic": ["XNYS"],
                "symbol": ["AAPL"],
                "ticker": ["AAPL"],
                "date": [day],
                "open": [close],
                "high": [close],
                "low": [close],
                "close": [close],
                "volume": [100],
            },
        ).write_parquet(partition / f"{day}.parquet")

    monkeypatch.setattr("equity_lake.core.paths.US_EQUITY_DIR", us_dir)

    loader = _covered_loader(InstrumentKey(market="us_equity", mic="XNYS", symbol="AAPL"))
    data = loader.load(tickers=["AAPL"], start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), markets=["us_equity"])

    closes = data.sort("date")["close"].to_list()
    # the 06-02 null gap is filled with the PRIOR close (ffill), never the next one
    assert closes[1] == pytest.approx(10.0)
    assert closes[2] == pytest.approx(12.0)


def test_backtest_data_loader_has_no_bfill_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B4: the lookahead-inducing fill_method parameter is removed from the API."""
    monkeypatch.setattr("equity_lake.core.paths.US_EQUITY_DIR", tmp_path / "us_equity")
    loader = BacktestDataLoader()
    invalid_options: dict[str, Any] = {
        "tickers": ["AAPL"],
        "start_date": date(2026, 6, 1),
        "end_date": date(2026, 6, 2),
        "markets": ["us_equity"],
        "fill_method": "bfill",
    }
    with pytest.raises(TypeError):
        loader.load(**invalid_options)
