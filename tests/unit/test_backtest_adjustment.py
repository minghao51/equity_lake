"""Tests for backtest corporate-action adjustment opt-in (ADR-0011, Wave C1).

``adjust="none"`` must be byte-identical to the pre-ADR behavior; adjusted
loads are verified against :func:`with_price_adjustment` directly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from equity_lake.backtesting.data_loader import BacktestDataLoader  # type: ignore[import-untyped]
from equity_lake.backtesting.strategy.base import BaseStrategy  # type: ignore[import-untyped]
from equity_lake.core.availability import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ActionCoverageProof,
)
from equity_lake.core.schemas import CORPORATE_ACTION_SCHEMA  # type: ignore[import-untyped]
from equity_lake.storage.delta import merge_delta  # type: ignore[import-untyped]
from equity_lake.storage.lake_reader import with_price_adjustment  # type: ignore[import-untyped]


@pytest.fixture()
def tmp_lake(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Tmp price + corporate-actions lake wired through the patched constants."""
    tmp = tmp_path_factory.mktemp("lake")
    import equity_lake.core.paths as paths  # type: ignore[import-untyped]
    import equity_lake.storage.delta as delta  # type: ignore[import-untyped]

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
            "market": ["us_equity", "us_equity"],
            "mic": ["XNYS", "XNYS"],
            "symbol": ["AAPL", "AAPL"],
            "ticker": ["AAPL", "AAPL"],
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "open": [100.0, 52.0],
            "high": [101.0, 53.0],
            "low": [99.0, 51.0],
            "close": [100.0, 52.0],
            "volume": [1000.0, 2000.0],
        }
    )


def _write_prices(lake: Path, df: pl.DataFrame) -> None:
    df = df.with_columns(pl.lit(None, dtype=pl.String).alias("adj_close"))
    merge_delta(
        df.group_by("market", "mic", "symbol", "ticker", "date").agg(
            pl.col("open").first(),
            pl.col("high").first(),
            pl.col("low").first(),
            pl.col("close").first(),
            pl.col("volume").first(),
            pl.col("adj_close").first(),
        ),
        "01_bronze/market_data/us_equity",
        key_columns=["market", "mic", "symbol", "date"],
        lake_dir=lake,
    )


def _write_actions(lake: Path) -> None:
    source_time = datetime(2023, 12, 29, 20, 0, tzinfo=UTC)
    actions = pl.DataFrame(
        [("AAPL", date(2024, 1, 2), "split", 0.5, "yahoo", datetime(2023, 12, 29, 20, 0), source_time)],
        schema=CORPORATE_ACTION_SCHEMA,
        orient="row",
    )
    for prefix in ("01_bronze", "02_silver"):
        merge_delta(
            actions,
            f"{prefix}/corporate_actions/us_equity",
            key_columns=["ticker", "ex_date", "action"],
            lake_dir=lake,
            partition_by=["ex_date"],
        )
    proof = ActionCoverageProof(
        market="us_equity",
        mic="XNYS",
        symbol="AAPL",
        source="yahoo",
        coverage_start=date(2024, 1, 1),
        coverage_end=date(2024, 1, 2),
        source_available_at=source_time,
        source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
    )
    merge_delta(
        pl.DataFrame([proof.model_dump()]),
        "02_silver/corporate_action_coverage/us_equity",
        key_columns=["market", "mic", "symbol", "source", "coverage_start", "coverage_end"],
        lake_dir=lake,
        partition_by=["coverage_end"],
    )


class TestLoaderAdjustment:
    def test_default_adjustment_is_split_only(self, tmp_lake: Path) -> None:
        _write_prices(tmp_lake, _price_frame())
        _write_actions(tmp_lake)
        loader = BacktestDataLoader()
        out = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"])
        assert out["close"].to_list() == pytest.approx([100.0, 104.0])
        assert set(out["price_adjustment_version"].to_list()) == {"split-only-v1"}
        assert out["point_in_time_available"].to_list() == [1, 1]

    def test_adjust_splits_fixes_boundary(self, tmp_lake: Path) -> None:
        _write_prices(tmp_lake, _price_frame())
        _write_actions(tmp_lake)
        loader = BacktestDataLoader()
        out = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"], adjust="splits")
        assert out["close"].to_list() == pytest.approx([100.0, 104.0])
        # The loader exposes split-neutral returns, not a final-window historical basis.
        load_inputs = __import__(
            "equity_lake.ingestion.corporate_actions",
            fromlist=["load_corporate_action_inputs"],
        ).load_corporate_action_inputs
        actions, coverage = load_inputs("us_equity", lake_dir=tmp_lake)
        direct = with_price_adjustment(_price_frame(), actions, coverage=coverage)
        assert out["close"][1] / out["close"][0] == pytest.approx(direct["close"][1] / direct["close"][0])

    def test_missing_action_coverage_is_masked_and_unavailable(self, tmp_lake: Path) -> None:
        _write_prices(tmp_lake, _price_frame())
        loader = BacktestDataLoader()
        out = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"])
        assert out["point_in_time_available"].to_list() == [0, 0]
        assert out["action_coverage"].to_list() == [0, 0]
        assert out["close"].null_count() == out.height

    def test_total_return_mode_is_rejected(self, tmp_lake: Path) -> None:
        _write_prices(tmp_lake, _price_frame())
        _write_actions(tmp_lake)
        loader = BacktestDataLoader()
        with pytest.raises(ValueError, match="split-only"):
            loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 2), markets=["us_equity"], adjust="total_return")


