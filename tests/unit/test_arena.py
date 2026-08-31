"""Tests for the strategy arena + report/FindingCard generation (Phase 1)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from equity_lake.backtesting.arena import (
    COST_REGIMES,
    MARKET_COST_DEFAULTS,
    STRATEGY_REGISTRY,
    _resolve_regime_costs,
    equal_weight_buyhold,
    run_arena,
)
from equity_lake.backtesting.report import (
    build_finding_cards,
    drawdown_series,
    write_arena_artifacts,
)
from equity_lake.findings import load_finding_cards

pytestmark = pytest.mark.slow


def _synthetic_data(n_tickers: int = 12, n_days: int = 320, seed: int = 42) -> pl.DataFrame:
    """Deterministic long OHLCV frame (business days only) for arena tests."""
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:02d}" for i in range(n_tickers)]
    start = date(2022, 1, 3)
    dates = [start + timedelta(days=i) for i in range(n_days) if (start + timedelta(days=i)).weekday() < 5]
    rows: list[dict] = []
    for t in tickers:
        px = 100.0
        for d in dates:
            px *= 1.0 + rng.normal(0.0004, 0.015)
            rows.append({"ticker": t, "date": d, "open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": 1e6})
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# Pure helpers (fast, no engine)
# ---------------------------------------------------------------------------


def test_equal_weight_buyhold_empty_returns_empty_schema() -> None:
    out = equal_weight_buyhold(pl.DataFrame(), initial_cash=50_000.0)
    assert out.schema == {"date": pl.Date, "equity": pl.Float64}
    assert out.is_empty()


def test_equal_weight_buyhold_grows_from_initial_cash() -> None:
    data = _synthetic_data(n_tickers=3, n_days=60, seed=1)
    out = equal_weight_buyhold(data, initial_cash=100_000.0)
    assert out.columns == ["date", "equity"]
    assert out.height > 1
    # first equity is ~initial_cash (first-day return is null -> filled 0 -> growth 1.0)
    assert out["equity"][0] == pytest.approx(100_000.0, rel=1e-6)


def test_drawdown_series_is_non_positive_and_peaks_at_zero() -> None:
    equity = pl.Series("equity", [100.0, 110.0, 90.0, 95.0])
    dd = drawdown_series(equity)
    assert dd.len() == 4
    assert dd.max() == pytest.approx(0.0)  # at the running peak
    assert dd.min() < 0.0  # the 90 trough


def test_drawdown_series_empty() -> None:
    dd = drawdown_series(pl.Series("equity", [], dtype=pl.Float64))
    assert dd.is_empty()


# ---------------------------------------------------------------------------
# Arena + report (uses polars-backtest engine)
# ---------------------------------------------------------------------------


def test_run_arena_matrix_and_benchmark() -> None:
    data = _synthetic_data()
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        data["date"].min(),
        data["date"].max(),
        preloaded_data=data,
        strategies=list(STRATEGY_REGISTRY),
        cost_regimes=list(COST_REGIMES),
    )
    # every (strategy, regime) combo attempted should produce a run on this data
    expected = {(s, r) for s in STRATEGY_REGISTRY for r in COST_REGIMES}
    actual = {(run.strategy, run.cost_regime) for run in outcome.runs}
    assert expected <= actual, f"missing runs: {expected - actual}"
    # benchmark is the same shared data scale
    assert outcome.benchmark.columns == ["date", "equity"]
    assert outcome.benchmark.height > 1
    assert outcome.initial_cash == 100_000.0


def test_run_arena_rejects_unknown_strategies() -> None:
    data = _synthetic_data(n_tickers=12, n_days=60)
    with pytest.raises(ValueError, match="Unknown strategies"):
        run_arena(
            ["T00"],
            data["date"].min(),
            data["date"].max(),
            preloaded_data=data,
            strategies=["bogus"],
        )


# ---------------------------------------------------------------------------
# Per-market realistic cost defaults (B5)
# ---------------------------------------------------------------------------


def test_realistic_regime_resolves_per_market_costs() -> None:
    """US runs pay no sell tax; CN pays stamp duty; other regimes pass through."""
    us = _resolve_regime_costs("realistic", ("us",), MARKET_COST_DEFAULTS)
    cn = _resolve_regime_costs("realistic", ("cn",), MARKET_COST_DEFAULTS)
    assert us["tax_ratio"] == 0.0
    assert cn["tax_ratio"] > 0.0  # A-share stamp duty on sells
    assert us["fee_ratio"] > 0.0
    # zero/high regimes are market-agnostic
    assert _resolve_regime_costs("zero", ("us",), MARKET_COST_DEFAULTS) == {"fee_ratio": 0.0, "tax_ratio": 0.0}
    assert _resolve_regime_costs("high", ("cn",), MARKET_COST_DEFAULTS)["fee_ratio"] == 0.005


def test_realistic_regime_multi_market_falls_back_to_engine_defaults() -> None:
    """One engine run cannot mix per-venue taxes; multi-market runs use engine defaults."""
    fallback = _resolve_regime_costs("realistic", ("us", "cn"), MARKET_COST_DEFAULTS)
    assert fallback == COST_REGIMES["realistic"]


def test_realistic_regime_accepts_market_cost_overrides() -> None:
    custom = {"us": {"fee_ratio": 0.002, "tax_ratio": 0.001}}
    resolved = _resolve_regime_costs("realistic", ("us",), custom)
    assert resolved == {"fee_ratio": 0.002, "tax_ratio": 0.001}


def test_build_finding_cards_axes_and_verdicts() -> None:
    data = _synthetic_data()
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        data["date"].min(),
        data["date"].max(),
        preloaded_data=data,
    )
    cards = build_finding_cards(outcome, run_date=date(2026, 8, 4), scope={"synthetic": True})
    axes = {c.axis for c in cards}
    assert axes <= {"strategy", "cost", "benchmark"}
    assert all(c.verdict in {"positive", "negative", "inconclusive"} for c in cards)
    # scope carries ticker count + the caller's tag
    assert all(c.scope.get("synthetic") is True for c in cards)
    assert all(c.scope.get("tickers") == 12 for c in cards)
    # B2: the Sharpe risk-free-rate convention is stated in card metadata
    assert all(c.scope.get("sharpe_risk_free_rate") == 0.02 for c in cards)
    # B5: benchmark lag/cost asymmetry is disclosed in card scope
    assert all("benchmark_asymmetry" in c.scope for c in cards)


def test_write_arena_artifacts_roundtrip(tmp_path: Path) -> None:
    data = _synthetic_data()
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        data["date"].min(),
        data["date"].max(),
        preloaded_data=data,
    )
    written = write_arena_artifacts(outcome, base=tmp_path, run_date=date(2026, 8, 4), scope={"synthetic": True})
    first_slug = f"{outcome.runs[0].strategy}__{outcome.runs[0].cost_regime}"
    assert (tmp_path / first_slug).is_dir()
    assert (tmp_path / first_slug / "metrics.json").exists()
    assert (tmp_path / "benchmark__equity.parquet").exists()
    # cards were written and reload cleanly
    loaded = load_finding_cards(base=tmp_path)
    assert {c.id for c in loaded} == {c.id for c in written}


def test_card_evidence_refs_point_at_artifacts_write_arena_actually_writes(tmp_path: Path) -> None:
    """B5: evidence_refs must reference real per-run artifact dirs, not phantom dirs."""
    data = _synthetic_data()
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        data["date"].min(),
        data["date"].max(),
        preloaded_data=data,
    )
    cards = write_arena_artifacts(outcome, base=tmp_path, run_date=date(2026, 8, 4))
    assert cards
    written_dirs = {p.name for p in tmp_path.iterdir() if p.is_dir()}
    written_files = {p.name for p in tmp_path.iterdir() if p.is_file()}
    for card in cards:
        assert card.evidence_refs, f"card {card.id} must carry evidence refs"
        for ref in card.evidence_refs:
            if ref.endswith("/"):
                assert ref.rstrip("/") in written_dirs, f"{card.id} references unwritten dir {ref}"
            else:
                assert ref in written_files, f"{card.id} references unwritten file {ref}"
