"""R00 harness controls: frozen research assumptions, refusal, frozen manifests.

These tests are offline by construction — no ingestion, no feature run, no model
fit, no network and no lake write. They pin the three R00 obligations: a changed
assumption refuses the run, an absent freeze refuses it, and unchanged assumptions
reproduce identical manifests.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from equity_lake.backtesting.evidence import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    CodeIdentity,
    CostApplication,
)
from equity_lake.backtesting.protocol import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    PROTOCOL_FIELD_ORDER,
    PROTOCOL_SCHEMA,
    FrozenProtocol,
    ProtocolError,
    ResearchProtocol,
    ResourceBudget,
    changed_fields,
    conformance_violations,
    cost_disclosures,
    freeze_protocol,
    load_frozen_protocols,
    protocol_binding,
    protocols_root,
    require_frozen_protocol,
    require_run_conformance,
    select_frozen_protocol,
)

RECORDED_AT = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
CODE = CodeIdentity(commit="ad74703", dirty_digest=None)


def _protocol(**overrides: Any) -> ResearchProtocol:
    values: dict[str, Any] = {
        "experiment": "R01-baseline-us",
        "universe": ["us_equity/XNAS/AAPL", "us_equity/XNYS/JPM"],
        "start_date": date(2024, 1, 2),
        "end_date": date(2024, 12, 31),
        "holdout_start": date(2024, 10, 1),
        "holdout_end": date(2024, 12, 31),
        "entry_clock": "next_session_open",
        "entry_price": "open",
        "label_horizon_days": 5,
        "label_maturity_days": 5,
        "purge_days": 5,
        "embargo_days": 1,
        "costs": [CostApplication(regime="realistic", fee_ratio=0.0005, tax_ratio=0.0)],
        "metrics": ["net_return", "sharpe_ratio", "max_drawdown"],
        "baselines": ["buy_and_hold", "equal_weight"],
        "acceptance_tolerances": {"net_return": 0.0, "sharpe_ratio": 0.05},
        "generator": None,
        "seeds": [],
        "resource_budget": ResourceBudget(max_runtime_seconds=600.0, max_symbols=50, max_sessions=252),
    }
    values.update(overrides)
    return ResearchProtocol(**values)


class TestProtocolIdentity:
    def test_set_like_fields_are_order_insensitive(self) -> None:
        first = _protocol(
            universe=["us_equity/XNYS/JPM", "us_equity/XNAS/AAPL", "us_equity/XNAS/AAPL"],
            metrics=["sharpe_ratio", "max_drawdown", "net_return"],
            baselines=["equal_weight", "buy_and_hold"],
        )
        assert first.digest() == _protocol().digest()

    def test_a_changed_cost_is_a_different_protocol(self) -> None:
        changed = _protocol(costs=[CostApplication(regime="realistic", fee_ratio=0.001, tax_ratio=0.0)])
        assert changed.digest() != _protocol().digest()
        assert changed_fields(_protocol(), changed) == ["costs"]

    def test_a_changed_holdout_is_a_different_protocol(self) -> None:
        changed = _protocol(holdout_start=date(2024, 9, 1))
        assert changed.digest() != _protocol().digest()
        assert changed_fields(_protocol(), changed) == ["holdout_start"]

    def test_payload_follows_the_documented_field_order(self) -> None:
        assert tuple(_protocol().protocol_payload()) == PROTOCOL_FIELD_ORDER
        assert _protocol().protocol == PROTOCOL_SCHEMA

    def test_digest_excludes_code_and_time(self, tmp_path: Path) -> None:
        first, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, code=CODE, root=tmp_path / "a")
        second, _ = freeze_protocol(
            _protocol(),
            recorded_at=datetime(2026, 11, 19, 12, 0, tzinfo=UTC),
            code=CodeIdentity(commit="deadbeef", dirty_digest=None),
            root=tmp_path / "b",
        )
        assert first.protocol_digest == second.protocol_digest


class TestAssumptionValidation:
    def test_holdout_must_lie_inside_the_window(self) -> None:
        with pytest.raises(ValidationError, match="holdout must lie inside the run window"):
            _protocol(holdout_end=date(2025, 1, 31))
        with pytest.raises(ValidationError, match="holdout must lie inside the run window"):
            _protocol(holdout_start=date(2023, 12, 1))

    def test_window_must_be_ordered(self) -> None:
        with pytest.raises(ValidationError, match="end_date must not precede start_date"):
            _protocol(end_date=date(2023, 12, 31))

    def test_execution_convention_is_closed_vocabulary(self) -> None:
        with pytest.raises(ValidationError, match="unknown entry clock"):
            _protocol(entry_clock="same_close_oracle")
        with pytest.raises(ValidationError, match="unknown entry price"):
            _protocol(entry_price="vwap")

    def test_seeds_require_a_generator_identity(self) -> None:
        with pytest.raises(ValidationError, match="predeclared seeds require a generator identity"):
            _protocol(seeds=[7, 11])
        assert _protocol(generator="synthetic:planted@1", seeds=[11, 7]).seeds == [7, 11]

    def test_resource_budget_is_mandatory(self) -> None:
        values = _protocol().model_dump()
        values.pop("resource_budget")
        with pytest.raises(ValidationError):
            ResearchProtocol(**values)
        with pytest.raises(ValidationError):
            ResourceBudget(max_runtime_seconds=0.0, max_symbols=50, max_sessions=252)

    def test_universe_basis_is_derived_by_parsing(self) -> None:
        assert _protocol().universe_basis == "canonical"
        assert _protocol(universe=["AAPL", "JPM"]).universe_basis == "ticker_only"

    def test_secret_named_config_is_never_hashed_or_stored(self, tmp_path: Path) -> None:
        public = {"ingestion": {"retry_attempts": 3}}
        with_secret = {**public, "api_key": "super-secret-value"}
        first, path = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE, config=public)
        second, _ = freeze_protocol(_protocol(), recorded_at=datetime(2026, 11, 18, 13, 0, tzinfo=UTC), root=tmp_path, code=CODE, config=with_secret)
        assert first.config_sha256 == second.config_sha256 is not None
        assert "super-secret-value" not in path.read_text(encoding="utf-8")


class TestFreezeAndManifest:
    def test_freeze_writes_one_dated_manifest_under_the_research_root(self, tmp_path: Path) -> None:
        record, path = freeze_protocol(
            _protocol(),
            recorded_at=RECORDED_AT,
            root=tmp_path,
            code=CODE,
            input_digests={"catalog": "a" * 64},
        )
        assert path == protocols_root(tmp_path) / f"20261118T120000Z-{_protocol().digest()[:12]}.manifest.json"
        assert path.parent.parent == tmp_path / "research"
        assert [item.relative_to(tmp_path).as_posix() for item in tmp_path.rglob("*") if item.is_file()] == [f"research/protocols/{path.name}"]
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["protocol_digest"] == _protocol().digest()
        assert stored["code"] == {"commit": "ad74703", "dirty_digest": None}
        assert stored["input_digests"] == {"catalog": "a" * 64}
        assert record.protocol_digest == stored["protocol_digest"]

    def test_unchanged_assumptions_reproduce_identical_manifests(self, tmp_path: Path) -> None:
        first, path = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        second, again = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        assert path == again
        assert first.canonical_json() == second.canonical_json()
        assert load_frozen_protocols(tmp_path) == [second]

    def test_a_different_manifest_at_the_same_name_is_refused(self, tmp_path: Path) -> None:
        freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="refusing to overwrite"):
            freeze_protocol(
                _protocol(),
                recorded_at=RECORDED_AT,
                root=tmp_path,
                code=CodeIdentity(commit="deadbeef", dirty_digest=None),
            )

    def test_tampered_manifest_is_refused_on_read(self, tmp_path: Path) -> None:
        _, path = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol"]["holdout_start"] = "2024-09-01"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ProtocolError, match="corrupt or was modified"):
            load_frozen_protocols(tmp_path)

    def test_manifest_digest_must_bind_its_protocol(self) -> None:
        with pytest.raises(ValidationError, match="protocol_digest does not match"):
            FrozenProtocol(protocol_digest="0" * 64, recorded_at=RECORDED_AT, protocol=_protocol())

    def test_input_digests_must_be_lowercase_sha256(self) -> None:
        with pytest.raises(ValidationError, match="input digest 'catalog'"):
            FrozenProtocol(
                protocol_digest=_protocol().digest(),
                recorded_at=RECORDED_AT,
                protocol=_protocol(),
                input_digests={"catalog": "NOT-A-DIGEST"},
            )


class TestRefusalGate:
    def test_absent_assumptions_refuse_the_run(self, tmp_path: Path) -> None:
        with pytest.raises(ProtocolError, match="assumptions are absent"):
            require_frozen_protocol(_protocol(), root=tmp_path)

    def test_unchanged_assumptions_admit_the_run(self, tmp_path: Path) -> None:
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        admitted = require_frozen_protocol(_protocol(), root=tmp_path)
        assert admitted == frozen

    def test_changed_assumptions_refuse_the_run_and_name_the_field(self, tmp_path: Path) -> None:
        freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        changed = _protocol(costs=[CostApplication(regime="pessimistic", fee_ratio=0.002, tax_ratio=0.001)])
        with pytest.raises(ProtocolError, match="assumptions are changed") as excinfo:
            require_frozen_protocol(changed, root=tmp_path)
        assert "costs" in str(excinfo.value)

    def test_a_second_experiment_does_not_satisfy_the_first(self, tmp_path: Path) -> None:
        freeze_protocol(_protocol(experiment="R01-baseline-us"), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="assumptions are absent"):
            require_frozen_protocol(_protocol(experiment="R02-ml-us"), root=tmp_path)

    def test_a_changed_protocol_can_be_frozen_as_a_new_explicit_record(self, tmp_path: Path) -> None:
        freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        changed = _protocol(holdout_start=date(2024, 9, 1))
        with pytest.raises(ProtocolError):
            require_frozen_protocol(changed, root=tmp_path)
        freeze_protocol(changed, recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        assert require_frozen_protocol(changed, root=tmp_path).protocol_digest == changed.digest()
        assert len(load_frozen_protocols(tmp_path)) == 2


#: The pre-holdout segment of the frozen window: what an R01 baseline may run.
PRE_HOLDOUT = (date(2024, 1, 2), date(2024, 9, 30))
FROZEN_UNIVERSE = ["us_equity/XNAS/AAPL", "us_equity/XNYS/JPM"]


class TestRunBinding:
    """R01: a run binds one frozen manifest, and its own facts must conform."""

    def test_an_existing_freeze_is_selected_by_experiment(self, tmp_path: Path) -> None:
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        assert select_frozen_protocol("R01-baseline-us", root=tmp_path) == frozen

    def test_an_absent_freeze_refuses_the_binding(self, tmp_path: Path) -> None:
        """Falsified by: selecting a manifest for an experiment that was never frozen."""
        with pytest.raises(ProtocolError, match="assumptions are absent"):
            select_frozen_protocol("R01-baseline-us", root=tmp_path)
        freeze_protocol(_protocol(experiment="R02-ml-us"), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="assumptions are absent"):
            select_frozen_protocol("R01-baseline-us", root=tmp_path)

    def test_a_digest_claim_must_match_a_frozen_record(self, tmp_path: Path) -> None:
        """Falsified by: accepting a run whose stated digest no frozen record carries."""
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        assert select_frozen_protocol("R01-baseline-us", root=tmp_path, digest=frozen.protocol_digest) == frozen
        with pytest.raises(ProtocolError, match="does not match a frozen record"):
            select_frozen_protocol("R01-baseline-us", root=tmp_path, digest="0" * 64)

    def test_two_distinct_freezes_of_one_experiment_are_ambiguous(self, tmp_path: Path) -> None:
        """Falsified by: silently picking one of two different assumption sets."""
        first, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        second, _ = freeze_protocol(_protocol(holdout_start=date(2024, 9, 1)), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="ambiguous"):
            select_frozen_protocol("R01-baseline-us", root=tmp_path)
        # The digest claim disambiguates exactly one of them.
        assert select_frozen_protocol("R01-baseline-us", root=tmp_path, digest=second.protocol_digest) == second
        assert select_frozen_protocol("R01-baseline-us", root=tmp_path, digest=first.protocol_digest) == first

    def test_a_conforming_pre_holdout_run_reports_no_violation(self, tmp_path: Path) -> None:
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        assert (
            conformance_violations(frozen, universe=FROZEN_UNIVERSE, start_date=PRE_HOLDOUT[0], end_date=PRE_HOLDOUT[1], cost_regimes=["realistic"])
            == []
        )
        require_run_conformance(frozen, universe=FROZEN_UNIVERSE, start_date=PRE_HOLDOUT[0], end_date=PRE_HOLDOUT[1], cost_regimes=["realistic"])

    def test_the_holdout_is_never_entered(self, tmp_path: Path) -> None:
        """Falsified by: admitting a window that overlaps the frozen holdout."""
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="overlaps the frozen holdout"):
            require_run_conformance(
                frozen,
                universe=FROZEN_UNIVERSE,
                start_date=date(2024, 9, 1),
                end_date=date(2024, 10, 31),
                cost_regimes=["realistic"],
            )

    def test_the_window_must_lie_inside_the_frozen_window(self, tmp_path: Path) -> None:
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="before the frozen start"):
            require_run_conformance(
                frozen, universe=FROZEN_UNIVERSE, start_date=date(2023, 12, 1), end_date=date(2024, 3, 1), cost_regimes=["realistic"]
            )

    def test_the_universe_is_the_frozen_universe_exactly(self, tmp_path: Path) -> None:
        """Falsified by: admitting an added or omitted instrument as the same run."""
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="universe differs from the frozen universe"):
            require_run_conformance(
                frozen,
                universe=[*FROZEN_UNIVERSE, "us_equity/XNAS/NVDA"],
                start_date=PRE_HOLDOUT[0],
                end_date=PRE_HOLDOUT[1],
                cost_regimes=["realistic"],
            )
        with pytest.raises(ProtocolError, match="universe differs from the frozen universe"):
            require_run_conformance(
                frozen, universe=[FROZEN_UNIVERSE[0]], start_date=PRE_HOLDOUT[0], end_date=PRE_HOLDOUT[1], cost_regimes=["realistic"]
            )

    def test_the_cost_regimes_are_the_frozen_regimes(self, tmp_path: Path) -> None:
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        with pytest.raises(ProtocolError, match="cost regimes"):
            require_run_conformance(frozen, universe=FROZEN_UNIVERSE, start_date=PRE_HOLDOUT[0], end_date=PRE_HOLDOUT[1], cost_regimes=["zero"])

    def test_a_differing_applied_ratio_is_disclosed_not_hidden(self, tmp_path: Path) -> None:
        """Falsified by: silently recording costs the frozen protocol does not state."""
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        applied = [CostApplication(regime="realistic", fee_ratio=0.001425, tax_ratio=0.003)]
        [disclosure] = cost_disclosures(frozen, applied)
        assert "cost asymmetry (disclosed)" in disclosure
        assert "0.001425" in disclosure and "0.0005" in disclosure
        # The exact frozen pair discloses nothing.
        assert cost_disclosures(frozen, [CostApplication(regime="realistic", fee_ratio=0.0005, tax_ratio=0.0)]) == []

    def test_the_binding_carries_the_digest_and_the_experiment(self, tmp_path: Path) -> None:
        frozen, _ = freeze_protocol(_protocol(), recorded_at=RECORDED_AT, root=tmp_path, code=CODE)
        assert protocol_binding(frozen) == {"protocol_digest": frozen.protocol_digest, "protocol_experiment": "R01-baseline-us"}


def test_the_engines_session_close_execution_is_a_frozen_choice() -> None:
    """The engine trades at the session close, so that is the honest frozen clock."""
    protocol = _protocol(entry_clock="session_close", entry_price="close")
    assert protocol.entry_clock == "session_close"
    assert protocol.entry_price == "close"
    with pytest.raises(ValidationError, match="unknown entry clock"):
        _protocol(entry_clock="same_close_oracle")
