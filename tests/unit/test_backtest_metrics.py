"""Tests for the shared equity-curve metrics helper and engine integration (handoff 08, B2/B3)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from runpy import run_path

import numpy as np
import polars as pl
import pytest

from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE, VectorBacktestEngine  # type: ignore[import-untyped]
from equity_lake.backtesting.metrics import (  # type: ignore[import-untyped]
    DEFAULT_RISK_FREE_RATE,
    TRADING_DAYS_PER_YEAR,
    equity_curve_metrics,
    sharpe_ratio,
)
from equity_lake.backtesting.strategy.trend_following import SMACrossoverStrategy  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]

_pit_fixtures = run_path(str(Path(__file__).with_name("_pit_fixtures.py")))
prepare_preloaded_backtest_data = _pit_fixtures["prepare_preloaded_backtest_data"]
us_equity_session_dates = _pit_fixtures["us_equity_session_dates"]


def _series(values: list[float]) -> pl.Series:
    return pl.Series("equity", values, dtype=pl.Float64)


# ---------------------------------------------------------------------------
# Shared helper — one convention for engine and report sides
# ---------------------------------------------------------------------------


def test_sharpe_matches_hand_computed_excess_return_formula() -> None:
    returns = np.array([0.01, -0.02, 0.03, 0.005, -0.01, 0.02, 0.015, -0.005, 0.01, 0.025])
    rf_periodic = (1.0 + DEFAULT_RISK_FREE_RATE) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0
    excess = returns - rf_periodic
    expected = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
    assert sharpe_ratio(returns) == pytest.approx(expected, rel=1e-12)
    assert sharpe_ratio(returns, rf=0.0) > sharpe_ratio(returns, rf=DEFAULT_RISK_FREE_RATE)


def test_sharpe_zero_for_constant_or_short_series() -> None:
    assert sharpe_ratio(np.array([0.01] * 10)) == 0.0  # zero excess-return std
    assert sharpe_ratio(np.array([0.01])) == 0.0  # fewer than 2 returns
    assert equity_curve_metrics(_series([100.0]), 100.0) == {"total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0}


def test_equity_curve_metrics_total_return_and_drawdown() -> None:
    out = equity_curve_metrics(_series([100.0, 120.0, 90.0, 99.0]), 100.0)
    assert out["total_return"] == pytest.approx(-0.01)
    assert out["max_drawdown"] == pytest.approx(-0.25)  # 90 vs 120 peak


def test_equity_curve_metrics_accepts_dataframe_with_equity_column() -> None:
    frame = pl.DataFrame({"date": [date(2025, 1, 1), date(2025, 1, 2)], "equity": [100.0, 101.0]})
    assert equity_curve_metrics(frame, 100.0)["total_return"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Engine-side unification + warnings + lazy loader
# ---------------------------------------------------------------------------


def _ohlcv(closes: list[float], ticker: str = "TEST") -> pl.DataFrame:
    start = date(2025, 1, 1)
    dates = us_equity_session_dates(start, len(closes))
    return pl.DataFrame(
        {
            "ticker": [ticker] * len(closes),
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )


@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")
def test_engine_headline_metrics_use_the_shared_helper() -> None:
    """result.sharpe/total_return/max_drawdown == shared helper on result.equity_curve."""
    closes = [100.0] * 25 + [100.0 + 1.0 * i for i in range(1, 40)] + [139.0 - 1.5 * i for i in range(1, 40)]
    data = _ohlcv(closes)
    dates = data["date"].to_list()
    engine = VectorBacktestEngine(
        strategy=SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20}),
        tickers=["TEST"],
        start_date=dates[0],
        end_date=dates[-1],
        initial_cash=10_000.0,
        preloaded_data=prepare_preloaded_backtest_data(data, InstrumentKey(market="us_equity", mic="XNAS", symbol="TEST")),
    )
    result = engine.run()

    shared = equity_curve_metrics(result.equity_curve, 10_000.0)
    assert result.sharpe_ratio == pytest.approx(shared["sharpe_ratio"])
    assert result.total_return == pytest.approx(shared["total_return"])
    assert result.max_drawdown == pytest.approx(shared["max_drawdown"])


@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")
def test_engine_with_preloaded_data_never_creates_a_data_loader() -> None:
    """Arena engines (preloaded_data) must not open DuckDB connections (B3)."""
    data = _ohlcv([100.0] * 40)
    dates = data["date"].to_list()
    engine = VectorBacktestEngine(
        strategy=SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20}),
        tickers=["TEST"],
        start_date=dates[0],
        end_date=dates[-1],
        preloaded_data=prepare_preloaded_backtest_data(data, InstrumentKey(market="us_equity", mic="XNAS", symbol="TEST")),
    )
    assert engine.data_loader is None
    engine.run()
    assert engine.data_loader is None  # still lazy after a full run


@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")
def test_engine_stats_failure_goes_to_warnings_list_not_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing get_stats() lands in BacktestResult.warnings; metrics stay floats."""
    from polars_backtest.namespace import BacktestNamespace

    closes = [100.0 + 1.0 * i for i in range(40)]
    data = _ohlcv(closes)
    dates = data["date"].to_list()

    class _FailingStatsReport:
        @property
        def trades(self) -> pl.DataFrame:
            return pl.DataFrame()

        @property
        def creturn(self) -> pl.DataFrame:
            return pl.DataFrame({"date": data["date"].to_list(), "creturn": [1.0 + 0.001 * i for i in range(len(closes))]})

        def get_stats(self, riskfree_rate: float = 0.02) -> pl.DataFrame:
            raise RuntimeError("stats boom")

    monkeypatch.setattr(BacktestNamespace, "backtest_with_report", lambda self, **kwargs: _FailingStatsReport())

    engine = VectorBacktestEngine(
        strategy=SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20}),
        tickers=["TEST"],
        start_date=dates[0],
        end_date=dates[-1],
        preloaded_data=prepare_preloaded_backtest_data(data, InstrumentKey(market="us_equity", mic="XNAS", symbol="TEST")),
    )
    result = engine.run()

    assert result.warnings == [
        "Backtest execution and trade prices use a split-neutral return index, not raw vendor quotes",
        "stats unavailable: stats boom",
    ]
    # metrics must remain dict[str, float] — no str smuggled in via a "warning" key
    assert all(isinstance(v, float) for v in result.metrics.values())
    assert "warning" not in result.metrics
    # headline metrics still computed from the equity curve via the shared helper
    shared = equity_curve_metrics(result.equity_curve, engine.initial_cash)
    assert result.sharpe_ratio == pytest.approx(shared["sharpe_ratio"])


def test_engine_install_message_says_group_not_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ImportError install hint must match the uv dependency-group reality."""
    import equity_lake.backtesting.engine as engine_mod  # type: ignore[import-untyped]

    monkeypatch.setattr(engine_mod, "POLARS_BACKTEST_AVAILABLE", False)
    engine = VectorBacktestEngine(
        strategy=SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20}),
        tickers=["TEST"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
    )
    with pytest.raises(ImportError, match=r"uv sync --group backtesting"):
        engine.run()
