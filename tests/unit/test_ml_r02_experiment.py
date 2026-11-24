"""R02: one frozen incremental ML experiment, artifact integrity and refusals.

These tests pin the ADR-0034 contract end to end on a synthetic weak-basis Gold
table written through the production merge seam: the prespecification is frozen into
the protocol manifest, the run binds to that digest, every artifact carries the basis
and the fixed disclosure, nothing is promoted, no prediction table is written, and no
holdout date is ever read. Model quality is *not* asserted — R02's result is whatever
it is, and a negative result is a valid outcome.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from runpy import run_path
from typing import Any, cast

import polars as pl
import pytest
from pydantic import ValidationError

from equity_lake.backtesting.protocol import ProtocolError, load_frozen_protocols, protocols_root
from equity_lake.core.availability import (
    VENDOR_EVENTS_NO_CLOCK_DISCLOSURE,
)
from equity_lake.core.instruments import InstrumentKey
from equity_lake.ml import experiment as r02
from equity_lake.ml.experiment import (
    R02_BASIS,
    R02_DISCLOSURE,
    R02_FEATURES,
    R02_UNIVERSE,
    R02BaselinesArtifact,
    R02Error,
    R02MetricsArtifact,
    R02ProtocolArtifact,
    R02RunManifest,
    R02Spec,
    freeze_r02_protocol,
    r02_protocol,
    r02_spec,
    run_r02,
)

_fixtures = run_path(str(Path(__file__).with_name("_weak_basis_fixtures.py")))
us_equity_session_dates = _fixtures["us_equity_session_dates"]
weak_gold_frame = _fixtures["weak_gold_frame"]
write_gold_lake = _fixtures["write_gold_lake"]

RECORDED_AT = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
HOLDOUT_START = date(2024, 10, 1)
_ARTIFACTS = ("protocol.json", "metrics.json", "baselines.json", "run.json")


def _instruments() -> list[InstrumentKey]:
    instruments: list[InstrumentKey] = []
    for display_key in R02_UNIVERSE:
        market, mic, symbol = display_key.split("/")
        instruments.append(InstrumentKey(market=market, mic=mic, symbol=symbol))
    return instruments


def _synthetic_root(root: Path) -> Path:
    sessions = [session for session in us_equity_session_dates(date(2024, 1, 2), 200) if session <= date(2024, 9, 30)]
    assert max(sessions) <= HOLDOUT_START, "the synthetic window must stop before the holdout"
    frames = [weak_gold_frame(instrument, sessions, seed=31 + index, features=R02_FEATURES) for index, instrument in enumerate(_instruments())]
    return cast(Path, write_gold_lake(root, frames))


@pytest.fixture(scope="module")
def frozen_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A synthetic weak-basis lake with the R02 protocol frozen above it."""
    root = tmp_path_factory.mktemp("r02-frozen")
    _synthetic_root(root)
    freeze_r02_protocol(recorded_at=RECORDED_AT, root=root)
    return root


@pytest.fixture(scope="module", autouse=True)
def run_summary(frozen_root: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        run_r02(
            lake_dir=frozen_root,
            root=frozen_root,
            results_dir=frozen_root / "research" / "r02",
            started_at=datetime(2026, 11, 21, 12, 30, tzinfo=UTC),
        ),
    )


# ---------------------------------------------------------------------------
# Prespecification and freezing
# ---------------------------------------------------------------------------


def test_prespecification_digest_is_stable_and_reproducible() -> None:
    assert r02_spec().digest() == r02_spec().digest()
    assert r02_spec().digest() == r02_spec().model_copy(deep=True).digest()


def test_prespecification_freezes_the_five_ticker_pre_holdout_design() -> None:
    spec = r02_spec()

    assert spec.train_end < spec.validation_start <= spec.validation_end < spec.test_start <= spec.test_end < spec.holdout_start
    assert spec.universe == sorted(R02_UNIVERSE)
    assert spec.label_horizon_sessions == 5
    assert spec.purge_sessions == 5
    assert spec.embargo_sessions == 1
    assert spec.holding_sessions == 5
    assert spec.uncertainty["block_sessions"] == 5
    assert spec.uncertainty["seed"] == 20261121
    assert spec.promoted is False
    assert spec.technical_rule == "momentum_20_session_sign"