class TestEngineAndFactoryThreading:
    def test_engine_defaults_to_none_and_forwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from equity_lake.backtesting.engine import VectorBacktestEngine  # type: ignore[import-untyped]

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
        monkeypatch.setattr(eng, "data_loader", _FakeLoader())
        assert eng.adjust == "splits"
        eng._load_data()
        assert captured["adjust"] == "splits"

        default_eng = VectorBacktestEngine(_Stub(), ["AAPL"], date(2024, 1, 1), date(2024, 1, 2))
        assert default_eng.adjust == "splits"

    def test_factory_forwards_adjust(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import equity_lake.backtesting as bt  # type: ignore[import-untyped]

        captured: dict = {}

        class _FakeEngine:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        monkeypatch.setattr(bt, "VectorBacktestEngine", _FakeEngine)
        from equity_lake.backtesting.factory import STRATEGY_REGISTRY, build_backtest_engine  # type: ignore[import-untyped]

        strategy = next(iter(STRATEGY_REGISTRY))
        build_backtest_engine(
            strategy=strategy,
            tickers=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            adjust="splits",
        )
        assert captured["adjust"] == "splits"


class TestCliFlag:
    def test_invalid_adjust_exits_1(self) -> None:
        from typer.testing import CliRunner

        from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]

        # --start-date/--end-date are required; validation of --adjust fires first.
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["backtest", "--start-date", "2024-01-01", "--end-date", "2024-02-01", "--adjust", "crazy"],
        )
        assert result.exit_code == 1
        assert "Unknown --adjust value" in result.output

    def test_help_documents_only_split_adjustment_modes(self) -> None:
        from typer.testing import CliRunner

        from equity_lake.cli.__main__ import app

        result = CliRunner().invoke(app, ["backtest", "--help"])
        assert result.exit_code == 0
        help_output = " ".join(result.output.split())
        assert "--adjust" in help_output
        assert "split-only" in help_output
        assert "default: splits" in help_output
        assert "'none'" in help_output and "legacy alias" in help_output
        assert "total_return" not in help_output

    def test_total_return_rejected_before_building_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from typer.testing import CliRunner

        from equity_lake.backtesting import factory
        from equity_lake.cli.__main__ import app

        engine_builds: list[dict[str, object]] = []
        monkeypatch.setattr(factory, "build_backtest_engine", lambda **kwargs: engine_builds.append(kwargs))
        result = CliRunner().invoke(
            app,
            ["backtest", "--start-date", "2024-01-01", "--end-date", "2024-02-01", "--adjust", "total_return"],
        )
        assert result.exit_code == 1
        assert "--adjust total_return is unsupported" in result.output
        assert engine_builds == []


def _point_in_time_loaded_data(*, with_split: bool, source_valid_until: datetime = datetime(2024, 1, 10, 23, 0, tzinfo=UTC)) -> pl.DataFrame:
    """Build loader handoff with a split published after the first two decisions."""
    from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]

    instrument = InstrumentKey(market="us_equity", mic="XNYS", symbol="AAPL")
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    prices = pl.DataFrame(
        {
            "market": [instrument.market] * 4,
            "mic": [instrument.mic] * 4,
            "symbol": [instrument.symbol] * 4,
            "ticker": [instrument.symbol] * 4,
            "date": dates,
            "open": [100.0, 102.0, 51.0, 52.0],
            "high": [100.0, 102.0, 51.0, 52.0],
            "low": [100.0, 102.0, 51.0, 52.0],
            "close": [100.0, 102.0, 51.0, 52.0],
            "volume": [1000.0] * 4,
        }
    )
    event_time = datetime(2024, 1, 3, 22, 0, tzinfo=UTC)
    actions = pl.DataFrame(
        [
            {
                "ticker": "AAPL",
                "ex_date": date(2024, 1, 4),
                "action": "split",
                "value": 0.5,
                "source_available_at": event_time,
            }
        ]
        if with_split
        else [],
        schema={
            "ticker": pl.String,
            "ex_date": pl.Date,
            "action": pl.String,
            "value": pl.Float64,
            "source_available_at": pl.Datetime("us", "UTC"),
        },
    )
    proof = ActionCoverageProof(
        market=instrument.market,
        mic=instrument.mic,
        symbol=instrument.symbol,
        source="synthetic-source",
        coverage_start=dates[0],
        coverage_end=dates[-1],
        source_available_at=datetime(2023, 12, 29, 20, 0, tzinfo=UTC),
        source_valid_until=source_valid_until,
    )
    loader = BacktestDataLoader(action_inputs={instrument.tuple: (actions, pl.DataFrame([proof.model_dump()]))})
    carried = loader._attach_point_in_time_context(prices, ["us_equity"])
    return cast(pl.DataFrame, loader._point_in_time_marks(carried, ["us_equity"], "splits"))


