"""F03 regressions: the ``cost-regime`` FindingCard must be truthful (audit A03).

Pure-report module: cards are built from a plain :class:`BacktestResult` +
:class:`ArenaRun`/:class:`ArenaOutcome` (no engine, no network, no marker), so the
whole file stays in the default fast suite.

Old behaviour pinned by these tests (``report.py`` before F03)::

    drop = (zero_s - real_s) / zero_s if zero_s != 0 else 0.0
    verdict = "negative" if drop > 0.3 else ("positive" if drop < 0.05 else "inconclusive")
    conclusion = "... ({drop:.0%} drop)." if drop > 0.05 else "...; turnover is low."

so (zero=-1.0, realistic=-2.0) reported ``positive`` with "turnover is low", a
0.0 zero-cost Sharpe produced ``inconclusive`` plus the false low-turnover claim,
and a missing metric was read as the convenience property's ``0``.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import polars as pl

from equity_lake.backtesting.arena import (
    BENCHMARK_MISSING_MARK_POLICY,
    BENCHMARK_POLICY,
    ArenaOutcome,
    ArenaRun,
    equal_weight_buyhold,
)
from equity_lake.backtesting.report import _COST_DELTA_TOLERANCE, _MANDATORY_SCOPE_KEYS, build_finding_cards
from equity_lake.backtesting.result import BacktestResult
from equity_lake.findings import FindingCard, load_finding_cards, write_finding_card

_D1, _D2, _D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)

# Every scenario's conclusion must stay free of inferred turnover / invented loss.
_FORBIDDEN_TEXT = ("turnover", "percent", "%", "statistically significant")

SCENARIOS: list[tuple[float | None, float | None]] = [
    (-1.0, -2.0),
    (1.0, -0.5),
    (0.0, -0.5),
    (0.0, 0.0),
    (2.0, 1.0),
    (0.5, 1.0),
    (None, -0.5),
    (float("nan"), -0.5),
]


def _ticker_frame(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    """Ticker-only fixture: ``[(ticker, date, close), ...]`` with no identity columns."""
    return pl.DataFrame(
        [{"ticker": ticker, "date": day, "close": close} for ticker, day, close in rows],
        schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64},
    )


def _result(sharpe: float | None) -> BacktestResult:
    """A stub result; ``sharpe=None`` omits ``sharpe_ratio`` from ``metrics`` entirely."""
    metrics: dict[str, float] = {"total_return": 0.0}
    if sharpe is not None:
        metrics["sharpe_ratio"] = sharpe
    return BacktestResult(
        strategy_name="example",
        tickers=["AAA", "BBB"],
        start_date=_D1,
        end_date=_D3,
        initial_cash=100.0,
        final_cash=100.0,
        equity_curve=pl.Series("equity", [100.0, 100.0, 100.0]),
        trades=[],
        metrics=metrics,
    )


def _outcome_runs(spec: list[tuple[str, float | None]]) -> ArenaOutcome:
    """Outcome from an explicit ``[(regime, sharpe), ...]`` list (duplicates allowed)."""
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
    return ArenaOutcome(
        runs=[ArenaRun("example", regime, _result(sharpe)) for regime, sharpe in spec],
        data=frame,
        benchmark=equal_weight_buyhold(frame, initial_cash=100.0),
        initial_cash=100.0,
    )


def _outcome(zero_sharpe: float | None, realistic_sharpe: float | None, *, high_sharpe: float | None = None) -> ArenaOutcome:
    spec: list[tuple[str, float | None]] = [("zero", zero_sharpe), ("realistic", realistic_sharpe)]
    if high_sharpe is not None:
        spec.append(("high", high_sharpe))
    return _outcome_runs(spec)


def _cost_card(outcome: ArenaOutcome) -> FindingCard:
    cards = [c for c in build_finding_cards(outcome, run_date=_D3) if c.id == "cost-regime"]
    assert len(cards) == 1, f"expected exactly one cost-regime card, got {[c.id for c in cards]}"
    return cards[0]


def _assert_no_non_finite(metrics: dict[str, float]) -> None:
    assert all(math.isfinite(value) for value in metrics.values()), metrics


def _assert_clean_text(conclusion: str) -> None:
    lowered = conclusion.lower()
    for token in _FORBIDDEN_TEXT:
        assert token not in lowered, f"{token!r} must not appear in {conclusion!r}"


def test_cost_card_negative_sharpe_deterioration_is_negative() -> None:
    """Old formula: drop = (-1 - -2) / -1 = -1.0 -> 'positive' + 'turnover is low'."""
    card = _cost_card(_outcome(-1.0, -2.0))
    assert card.verdict == "negative"
    assert card.metrics["cost_regime.sharpe_delta"] == -1.0
    assert card.metrics["zero.sharpe"] == -1.0
    assert card.metrics["realistic.sharpe"] == -2.0
    assert "-1.00" in card.conclusion
    assert "positive" not in card.conclusion.lower()
    _assert_no_non_finite(card.metrics)
    _assert_clean_text(card.conclusion)


def test_cost_card_positive_to_negative_sharpe() -> None:
    """Old formula: drop = (1 - -0.5)/1 = 1.5 -> 'negative' by luck, but text shows a 150% drop."""
    card = _cost_card(_outcome(1.0, -0.5))
    assert card.verdict == "negative"
    assert card.metrics["cost_regime.sharpe_delta"] == -1.5
    assert "-1.50" in card.conclusion
    _assert_no_non_finite(card.metrics)
    _assert_clean_text(card.conclusion)


def test_cost_card_zero_to_negative() -> None:
    """Old formula: zero denominator -> drop = 0.0 -> 'inconclusive' + false low-turnover claim."""
    card = _cost_card(_outcome(0.0, -0.5))
    assert card.verdict == "negative"
    assert card.metrics["cost_regime.sharpe_delta"] == -0.5
    assert card.metrics["zero.sharpe"] == 0.0
    assert card.metrics["realistic.sharpe"] == -0.5
    _assert_no_non_finite(card.metrics)
    _assert_clean_text(card.conclusion)


def test_cost_card_unchanged_zero() -> None:
    """Old formula: drop = 0.0 -> 'inconclusive' with the invented low-turnover sentence."""
    card = _cost_card(_outcome(0.0, 0.0))
    assert card.verdict == "inconclusive"
    assert card.metrics["cost_regime.sharpe_delta"] == 0.0
    assert "no measured cost effect" in card.conclusion
    _assert_no_non_finite(card.metrics)
    _assert_clean_text(card.conclusion)


def test_cost_card_positive_deterioration() -> None:
    """Old formula: drop = (2-1)/2 = 0.5 -> 'negative', 50% drop (no denominator guard)."""
    card = _cost_card(_outcome(2.0, 1.0))
    assert card.verdict == "negative"
    assert card.metrics["cost_regime.sharpe_delta"] == -1.0
    assert "-1.00" in card.conclusion
    _assert_no_non_finite(card.metrics)
    _assert_clean_text(card.conclusion)


def test_cost_card_observed_improvement() -> None:
    """Old formula: drop = (0.5-1.0)/0.5 = -1.0 -> 'positive' with a -100% "drop" and no caveat."""
    card = _cost_card(_outcome(0.5, 1.0))
    assert card.verdict == "positive"
    assert card.metrics["cost_regime.sharpe_delta"] == 0.5
    lowered = card.conclusion.lower()
    assert "observed difference" in lowered
    assert "not evidence" in lowered
    assert "causal" in lowered
    _assert_no_non_finite(card.metrics)
    _assert_clean_text(card.conclusion)


def test_cost_card_missing_metric_is_inconclusive() -> None:
    """A missing key is unknown, not ``BacktestResult.sharpe_ratio``'s silent 0."""
    zero_missing = _cost_card(_outcome(None, -0.5))
    assert zero_missing.verdict == "inconclusive"
    assert "zero.sharpe" not in zero_missing.metrics
    assert zero_missing.metrics["realistic.sharpe"] == -0.5
    assert "cost_regime.sharpe_delta" not in zero_missing.metrics
    assert "zero-cost" in zero_missing.conclusion
    assert "cannot be measured" in zero_missing.conclusion
    _assert_no_non_finite(zero_missing.metrics)

    realistic_missing = _cost_card(_outcome(1.0, None))
    assert realistic_missing.verdict == "inconclusive"
    assert "realistic.sharpe" not in realistic_missing.metrics
    assert realistic_missing.metrics["zero.sharpe"] == 1.0
    assert "cost_regime.sharpe_delta" not in realistic_missing.metrics
    assert "realistic-cost" in realistic_missing.conclusion
    _assert_no_non_finite(realistic_missing.metrics)


