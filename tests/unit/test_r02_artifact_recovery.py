"""RS-R02-D1: the archived R02 artifacts verify, refuse tampering, and recompute offline.

The archive itself lives in a gitignored root (`data/*`), so these tests run against a
faithful *synthetic* R02 run — the same frozen code path as the real experiment, on the
weak-basis Gold fixtures — and never against the operator's artifacts. The one test that
does read the live archive skips with a stated reason when it is absent, which is the
normal state of a fresh clone or CI.

Nothing here fits a model beyond the synthetic run's own single prespecified fit: the
verifier recomputes metrics from the artifacts, and no verification path writes into
`data/`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from runpy import run_path
from typing import cast

import polars as pl
import pytest

from equity_lake.core.instruments import InstrumentKey
from equity_lake.devtools import verify_r02_artifacts as verify_module
from equity_lake.devtools.verify_r02_artifacts import (
    ARTIFACT_NAMES,
    DEFAULT_ANCHOR,
    ArtifactsAbsent,
    Outcome,
    load_anchor,
    main,
    sha256_file,
    verify,
)
from equity_lake.ml.experiment import (
    R02_FEATURES,
    R02_UNIVERSE,
    freeze_r02_protocol,
    r02_protocol,
    r02_spec,
    run_r02,
)

_fixtures = run_path(str(Path(__file__).with_name("_weak_basis_fixtures.py")))
us_equity_session_dates = _fixtures["us_equity_session_dates"]
weak_gold_frame = _fixtures["weak_gold_frame"]
write_gold_lake = _fixtures["write_gold_lake"]

RECORDED_AT = datetime(2026, 11, 21, 13, 0, tzinfo=UTC)
HOLDOUT_START = date(2024, 10, 1)

# Each verification recomputes every bootstrap interval with the frozen estimators, which
# is the point of the check and takes tens of seconds per run.
pytestmark = pytest.mark.slow


def _instruments() -> list[InstrumentKey]:
    instruments: list[InstrumentKey] = []
    for display_key in R02_UNIVERSE:
        market, mic, symbol = display_key.split("/")
        instruments.append(InstrumentKey(market=market, mic=mic, symbol=symbol))
    return instruments


def _synthetic_root(root: Path) -> Path:
    sessions = [session for session in us_equity_session_dates(date(2024, 1, 2), 200) if session <= date(2024, 9, 30)]
    assert max(sessions) < HOLDOUT_START, "the synthetic window must stop before the holdout"
    frames = [weak_gold_frame(instrument, sessions, seed=41 + index, features=R02_FEATURES) for index, instrument in enumerate(_instruments())]
    return cast(Path, write_gold_lake(root, frames))


def _write_anchor(anchor_path: Path, research_dir: Path) -> Path:
    """Render the tracked digest table from an archive (what a reviewer records once)."""
    lines = ["# verification anchor", "", "| Artifact | sha256 |", "| --- | --- |"]
    for name in ARTIFACT_NAMES:
        lines.append(f"| `{name}` | `{sha256_file(research_dir / name)}` |")
    lines += ["", "| Binding | sha256 |", "| --- | --- |"]
    lines.append(f"| `protocol_digest` | `{r02_protocol().digest()}` |")
    lines.append(f"| `prespecification_digest` | `{r02_spec().digest()}` |")
    anchor_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return anchor_path


def _copy_archive(source: Path, destination: Path) -> Path:
    """A writable copy of the archive plus its sibling protocols root, for tampering."""
    copy = destination / "research" / "r02"
    copy.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_NAMES:
        (copy / name).write_bytes((source / name).read_bytes())
    protocols = source.parent / "protocols"
    (destination / "research" / "protocols").mkdir(parents=True, exist_ok=True)
    for manifest in sorted(protocols.glob("*.json")):
        (destination / "research" / "protocols" / manifest.name).write_bytes(manifest.read_bytes())
    return copy


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A faithful R02 archive produced by the frozen code path on synthetic weak-basis rows."""
    root = tmp_path_factory.mktemp("r02-recovery")
    _synthetic_root(root)
    freeze_r02_protocol(recorded_at=RECORDED_AT, root=root)
    run_r02(
        lake_dir=root,
        root=root,
        results_dir=root / "research" / "r02",
        started_at=datetime(2026, 11, 21, 13, 30, tzinfo=UTC),
    )
    return root / "research" / "r02"


