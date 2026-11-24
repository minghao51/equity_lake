"""Tests for the strategy arena + report/FindingCard generation (Phase 1)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl
import pytest

from equity_lake.backtesting.arena import (
    BENCHMARK_MISSING_MARK_POLICY,
    BENCHMARK_POLICY,
    COST_REGIMES,
    MARKET_COST_DEFAULTS,
    STRATEGY_REGISTRY,
    ArenaOutcome,
    ArenaRun,
    _require_usable_benchmark,
    _resolve_regime_costs,
    equal_weight_buyhold,
    run_arena,
)
from equity_lake.backtesting.attempts import BENCHMARK_ATTEMPT_ARTIFACT
from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE
from equity_lake.backtesting.evidence_view import latest_attempt
from equity_lake.backtesting.report import (
    build_finding_cards,
    drawdown_series,
    write_arena_artifacts,
)
from equity_lake.backtesting.result import BacktestResult
from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ActionCoverageProof,
)
from equity_lake.findings import load_finding_cards

pytestmark = pytest.mark.slow

# The engine is an optional group (`uv sync --group backtesting`); a bare
# `uv sync` must still produce a green suite, so only the engine-backed tests
# skip. The pure helpers below keep running without the group.
requires_engine = pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")


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
    data = pl.DataFrame(rows).with_columns(
        pl.lit("us_equity").alias("market"),
        pl.lit("XNYS").alias("mic"),
        pl.col("ticker").alias("symbol"),
    )
    context_by_ticker: dict[str, str] = {}
    for ticker in tickers:
        proof = ActionCoverageProof(
            market="us_equity",
            mic="XNYS",
            symbol=ticker,
            source="synthetic-arena",
            coverage_start=min(dates),
            coverage_end=max(dates),
            source_available_at=datetime(2021, 12, 31, 20, 0, tzinfo=UTC),
            source_valid_until=datetime(2023, 1, 1, tzinfo=UTC),
        )
        context_by_ticker[ticker] = json.dumps({"actions": [], "coverage": [proof.model_dump(mode="json")]})
    return data.with_columns(
        *[pl.col(column).alias(f"__pit_raw_{column}") for column in ("open", "high", "low", "close", "volume")],
        pl.col("ticker").replace_strict(context_by_ticker, return_dtype=pl.String).alias("__pit_action_context"),
    )


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
    # F01: equal capital is allocated at the inception mark, so the first equity
    # point is exactly the initial cash (no return to compound yet).
    assert out["equity"][0] == pytest.approx(100_000.0, rel=1e-12)


def test_drawdown_series_is_non_positive_and_peaks_at_zero() -> None:
    equity = pl.Series("equity", [100.0, 110.0, 90.0, 95.0])
    dd = drawdown_series(equity)
    assert dd.len() == 4
    assert dd.max() == pytest.approx(0.0)  # at the running peak
    assert cast(float, dd.min()) < 0.0  # the 90 trough


def test_drawdown_series_empty() -> None:
    dd = drawdown_series(pl.Series("equity", [], dtype=pl.Float64))
    assert dd.is_empty()


# ---------------------------------------------------------------------------
# Arena + report (uses polars-backtest engine)
# ---------------------------------------------------------------------------


@requires_engine
def test_run_arena_matrix_and_benchmark() -> None:
    data = _synthetic_data(n_days=80)
    start_date, end_date = cast(date, data["date"].min()), cast(date, data["date"].max())
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        start_date,
        end_date,
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
    start_date, end_date = cast(date, data["date"].min()), cast(date, data["date"].max())
    with pytest.raises(ValueError, match="Unknown strategies"):
        run_arena(
            ["T00"],
            start_date,
            end_date,
            preloaded_data=data,
            strategies=["bogus"],
        )


# ---------------------------------------------------------------------------
# Per-market realistic cost defaults (B5)
# ---------------------------------------------------------------------------


def test_realistic_regime_resolves_per_market_costs() -> None:
    """US runs pay no sell tax; CN pays stamp duty; other regimes pass through."""
    us = _resolve_regime_costs("realistic", ("us_equity",), MARKET_COST_DEFAULTS)
    cn = _resolve_regime_costs("realistic", ("cn_ashare",), MARKET_COST_DEFAULTS)
    assert us["tax_ratio"] == 0.0
    assert cn["tax_ratio"] > 0.0  # A-share stamp duty on sells
    assert us["fee_ratio"] > 0.0
    # zero/high regimes are market-agnostic
    assert _resolve_regime_costs("zero", ("us_equity",), MARKET_COST_DEFAULTS) == {"fee_ratio": 0.0, "tax_ratio": 0.0}
    assert _resolve_regime_costs("high", ("cn_ashare",), MARKET_COST_DEFAULTS)["fee_ratio"] == 0.005


def test_realistic_regime_multi_market_falls_back_to_engine_defaults() -> None:
    """One engine run cannot mix per-venue taxes; multi-market runs use engine defaults."""
    fallback = _resolve_regime_costs("realistic", ("us_equity", "cn_ashare"), MARKET_COST_DEFAULTS)
    assert fallback == COST_REGIMES["realistic"]


def test_realistic_regime_accepts_market_cost_overrides() -> None:
    custom = {"us_equity": {"fee_ratio": 0.002, "tax_ratio": 0.001}}
    resolved = _resolve_regime_costs("realistic", ("us_equity",), custom)
    assert resolved == {"fee_ratio": 0.002, "tax_ratio": 0.001}


@requires_engine
def test_build_finding_cards_axes_and_verdicts() -> None:
    """Cards must come from genuinely evaluated runs, not an empty no-trade sweep.

    F04: the old 80-day/all-strategy fixture left every run ``no_trades``, so
    ``build_finding_cards`` returned ``[]`` and every assertion below passed on
    empty collections. A long-enough fixture with strategies that actually trade
    (momentum's 252+21 lookback cannot trade here) keeps this a real check.
    """
    data = _synthetic_data(n_days=320)
    start_date, end_date = cast(date, data["date"].min()), cast(date, data["date"].max())
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        start_date,
        end_date,
        preloaded_data=data,
        strategies=["mean_reversion", "trend_following"],
        cost_regimes=["zero", "realistic"],
    )
    # Precondition: every requested pair produced *evaluated* evidence.
    assert {(run.strategy, run.cost_regime) for run in outcome.runs} == {
        ("mean_reversion", "zero"),
        ("mean_reversion", "realistic"),
        ("trend_following", "zero"),
        ("trend_following", "realistic"),
    }
    assert {run.evaluation_status for run in outcome.runs} == {"evaluated"}
    assert outcome.failed == []

    cards = build_finding_cards(outcome, run_date=date(2026, 8, 4), scope={"synthetic": True})
    assert cards, "evaluated runs must produce cards (the old fixture produced none)"
    by_id = {card.id: card for card in cards}
    assert set(by_id) == {"strategy-comparison", "vs-benchmark", "cost-regime"}, sorted(by_id)
    axes = {c.axis for c in cards}
    assert axes <= {"strategy", "cost", "benchmark"}
    assert all(c.verdict in {"positive", "negative", "inconclusive"} for c in cards)
    # Measured values, not an unavailable/empty card.
    assert by_id["strategy-comparison"].metrics
    assert "benchmark.sharpe" in by_id["strategy-comparison"].metrics
    assert "cost_regime.sharpe_delta" in by_id["cost-regime"].metrics
    # Numeric pin (not key-presence only): the cost card's delta is the measured
    # realistic-minus-zero Sharpe of the representative strategy's own runs. The
    # representative strategy is the first *realistic* evaluated run, which is
    # ``mean_reversion`` here (strategies are the outer loop in ``run_arena``).
    pair_runs = {(run.strategy, run.cost_regime): run for run in outcome.runs}
    zero_sharpe = pair_runs[("mean_reversion", "zero")].result.metrics["sharpe_ratio"]
    realistic_sharpe = pair_runs[("mean_reversion", "realistic")].result.metrics["sharpe_ratio"]
    cost_metrics = by_id["cost-regime"].metrics
    assert cost_metrics["cost_regime.sharpe_delta"] == pytest.approx(realistic_sharpe - zero_sharpe, abs=1e-12)
    assert cost_metrics["zero.sharpe"] == pytest.approx(zero_sharpe, abs=1e-12)
    assert cost_metrics["realistic.sharpe"] == pytest.approx(realistic_sharpe, abs=1e-12)
    assert cost_metrics["cost_regime.sharpe_delta"] != 0.0, "a zero delta would make this pin vacuous"
    # scope carries ticker count + the caller's tag
    assert all(c.scope.get("synthetic") is True for c in cards)
    assert all(c.scope.get("tickers") == 12 for c in cards)
    # B2: the Sharpe risk-free-rate convention is stated in card metadata
    assert all(c.scope.get("sharpe_risk_free_rate") == 0.02 for c in cards)
    # B5: benchmark lag/cost asymmetry is disclosed in card scope
    assert all("benchmark_asymmetry" in c.scope for c in cards)
    # F04: completion/evaluation counts are disclosed on every card.
    assert all(c.scope.get("requested_runs") == 4 for c in cards)
    assert all(c.scope.get("completed_runs") == 4 for c in cards)
    assert all(c.scope.get("failed_runs") == 0 for c in cards)
    assert all(c.scope.get("non_evaluable_runs") == 0 for c in cards)


@requires_engine
def test_write_arena_artifacts_roundtrip(tmp_path: Path) -> None:
    data = _synthetic_data(n_days=80)
    start_date, end_date = cast(date, data["date"].min()), cast(date, data["date"].max())
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        start_date,
        end_date,
        preloaded_data=data,
    )
    written = write_arena_artifacts(outcome, base=tmp_path, run_date=date(2026, 8, 4), scope={"synthetic": True})
    first_slug = f"{outcome.runs[0].strategy}__{outcome.runs[0].cost_regime}"
    # E01b.1: no legacy pair directory and no flat benchmark/card is written any more.
    assert not (tmp_path / first_slug).exists()
    assert not (tmp_path / "benchmark__equity.parquet").exists()
    # The invocation's attempt directory owns the benchmark copy and the cards.
    attempt = latest_attempt(tmp_path)
    assert attempt is not None
    assert (attempt.directory / "benchmark__equity.parquet").exists()
    # cards were written and reload cleanly
    loaded = load_finding_cards(base=attempt.directory)
    assert {c.id for c in loaded} == {c.id for c in written}


@requires_engine
def test_card_evidence_refs_point_at_artifacts_write_arena_actually_writes(tmp_path: Path) -> None:
    """B5: the returned cards' evidence_refs name this invocation's real evidence.

    The cards ``write_arena_artifacts`` returns are the ones it persisted, so their refs
    are already bound: every ref must be a ``run:<run_id>`` for a run this invocation
    finalised, or the attempt-owned benchmark copy, never a mutable legacy path.

    F04: cards admit only ``evaluated`` runs, so the fixture must be long enough for
    the strategy windows to trade (momentum's 252+21 lookback is why the default
    mega-cap arena reports ``ineligible``/``no_trades`` instead of evidence).
    """
    data = _synthetic_data(n_days=320)
    start_date, end_date = cast(date, data["date"].min()), cast(date, data["date"].max())
    outcome = run_arena(
        [f"T{i:02d}" for i in range(12)],
        start_date,
        end_date,
        preloaded_data=data,
        strategies=["mean_reversion", "trend_following"],
        cost_regimes=["zero", "realistic"],
    )
    assert {run.evaluation_status for run in outcome.runs} == {"evaluated"}
    cards = write_arena_artifacts(outcome, base=tmp_path, run_date=date(2026, 8, 4))
    assert cards
    attempt = latest_attempt(tmp_path)
    assert attempt is not None
    # E01b.1: the cards are owned by this invocation's attempt directory (never a flat
    # ``<root>/<id>.json``), so a card cannot inherit a later run's overwritten curve.
    assert {path.name for path in attempt.directory.glob("*.json")} >= {f"{card.id}.json" for card in cards}
    assert sorted(tmp_path.glob("*.json")) == []
    published_run_ids = {pair_outcome.run_id for pair_outcome in attempt.record.outcomes if pair_outcome.run_id is not None}
    assert published_run_ids
    for card in cards:
        assert card.evidence_refs, f"card {card.id} must carry evidence refs"
        for ref in card.evidence_refs:
            if ref.startswith("run:"):
                # A run reference names a run this invocation actually finalised.
                assert ref.removeprefix("run:") in published_run_ids, f"{card.id} references an unpublished run {ref}"
                assert (tmp_path / "runs").is_dir()
            else:
                # The only other ref is the attempt's own benchmark copy.
                assert ref == f"attempt:{attempt.record.attempt_id}/{BENCHMARK_ATTEMPT_ARTIFACT}", ref
                assert (attempt.directory / BENCHMARK_ATTEMPT_ARTIFACT).is_file()


# ---------------------------------------------------------------------------
# F01 — fixed-share, identity-safe equal-weight buy-and-hold benchmark (A01)
# ---------------------------------------------------------------------------

_D1, _D2, _D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
_D4 = date(2024, 1, 5)


def _ticker_frame(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    """Ticker-only fixture: [(ticker, date, close), ...] with no identity columns."""
    return pl.DataFrame(
        [{"ticker": ticker, "date": day, "close": close} for ticker, day, close in rows],
        schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64},
    )


def _venue_frame(rows: list[tuple[str, str, str, date, float]]) -> pl.DataFrame:
    """Canonical-identity fixture: [(market, mic, symbol, date, close), ...]."""
    return pl.DataFrame(
        [{"market": market, "mic": mic, "symbol": symbol, "ticker": symbol, "date": day, "close": close} for market, mic, symbol, day, close in rows],
        schema={
            "market": pl.String,
            "mic": pl.String,
            "symbol": pl.String,
            "ticker": pl.String,
            "date": pl.Date,
            "close": pl.Float64,
        },
    )


def _equities(frame: pl.DataFrame, initial_cash: float = 100.0) -> list[float]:
    return [float(value) for value in equal_weight_buyhold(frame, initial_cash=initial_cash)["equity"].to_list()]


def _old_daily_return_average_terminal(frame: pl.DataFrame, initial_cash: float) -> float:
    """The removed pre-F01 rule (compound the daily equal-weight mean return)."""
    daily = (
        frame.select("date", "ticker", "close")
        .sort("date")
        .with_columns(pl.col("close").pct_change().over("ticker").alias("ret"))
        .group_by("date")
        .agg(pl.col("ret").drop_nulls().mean().alias("ew_ret"))
        .sort("date")
        .with_columns((1.0 + pl.col("ew_ret").fill_null(0.0)).cum_prod().alias("growth"))
    )
    return float((daily["growth"] * initial_cash)[-1])


def test_buyhold_uses_fixed_initial_shares() -> None:
    """A 100→200→100 with B flat must end at 100.0, not the old 112.5."""
    frame = _ticker_frame(
        [
            ("AAA", _D1, 100.0),
            ("BBB", _D1, 100.0),
            ("AAA", _D2, 200.0),
            ("BBB", _D2, 100.0),
            ("AAA", _D3, 100.0),
            ("BBB", _D3, 100.0),
        ]
    )
    assert _equities(frame, 100.0) == [100.0, 150.0, 100.0]
    # Non-vacuity: the removed daily-return-averaging rule yields 112.5 on this
    # exact input, so the 100.0 terminal assertion cannot pass on the old code.
    assert _old_daily_return_average_terminal(frame, 100.0) == pytest.approx(112.5)


@pytest.mark.parametrize("initial_cash", [100.0, 250.0, 1_000_000.0])
def test_buyhold_single_instrument_matches_price_path(initial_cash: float) -> None:
    frame = _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, 120.0), ("AAA", _D3, 90.0)])
    assert _equities(frame, initial_cash) == [initial_cash, initial_cash * 1.2, initial_cash * 0.9]


def test_buyhold_scales_linearly_with_initial_cash() -> None:
    frame = _venue_frame(
        [
            ("us_equity", "XNYS", "AAA", _D1, 100.0),
            ("us_equity", "XNYS", "BBB", _D1, 50.0),
            ("us_equity", "XNYS", "AAA", _D2, 175.0),
            ("us_equity", "XNYS", "BBB", _D2, 20.0),
            ("us_equity", "XNYS", "AAA", _D3, 90.0),
            ("us_equity", "XNYS", "BBB", _D3, 60.0),
        ]
    )
    base = equal_weight_buyhold(frame, initial_cash=100.0)["equity"].to_list()
    doubled = equal_weight_buyhold(frame, initial_cash=200.0)["equity"].to_list()
    assert doubled == [value * 2 for value in base]


def test_buyhold_unequal_paths_match_hand_math() -> None:
    frame = _venue_frame(
        [
            ("us_equity", "XNYS", "AAA", _D1, 100.0),
            ("us_equity", "XNYS", "BBB", _D1, 50.0),
            ("us_equity", "XNYS", "AAA", _D2, 200.0),
            ("us_equity", "XNYS", "BBB", _D2, 50.0),
            ("us_equity", "XNYS", "AAA", _D3, 100.0),
            ("us_equity", "XNYS", "BBB", _D3, 100.0),
        ]
    )
    # q_AAA = 50/100 = 0.5, q_BBB = 50/50 = 1.0 -> 100, (0.5*200 + 50), (0.5*100 + 100)
    assert _equities(frame, 100.0) == [100.0, 150.0, 150.0]


def test_buyhold_inception_matches_initial_cash_within_float_tolerance() -> None:
    """Inception equity equals ``initial_cash`` only to float tolerance (1-ULP sum).

    The fixed-share sum ``sum_i q_i * close_i(inception)`` is accumulated in
    floating point and is deliberately not clamped to ``initial_cash``. On the
    reviewer's large-cash shape (cash=4002996831.8095174, inception closes 3 and 7)
    the observed inception value is 4002996831.809517: 4.77e-07 absolute,
    1.19e-16 relative (one ULP at that magnitude).
    """
    cash = 4_002_996_831.8095174
    frame = _venue_frame(
        [
            ("us_equity", "XNYS", "AAA", _D1, 3.0),
            ("us_equity", "XNYS", "BBB", _D1, 7.0),
            ("us_equity", "XNYS", "AAA", _D2, 4.0),
            ("us_equity", "XNYS", "BBB", _D2, 5.0),
            ("us_equity", "XNYS", "AAA", _D3, 6.0),
            ("us_equity", "XNYS", "BBB", _D3, 2.0),
        ]
    )
    curve = equal_weight_buyhold(frame, initial_cash=cash)
    inception = float(curve["equity"][0])
    assert inception == pytest.approx(cash, rel=1e-12)
    relative_error = abs(inception - cash) / cash
    assert relative_error < 1e-12, f"inception drift {relative_error:.3e} exceeds the float budget"
    # The rest of the curve still follows the fixed-share formula.
    # equity(D2) = cash/2 * (4/3 + 5/7) with shares fixed at the D1 marks (3 and 7).
    assert float(curve["equity"][1]) == pytest.approx(cash / 2 * (4.0 / 3.0 + 5.0 / 7.0), rel=1e-12)


def test_buyhold_keeps_same_symbol_different_venues_distinct() -> None:
    """One ticker on two MICs is two scoped instruments, never one collapsed series."""
    frame = _venue_frame(
        [
            ("us_equity", "XNYS", "DUAL", _D1, 100.0),
            ("us_equity", "XNAS", "DUAL", _D1, 100.0),
            ("us_equity", "XNYS", "DUAL", _D2, 300.0),
            ("us_equity", "XNAS", "DUAL", _D2, 100.0),
        ]
    )
    assert _equities(frame, 100.0) == [100.0, 200.0]  # 50 + 150, not 300 (single venue)


def test_buyhold_is_order_invariant() -> None:
    frame = _venue_frame(
        [
            ("us_equity", "XNYS", "AAA", _D1, 100.0),
            ("us_equity", "XNYS", "BBB", _D1, 40.0),
            ("us_equity", "XNAS", "CCC", _D1, 7.5),
            ("us_equity", "XNYS", "AAA", _D2, 110.0),
            ("us_equity", "XNYS", "BBB", _D2, 41.5),
            ("us_equity", "XNAS", "CCC", _D2, 6.25),
            ("us_equity", "XNYS", "AAA", _D3, 90.0),
            ("us_equity", "XNYS", "BBB", _D3, 43.0),
            ("us_equity", "XNAS", "CCC", _D3, 8.125),
        ]
    )
    expected = equal_weight_buyhold(frame, initial_cash=1_000.0)
    reversed_rows = pl.DataFrame([frame.row(index, named=True) for index in reversed(range(frame.height))], schema=frame.schema)
    assert equal_weight_buyhold(reversed_rows, initial_cash=1_000.0).equals(expected)
    for seed in (7, 11):
        order = np.random.default_rng(seed).permutation(frame.height)
        shuffled = pl.DataFrame([frame.row(int(index), named=True) for index in order], schema=frame.schema)
        assert equal_weight_buyhold(shuffled, initial_cash=1_000.0).equals(expected)


def test_buyhold_empty_input_preserves_schema() -> None:
    empty = equal_weight_buyhold(_venue_frame([]), initial_cash=50_000.0)
    assert empty.is_empty()
    assert empty.schema == {"date": pl.Date, "equity": pl.Float64}
    assert equal_weight_buyhold(pl.DataFrame(), initial_cash=50_000.0).schema == {"date": pl.Date, "equity": pl.Float64}


def test_buyhold_rejects_nonempty_input_without_close() -> None:
    """Only a truly empty frame is an empty benchmark; a missing ``close`` raises.

    Non-vacuity: the pre-fix helper returned a successful *empty* curve on this
    exact non-empty frame, so the ``pytest.raises`` below cannot pass on it.
    """
    frame = pl.DataFrame({"ticker": ["A"], "date": [date(2024, 1, 1)]})
    assert not frame.is_empty()
    with pytest.raises(ValueError) as excinfo:
        equal_weight_buyhold(frame, initial_cash=1_000.0)
    message = str(excinfo.value)
    assert "'close'" in message  # names the missing column
    assert "present columns: ['date', 'ticker']" in message  # and what it did get
    assert "never an empty benchmark" in message


def test_buyhold_rejects_instrument_that_ends_early() -> None:
    """An instrument whose marks stop before the cohort's last date raises."""
    frame = _ticker_frame(
        [
            ("AAA", _D1, 100.0),
            ("BBB", _D1, 100.0),
            ("AAA", _D2, 110.0),
            ("BBB", _D2, 105.0),
            ("AAA", _D3, 120.0),  # BBB has no trailing mark on D3
        ]
    )
    with pytest.raises(ValueError) as excinfo:
        equal_weight_buyhold(frame, initial_cash=100.0)
    message = str(excinfo.value)
    assert "Missing price mark" in message
    assert "BBB" in message
    assert "2024-01-04" in message


def test_buyhold_rejects_date_present_for_only_one_instrument() -> None:
    """A date in the union priced for one instrument only raises (never dropped)."""
    frame = _ticker_frame(
        [
            ("AAA", _D1, 100.0),
            ("BBB", _D1, 100.0),
            ("AAA", _D2, 110.0),
            ("BBB", _D2, 105.0),
            ("AAA", _D3, 120.0),  # D3 exists for AAA only; BBB resumes on D4
            ("AAA", _D4, 125.0),
            ("BBB", _D4, 99.0),
        ]
    )
    with pytest.raises(ValueError) as excinfo:
        equal_weight_buyhold(frame, initial_cash=100.0)
    message = str(excinfo.value)
    assert "Missing price mark" in message
    assert "BBB" in message
    assert "2024-01-04" in message
    assert "2024-01-05" not in message  # the hole is D3, not BBB's later mark


def test_buyhold_validates_only_identities_in_its_own_frame() -> None:
    """The standalone helper cannot know about instruments absent from its frame."""
    only_aaa = _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, 200.0)])
    assert _equities(only_aaa, 100.0) == [100.0, 200.0]


