"""RS-R01: the six leakage controls, the freeze/refusal gates and the preregistered statistic.

Every leakage control in residual plan §5 has one named regression here with a fixed fixture
and an expected result. ``uv run python -m equity_lake.ml.r01_controls --mode mutations``
removes one rule at a time from the source and requires the *named* test below to fail, so a
control that cannot notice its own invariant is visible as a surviving mutant.

Nothing in this file fits a market model, reads the real root, touches ``data/research/r02`` or
writes a lake table: the fixture is synthetic, the run window stops before its own untouchable
holdout, and every statistic names the mean per-session net return rather than the compounded
one.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from equity_lake.backtesting.protocol import ProtocolError, conformance_violations, require_frozen_protocol  # type: ignore[import-untyped]
from equity_lake.ml.r01_controls import (  # type: ignore[import-untyped]
    AGGREGATE_CEILING_SECONDS,
    CONTROL_UNIVERSE,
    FPR_UPPER_BOUND_MAX,
    LEAKAGE_CONTROLS,
    NULL_SEEDS,
    PLANTED_SEEDS,
    POWER_LOWER_BOUND_MIN,
    R01_COST_REGIME,
    R01_EXPERIMENT,
    R01_GENERATOR,
    SCALE_SIZES,
    binomial_bounds,
    control_outcome,
    freeze_r01_protocol,
    leakage_control_append_after_cutoff,
    leakage_control_delayed_publication,
    leakage_control_execution_lag_horizon,
    leakage_control_foreign_identity_proof,
    leakage_control_missing_or_expired_coverage,
    leakage_control_pending_labels,
    r01_protocol,
    r01_spec,
    run_controls,
    run_leakage_controls,
    run_sessions,
)

RECORDED_AT = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The six leakage controls (one named regression each)
# ---------------------------------------------------------------------------


def test_leakage_control_1_append_after_cutoff_is_invariant() -> None:
    """Appending data after the cutoff leaves features, weights, predictions and decisions equal."""
    result = leakage_control_append_after_cutoff()
    assert result.passed, result.observed
    assert result.observed["appended_rows"] > 0
    assert result.observed["features_and_predictions_equal"] is True
    assert result.observed["max_abs_weight_difference"] == 0.0
    assert result.observed["decisions_before"] == result.observed["decisions_after"]


def test_leakage_control_2_delayed_publication_cannot_reach_earlier_outputs() -> None:
    """A delayed observation is unavailable before its observable session and changes nothing earlier."""
    result = leakage_control_delayed_publication()
    assert result.passed, result.observed
    assert result.observed["delayed_row_eligible_before_publication"] is False
    assert result.observed["delayed_row_eligible_after_publication"] is True
    assert result.observed["outputs_equal_to_dropped_row"] is True
    assert result.observed["available_before_observable_session"] is True


def test_leakage_control_3_foreign_identity_proof_is_refused_before_fit(tmp_path: Path) -> None:
    """Another instrument's proof is refused on identity before any fit and before any write."""
    result = leakage_control_foreign_identity_proof()
    assert result.passed, result.observed
    assert result.observed["refused"] is True
    assert result.observed["identity_refusal"] is True
    assert result.observed["fitted_before_refusal"] is False
    assert result.observed["files_written"] == []
    assert not (tmp_path / "r01-guarded-run.json").exists()


def test_leakage_control_4_pending_labels_never_enter_the_sample() -> None:
    """No admitted row's label maturity lies after the decision cutoff."""
    result = leakage_control_pending_labels()
    assert result.passed, result.observed
    assert result.observed["pending_rows"] > 0
    assert result.observed["pending_rows_admitted"] == 0
    assert date.fromisoformat(str(result.observed["admitted_max_label_end"])) <= date.fromisoformat(str(result.observed["cutoff"]))
    assert result.observed["admitted_rows"] == result.observed["hand_computed_admitted"]


def test_leakage_control_5_expired_or_missing_coverage_is_refused() -> None:
    """Strict coverage validation refuses expired or missing coverage and never falls back to raw marks."""
    result = leakage_control_missing_or_expired_coverage()
    assert result.passed, result.observed
    assert result.observed["expired_refused"] is True
    assert result.observed["missing_refused"] is True
    assert result.observed["rows_returned_by_strict_gate"] == 0
    assert result.observed["fallback_to_raw_marks"] is False


def test_leakage_control_6_execution_lag_and_purge_match_hand_math() -> None:
    """A decision enters on the frozen next-session open only, and crossing labels are purged."""
    result = leakage_control_execution_lag_horizon()
    assert result.passed, result.observed
    assert result.observed["entry_session"] == 1
    assert result.observed["entry_price"] == 110.0
    assert result.observed["gross_return"] == pytest.approx(0.1, abs=1e-12)
    assert result.observed["same_bar_entry_price"] == 100.0
    assert result.observed["decision_session_purged_from_fit"] is True


def test_all_six_leakage_controls_are_measured_and_pass() -> None:
    records = run_leakage_controls()
    assert [record["name"] for record in records] == [name for name, _ in LEAKAGE_CONTROLS]
    assert all(record["passed"] for record in records), [(record["name"], record["observed"]) for record in records if not record["passed"]]
    assert all(record["regression_test"].startswith("tests/unit/test_r01_controls.py::") for record in records)


# ---------------------------------------------------------------------------
# The preregistered defaults (residual plan §2.12) are frozen, not chosen here
# ---------------------------------------------------------------------------