@pytest.fixture(scope="module")
def anchor(archive: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write_anchor(tmp_path_factory.mktemp("r02-anchor") / "anchor.md", archive)


@pytest.fixture(scope="module")
def outcome(archive: Path, anchor: Path) -> Outcome:
    """One verification without a lake: discrimination and bindings only."""
    return verify(archive, anchor)


@pytest.fixture(scope="module")
def outcome_with_lake(archive: Path, anchor: Path) -> Outcome:
    """One verification with the synthetic Gold closes: every metric and interval."""
    return verify(archive, anchor, lake_dir=archive.parent.parent)


# ---------------------------------------------------------------------------
# Verification passes on a faithful archive
# ---------------------------------------------------------------------------


def test_a_faithful_archive_verifies_without_a_mismatch(archive: Path, anchor: Path, outcome: Outcome) -> None:
    anchored = {name: digest for name, digest in load_anchor(anchor).items() if name in ARTIFACT_NAMES}

    assert outcome.mismatches == [], outcome.mismatches
    assert outcome.verified is True
    assert outcome.checked > 100, "the verification must actually check the recomputed metrics"
    assert outcome.digests == anchored
    # Read-only by construction: after verification every archived byte still matches the anchor taken before it.
    assert {name: sha256_file(archive / name) for name in ARTIFACT_NAMES} == anchored


def test_discrimination_and_intervals_are_recomputed_from_the_predictions(archive: Path, outcome: Outcome) -> None:
    metrics = json.loads((archive / "metrics.json").read_text(encoding="utf-8"))

    # Every arm and both folds are covered, scores and bootstrap bounds alike.
    per_arm = len(verify_module.ARM_NAMES) * len(verify_module.FOLD_NAMES)
    assert outcome.checked > per_arm * (len(verify_module.DISCRIMINATION_KEYS) + 4)
    assert metrics["coverage_basis"] == "vendor_events_no_clock"


def test_the_economic_metrics_are_recomputed_when_the_lake_is_given(outcome: Outcome, outcome_with_lake: Outcome) -> None:
    assert outcome.mismatches == [] and outcome_with_lake.mismatches == [], outcome_with_lake.mismatches
    assert any("economic metrics" in reason for reason in outcome.unverified)
    assert not any("economic metrics" in reason for reason in outcome_with_lake.unverified)
    assert outcome_with_lake.checked > outcome.checked
    # Every bootstrap interval — economics included — is reproduced.
    assert any("paired-difference estimands" in finding for finding in outcome_with_lake.findings)


def test_the_estimand_finding_names_both_quantities(archive: Path, outcome_with_lake: Outcome) -> None:
    metrics = json.loads((archive / "metrics.json").read_text(encoding="utf-8"))
    primary = metrics["folds"]["test"]["strategies"]["primary_model"]["economics"]
    technical = metrics["folds"]["test"]["strategies"]["technical_rule"]["economics"]
    paired = metrics["intervals"]["test"]["paired_difference_net_return_primary_minus_technical"]

    compounded = primary["net_return"] - technical["net_return"]
    per_session = primary["mean_daily_return"] - technical["mean_daily_return"]
    finding = next(text for text in outcome_with_lake.findings if "paired-difference estimands" in text)
    assert f"{compounded:.10f}" in finding
    assert f"{per_session:.10f}" in finding
    assert paired["lower"] <= per_session <= paired["upper"]


# ---------------------------------------------------------------------------
# Tamper, perturbation and binding mutations are refused
# ---------------------------------------------------------------------------


def test_a_tampered_artifact_is_refused_by_its_recorded_digest(
    archive: Path, anchor: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tampered = _copy_archive(archive, tmp_path)
    payload = json.loads((tampered / "baselines.json").read_text(encoding="utf-8"))
    payload["comparison"]["test"]["primary_minus_technical_net_return"] = -0.5
    (tampered / "baselines.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["--research-dir", str(tampered), "--anchor", str(anchor)]) == 1
    printed = capsys.readouterr().out

    assert "MISMATCH: digest baselines.json" in printed, printed


def test_perturbed_predictions_are_flagged_by_the_recomputation(
    archive: Path, anchor: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tampered = _copy_archive(archive, tmp_path)
    predictions = pl.read_parquet(tampered / "predictions.parquet").with_row_index("row")
    target = predictions.filter(pl.col("fold") == "test")["row"][0]
    perturbed = predictions.with_columns(
        pl.when(pl.col("row") == target)
        .then(pl.lit(1.0) - pl.col("probability_primary_model"))
        .otherwise(pl.col("probability_primary_model"))
        .alias("probability_primary_model")
    ).drop("row")
    perturbed.write_parquet(tampered / "predictions.parquet")
    # Re-anchor, so only the recomputation can catch the perturbation — one rule at a time.
    refreshed = _write_anchor(tmp_path / "anchor-after-perturbation.md", tampered)

    assert main(["--research-dir", str(tampered), "--anchor", str(refreshed)]) == 1
    printed = capsys.readouterr().out

    assert "brier_score" in printed, printed
    assert "MISMATCH: digest " not in printed, "the digests were re-anchored; only the recomputation may fail here"


def test_a_mutated_prespecification_binding_is_refused(archive: Path, anchor: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tampered = _copy_archive(archive, tmp_path)
    payload = json.loads((tampered / "metrics.json").read_text(encoding="utf-8"))
    digest = payload["prespecification_digest"]
    payload["prespecification_digest"] = ("0" if digest[0] != "0" else "1") + digest[1:]
    (tampered / "metrics.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    refreshed = _write_anchor(tmp_path / "anchor-after-binding.md", tampered)

    assert main(["--research-dir", str(tampered), "--anchor", str(refreshed)]) == 1
    printed = capsys.readouterr().out

    assert "prespecification digest" in printed, printed
    assert "MISMATCH: digest " not in printed, "the digests were re-anchored; the binding rule must fire on its own"


def test_a_mutated_manifest_binding_is_refused(archive: Path, anchor: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tampered = _copy_archive(archive, tmp_path)
    protocols = tampered.parent / "protocols"
    manifest = next(iter(sorted(protocols.glob("*.manifest.json"))))
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["input_digests"]["r02_prespecification"] = "0" * 64
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert main(["--research-dir", str(tampered), "--anchor", str(anchor)]) == 1
    printed = capsys.readouterr().out

    assert "binds the frozen prespecification digest" in printed, printed


# ---------------------------------------------------------------------------
# Absence, the repository anchor, and the positive control
# ---------------------------------------------------------------------------


def test_an_absent_archive_is_skipped_with_a_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "no-such-archive"

    with pytest.raises(ArtifactsAbsent, match="absent"):
        verify(missing, DEFAULT_ANCHOR)

    assert main(["--research-dir", str(missing), "--anchor", str(DEFAULT_ANCHOR)]) == 2
    assert "SKIPPED" in capsys.readouterr().err


def test_the_tracked_anchor_records_the_six_files_and_two_bindings() -> None:
    entries = load_anchor(DEFAULT_ANCHOR)

    assert set(entries) == set(ARTIFACT_NAMES) | {"protocol_digest", "prespecification_digest"}
    assert entries["protocol_digest"] == r02_protocol().digest()
    assert entries["prespecification_digest"] == r02_spec().digest()
    assert all(len(digest) == 64 for digest in entries.values())


def test_the_live_archive_verifies_or_the_check_is_skipped() -> None:
    research_dir = Path(__file__).resolve().parents[2] / "data" / "research" / "r02"
    if not research_dir.is_dir():
        pytest.skip(f"{research_dir} is absent in this checkout (data/* is gitignored); the live archive verifies where it exists")

    outcome = verify(research_dir, DEFAULT_ANCHOR)

    assert outcome.mismatches == [], outcome.mismatches
    assert outcome.digests == {name: digest for name, digest in load_anchor(DEFAULT_ANCHOR).items() if name in ARTIFACT_NAMES}
    payload = json.loads((research_dir / "metrics.json").read_text(encoding="utf-8"))
    assert payload["promoted"] is False