_VALID_CASH_FRAME = _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, 100.0)])
_CASH_FRAGMENT = "initial_cash must be a positive finite number"


@pytest.mark.parametrize(
    ("frame", "initial_cash", "expected_fragments"),
    [
        pytest.param(
            _ticker_frame(
                [
                    ("AAA", _D1, 100.0),
                    ("AAA", _D1, 100.0),
                    ("BBB", _D1, 100.0),
                    ("AAA", _D2, 110.0),
                    ("BBB", _D2, 110.0),
                ]
            ),
            100.0,
            ["Duplicate identity/date key", "AAA", "2024-01-02"],
            id="duplicate-ticker-date",
        ),
        pytest.param(
            _venue_frame(
                [
                    ("us_equity", "XNYS", "AAA", _D1, 100.0),
                    ("us_equity", "XNYS", "AAA", _D1, 100.0),
                    ("us_equity", "XNAS", "AAA", _D1, 100.0),
                ]
            ),
            100.0,
            ["Duplicate identity/date key", "us_equity/XNYS/AAA"],
            id="duplicate-canonical-date",
        ),
        pytest.param(
            pl.DataFrame(
                [{"market": "us_equity", "ticker": "AAA", "date": _D1, "close": 100.0}],
                schema={"market": pl.String, "ticker": pl.String, "date": pl.Date, "close": pl.Float64},
            ),
            100.0,
            ["partial canonical instrument identity", "present ['market']", "missing ['mic', 'symbol']"],
            id="partial-identity-columns",
        ),
        pytest.param(
            pl.DataFrame(
                [{"market": "us_equity", "mic": None, "symbol": "AAA", "date": _D1, "close": 100.0}],
                schema={"market": pl.String, "mic": pl.String, "symbol": pl.String, "date": pl.Date, "close": pl.Float64},
            ),
            100.0,
            ["partial canonical instrument identity", "mic=None"],
            id="partial-identity-values",
        ),
        pytest.param(
            _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, 100.0), ("BBB", _D2, 100.0)]),
            100.0,
            ["BBB", "no mark at benchmark inception", "2024-01-02"],
            id="missing-inception-mark",
        ),
        pytest.param(
            _ticker_frame([("AAA", _D1, 100.0), ("BBB", _D1, 100.0), ("AAA", _D2, 100.0), ("AAA", _D3, 100.0), ("BBB", _D3, 100.0)]),
            100.0,
            ["Missing price mark", "BBB", "2024-01-03"],
            id="missing-interior-mark",
        ),
        pytest.param(
            _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, 0.0)]),
            100.0,
            ["Invalid buy-and-hold price mark", "AAA", "non-positive"],
            id="zero-mark",
        ),
        pytest.param(
            _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, -5.0)]),
            100.0,
            ["Invalid buy-and-hold price mark", "non-positive"],
            id="negative-mark",
        ),
        pytest.param(
            _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, float("nan"))]),
            100.0,
            ["Invalid buy-and-hold price mark", "non-finite"],
            id="nan-mark",
        ),
        pytest.param(
            _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, float("inf"))]),
            100.0,
            ["Invalid buy-and-hold price mark", "non-finite"],
            id="infinite-mark",
        ),
        pytest.param(
            pl.DataFrame(
                [{"ticker": "AAA", "date": _D1, "close": None}],
                schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64},
            ),
            100.0,
            ["Invalid buy-and-hold price mark", "(missing)"],
            id="null-mark",
        ),
        pytest.param(
            pl.DataFrame(
                [{"ticker": "AAA", "date": None, "close": 100.0}],
                schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64},
            ),
            100.0,
            ["null date"],
            id="null-date",
        ),
        pytest.param(
            _ticker_frame([("", _D1, 100.0)]),
            100.0,
            ["missing or blank ticker"],
            id="blank-ticker",
        ),
        pytest.param(_VALID_CASH_FRAME, 0.0, [_CASH_FRAGMENT], id="zero-cash"),
        pytest.param(_VALID_CASH_FRAME, -100.0, [_CASH_FRAGMENT], id="negative-cash"),
        pytest.param(_VALID_CASH_FRAME, float("nan"), [_CASH_FRAGMENT], id="nan-cash"),
        pytest.param(_VALID_CASH_FRAME, float("inf"), [_CASH_FRAGMENT], id="inf-cash"),
    ],
)
def test_buyhold_rejects_invalid_input_visibly(frame: pl.DataFrame, initial_cash: float, expected_fragments: list[str]) -> None:
    with pytest.raises(ValueError) as excinfo:
        equal_weight_buyhold(frame, initial_cash=initial_cash)
    message = str(excinfo.value)
    for fragment in expected_fragments:
        assert fragment in message, f"missing {fragment!r} in {message!r}"