def test_cost_card_non_finite_metric_is_inconclusive(tmp_path: Path) -> None:
    """NaN/inf is not evidence: omit the field, explain, and never persist it."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        card = _cost_card(_outcome(bad, -0.5))
        assert card.verdict == "inconclusive", bad
        assert "zero.sharpe" not in card.metrics, bad
        assert "cost_regime.sharpe_delta" not in card.metrics, bad
        assert card.metrics["realistic.sharpe"] == -0.5
        assert "no finite sharpe_ratio" in card.conclusion
        _assert_no_non_finite(card.metrics)
        payload = write_finding_card(card, base=tmp_path).read_text(encoding="utf-8")
        assert "NaN" not in payload and "Infinity" not in payload, payload


def test_cost_card_does_not_infer_turnover() -> None:
    """Pins the text: no turnover claim and no invented percent loss anywhere."""
    for zero_sharpe, real_sharpe in SCENARIOS:
        card = _cost_card(_outcome(zero_sharpe, real_sharpe))
        _assert_clean_text(card.conclusion)
        _assert_no_non_finite(card.metrics)
        # absolute signed deltas are the only quantitative change stated
        if "cost_regime.sharpe_delta" in card.metrics:
            delta = card.metrics["cost_regime.sharpe_delta"]
            assert f"{delta:+.2f}" in card.conclusion, (delta, card.conclusion)


def test_cost_card_preserves_regime_metrics_and_delta() -> None:
    """Regime keys keep the ``<regime>.sharpe`` shape; delta is signed; scope is intact."""
    card = _cost_card(_outcome(1.5, 0.75, high_sharpe=0.5))
    assert card.id == "cost-regime"
    assert card.axis == "cost"
    assert card.metrics["zero.sharpe"] == 1.5
    assert card.metrics["realistic.sharpe"] == 0.75
    assert card.metrics["high.sharpe"] == 0.5
    assert card.metrics["cost_regime.sharpe_delta"] == -0.75
    assert card.evidence_refs == ["example__zero/", "example__realistic/"]
    assert card.scope["strategy"] == "example"
    # F01 caller-scope protection is untouched by F03
    assert card.scope["benchmark_policy"] == BENCHMARK_POLICY
    assert card.scope["benchmark_missing_mark_policy"] == BENCHMARK_MISSING_MARK_POLICY
    assert "zero costs and zero lag" in card.scope["benchmark_asymmetry"]
    assert card.scope["sharpe_risk_free_rate"] == 0.02
    for key in _MANDATORY_SCOPE_KEYS:
        assert key in card.scope
    _assert_no_non_finite(card.metrics)


def test_cost_card_needs_both_regimes_but_emits_inconclusive_when_computed() -> None:
    """Only runs that exist produce a card; an inconclusive computed card is still emitted."""
    frame = _ticker_frame([("AAA", _D1, 100.0), ("BBB", _D1, 100.0), ("AAA", _D2, 200.0), ("BBB", _D2, 100.0)])
    only_zero = ArenaOutcome(
        runs=[ArenaRun("example", "zero", _result(1.0))],
        data=frame,
        benchmark=equal_weight_buyhold(frame, initial_cash=100.0),
        initial_cash=100.0,
    )
    assert [c.id for c in build_finding_cards(only_zero, run_date=_D3)] == []
    both_unknown = _cost_card(_outcome(None, None))
    assert both_unknown.verdict == "inconclusive"
    assert both_unknown.metrics == {}
    assert "cannot be measured" in both_unknown.conclusion


# --- F03 fix round 1: overflow, duplicate runs, and boundary pins -----------------

_OVERFLOW_PAIRS: list[tuple[float, float]] = [(-1.7e308, 1.7e308), (1.7e308, -1.7e308)]


def test_cost_card_overflowing_delta_is_inconclusive(tmp_path: Path) -> None:
    """Finite regime Sharpes can still overflow the delta: never emit inf/NaN/null.

    ``real_s - zero_s`` on (+-1.7e308) yields ``inf``; emitting it made the conclusion
    read "+inf Sharpe points", made ``write_finding_card`` serialize ``null`` and made
    ``load_finding_cards`` reject the card (metrics must be floats).
    """
    for index, (zero_sharpe, real_sharpe) in enumerate(_OVERFLOW_PAIRS):
        card = _cost_card(_outcome(zero_sharpe, real_sharpe))
        assert card.verdict == "inconclusive", (zero_sharpe, real_sharpe, card.conclusion)
        # the delta is unusable, so neither the key nor its value is emitted
        assert "cost_regime.sharpe_delta" not in card.metrics, card.metrics
        # the regime values themselves are finite measurements and stay as evidence
        assert card.metrics["zero.sharpe"] == zero_sharpe
        assert card.metrics["realistic.sharpe"] == real_sharpe
        assert "could not be measured finitely" in card.conclusion
        assert "no delta is reported" in card.conclusion
        assert "inf" not in card.conclusion.lower().replace("inconclusive", "")
        _assert_no_non_finite(card.metrics)
        _assert_clean_text(card.conclusion)

        base = tmp_path / f"overflow-{index}"
        payload = write_finding_card(card, base=base).read_text(encoding="utf-8")
        for token in ("Infinity", "infinity", "NaN", "null"):
            assert token not in payload, (token, payload)
        # every emitted metric is a JSON number, never a null
        loaded_card = json.loads(payload)
        assert all(isinstance(value, float) for value in loaded_card["metrics"].values()), loaded_card["metrics"]
        # the written card is readable again (the null-metric bug broke this)
        reloaded = [c for c in load_finding_cards(base) if c.id == "cost-regime"]
        assert len(reloaded) == 1, reloaded
        assert reloaded[0].verdict == "inconclusive"
        assert reloaded[0].metrics == card.metrics


def test_cost_card_duplicate_zero_run_is_inconclusive_and_order_independent() -> None:
    """A duplicated regime must not make the measurement depend on run list order."""
    forward = _cost_card(_outcome_runs([("zero", 1.0), ("zero", -2.0), ("realistic", 0.5)]))
    reverse = _cost_card(_outcome_runs([("realistic", 0.5), ("zero", -2.0), ("zero", 1.0)]))
    assert forward.model_dump() == reverse.model_dump(), (forward.model_dump(), reverse.model_dump())
    assert forward.verdict == "inconclusive"
    # the ambiguous regime's key and the delta are omitted; the unique regime is kept
    assert "zero.sharpe" not in forward.metrics
    assert "cost_regime.sharpe_delta" not in forward.metrics
    assert forward.metrics["realistic.sharpe"] == 0.5
    assert "zero (2 runs)" in forward.conclusion
    assert "list order" in forward.conclusion
    _assert_no_non_finite(forward.metrics)
    _assert_clean_text(forward.conclusion)


def test_cost_card_duplicate_regime_is_named_in_explanation() -> None:
    """The inconclusive explanation names every duplicated regime of the strategy."""
    for duplicated_regime in ("zero", "realistic"):
        card = _cost_card(_outcome_runs([("zero", 1.0), ("realistic", 0.5), (duplicated_regime, 9.0)]))
        assert card.verdict == "inconclusive", duplicated_regime
        assert f"{duplicated_regime} (2 runs)" in card.conclusion, card.conclusion
        assert f"{duplicated_regime}.sharpe" not in card.metrics
        assert "cost_regime.sharpe_delta" not in card.metrics
        other = "realistic" if duplicated_regime == "zero" else "zero"
        assert f"{other}.sharpe" in card.metrics
        _assert_no_non_finite(card.metrics)
        _assert_clean_text(card.conclusion)


def test_cost_card_duplicate_non_compared_regime_also_blocks_the_comparison() -> None:
    """Policy: *any* duplicated regime of the representative strategy makes the card inconclusive.

    The card's regime-metric set is ambiguous as a whole, so no comparison is claimed
    even though ``zero``/``realistic`` are unique here.
    """
    card = _cost_card(_outcome_runs([("zero", 1.0), ("realistic", 0.5), ("high", 0.1), ("high", 0.2)]))
    assert card.verdict == "inconclusive"
    assert "high (2 runs)" in card.conclusion
    assert card.metrics == {"zero.sharpe": 1.0, "realistic.sharpe": 0.5}
    _assert_clean_text(card.conclusion)


def test_cost_card_tolerance_boundary_classification() -> None:
    """Pin the ``abs(delta) <= 1e-12`` boundary on the measured float difference."""
    assert _COST_DELTA_TOLERANCE == 1e-12
    # At Sharpe 1.0 the float spacing is ~2.2e-16, so ``1.0 + 1e-12`` rounds to a value
    # whose distance from 1.0 is 1.0000889e-12 (just outside) while ``1.0 - 1e-12``
    # lands at 9.99978e-13 (inside). Classification uses the measured float delta.
    cases = [
        (1.0, 1.0 - 1e-12, "inconclusive"),
        (1.0, 1.0 - 1e-13, "inconclusive"),
        (1.0, 1.0 + 1e-12, "positive"),
        (1.0, 1.0 + 1e-13, "inconclusive"),
    ]
    for zero_sharpe, real_sharpe, expected in cases:
        delta = real_sharpe - zero_sharpe
        card = _cost_card(_outcome(zero_sharpe, real_sharpe))
        assert card.verdict == expected, (zero_sharpe, real_sharpe, delta, card.conclusion)
        assert card.metrics["cost_regime.sharpe_delta"] == delta
        if expected == "inconclusive":
            assert abs(delta) <= _COST_DELTA_TOLERANCE, (delta, card.conclusion)
        else:
            assert abs(delta) > _COST_DELTA_TOLERANCE, (delta, card.conclusion)


def test_cost_card_exact_tolerance_boundary_is_inconclusive() -> None:
    """Exactly +-1e-12 is inside the inclusive tolerance; +-2e-12 is outside it."""
    for zero_sharpe, real_sharpe, expected in [
        (0.0, 1e-12, "inconclusive"),
        (0.0, -1e-12, "inconclusive"),
        (0.0, 2e-12, "positive"),
        (0.0, -2e-12, "negative"),
    ]:
        card = _cost_card(_outcome(zero_sharpe, real_sharpe))
        assert card.metrics["cost_regime.sharpe_delta"] == real_sharpe - zero_sharpe
        assert card.verdict == expected, (zero_sharpe, real_sharpe, card.conclusion)


def test_cost_card_both_sides_missing_metric_omits_every_numeric_key() -> None:
    """Both regimes unknown: inconclusive, no numeric key at all, both sides named."""
    for zero_sharpe, real_sharpe in [
        (None, None),
        (float("nan"), float("nan")),
        (float("inf"), float("-inf")),
    ]:
        card = _cost_card(_outcome(zero_sharpe, real_sharpe))
        assert card.verdict == "inconclusive"
        assert card.metrics == {}
        assert "zero-cost" in card.conclusion and "realistic-cost" in card.conclusion
        assert "cannot be measured" in card.conclusion
        _assert_clean_text(card.conclusion)


def test_cost_card_single_regime_emits_no_card() -> None:
    """Pins today's behaviour: the cost card needs both ``zero`` and ``realistic``.

    A later change must not silently fabricate a comparison from one regime (e.g. by
    treating ``high`` or a missing run as the counterpart).
    """
    cases: list[list[tuple[str, float | None]]] = [
        [("zero", 1.0)],
        [("realistic", 1.0)],
        [("zero", 1.0), ("high", 0.5)],
        [("realistic", 1.0), ("high", 0.5)],
    ]
    for spec in cases:
        outcome = _outcome_runs(spec)
        assert [c.id for c in build_finding_cards(outcome, run_date=_D3) if c.id == "cost-regime"] == [], spec


def test_cost_card_high_regime_key_is_preserved_but_never_fabricated() -> None:
    """``high`` is carried as evidence when measured; a missing high value is omitted."""
    measured = _cost_card(_outcome(1.5, 0.75, high_sharpe=0.25))
    assert measured.verdict == "negative"
    assert measured.metrics["high.sharpe"] == 0.25
    assert measured.metrics["cost_regime.sharpe_delta"] == -0.75
    unknown = _cost_card(_outcome(1.5, 0.75, high_sharpe=float("nan")))
    assert "high.sharpe" not in unknown.metrics
    assert unknown.metrics["cost_regime.sharpe_delta"] == -0.75
    assert unknown.verdict == "negative"
    _assert_no_non_finite(measured.metrics)
    _assert_no_non_finite(unknown.metrics)
