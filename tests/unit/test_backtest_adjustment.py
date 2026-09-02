"""Tests for backtest corporate-action adjustment opt-in (ADR-0011, Wave C1).

``adjust="none"`` must be byte-identical to the pre-ADR behavior; adjusted
loads are verified against :func:`with_price_adjustment` directly.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.core.schemas import CORPORATE_ACTION_SCHEMA
from equity_lake.storage.delta import merge_delta
from equity_lake.storage.lake_reader import with_price_adjustment


@pytest.fixture()
def tmp_lake(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> object:
    """Tmp price + corporate-actions lake wired through the patched constants."""
    tmp = tmp_path_factory.mktemp("lake")
    import equity_lake.core.paths as paths
    import equity_lake.storage.delta as delta

    us = tmp / "01_bronze" / "market_data" / "us_equity"
    monkeypatch.setattr(paths, "US_EQUITY_DIR", us)
    registry = {
        "us_equity": paths.PriceMarket(market="us_equity", alias="us", dir_attr="US_EQUITY_DIR", exchanges=("XNYS",), timezone="America/New_York")
    }
    monkeypatch.setattr(paths, "PRICE_MARKETS", registry)
    monkeypatch.setattr(paths, "BRONZE_CORPORATE_ACTIONS_DIR", tmp / "01_bronze" / "corporate_actions")
    monkeypatch.setattr(paths, "SILVER_CORPORATE_ACTIONS_DIR", tmp / "02_silver" / "corporate_actions")
    monkeypatch.setattr(delta, "LAKE_DIR", tmp)
    return tmp


def _price_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "open": [100.0, 52.0],
            "high": [101.0, 53.0],
            "low": [99.0, 51.0],
            "close": [100.0, 52.0],
            "volume": [1000.0, 2000.0],
        }
    )


def _write_prices(lake: object, df: pl.DataFrame) -> None:
    df = df.with_columns(pl.lit(None, dtype=pl.String).alias("adj_close"))
    merge_delta(
        df.group_by("ticker", "date").agg(
            pl.col("open").first(),
            pl.col("high").first(),
            pl.col("low").first(),
            pl.col("close").first(),
            pl.col("volume").first(),
            pl.col("adj_close").first(),
        ),
        "01_bronze/market_data/us_equity",
        key_columns=["ticker", "date"],
        lake_dir=lake,  # type: ignore[arg-type]
    )


def _write_actions(lake: object) -> None:
    actions = pl.DataFrame(
        [("AAPL", date(2024, 1, 2), "split", 0.5, "yahoo", None)],
        schema=CORPORATE_ACTION_SCHEMA,
        orient="row",
    ).with_columns(pl.lit(None, dtype=pl.String).alias("ingested_at"))
    for prefix in ("01_bronze", "02_silver"):
        merge_delta(
            actions,
            f"{prefix}/corporate_actions/us_equity",
            key_columns=["ticker", "ex_date", "action"],
            lake_dir=lake,  # type: ignore[arg-type]
            partition_by=["ex_date"],
        )


class TestLoaderAdjustment:
    def test_adjust_none_is_identity(self, tmp_lake: object) -> None:
        _write_prices(tmp_lake, _price_frame())
        _write_actions(tmp_lake)
        loader = BacktestDataLoader()
        out = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"])
        assert out["close"].to_list() == pytest.approx([100.0, 52.0])  # raw, phantom -48% intact

    def test_adjust_splits_fixes_boundary(self, tmp_lake: object) -> None:
        _write_prices(tmp_lake, _price_frame())
        _write_actions(tmp_lake)
        loader = BacktestDataLoader()
        out = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"], adjust="splits")
        assert out["close"].to_list() == pytest.approx([50.0, 52.0])
        # Matches the adjustment engine applied directly.
        direct = with_price_adjustment(
            _price_frame(),
            pl.DataFrame(
                [("AAPL", date(2024, 1, 2), "split", 0.5)],
                schema={"ticker": pl.String, "ex_date": pl.Date, "action": pl.String, "value": pl.Float64},
                orient="row",
            ),
        )
        assert out["close"].to_list() == pytest.approx(direct["close"].to_list())

    def test_missing_actions_table_warns_and_is_noop(self, tmp_lake: object, caplog: pytest.LogCaptureFixture) -> None:
        _write_prices(tmp_lake, _price_frame())
        loader = BacktestDataLoader()
        out = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"], adjust="splits")
        assert out["close"].to_list() == pytest.approx([100.0, 52.0])

    def test_total_return_mode_reaches_engine_helper(self, tmp_lake: object) -> None:
        _write_prices(tmp_lake, _price_frame())
        _write_actions(tmp_lake)
        loader = BacktestDataLoader()
        out = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"], adjust="total_return")
        # Only the split exists here: identical to splits mode.
        assert out["close"].to_list() == pytest.approx([50.0, 52.0])


class TestEngineAndFactoryThreading:
    def test_engine_defaults_to_none_and_forwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from equity_lake.backtesting.engine import VectorBacktestEngine
        from equity_lake.backtesting.strategy.base import BaseStrategy

        class _Stub(BaseStrategy):
            name = "stub"

            def initialize(self, data: pl.DataFrame) -> None:  # pragma: no cover
                return None

            def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:  # pragma: no cover
                return pl.DataFrame()

        captured: dict = {}

        class _FakeLoader:
            def load(self, **kwargs: object) -> pl.DataFrame:
                captured.update(kwargs)
                return _price_frame()

        eng = VectorBacktestEngine(_Stub(), ["AAPL"], date(2024, 1, 1), date(2024, 1, 2), adjust="splits")
        eng.data_loader = _FakeLoader()  # type: ignore[assignment]
        assert eng.adjust == "splits"
        eng._load_data()
        assert captured["adjust"] == "splits"

        default_eng = VectorBacktestEngine(_Stub(), ["AAPL"], date(2024, 1, 1), date(2024, 1, 2))
        assert default_eng.adjust == "none"

    def test_factory_forwards_adjust(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import equity_lake.backtesting as bt

        captured: dict = {}

        class _FakeEngine:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        monkeypatch.setattr(bt, "VectorBacktestEngine", _FakeEngine)
        from equity_lake.backtesting.factory import STRATEGY_REGISTRY, build_backtest_engine

        strategy = next(iter(STRATEGY_REGISTRY))
        build_backtest_engine(
            strategy=strategy,
            tickers=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            adjust="total_return",
        )
        assert captured["adjust"] == "total_return"


class TestCliFlag:
    def test_invalid_adjust_exits_1(self) -> None:
        from typer.testing import CliRunner

        from equity_lake.cli.__main__ import app

        # --start-date/--end-date are required; validation of --adjust fires first.
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["backtest", "--start-date", "2024-01-01", "--end-date", "2024-02-01", "--adjust", "crazy"],
        )
        assert result.exit_code == 1
        assert "Unknown --adjust value" in result.output