def test_run_arena_rejects_absent_requested_instrument() -> None:
    """run_arena validates the requested cohort, not just the identities it loaded."""
    data = _synthetic_data(n_tickers=2, n_days=10)
    start_date, end_date = cast(date, data["date"].min()), cast(date, data["date"].max())
    with pytest.raises(ValueError) as excinfo:
        run_arena(["T00", "ABSENT"], start_date, end_date, preloaded_data=data, strategies=["momentum"], cost_regimes=["zero"])
    message = str(excinfo.value)
    assert "Arena cohort mismatch" in message
    assert "ABSENT" in message
    assert "us_equity/XNYS/T00" in message  # loaded identities are listed for the remedy


def test_run_arena_rejects_incomplete_benchmark_panel() -> None:
    """The conservative complete-panel policy fails before any engine run."""
    data = _ticker_frame(
        [
            ("AAA", _D1, 100.0),
            ("BBB", _D1, 100.0),
            ("AAA", _D2, 110.0),
            ("AAA", _D3, 120.0),
            ("BBB", _D3, 90.0),
        ]
    )
    with pytest.raises(ValueError) as excinfo:
        run_arena(["AAA", "BBB"], _D1, _D3, preloaded_data=data, strategies=["momentum"], cost_regimes=["zero"])
    assert "Missing price mark" in str(excinfo.value)