class _PITThresholdStrategy(BaseStrategy):
    """Minimal strategy whose rolling input reveals a full-window adjustment leak."""

    def __init__(self) -> None:
        super().__init__()
        self._data = pl.DataFrame()

    def initialize(self, data: pl.DataFrame) -> None:
        self._data = data

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        max_close = self._data["close"].max()
        weight = 1.0 if isinstance(max_close, (int, float)) and max_close >= 80 else 0.0
        return data.select("date", "market", "mic", "symbol").with_columns(pl.lit(weight).alias("weight"))

    def finalize(self) -> None:
        return None


def test_expired_proof_excludes_later_sessions_without_erasing_earlier_results() -> None:
    from equity_lake.backtesting.engine import VectorBacktestEngine
    from equity_lake.core.instruments import InstrumentKey

    data = _point_in_time_loaded_data(with_split=False, source_valid_until=datetime(2024, 1, 3, 23, 0, tzinfo=UTC))
    assert data["point_in_time_available"].to_list() == [1, 1, 0, 0]
    instrument = InstrumentKey(market="us_equity", mic="XNYS", symbol="AAPL")
    engine = VectorBacktestEngine(_PITThresholdStrategy(), [instrument], date(2024, 1, 2), date(2024, 1, 5), preloaded_data=data)
    marks, weights = engine._prepare_point_in_time_inputs(data)
    assert marks["date"].to_list() == [date(2024, 1, 2), date(2024, 1, 3)]
    assert weights["date"].to_list() == [date(2024, 1, 2), date(2024, 1, 3)]


def test_backtest_recomputes_roll_windows_asof_and_keeps_split_returns_neutral() -> None:
    from equity_lake.backtesting.engine import VectorBacktestEngine
    from equity_lake.core.instruments import InstrumentKey

    actual = _point_in_time_loaded_data(with_split=True)
    no_action = _point_in_time_loaded_data(with_split=False)
    strategy = _PITThresholdStrategy()
    instrument = InstrumentKey(market="us_equity", mic="XNYS", symbol="AAPL")
    engine = VectorBacktestEngine(strategy, [instrument], date(2024, 1, 2), date(2024, 1, 5), preloaded_data=actual)
    split_marks, split_weights = engine._prepare_point_in_time_inputs(actual)
    no_split_engine = VectorBacktestEngine(_PITThresholdStrategy(), [instrument], date(2024, 1, 2), date(2024, 1, 5), preloaded_data=no_action)
    no_split_marks, no_split_weights = no_split_engine._prepare_point_in_time_inputs(no_action)

    assert split_weights.sort("date")["weight"].to_list()[:2] == no_split_weights.sort("date")["weight"].to_list()[:2] == [1.0, 1.0]
    # Once the split is effective and observable, the rolling history is recomputed
    # on its newly comparable share basis; the future final-window basis never
    # rewrites the earlier decision context.
    assert split_weights.sort("date")["weight"].to_list() == [1.0, 1.0, 0.0, 0.0]
    split_closes = split_marks.sort("date")["close"].to_list()
    raw_no_split_closes = no_split_marks.sort("date")["close"].to_list()
    assert split_closes[:2] == raw_no_split_closes[:2] == pytest.approx([100.0, 102.0])
    assert split_closes[2] == pytest.approx(102.0)  # no split-induced 50% execution loss
    assert split_closes[3] == pytest.approx(104.0)

    narrow_engine = VectorBacktestEngine(strategy, [instrument], date(2024, 1, 3), date(2024, 1, 4), preloaded_data=actual)
    narrow_marks, narrow_weights = narrow_engine._prepare_point_in_time_inputs(actual)
    assert narrow_marks["date"].to_list() == [date(2024, 1, 3), date(2024, 1, 4)]
    assert narrow_weights["date"].to_list() == [date(2024, 1, 3), date(2024, 1, 4)]


def test_unverified_preloaded_backtest_context_is_excluded_not_treated_as_raw() -> None:
    from equity_lake.backtesting.engine import VectorBacktestEngine
    from equity_lake.core.availability import AvailabilityContractError

    raw = _price_frame()
    engine = VectorBacktestEngine(_PITThresholdStrategy(), ["AAPL"], date(2024, 1, 2), date(2024, 1, 5), preloaded_data=raw)
    with pytest.raises(AvailabilityContractError, match="lacks verified point-in-time action context"):
        engine._prepare_point_in_time_inputs(raw)