def test_prespecification_refuses_a_fold_inside_the_holdout() -> None:
    with pytest.raises(ValidationError, match="holdout"):
        R02Spec.model_validate({**r02_spec().model_dump(mode="json"), "test_end": "2024-10-15"})


def test_prespecification_refuses_a_promotion() -> None:
    with pytest.raises(ValidationError, match="promotes nothing"):
        R02Spec.model_validate({**r02_spec().model_dump(mode="json"), "promoted": True})


def test_freeze_binds_the_prespecification_digest(frozen_root: Path) -> None:
    records = load_frozen_protocols(frozen_root)

    assert len(records) == 1
    assert records[0].protocol.experiment == "R02-ml-research-2024"
    assert records[0].input_digests["r02_prespecification"] == r02_spec().digest()
    assert records[0].protocol_digest == r02_protocol().digest()


def test_an_unfrozen_protocol_refuses_the_run(tmp_path: Path) -> None:
    _synthetic_root(tmp_path)

    with pytest.raises(ProtocolError, match="assumptions are absent"):
        run_r02(lake_dir=tmp_path, root=tmp_path, results_dir=tmp_path / "research" / "r02")


def test_a_manifest_without_this_prespecification_refuses_the_run(tmp_path: Path) -> None:
    _synthetic_root(tmp_path)
    r02.freeze_protocol(
        r02_protocol(),
        recorded_at=RECORDED_AT,
        root=tmp_path,
        input_digests={"r02_prespecification": "0" * 64},
    )

    with pytest.raises(ProtocolError, match="does not bind this prespecification"):
        run_r02(lake_dir=tmp_path, root=tmp_path, results_dir=tmp_path / "research" / "r02")


def test_a_claimed_digest_must_match_the_frozen_record(frozen_root: Path) -> None:
    with pytest.raises(ProtocolError, match="not the claimed"):
        run_r02(
            lake_dir=frozen_root,
            root=frozen_root,
            results_dir=frozen_root / "research" / "r02-claim",
            protocol_digest="f" * 64,
        )


def test_a_protocol_change_is_refused_before_the_run() -> None:
    changed = r02_protocol().model_copy(update={"purge_days": 0})

    with pytest.raises(ProtocolError):
        r02.require_frozen_protocol(changed, root=Path("/nonexistent"))


# ---------------------------------------------------------------------------
# The run's artifacts
# ---------------------------------------------------------------------------


def test_the_run_writes_every_artifact(frozen_root: Path, run_summary: dict[str, Any]) -> None:
    results = frozen_root / "research" / "r02"

    for name in (*_ARTIFACTS, "predictions.parquet", "report.md"):
        assert (results / name).is_file(), f"{name} is missing"
    assert run_summary["protocol_digest"] == r02_protocol().digest()
    assert run_summary["prespecification_digest"] == r02_spec().digest()
    assert run_summary["promoted"] is False


def test_every_json_artifact_carries_the_basis_and_the_exact_disclosure(frozen_root: Path, run_summary: dict[str, Any]) -> None:
    results = frozen_root / "research" / "r02"
    assert run_summary["promoted"] is False

    for name in _ARTIFACTS:
        payload = json.loads((results / name).read_text(encoding="utf-8"))
        assert payload["coverage_basis"] == R02_BASIS == "vendor_events_no_clock"
        assert payload["disclosure"] == R02_DISCLOSURE == VENDOR_EVENTS_NO_CLOCK_DISCLOSURE
        assert payload["promoted"] is False
        assert payload["protocol_digest"] == r02_protocol().digest()
        assert payload["prespecification_digest"] == r02_spec().digest()