def test_run_arena_rejects_nonempty_load_without_close() -> None:
    """A non-empty load with no ``close`` marks raises; it is never an empty benchmark."""
    data = pl.DataFrame({"ticker": ["AAA"], "date": [_D1]}, schema={"ticker": pl.String, "date": pl.Date})
    with pytest.raises(ValueError) as excinfo:
        run_arena(["AAA"], _D1, _D1, preloaded_data=data, strategies=["momentum"], cost_regimes=["zero"])
    message = str(excinfo.value)
    assert "'close'" in message
    assert "present columns: ['date', 'ticker']" in message


def _curve(dates: list[date], equities: list[float]) -> pl.DataFrame:
    """A ``[date, equity]`` benchmark-curve fixture."""
    return pl.DataFrame({"date": dates, "equity": equities}, schema={"date": pl.Date, "equity": pl.Float64})


_WRONG_BENCHMARK_CURVES = [
    pytest.param(_curve([], []), ["Arena benchmark is empty or incomplete"], id="empty-curve"),
    pytest.param(_curve([_D1, _D1], [100.0, 110.0]), ["repeats date 2024-01-02"], id="duplicate-date"),
    pytest.param(
        _curve([date(1999, 1, 1), date(1999, 1, 2)], [100.0, 110.0]),
        ["not in the load's date union", "1999-01-01"],
        id="shifted-dates",
    ),
    pytest.param(_curve([_D2, _D1], [110.0, 100.0]), ["out of ascending order", "2024-01-02"], id="unsorted-dates"),
    pytest.param(_curve([_D1], [100.0]), ["Arena benchmark is empty or incomplete", "missing load date 2024-01-03"], id="missing-union-date"),
    pytest.param(_curve([_D1, _D2], [100.0, float("nan")]), ["null or non-finite equity"], id="non-finite-equity"),
    pytest.param(
        _curve([_D1, _D2], [100.0, 110.0]).with_columns(pl.lit("extra").alias("note")),
        ["instead of exactly ['date', 'equity']"],
        id="extra-column",
    ),
]


