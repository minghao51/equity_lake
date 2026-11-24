"""E01-6 commit 1: protocol ``equity-run-manifest/4`` reader support (no writer).

Scope: ADR-0027 §4.4 (per-snapshot ``PriceOriginSummary``), §4.5 (the aggregate is
re-derived from summaries and an unsupported claim is refused, never downgraded) and
§5 / decision D4 (a new protocol for the strengthened derivation, with reader support
landing **before** any v4 writer).

Reader-first matters here: v3 evidence keeps its own rules and its own bytes, and a
stored v3 manifest never gains a summary or a bump. Every shipped writer still mints
v3, so ``data_provenance`` stays ``unknown`` on every real path.

The canonical byte encodings and golden digests of ADR-0027 §4.1-§4.3 describe the
*price batch*, the *generator evidence* and the *envelope projection*; they are pinned
with the stages that actually produce those bytes (storage and the read seam), not
here. This file pins the protocol, the grammar, the summary rules and the derivation.

Every test uses ``tmp_path`` only: nothing under ``data/`` is read or written, no
seeding run, no provider/network/LLM call and no lake mutation.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast, get_args

import polars as pl
import pytest
from pydantic import ValidationError

from equity_lake.backtesting.evidence import (
    MANIFEST_NAME,
    RUN_PROTOCOL,
    RUN_PROTOCOL_V3,
    RUN_PROTOCOL_V4,
    CodeIdentity,
    CostApplication,
    FinalisedRun,
    InputSnapshot,
    PriceOriginCounts,
    PriceOriginSummary,
    RunManifest,
    RunScope,
    UnknownReason,
    finalise_run,
    is_price_snapshot_name,
    price_origin_aggregate,
    universe_basis_for,
    validate_run_directory,
)
from equity_lake.backtesting.evidence_view import (
    latest_success_by_slug,
    validated_runs,
)
from equity_lake.backtesting.result import BacktestResult
from equity_lake.core.schemas import (
    PRICE_ORIGIN_UNKNOWN_REASONS,
    PRICE_SNAPSHOT_NAME_PREFIX,
    PRICE_SOURCE_PATTERN,
)

D0, D1, D2 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
DATES = [D0, D1, D2]
VALUES = [100.0, 110.0, 100.0]
MARKET = "us_equity"
TS = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
SOURCE = "generator:synthetic_demo_seed@1"
SCHEMA = "price-source-evidence/1"


# ---------------------------------------------------------------------------
# Helpers (tmp_path only)
# ---------------------------------------------------------------------------


def _result() -> BacktestResult:
    """A dated result, so finalisation has real evidence to validate."""
    return BacktestResult(
        strategy_name="stub",
        tickers=["AAA"],
        start_date=D0,
        end_date=D2,
        initial_cash=100.0,
        final_cash=VALUES[-1],
        equity_curve=pl.Series("equity", VALUES, dtype=pl.Float64),
        trades=[{"ticker": "AAA", "entry_date": D0, "exit_date": D1, "entry_price": 1.0, "exit_price": 1.1, "shares": 1.0, "pnl": 0.1}],
        metrics={"total_return": 0.01, "sharpe_ratio": 0.5},
        equity_dates=list(DATES),
    )


def _scope(*, protocol: str = RUN_PROTOCOL) -> RunScope:
    return RunScope(
        protocol=protocol,
        universe=["us_equity/XNYS/AAA"],
        start_date=D0,
        end_date=D2,
        strategies=["stub"],
        costs=[CostApplication(regime="zero", fee_ratio=0.0, tax_ratio=0.0)],
        initial_cash=100.0,
    )


def _snapshot(name: str = f"{PRICE_SNAPSHOT_NAME_PREFIX}{MARKET}", *, version: str | None = "4") -> InputSnapshot:
    return InputSnapshot(name=name, sha256="a" * 64, source="lake:delta", version=version)


def _summary(
    *,
    snapshot: InputSnapshot | None = None,
    synthetic: int = 3,
    real: int = 0,
    unknown: int = 0,
    unknown_reasons: dict[str, int] | None = None,
    sources: list[str] | None = None,
    schema_version: int = 1,
) -> PriceOriginSummary:
    """A price-origin summary over one snapshot, defaulting to an all-synthetic batch."""
    bound = snapshot if snapshot is not None else _snapshot()
    default_sources = [SOURCE] if synthetic + real > 0 else []
    return PriceOriginSummary(
        schema_version=schema_version,
        snapshot=bound,
        consumed_rows=synthetic + real + unknown,
        counts=PriceOriginCounts(
            synthetic=synthetic,
            real=real,
            unknown=unknown,
            unknown_reasons=cast("dict[UnknownReason, int]", unknown_reasons or {}),
        ),
        sources=default_sources if sources is None else sources,
        envelope_sha256="b" * 64,
    )


def _manifest(scope: RunScope, **overrides: Any) -> RunManifest:
    """A manifest for ``scope``, with every field explicit so overrides are visible."""
    payload: dict[str, Any] = {
        "run_id": scope.run_id(),
        "run_dir": "20261107T120000Z-000000000000",
        "created_at": TS,
        "finalized_at": TS,
        "protocol": scope.protocol,
        "start_date": scope.start_date,
        "end_date": scope.end_date,
        "universe": list(scope.universe),
        "universe_basis": universe_basis_for(scope.universe),
        "strategies": list(scope.strategies),
        "cost_regimes": scope.cost_regimes,
        "initial_cash": scope.initial_cash,
        "parameters": dict(scope.parameters),
        "costs": [cost.model_dump() for cost in scope.costs],
        "data_provenance": "unknown",
        "input_snapshots": [],
        "price_origin_summaries": [],
    }
    payload.update(overrides)
    return RunManifest.model_validate(payload)


def _finalise(tmp_path: Path, *, scope: RunScope, **overrides: Any) -> FinalisedRun:
    kwargs: dict[str, Any] = {"code": CodeIdentity(commit="a" * 40), "root": tmp_path, "now": TS}
    kwargs.update(overrides)
    return finalise_run(_result(), scope=scope, **kwargs)


def _manifest_json(directory: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 1. Vocabulary: protocol, closed reason set, source grammar (D2/D4)
# ---------------------------------------------------------------------------


def test_writers_mint_v4_and_v3_stays_readable() -> None:
    """Reader support landed first (decision D4); the writer now mints v4.

    Falsified by: emitting v4 evidence before its reader exists, dropping v3 from the
    readable set, or reusing the v3 literal for the strengthened derivation (two
    different evidence formats would share an address).
    """
    assert RUN_PROTOCOL == RUN_PROTOCOL_V4 == "equity-run-manifest/4"
    assert RUN_PROTOCOL_V3 == "equity-run-manifest/3"
    assert RunScope.model_fields["protocol"].default == RUN_PROTOCOL  # new scopes are v4

    # The protocol is a scope field, so the bump mints different scope IDs by design.
    assert _scope().run_id() != _scope(protocol=RUN_PROTOCOL_V3).run_id()


def test_unknown_reasons_are_the_shared_closed_set() -> None:
    """The literal, the shared tuple and the model all name the same five reasons.

    Falsified by: adding a reason to the read seam (which cannot import this module)
    without extending the manifest vocabulary, so a summary would have no legal key.
    """
    assert set(get_args(UnknownReason)) == set(PRICE_ORIGIN_UNKNOWN_REASONS)
    assert len(PRICE_ORIGIN_UNKNOWN_REASONS) == 5


@pytest.mark.parametrize("identifier", [SOURCE, "generator:demo_v1@1", "provider:nasdaq_basic@12", "provider:x9_@9999"])
def test_price_source_grammar_accepts_d2_identifiers(identifier: str) -> None:
    """A recorded ``price_source`` is a non-secret, namespaced producer identity."""
    assert _summary(sources=[identifier]).sources == [identifier]


@pytest.mark.parametrize(
    "identifier",
    [
        "synthetic",  # a bare kind, not an identity
        "generator:synthetic_demo_seed",  # no revision
        "generator:ab@1",  # id shorter than the 3-character minimum
        "generator:Synthetic_Demo_Seed@1",  # upper case
        "generator:synthetic_demo_seed@0",  # revision must start at 1
        "generator:synthetic_demo_seed@10000",  # revision wider than 4 digits
        "provider:/etc/passwd@1",  # absolute path
        "provider:nasdaq basic@1",  # whitespace
        "provider:nasdaq_basic@1?token=abc",  # query token
        "https://vendor.example/prices@1",  # URL
    ],
)
def test_price_source_grammar_refuses_non_d2_identifiers(identifier: str) -> None:
    """Evidence can never carry a path, a URL, a token or a bare label as a source.

    Falsified by: accepting an arbitrary string as ``price_source``, which is how a
    credential or a machine-specific path would end up in a manifest.
    """
    with pytest.raises(ValidationError, match="must match"):
        _summary(sources=[identifier])
    assert PRICE_SOURCE_PATTERN.startswith("^(generator|provider):")


def test_sources_are_unique_and_canonically_ordered() -> None:
    """``sources`` is a set-like list: no duplicates, sorted for a stable encoding."""
    assert _summary(sources=["provider:nasdaq_basic@1", SOURCE]).sources == sorted([SOURCE, "provider:nasdaq_basic@1"])
    with pytest.raises(ValidationError, match="must be unique"):
        _summary(sources=[SOURCE, SOURCE])


# ---------------------------------------------------------------------------
# 2. Summary consistency rules (ADR-0027 §4.4)
# ---------------------------------------------------------------------------


def test_counts_must_cover_exactly_the_consumed_rows() -> None:
    """``synthetic + real + unknown == consumed_rows``: no partial coverage.

    Falsified by: recording counts for only the terminal marks, so warm-up or other
    markets stay silently unclassified while the summary claims the snapshot.
    """
    assert _summary(synthetic=2, real=1, unknown=0).consumed_rows == 3
    with pytest.raises(ValidationError, match="must cover exactly the consumed rows"):
        PriceOriginSummary(
            snapshot=_snapshot(),
            consumed_rows=5,
            counts=PriceOriginCounts(synthetic=2, real=0, unknown=0),
            sources=[SOURCE],
            envelope_sha256="b" * 64,
        )


def test_unknown_reason_counts_must_add_up_to_the_unknown_rows() -> None:
    """Every unknown row names exactly one closed-set reason, and no other case does.

    Falsified by: a summary that reports unknown rows with no reason, reasons that add
    up to a different total, a reason outside the shared set, or a zero-count reason
    that makes "no reasons" and "reasons that cancel out" indistinguishable.
    """
    summary = _summary(synthetic=2, unknown=2, unknown_reasons={"missing_envelope": 1, "unsupported_version": 1})
    assert summary.counts.unknown_reasons == {"missing_envelope": 1, "unsupported_version": 1}

    with pytest.raises(ValidationError, match="must add up to the unknown row count"):
        _summary(synthetic=2, unknown=2, unknown_reasons={"missing_envelope": 1})
    with pytest.raises(ValidationError, match="must not record unknown_reasons"):
        _summary(synthetic=2, unknown=0, unknown_reasons={"missing_envelope": 1})
    with pytest.raises(ValidationError, match="keys must be one of"):
        _summary(synthetic=0, unknown=1, unknown_reasons={"provenance_looks_old": 1})
    # ``empty_scope`` is derived for a zero-row scope, never counted per row.
    with pytest.raises(ValidationError, match="must not record 'empty_scope'"):
        _summary(synthetic=1, unknown=1, unknown_reasons={"empty_scope": 1})
    with pytest.raises(ValidationError, match="counts must be positive"):
        _summary(synthetic=1, unknown=1, unknown_reasons={"missing_envelope": 0})


def test_a_supported_count_must_name_its_source() -> None:
    """A synthetic/real label without a source identifier is an incomplete envelope.

    Falsified by: emitting the label while dropping the producer identity, which is the
    "no fallback label" rule of ADR-0027 §2 (a label alone proves nothing).
    """
    with pytest.raises(ValidationError, match="must name at least one price_source"):
        _summary(sources=[])


def test_a_summary_describes_only_a_price_snapshot() -> None:
    """Action, coverage and fixture snapshots carry no price rows, so they never bind.

    Falsified by: allowing a summary to point at ``action_coverage:<market>``, which
    would let action provenance stand in for price origin (explicitly rejected).
    """
    assert is_price_snapshot_name(f"{PRICE_SNAPSHOT_NAME_PREFIX}{MARKET}")
    assert not is_price_snapshot_name(PRICE_SNAPSHOT_NAME_PREFIX)
    assert not is_price_snapshot_name(f"action_coverage:{MARKET}")
    assert not is_price_snapshot_name("fixture:ohlcv")

    with pytest.raises(ValidationError, match="must describe a price snapshot"):
        _summary(snapshot=_snapshot(f"action_coverage:{MARKET}"))


def test_a_future_summary_version_is_readable_but_not_interpreted() -> None:
    """A v1-shaped summary with a future ``schema_version`` parses but derives unknown.

    Falsified by: refusing a well-formed summary that merely carries a newer version
    number (forward compatibility), or by interpreting it with version-1 rules. A
    structurally different future encoding is a different case: ``extra="forbid"``
    refuses it as invalid manifest evidence instead of guessing at its meaning.
    """
    future = _summary(schema_version=2)
    assert future.schema_version == 2
    assert price_origin_aggregate([future], [future.snapshot]) == ("unknown", ("unsupported_version",))
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs are not permitted"):
        PriceOriginSummary.model_validate(
            {
                **future.model_dump(),
                "schema_version": 2,
                "rows": [{"identity": "us_equity|XNAS|AAA|2024-01-02", "origin": "synthetic"}],
            }
        )


# ---------------------------------------------------------------------------
# 3. Derivation (ADR-0027 §4.5): unknown dominates, mixed is aggregate-only
# ---------------------------------------------------------------------------


def test_all_synthetic_and_all_real_derive_their_labels() -> None:
    """Complete, snapshot-bound summaries derive ``synthetic`` and ``real``.

    Falsified by: accepting the label from the manifest instead of re-deriving it, or by
    requiring anything beyond the summary to reach a supported label.
    """
    synthetic = _summary(synthetic=3)
    real = _summary(synthetic=0, real=3, sources=["provider:nasdaq_basic@1"])
    assert price_origin_aggregate([synthetic], [synthetic.snapshot]) == ("synthetic", ())
    assert price_origin_aggregate([real], [real.snapshot]) == ("real", ())


def test_synthetic_and_real_together_are_mixed_never_a_row_kind() -> None:
    """A complete mix is the aggregate ``mixed``; no row ever carries that kind.

    Falsified by: emitting ``mixed`` as a row value, or by calling a mixed frame
    ``synthetic`` because the majority of rows were.
    """
    first = _summary(synthetic=2)
    second = _summary(snapshot=_snapshot(f"{PRICE_SNAPSHOT_NAME_PREFIX}cn_ashare"), synthetic=0, real=2, sources=["provider:nasdaq_basic@1"])
    assert price_origin_aggregate([first, second], [first.snapshot, second.snapshot]) == ("mixed", ())


def test_unknown_rows_dominate_a_known_mix() -> None:
    """One unproved row makes the whole aggregate ``unknown``, with reasons visible.

    Falsified by: majority voting over row provenance, which would let known synthetic
    and known real rows outvote a row whose origin nobody can prove.
    """
    pulled_in = _summary(
        snapshot=_snapshot(f"{PRICE_SNAPSHOT_NAME_PREFIX}cn_ashare"),
        synthetic=2,
        unknown=1,
        unknown_reasons={"missing_envelope": 1},
    )
    assert price_origin_aggregate([_summary(), pulled_in], [_summary().snapshot, pulled_in.snapshot]) == ("unknown", ("missing_envelope",))


def test_an_empty_scope_is_unknown_even_with_a_cycle_free_summary() -> None:
    """Zero consumed rows is ``unknown``, never an all-synthetic empty claim.

    Falsified by: treating "no unknown rows" as "all synthetic rows" for an empty scope.
    """
    empty = _summary(synthetic=0)
    assert empty.consumed_rows == 0
    assert price_origin_aggregate([empty], [empty.snapshot]) == ("unknown", ("empty_scope",))


def test_snapshots_without_summaries_and_missing_summaries_stay_unknown() -> None:
    """Snapshots alone never derive a label; every price snapshot needs its summary.

    Falsified by: deriving provenance from snapshot presence (the ADR-0025 trap), or by
    ignoring a price snapshot that no summary covers.
    """
    snapshot = _snapshot()
    assert price_origin_aggregate([], [snapshot]) == ("unknown", ("missing_envelope",))
    assert price_origin_aggregate([], []) == ("unknown", ("untracked_input",))

    other = _snapshot(f"{PRICE_SNAPSHOT_NAME_PREFIX}cn_ashare")
    covered = _summary()
    assert price_origin_aggregate([covered], [covered.snapshot, other]) == ("unknown", ("missing_envelope",))


def test_a_summary_bound_to_an_unrecorded_snapshot_is_unknown() -> None:
    """The binding is (name, digest, source, version), not a name alone.

    Two distinct ways in: a summary whose snapshot reference the manifest never recorded,
    and a summary describing a different reading of a recorded name. Both are refused
    because a claim may only describe snapshots the manifest actually carries.

    Falsified by: binding on the name only, or by accepting a summary for a snapshot that
    is not in ``input_snapshots`` at all — including the case where every *recorded* price
    snapshot is fully described and the extra summary would otherwise lift the aggregate.
    """
    recorded = _snapshot(version="4")
    stale = _summary(snapshot=_snapshot(version="5"))
    assert price_origin_aggregate([stale], [recorded]) == ("unknown", ("incomplete_binding",))

    phantom = _summary(snapshot=_snapshot(f"{PRICE_SNAPSHOT_NAME_PREFIX}cn_ashare"))
    assert price_origin_aggregate([_summary(snapshot=recorded), phantom], [recorded]) == ("unknown", ("incomplete_binding",))


def test_two_readings_of_one_price_snapshot_name_are_unknown() -> None:
    """Coverage is by exact reference: one summary cannot cover two different readings.

    A market's price table is read once per load, but the manifest accepts any snapshot
    list, so two ``price_marks:<market>`` entries with different versions/hashes are
    representable. Tracking coverage by name would let the second reading inherit the
    summary that described other bytes, and the aggregate would claim ``synthetic``.

    Falsified by: a described-names set instead of per-reference matching.
    """
    first, second = _snapshot(version="4"), _snapshot(version="5")
    assert price_origin_aggregate([_summary(snapshot=first)], [first, second]) == ("unknown", ("incomplete_binding",))
    # The *same* reference twice is one snapshot, so the one summary does cover both.
    assert price_origin_aggregate([_summary(snapshot=first)], [first, first]) == ("synthetic", ())


# ---------------------------------------------------------------------------
# 4. Manifest: summaries are v4-only, and the claim must match the derivation
# ---------------------------------------------------------------------------


def test_a_v3_manifest_may_not_carry_summaries() -> None:
    """v3 evidence is parsed under its own rules and never acquires v4 fields.

    Falsified by: allowing a stored v3 run to gain a summary, which would be the
    retroactive relabelling ADR-0027 §6 forbids.
    """
    with pytest.raises(ValidationError, match="require protocol 'equity-run-manifest/4'"):
        _manifest(_scope(protocol=RUN_PROTOCOL_V3), price_origin_summaries=[_summary()], input_snapshots=[_snapshot()])


def test_a_v4_manifest_derives_its_provenance_from_the_summaries() -> None:
    """A complete summary lifts the run to ``synthetic``; a v4 run without one stays unknown.

    Falsified by: hardcoding ``unknown`` in the writer path, or accepting a v4 manifest
    whose claim the evidence does not support.
    """
    scope = _scope(protocol=RUN_PROTOCOL_V4)
    snapshot = _snapshot()
    supported = _manifest(scope, input_snapshots=[snapshot], price_origin_summaries=[_summary(snapshot=snapshot)], data_provenance="synthetic")
    assert supported.data_provenance == "synthetic"

    bare = _manifest(scope, input_snapshots=[snapshot])
    assert bare.data_provenance == "unknown"
    assert price_origin_aggregate(bare.price_origin_summaries, bare.input_snapshots)[0] == "unknown"


def test_an_unsupported_manifest_protocol_is_refused() -> None:
    """Only v3 and v4 are interpretable; a future protocol is refused, not guessed.

    Reading an unknown format as if it were v3 would let a future manifest's
    self-asserted claim pass a rule that format was never checked against. The
    forward-compatible "readable as unknown" rule of ADR-0027 §4.5 covers the row
    envelope, not the run protocol.

    Falsified by: an ``else`` branch that treats every non-v4 protocol as v3.
    """
    future = "equity-run-manifest/5"
    with pytest.raises(ValidationError, match="unsupported evidence protocol"):
        _manifest(_scope(protocol=future), input_snapshots=[_snapshot()], data_provenance="synthetic")
    with pytest.raises(ValidationError, match="unsupported evidence protocol"):
        _manifest(_scope(protocol=future))


def test_a_v4_claim_without_summaries_is_refused_not_downgraded() -> None:
    """Under v4 a non-unknown claim with snapshots but no summaries is an error.

    Falsified by: silently downgrading an over-reaching v4 claim, which would publish
    a successful run whose recorded label nobody can audit.
    """
    with pytest.raises(ValidationError, match="requires complete, snapshot-bound"):
        _manifest(_scope(protocol=RUN_PROTOCOL_V4), input_snapshots=[_snapshot()], data_provenance="synthetic")
    with pytest.raises(ValidationError, match="requires complete, snapshot-bound"):
        _manifest(_scope(protocol=RUN_PROTOCOL_V4), data_provenance="real")


def test_stored_v3_evidence_keeps_its_own_rules() -> None:
    """v3 keeps ADR-0025's rule and is never retro-invalidated by the v4 contract.

    ADR-0027 §5 requires explicit parsing of existing v3 evidence "under its own
    rules": a v3 manifest must not start failing validation because a later protocol
    strengthened the derivation. So v3 stays self-asserted-with-snapshots and can
    never carry a summary, while v4 is the derived form.

    Falsified by: applying the v4 derivation to v3, which would silently drop stored
    runs from every reader projection — including runs whose recorded claim was legal
    when it was written.
    """
    legacy = _manifest(_scope(protocol=RUN_PROTOCOL_V3), input_snapshots=[_snapshot()], data_provenance="synthetic")
    assert legacy.data_provenance == "synthetic"  # parsed under v3 rules, not re-derived
    assert legacy.price_origin_summaries == []

    # v3 still refuses a claim with no snapshot evidence at all (ADR-0025 Decision 3).
    with pytest.raises(ValidationError, match="requires input_snapshots evidence under protocol"):
        _manifest(_scope(protocol=RUN_PROTOCOL_V3), data_provenance="real")


@pytest.mark.parametrize(
    ("summaries", "claimed"),
    [
        ("synthetic", "real"),
        ("synthetic", "mixed"),
        ("synthetic", "unknown"),
        ("mixed", "synthetic"),
        ("unknown", "synthetic"),
    ],
)
def test_a_claim_that_disagrees_with_the_summaries_is_refused(summaries: str, claimed: str) -> None:
    """Only the re-derived value is accepted — including refusing an under-claim.

    Falsified by: checking one direction only, so an under-claim (evidence says
    synthetic, manifest says unknown) could sit next to evidence that contradicts it.
    """
    real_source = ["provider:nasdaq_basic@1"]
    built = {
        "synthetic": [_summary(synthetic=2)],
        "mixed": [_summary(synthetic=1, real=1, sources=real_source)],
        "unknown": [_summary(synthetic=1, unknown=1, unknown_reasons={"missing_envelope": 1})],
    }[summaries]
    snapshots = [entry.snapshot for entry in built]
    with pytest.raises(ValidationError, match="is refused"):
        _manifest(_scope(protocol=RUN_PROTOCOL_V4), input_snapshots=snapshots, price_origin_summaries=built, data_provenance=claimed)


def test_one_summary_per_snapshot() -> None:
    """Two summaries for one snapshot could disagree with no rule to choose.

    Falsified by: storing a per-market summary list that repeats a snapshot name.
    """
    snapshot = _snapshot()
    with pytest.raises(ValidationError, match="at most one summary per price snapshot"):
        _manifest(
            _scope(protocol=RUN_PROTOCOL_V4),
            input_snapshots=[snapshot],
            price_origin_summaries=[_summary(snapshot=snapshot), _summary(snapshot=snapshot)],
            data_provenance="synthetic",
        )


# ---------------------------------------------------------------------------
# 5. On-disk compatibility and the reader surface
# ---------------------------------------------------------------------------


def test_a_written_v3_manifest_gains_no_v4_key(tmp_path: Path) -> None:
    """A v3 manifest written now is byte-shaped like the v3 format readers know.

    Falsified by: dumping the new model wholesale, which would add
    ``price_origin_summaries: []`` to every v3 file and leak a v4 field into v3
    evidence.
    """
    run = _finalise(tmp_path, scope=_scope())
    payload = _manifest_json(run.directory)
    assert "price_origin_summaries" not in payload
    assert payload["protocol"] == RUN_PROTOCOL
    assert payload["data_provenance"] == "unknown"
    assert validate_run_directory(run.directory).finalized


def test_a_written_v4_run_round_trips_and_is_read_by_the_view(tmp_path: Path) -> None:
    """A v4 run with a complete summary validates and is listed with its label.

    Falsified by: writing a summary the reader cannot re-validate, or by a run listing
    that reports a value different from the manifest's derived one.
    """
    snapshot = _snapshot()
    run = _finalise(
        tmp_path,
        scope=_scope(protocol=RUN_PROTOCOL_V4),
        input_snapshots=[snapshot],
        price_origin_summaries=[_summary(snapshot=snapshot)],
        data_provenance="synthetic",
    )

    payload = _manifest_json(run.directory)
    assert payload["protocol"] == RUN_PROTOCOL_V4
    assert payload["price_origin_summaries"][0]["counts"]["synthetic"] == 3
    assert payload["data_provenance"] == "synthetic"

    verdict = validate_run_directory(run.directory)
    assert verdict.finalized, verdict.reasons
    assert verdict.manifest is not None
    assert verdict.manifest.data_provenance == "synthetic"

    assert [verdict.directory for verdict in validated_runs(tmp_path)] == [str(run.directory)]
    latest = latest_success_by_slug(tmp_path)
    assert latest["stub__zero"].manifest.data_provenance == "synthetic"


def test_a_v3_run_still_reads_alongside_a_v4_run(tmp_path: Path) -> None:
    """v3 and v4 evidence coexist; neither reader path upgrades the other.

    Falsified by: a protocol branch that stops listing v3 runs, or a v4 run that makes
    an older v3 run's recorded ``unknown`` look like a defect or get rewritten.
    """
    v3 = _finalise(tmp_path / "v3", scope=_scope())
    snapshot = _snapshot()
    v4 = _finalise(
        tmp_path / "v4",
        scope=_scope(protocol=RUN_PROTOCOL_V4),
        input_snapshots=[snapshot],
        price_origin_summaries=[_summary(snapshot=snapshot)],
        data_provenance="synthetic",
    )

    assert _manifest_json(v3.directory)["data_provenance"] == "unknown"
    assert [verdict.manifest.protocol for verdict in validated_runs(tmp_path / "v3") if verdict.manifest] == [RUN_PROTOCOL]
    assert [verdict.manifest.protocol for verdict in validated_runs(tmp_path / "v4") if verdict.manifest] == [RUN_PROTOCOL_V4]
    assert _manifest_json(v4.directory)["data_provenance"] == "synthetic"