def test_prediction_rows_carry_the_basis_the_disclosure_and_the_outcome(frozen_root: Path, run_summary: dict[str, Any]) -> None:
    predictions = pl.read_parquet(frozen_root / "research" / "r02" / "predictions.parquet")

    for column in (
        "date",
        "market",
        "mic",
        "symbol",
        "probability_primary_model",
        "outcome",
        "coverage_basis",
        "disclosure",
        "protocol_digest",
        "prespecification_digest",
    ):
        assert column in predictions.columns, f"prediction column {column} is missing"
    assert set(predictions["coverage_basis"].unique().to_list()) == {R02_BASIS}
    assert set(predictions["disclosure"].unique().to_list()) == {R02_DISCLOSURE}
    assert set(predictions["protocol_digest"].unique().to_list()) == {r02_protocol().digest()}
    assert set(predictions["prespecification_digest"].unique().to_list()) == {r02_spec().digest()}
    assert set(predictions["fold"].unique().to_list()) <= {"train", "validation", "test"}
    assert predictions["outcome"].null_count() == 0
    probabilities = predictions["probability_primary_model"].to_numpy()
    assert 0.0 <= float(probabilities.min()) <= float(probabilities.max()) <= 1.0


def test_the_report_states_the_disclosure_and_promotes_nothing(frozen_root: Path, run_summary: dict[str, Any]) -> None:
    report = (frozen_root / "research" / "r02" / "report.md").read_text(encoding="utf-8")

    assert R02_DISCLOSURE in report
    assert "nothing is promoted" in report.lower()
    assert "vendor_events_no_clock" in report
    assert f"{r02_protocol().digest()}" in report


def test_no_holdout_date_reaches_an_artifact(frozen_root: Path, run_summary: dict[str, Any]) -> None:
    results = frozen_root / "research" / "r02"
    predictions = pl.read_parquet(results / "predictions.parquet")
    metrics = json.loads((results / "metrics.json").read_text(encoding="utf-8"))
    run = json.loads((results / "run.json").read_text(encoding="utf-8"))

    assert all(value < HOLDOUT_START for value in predictions["date"].to_list())
    assert all(value is not None and value < HOLDOUT_START for value in predictions["label_end_date"].to_list())
    assert metrics["folds_evidence"]["holdout"]["rows_read"] == 0
    assert run["holdout_untouched"]["read"] is False
    assert run["read_window"].endswith("2024-09-30")
    assert run_summary["row_counts"]["test_rows"] > 0


def test_each_fold_is_matured_purged_and_embargoed(frozen_root: Path) -> None:
    metrics = json.loads((frozen_root / "research" / "r02" / "metrics.json").read_text(encoding="utf-8"))
    evidence = metrics["folds_evidence"]
    spec = r02_spec()

    assert evidence["train"]["label_end_date_max"] < str(spec.validation_start)
    assert evidence["validation"]["label_end_date_max"] <= str(spec.validation_end)
    assert evidence["test"]["label_end_date_max"] <= str(spec.test_end)
    for fold in ("train", "validation", "test"):
        assert evidence[fold]["sessions_embargoed"] == 1
        assert evidence[fold]["label_maturity_sessions"] == 5
        assert evidence[fold]["rows"] > 0


def test_uncertainty_intervals_are_prespecified_and_present(frozen_root: Path) -> None:
    metrics = json.loads((frozen_root / "research" / "r02" / "metrics.json").read_text(encoding="utf-8"))
    intervals = metrics["intervals"]["test"]

    for metric in ("net_return", "sharpe_ratio", "max_drawdown"):
        interval = intervals["primary_model"][metric]
        assert interval["lower"] <= interval["upper"]
    for metric in ("brier_score", "roc_auc"):
        interval = intervals["primary_model"]["discrimination"][metric]
        assert interval["lower"] <= interval["upper"]
    assert "paired_difference_net_return_primary_minus_technical" in intervals
    assert metrics["promotion"]["promoted"] is False


def test_the_baselines_are_scored_on_matched_dates(frozen_root: Path) -> None:
    baselines = json.loads((frozen_root / "research" / "r02" / "baselines.json").read_text(encoding="utf-8"))
    metrics = json.loads((frozen_root / "research" / "r02" / "metrics.json").read_text(encoding="utf-8"))

    for fold in ("validation", "test"):
        assert baselines["matched_dates"][fold]["rows"] == metrics["folds"][fold]["rows"]
        assert baselines["matched_dates"][fold]["sessions"] == metrics["folds"][fold]["sessions"]
        strategies = baselines["comparison"][fold]["strategies"]
        assert set(strategies) == {"primary_model", "simple_model", "technical_rule", "no_skill"}
        # Every strategy is measured on the same economic sessions.
        assert len({strategies[name]["economics"]["sessions"] for name in strategies}) == 1