@pytest.mark.parametrize(("curve", "expected_fragments"), _WRONG_BENCHMARK_CURVES)
def test_run_arena_refuses_outcome_with_invalid_benchmark_curve(
    monkeypatch: pytest.MonkeyPatch,
    curve: pl.DataFrame,
    expected_fragments: list[str],
) -> None:
    """Backstop: a finite-but-wrong curve never becomes arena evidence.

    Each case is rejected by the strengthened post-condition guard. Three of
    them (duplicate-date, shifted-dates, unsorted-dates) would have passed the
    previous point-count/finite-only check; the rest pin the empty-curve, the
    missing-union-date, the non-finite-equity and the shape contracts that the
    current guard also owns.
    """
    data = _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, 110.0)])
    monkeypatch.setattr("equity_lake.backtesting.arena.equal_weight_buyhold", lambda *args, **kwargs: curve)
    with pytest.raises(ValueError) as excinfo:
        run_arena(["AAA"], _D1, _D2, preloaded_data=data, strategies=["momentum"], cost_regimes=["zero"])
    message = str(excinfo.value)
    assert "inconsistent" in message  # stated as an internal post-condition, not input error
    for fragment in expected_fragments:
        assert fragment in message, f"missing {fragment!r} in {message!r}"


def test_require_usable_benchmark_accepts_single_instrument_and_panel_curves() -> None:
    """The strengthened guard must not false-positive on legitimate curves."""
    single = _ticker_frame([("AAA", _D1, 100.0), ("AAA", _D2, 110.0)])
    panel = _ticker_frame(
        [
            ("AAA", _D1, 100.0),
            ("BBB", _D1, 50.0),
            ("AAA", _D2, 110.0),
            ("BBB", _D2, 55.0),
            ("AAA", _D3, 120.0),
            ("BBB", _D3, 60.0),
        ]
    )
    for frame in (single, panel):
        curve = equal_weight_buyhold(frame, initial_cash=100.0)
        _require_usable_benchmark(curve, frame)  # must not raise
        assert curve["date"].to_list() == frame["date"].unique().sort().to_list()
        assert curve.height == frame["date"].n_unique()