def test_the_frozen_seed_sets_are_the_preregistered_ones() -> None:
    assert tuple(range(1729, 1829)) == NULL_SEEDS
    assert tuple(range(11729, 11779)) == PLANTED_SEEDS
    assert len(NULL_SEEDS) == 100
    assert len(PLANTED_SEEDS) == 50
    assert not set(NULL_SEEDS) & set(PLANTED_SEEDS)


def test_the_statistic_names_one_estimand_and_never_the_compounded_return() -> None:
    spec = r01_spec()
    assert spec.statistic == "mean_per_session_net_return"
    assert "NOT the compounded net return" in spec.estimand
    assert spec.resampling["method"] == "moving_block_bootstrap_over_sessions"
    assert spec.resampling["block_sessions"] == 5
    assert "one-sided 95% lower bound" in str(spec.resampling["interval"])
    assert spec.nominal_alpha_one_sided == 0.05
    assert spec.fpr_upper_bound_max == 0.10
    assert spec.power_lower_bound_min == 0.80
    assert "no individual null seed is required to lose" in spec.pass_rule
    assert spec.promoted is False


def test_the_outcome_records_the_estimand_it_actually_used() -> None:
    outcome = control_outcome(1001, kind="null_noise")
    assert outcome["statistic"] == "mean_per_session_net_return"
    assert outcome["estimand"] == "mean_per_session_net_return"
    assert isinstance(outcome["lower_bound_one_sided_95"], float)
    assert isinstance(outcome["compounded_net_return"], float)
    assert outcome["compounded_net_return"] != outcome["mean_per_session_net_return"]


def test_the_protocol_predeclares_generator_seeds_universe_and_budget() -> None:
    protocol = r01_protocol()
    assert protocol.experiment == R01_EXPERIMENT
    assert protocol.generator == R01_GENERATOR
    assert protocol.seeds == sorted([*NULL_SEEDS, *PLANTED_SEEDS])
    assert protocol.universe == sorted(CONTROL_UNIVERSE)
    assert protocol.resource_budget.max_runtime_seconds == AGGREGATE_CEILING_SECONDS
    assert protocol.resource_budget.max_symbols == max(SCALE_SIZES)
    assert protocol.resource_budget.max_sessions >= 780
    assert [cost.regime for cost in protocol.costs] == [R01_COST_REGIME]


def test_a_conforming_control_run_passes_and_a_widened_universe_still_refuses(tmp_path: Path) -> None:
    frozen, _path = freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    sessions = run_sessions()
    conforming = conformance_violations(
        frozen,
        universe=CONTROL_UNIVERSE,
        start_date=sessions[0],
        end_date=sessions[-1],
        cost_regimes=[R01_COST_REGIME],
    )
    assert conforming == []
    widened = conformance_violations(
        frozen,
        universe=[*CONTROL_UNIVERSE, "us_equity/XNAS/IBM"],
        start_date=sessions[0],
        end_date=sessions[-1],
        cost_regimes=[R01_COST_REGIME],
    )
    assert any("universe differs" in violation for violation in widened)


def test_the_one_sided_binomial_bounds_are_the_exact_pair() -> None:
    empty = binomial_bounds(0, 100)
    assert empty["upper"] == pytest.approx(1.0 - 0.05 ** (1.0 / 100.0), abs=1e-9)
    assert empty["lower"] == 0.0
    assert empty["upper"] <= FPR_UPPER_BOUND_MAX
    full = binomial_bounds(50, 50)
    assert full["lower"] == pytest.approx(0.05 ** (1.0 / 50.0), abs=1e-9)
    assert full["upper"] == 1.0
    assert full["lower"] >= POWER_LOWER_BOUND_MIN


# ---------------------------------------------------------------------------
# Freeze and ceiling refusals (mutated by the freeze/ceiling mutation proofs)
# ---------------------------------------------------------------------------


def test_freezing_r01_is_byte_reproducible_and_binds_the_preregistration(tmp_path: Path) -> None:
    first, path = freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    before = path.read_bytes()
    second, same_path = freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    assert same_path == path
    assert path.read_bytes() == before
    assert first.protocol_digest == second.protocol_digest
    assert first.input_digests["r01_preregistration"] == r01_spec().digest()


def test_an_unfrozen_protocol_refuses_the_run(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError) as excinfo:
        run_controls(protocol_root=tmp_path)
    assert "assumptions are absent" in str(excinfo.value)


def test_a_foreign_frozen_protocol_cannot_stand_in_for_this_one(tmp_path: Path) -> None:
    """Another experiment's frozen manifest never satisfies this run's freeze."""
    freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    foreign = r01_protocol().model_copy(update={"experiment": "R01-unrelated-experiment"})
    with pytest.raises(ProtocolError) as excinfo:
        require_frozen_protocol(foreign, root=tmp_path)
    assert "assumptions are absent" in str(excinfo.value)


def test_a_changed_preregistration_refuses_the_run(tmp_path: Path) -> None:
    _frozen, path = freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input_digests"]["r01_preregistration"] = "0" * 64
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ProtocolError) as excinfo:
        run_controls(protocol_root=tmp_path)
    assert "does not bind this prespecification" in str(excinfo.value)


def test_a_bounded_demonstration_is_censored_and_never_passed(tmp_path: Path) -> None:
    freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    payload = run_controls(protocol_root=tmp_path, null_seed_limit=3, planted_seed_limit=3)
    planted_null = payload["planted_null"]
    assert planted_null["censored"] is True
    assert planted_null["complete_set_sizes"] is False
    assert payload["verdict"]["passed"] is False
    assert payload["verdict"]["censored"] is True
    assert all(row["seeds"] == 3 for row in planted_null["null"]["sets"])
    assert planted_null["planted"]["seeds"] == 3