def test_the_run_records_one_fit_and_no_sweep(frozen_root: Path) -> None:
    run = json.loads((frozen_root / "research" / "r02" / "run.json").read_text(encoding="utf-8"))
    metrics = json.loads((frozen_root / "research" / "r02" / "metrics.json").read_text(encoding="utf-8"))

    assert run["model"]["fits"] == 1
    assert run["model"]["refits"] == 0
    assert run["model"]["sweeps"] == 0
    assert run["model"]["holdout_tuning"] is False
    assert run["uncertainty"]["method"] == "moving_block_bootstrap"
    assert run["uncertainty"]["block_sessions"] == 5
    assert run["uncertainty"]["seed"] == 20261121
    assert run["runtime"]["wall_time_seconds"] <= metrics["resource"]["max_runtime_seconds"]
    assert metrics["resource"]["symbols"] <= metrics["resource"]["max_symbols"]
    assert metrics["resource"]["sessions"] <= metrics["resource"]["max_sessions"]


def test_no_prediction_table_is_written(frozen_root: Path, run_summary: dict[str, Any]) -> None:
    platinum = [path for path in frozen_root.rglob("*") if "04_platinum" in path.parts]
    assert platinum == [], f"R02 wrote into a Platinum path: {platinum}"
    for path in run_summary["artifacts"].values():
        assert "04_platinum" not in Path(path).parts
        assert Path(path).is_relative_to(frozen_root / "research" / "r02")


def test_writing_into_a_prediction_table_is_refused() -> None:
    with pytest.raises(R02Error, match="research artifacts live under data/research/r02"):
        r02._refuse_unsafe_target(Path("data/lake/04_platinum/predictions/rows.parquet"))


# ---------------------------------------------------------------------------
# The artifact contract itself (mutation anchor for the basis/disclosure checks)
# ---------------------------------------------------------------------------


def _artifact_payload(model: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"protocol_digest": "a" * 64, "prespecification_digest": "b" * 64}
    if model is R02MetricsArtifact:
        return {**base, "folds": {}, "intervals": {}, "promotion": {}, "folds_evidence": {}, "resource": {}, "calibration": {}, "inputs": {}}
    if model is R02ProtocolArtifact:
        return {
            **base,
            "manifest_name": "20261121T120000Z",
            "recorded_at": RECORDED_AT,
            "frozen_protocol": {},
            "prespecification": {},
        }
    if model is R02BaselinesArtifact:
        return {**base, "matched_dates": {}, "comparison": {}}
    return {
        **base,
        "protocol_binding": {},
        "lake_root": "data/lake_real",
        "read_window": "2024-01-02..2024-09-30",
        "holdout_untouched": {},
        "row_counts": {},
        "runtime": {},
        "model": {},
        "uncertainty": {},
    }


@pytest.mark.parametrize("model", [R02MetricsArtifact, R02ProtocolArtifact, R02BaselinesArtifact, R02RunManifest])
def test_an_artifact_without_the_weak_basis_is_refused(model: Any) -> None:
    payload = _artifact_payload(model)

    assert model.model_validate(payload).coverage_basis == R02_BASIS
    with pytest.raises(ValidationError, match="one_basis|coverage_basis"):
        model.model_validate({**payload, "coverage_basis": "source_clock"})
    with pytest.raises(ValidationError, match="disclosure"):
        model.model_validate({**payload, "disclosure": "Computed on a weak basis, probably fine."})
    with pytest.raises(ValidationError, match="promotes nothing|promoted"):
        model.model_validate({**payload, "promoted": True})


def test_the_run_never_promotes(frozen_root: Path) -> None:
    metrics = json.loads((frozen_root / "research" / "r02" / "metrics.json").read_text(encoding="utf-8"))
    spec = r02_spec()

    assert metrics["promotion"]["promoted"] is False
    assert "nothing_promoted_reason" in metrics["promotion"]
    assert spec.promoted is False
    assert cast(Path, protocols_root(frozen_root)).is_dir()