def _stub_result(sharpe: float) -> BacktestResult:
    return BacktestResult(
        strategy_name="example",
        tickers=["AAA", "BBB"],
        start_date=_D1,
        end_date=_D3,
        initial_cash=100.0,
        final_cash=100.0,
        equity_curve=pl.Series("equity", [100.0, 100.0, 100.0]),
        trades=[],
        metrics={"sharpe_ratio": sharpe, "total_return": 0.0},
    )


def test_arena_cards_and_artifact_disclose_fixed_share_benchmark(tmp_path: Path) -> None:
    """Cards state the benchmark policy and the artifact is the corrected curve."""
    frame = _ticker_frame(
        [
            ("AAA", _D1, 100.0),
            ("BBB", _D1, 100.0),
            ("AAA", _D2, 200.0),
            ("BBB", _D2, 100.0),
            ("AAA", _D3, 100.0),
            ("BBB", _D3, 100.0),
        ]
    )
    benchmark = equal_weight_buyhold(frame, initial_cash=100.0)
    outcome = ArenaOutcome(
        runs=[ArenaRun("example", "zero", _stub_result(-1.0)), ArenaRun("example", "realistic", _stub_result(-2.0))],
        data=frame,
        benchmark=benchmark,
        initial_cash=100.0,
    )
    cards = write_arena_artifacts(outcome, base=tmp_path, run_date=_D3)
    assert cards
    for card in cards:
        assert card.scope["benchmark_policy"] == BENCHMARK_POLICY
        assert card.scope["benchmark_missing_mark_policy"] == BENCHMARK_MISSING_MARK_POLICY
        assert "benchmark_asymmetry" in card.scope
    attempt = latest_attempt(tmp_path)
    assert attempt is not None
    written = pl.read_parquet(attempt.directory / "benchmark__equity.parquet")
    assert written.equals(benchmark)  # the arena's curve, not a report-side reconstruction
    assert written["equity"].to_list() == [100.0, 150.0, 100.0]
    assert not (tmp_path / "benchmark__equity.parquet").exists()  # no flat legacy benchmark


@requires_engine
def test_run_arena_benchmark_reaches_cards_and_artifact(tmp_path: Path) -> None:
    """End-to-end: ``run_arena``'s corrected curve is what cards and artifacts carry.

    F04: the realistic-cost runs must be ``evaluated`` to be admitted as card
    evidence, so the fixture uses a window the strategies actually trade in.
    """
    data = _synthetic_data(n_tickers=6, n_days=320, seed=3)
    start_date, end_date = cast(date, data["date"].min()), cast(date, data["date"].max())
    tickers = [f"T{i:02d}" for i in range(6)]
    outcome = run_arena(
        tickers,
        start_date,
        end_date,
        preloaded_data=data,
        strategies=["mean_reversion", "trend_following"],
        cost_regimes=["realistic"],
    )
    assert {run.evaluation_status for run in outcome.runs} == {"evaluated"}

    expected = equal_weight_buyhold(data, initial_cash=100_000.0)
    assert not outcome.benchmark.is_empty()  # no silent empty benchmark
    assert outcome.benchmark.equals(expected)  # exactly the directly computed fixed-share curve

    cards = write_arena_artifacts(outcome, base=tmp_path, run_date=date(2026, 8, 4))
    assert cards
    attempt = latest_attempt(tmp_path)
    assert attempt is not None
    written = pl.read_parquet(attempt.directory / "benchmark__equity.parquet")
    assert written.equals(expected)
    assert not (tmp_path / "benchmark__equity.parquet").exists()  # no flat legacy benchmark
    for card in cards:
        assert card.scope["benchmark_policy"] == BENCHMARK_POLICY
        assert card.scope["benchmark_missing_mark_policy"] == BENCHMARK_MISSING_MARK_POLICY


def test_caller_scope_cannot_override_mandatory_disclosures() -> None:
    """Caller scope adds keys but can never rewrite the benchmark/Sharpe disclosures."""
    frame = _ticker_frame(
        [
            ("AAA", _D1, 100.0),
            ("BBB", _D1, 100.0),
            ("AAA", _D2, 200.0),
            ("BBB", _D2, 100.0),
            ("AAA", _D3, 100.0),
            ("BBB", _D3, 100.0),
        ]
    )
    outcome = ArenaOutcome(
        runs=[ArenaRun("example", "zero", _stub_result(2.0)), ArenaRun("example", "realistic", _stub_result(1.0))],
        data=frame,
        benchmark=equal_weight_buyhold(frame, initial_cash=100.0),
        initial_cash=100.0,
    )
    cards = build_finding_cards(
        outcome,
        run_date=_D3,
        scope={
            "benchmark_policy": "caller rewrote the benchmark policy",
            "benchmark_missing_mark_policy": "caller rewrote the missing-mark policy",
            "benchmark_asymmetry": "no asymmetry",
            "sharpe_risk_free_rate": 0.0,
            "synthetic": True,
            "universe": "unit-test",
        },
    )
    assert cards
    for card in cards:
        assert card.scope["benchmark_policy"] == BENCHMARK_POLICY
        assert card.scope["benchmark_missing_mark_policy"] == BENCHMARK_MISSING_MARK_POLICY
        assert "zero costs and zero lag" in card.scope["benchmark_asymmetry"]
        assert card.scope["sharpe_risk_free_rate"] == 0.02
        # caller keys still land, and ticker scope keeps its computed value
        assert card.scope["synthetic"] is True
        assert card.scope["universe"] == "unit-test"
        assert card.scope["tickers"] == 2
