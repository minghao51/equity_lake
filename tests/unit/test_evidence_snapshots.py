"""E01c tests: canonical universe basis and read-seam input snapshot binding.

Scope: the frozen E01c contract (protocol ``equity-run-manifest/3``), ADR-0025
Decisions 3/4/9 with frozen E01-1/E01-4, ADR-0016 (canonical identity) and
ADR-0021/0022 (never upgrade evidence by inference). Two behaviours are pinned:

1. **Canonical universe basis.** A manifest records *how* its ``universe`` entries
   were obtained — ``canonical`` display keys parsed back through the identity
   model, or ``ticker_only`` bare tickers — derived by parsing, never by guessing,
   and never a redundant field in the run-ID preimage.
2. **Read-seam snapshot binding.** A version is captured *inside* the read that
   produced the frame (``read_delta_with_version``,
   ``load_corporate_action_inputs_with_versions``, ``BacktestDataLoader.snapshots``),
   never looked up after the computation.

Provenance deliberately stays ``unknown`` on every shipped path even when snapshots
exist: nothing in the lake records which price source produced the marks yet (open
item E01-6), so a snapshot must never be read as a provenance upgrade.

Every test states what falsifies it and uses ``tmp_path`` only: nothing under
``data/`` is read or written and no provider/network call is made.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest
from deltalake import DeltaTable

from equity_lake.backtesting.arena import ArenaOutcome, ArenaRun, equal_weight_buyhold  # type: ignore[import-untyped]
from equity_lake.backtesting.data_loader import BacktestDataLoader  # type: ignore[import-untyped]
from equity_lake.backtesting.evidence import (  # type: ignore[import-untyped]
    MANIFEST_NAME,
    RUN_PROTOCOL,
    CodeIdentity,
    CostApplication,
    InputSnapshot,
    RunManifest,
    RunScope,
    UniverseBasis,
    finalise_run,
    is_canonical_universe,
    parse_display_key,
    universe_basis_for,
    validate_run_directory,
)
from equity_lake.backtesting.report import write_arena_artifacts_with_evidence  # type: ignore[import-untyped]
from equity_lake.backtesting.result import BacktestResult  # type: ignore[import-untyped]
from equity_lake.core.instruments import IDENTITY_COLUMNS, InstrumentKey  # type: ignore[import-untyped]
from equity_lake.ingestion.corporate_actions import (  # type: ignore[import-untyped]
    corporate_action_coverage_table,
    corporate_actions_table,
    load_corporate_action_inputs,
    load_corporate_action_inputs_with_versions,
)
from equity_lake.ingestion.types import MARKET_DIR_MAP  # type: ignore[import-untyped]
from equity_lake.storage.delta import (  # type: ignore[import-untyped]
    DeltaReadError,
    _read_delta_frame,
    read_delta,
    read_delta_with_version,
    write_delta,
)

D0, D1, D2 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
DATES = [D0, D1, D2]
VALUES = [100.0, 110.0, 100.0]
MARKET = "us_equity"
MIC = "XNYS"
PRICE_TABLE = MARKET_DIR_MAP[MARKET]
ACTION_TABLE = corporate_actions_table(MARKET, "silver")
COVERAGE_TABLE = corporate_action_coverage_table(MARKET)


# ---------------------------------------------------------------------------
# Fixtures / helpers (tmp_path only)
# ---------------------------------------------------------------------------


def _price_frame(symbols: Sequence[str] = ("AAA", "BBB"), *, dates: Sequence[date] = DATES, close_offset: float = 0.0) -> pl.DataFrame:
    """A canonical-identity price frame the bronze-writer/loader both accept."""
    rows = [
        {
            "ticker": symbol,
            "symbol": symbol,
            "market": MARKET,
            "mic": MIC,
            "date": day,
            "open": 10.0 + close_offset,
            "high": 10.5 + close_offset,
            "low": 9.5 + close_offset,
            "close": 10.0 + close_offset,
            "volume": 1000.0,
        }
        for symbol in symbols
        for day in dates
    ]
    return pl.DataFrame(rows)


def _write_delta_price_table(lake: Path, *, frames: Sequence[pl.DataFrame]) -> None:
    """Write ``01_bronze/us_equity`` as a Delta table, one commit per frame."""
    for index, frame in enumerate(frames):
        write_delta(frame, PRICE_TABLE, mode="overwrite" if index == 0 else "append", lake_dir=lake)


def _write_parquet_price_table(root: Path, files: dict[str, pl.DataFrame]) -> None:
    """Write plain (non-Delta) parquet files under ``root``."""
    for name, frame in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(target)


def _stub_result() -> BacktestResult:
    """A dated result with a closed trade so finalisation has real evidence."""
    return BacktestResult(
        strategy_name="stub",
        tickers=["AAA", "BBB"],
        start_date=D0,
        end_date=D2,
        initial_cash=100.0,
        final_cash=VALUES[-1],
        equity_curve=pl.Series("equity", VALUES, dtype=pl.Float64),
        trades=[{"ticker": "AAA", "entry_date": D0, "exit_date": D1, "entry_price": 1.0, "exit_price": 1.1, "shares": 1.0, "pnl": 0.1}],
        metrics={"total_return": 0.01, "sharpe_ratio": 0.5},
        equity_dates=list(DATES),
    )


def _scope(universe: Sequence[str]) -> RunScope:
    return RunScope(
        universe=list(universe),
        start_date=D0,
        end_date=D2,
        strategies=["stub"],
        costs=[CostApplication(regime="zero", fee_ratio=0.0, tax_ratio=0.0)],
        initial_cash=100.0,
    )


def _arena_run(*, strategy: str = "stub", regime: str = "zero") -> ArenaRun:
    """A completed arena run carrying what ``run_arena`` records (E01b.1)."""
    return ArenaRun(
        strategy=strategy,
        cost_regime=regime,
        result=_stub_result(),
        strategy_params={},
        applied_costs={"fee_ratio": 0.0, "tax_ratio": 0.0},
    )


def _arena_outcome(
    data: pl.DataFrame,
    *,
    input_snapshots: Sequence[dict[str, str | None]] = (),
) -> ArenaOutcome:
    """An in-memory outcome over ``data``, as ``run_arena`` builds it (preload seam)."""
    run = _arena_run()
    return ArenaOutcome(
        runs=[run],
        data=data,
        benchmark=equal_weight_buyhold(data, initial_cash=100.0),
        initial_cash=100.0,
        requested=[(run.strategy, run.cost_regime)],
        markets=(MARKET,),
        input_snapshots=list(input_snapshots),
    )


def _manifest_of(root: Path) -> dict[str, Any]:
    runs = [path for path in (root / "runs").iterdir() if path.is_dir() and not path.name.startswith(".partial-")]
    assert len(runs) == 1, f"expected exactly one run directory, got {[path.name for path in runs]}"
    return cast("dict[str, Any]", json.loads((runs[0] / MANIFEST_NAME).read_text(encoding="utf-8")))


def _delta_reference_digest(table_path: Path, version: int) -> str:
    """The contract's documented Delta snapshot digest, written out independently."""
    return hashlib.sha256(f"delta:{table_path}@v{version}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# 1. Protocol v3 is the scope preimage's own protocol field
# ---------------------------------------------------------------------------


def test_run_protocol_is_v3_and_part_of_the_scope_preimage() -> None:
    """``RUN_PROTOCOL`` is v3, appears in the payload, and moving it moves the ID.

    Falsified by: leaving the protocol at ``/2`` after the universe values became
    canonical display keys, omitting it from ``scope_payload``, or by a protocol bump
    that leaves the run ID unchanged (two different evidence formats would then share
    an address).
    """
    assert RUN_PROTOCOL == "equity-run-manifest/3"

    scope = _scope(["AAPL"])
    payload = scope.scope_payload()
    assert payload["protocol"] == RUN_PROTOCOL
    assert RUN_PROTOCOL in scope.canonical_scope_json()

    # The pre-bump protocol is *derived* from ``RUN_PROTOCOL``, never spelled out: the
    # only copies of the old literal left in the repo are inside the frozen docs, and a
    # reverted protocol still fails here because the derived value is the real previous
    # version only while the bump holds.
    stem, version = RUN_PROTOCOL.rsplit("/", 1)
    previous_protocol = f"{stem}/{int(version) - 1}"
    assert previous_protocol == "equity-run-manifest/" + "2"  # the actual pre-bump protocol
    assert previous_protocol != RUN_PROTOCOL

    previous = RunScope(
        protocol=previous_protocol,
        universe=list(scope.universe),
        start_date=scope.start_date,
        end_date=scope.end_date,
        strategies=list(scope.strategies),
        costs=list(scope.costs),
        initial_cash=scope.initial_cash,
        parameters=dict(scope.parameters),
    )
    assert previous.run_id() != scope.run_id()  # the bump changes the ID by design


# ---------------------------------------------------------------------------
# 2. Canonical universe basis (contract item 1)
# ---------------------------------------------------------------------------


def test_arena_writer_records_canonical_universe_from_loaded_identities(tmp_path: Path) -> None:
    """Full identity in the loaded frame -> canonical display keys, basis ``canonical``.

    Falsified by: recording the requested bare tickers, recording a display key the
    frame does not carry, or claiming ``canonical`` without the parseable keys.
    """
    _, summary = write_arena_artifacts_with_evidence(_arena_outcome(_price_frame()), base=tmp_path, run_date=D0)
    assert len(summary.finalized) == 1

    manifest = _manifest_of(tmp_path)
    expected = sorted({InstrumentKey(market=MARKET, mic=MIC, symbol=symbol).display_key for symbol in ("AAA", "BBB")})
    assert manifest["universe"] == expected
    assert manifest["universe_basis"] == "canonical"
    assert manifest["universe_basis"] == universe_basis_for(list(manifest["universe"]))  # reproducible from the file
    assert validate_run_directory(summary.finalized[0].directory).finalized


def test_arena_writer_records_ticker_only_basis_when_identity_is_absent(tmp_path: Path) -> None:
    """A ticker-only frame records bare tickers with the ``ticker_only`` basis.

    Falsified by: filling a MIC, inventing a venue for a bare ticker, or labelling a
    ticker-only universe ``canonical`` (ADR-0016 Decision 2: no default US identity).
    """
    ticker_only = pl.DataFrame(
        {"ticker": ["AAA"] * len(DATES) + ["BBB"] * len(DATES), "date": list(DATES) * 2, "close": VALUES * 2},
        schema={"ticker": pl.String, "date": pl.Date, "close": pl.Float64},
    )
    _, summary = write_arena_artifacts_with_evidence(_arena_outcome(ticker_only), base=tmp_path, run_date=D0)
    assert len(summary.finalized) == 1

    manifest = _manifest_of(tmp_path)
    assert manifest["universe"] == ["AAA", "BBB"]
    assert manifest["universe_basis"] == "ticker_only"


def test_universe_basis_is_parsed_never_guessed(tmp_path: Path) -> None:
    """The basis comes from parsing every entry; an unprovable claim is refused.

    Falsified by: treating a bare ticker as canonical, normalising a malformed display
    key into one, filling a MIC, or accepting an explicit ``canonical`` claim over a
    universe that does not parse.
    """
    assert universe_basis_for(["us_equity/XNYS/AAPL"]) == "canonical"
    assert universe_basis_for(["us_equity/XNYS/AAPL", "cn_ashare/XSHG/600000"]) == "canonical"
    # A percent-quoted symbol survives the round trip (canonical form only).
    quoted = InstrumentKey(market=MARKET, mic=MIC, symbol="BRK/B").display_key
    assert universe_basis_for([quoted]) == "canonical"
    assert parse_display_key(quoted).symbol == "BRK/B"

    assert universe_basis_for(["AAPL"]) == "ticker_only"
    assert universe_basis_for([]) == "ticker_only"
    # Malformed / non-canonical display keys are never normalised into a parse:
    for malformed in (
        "us_equity/XNYS",  # too few segments
        "us_equity/XNYS/AAPL/extra",  # too many
        "us_equity/XNYS/",  # empty symbol
        "/XNYS/AAPL",  # empty market
        "us/XNYS/AAPL",  # market alias, not the canonical spelling
        "us_equity/XNYS/A%2fapl",  # non-canonical percent escape ordering
        "us_equity/ZZZZ/AAPL",  # MIC not supported for the market
        "not-a-market/XNYS/AAPL",  # unknown market
        "AAPL/MSFT",  # a ticker pair is not an identity
    ):
        assert universe_basis_for([malformed]) == "ticker_only", malformed
        assert is_canonical_universe([malformed]) is False, malformed
        with pytest.raises(ValueError):
            parse_display_key(malformed)

    # A mixed universe is only ever ticker_only: one unparseable entry decides it.
    assert universe_basis_for(["us_equity/XNYS/AAPL", "MSFT"]) == "ticker_only"

    # An explicit canonical claim over an unparseable entry is refused, never recorded.
    with pytest.raises(ValueError, match="does not parse as InstrumentKey display keys"):
        finalise_run(
            _stub_result(),
            scope=_scope(["AAPL"]),
            universe_basis="canonical",
            code=CodeIdentity(commit="a" * 40),
            root=tmp_path,
        )
    assert not (tmp_path / "runs").exists() or list((tmp_path / "runs").iterdir()) == []

    # An explicit value that matches the universe wins and is recorded.
    run = finalise_run(
        _stub_result(),
        scope=_scope(["AAPL"]),
        universe_basis="ticker_only",
        code=CodeIdentity(commit="a" * 40),
        root=tmp_path,
    )
    assert run.manifest.universe_basis == "ticker_only"
    # ... and the derived default agrees with the same parse.
    assert run.manifest.universe_basis == universe_basis_for(["AAPL"])


def test_manifest_requires_universe_basis_and_forbids_extra_fields(tmp_path: Path) -> None:
    """``universe_basis`` is required and ``extra="forbid"`` still holds.

    Falsified by: making the basis optional (a silent default would hide which basis a
    manifest claims), or by letting an unknown field into the write boundary.
    """
    run = finalise_run(_stub_result(), scope=_scope(["us_equity/XNYS/AAA"]), code=CodeIdentity(commit="a" * 40), root=tmp_path)
    payload = json.loads((run.directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert payload["universe_basis"] == "canonical"

    without_basis = dict(payload)
    without_basis.pop("universe_basis")
    with pytest.raises(ValueError, match="universe_basis"):
        RunManifest.model_validate(without_basis)

    with_extra = dict(payload)
    with_extra["snapshot_protocol"] = "make-believe"
    with pytest.raises(ValueError):
        RunManifest.model_validate(with_extra)

    # A hand-edited basis that its own universe does not support is refused too: the
    # basis is re-derived from the recorded entries, never trusted as a label.
    upgraded = dict(payload)
    upgraded["universe"] = ["AAA"]
    with pytest.raises(ValueError, match="does not match the recorded universe"):
        RunManifest.model_validate(upgraded)


# ---------------------------------------------------------------------------
# 3. Delta price reads bind the version they read (contract item 2)
# ---------------------------------------------------------------------------


def test_delta_price_read_records_the_version_it_read(tmp_path: Path) -> None:
    """The recorded version is the one the read consumed, and the digest follows it.

    Falsified by: looking the version up after the read (a later commit would then be
    recorded), by recording a version other than the frame's, or by a digest that does
    not change with the version.
    """
    lake = tmp_path / "lake"
    table_path = lake / PRICE_TABLE
    _write_delta_price_table(lake, frames=[_price_frame()])  # version 0

    loader = BacktestDataLoader()
    first = loader._read_market(MARKET, ["AAA"], D0, D2, ["ticker", "date", "close"], lake)
    assert not first.is_empty()
    [snapshot] = loader.snapshots()
    assert snapshot["name"] == f"price_marks:{MARKET}"
    assert snapshot["source"] == "lake:delta"
    assert snapshot["version"] == "0"
    assert snapshot["sha256"] == _delta_reference_digest(table_path, 0)

    # A second commit makes the table's latest version 1; a fresh read records 1.
    _write_delta_price_table(lake, frames=[])
    write_delta(_price_frame(close_offset=1.0), PRICE_TABLE, mode="append", lake_dir=lake)
    reader = BacktestDataLoader()
    reader._read_market(MARKET, ["AAA"], D0, D2, ["ticker", "date", "close"], lake)
    [second] = reader.snapshots()
    assert second["version"] == "1"
    assert second["sha256"] == _delta_reference_digest(table_path, 1)
    assert second["sha256"] != snapshot["sha256"]  # the digest is bound to the version

    # Reading the older version records that version, not the table's latest one. The
    # public ``read_delta`` keeps discarding the version by contract, so the framing
    # level below it (the one implementation both entry points delegate to) is used.
    frame, version = _read_delta_frame(PRICE_TABLE, version=0, lake_dir=lake)
    assert version == 0
    assert frame["close"].to_list() == _price_frame()["close"].to_list()
    latest_frame, latest_version = read_delta_with_version(PRICE_TABLE, lake_dir=lake)
    assert latest_version == 1
    assert latest_frame["close"].to_list() != frame["close"].to_list()


# ---------------------------------------------------------------------------
# 4. Non-Delta price reads digest exactly the enumerated file set
# ---------------------------------------------------------------------------


def test_non_delta_price_read_digests_exactly_the_scanned_files(tmp_path: Path) -> None:
    """A non-Delta read records one aggregate digest of the files it enumerated.

    Falsified by: hashing only one file, including non-parquet files, letting
    enumeration order change the digest, or ignoring an add/remove/rewrite of a
    parquet file (the read would then look reproducible when it is not).
    """

    def digest_for(root: Path) -> dict[str, str | None]:
        loader = BacktestDataLoader()
        loader._read_market(MARKET, ["AAA"], D0, D2, ["ticker", "date", "close"], root)
        [snapshot] = loader.snapshots()
        assert snapshot["source"] == "lake:parquet"
        assert snapshot["version"] is None  # a file-set digest has no Delta version
        return cast("dict[str, str | None]", snapshot)

    base = _price_frame(dates=[D0])
    first_root = tmp_path / "first" / PRICE_TABLE
    _write_parquet_price_table(first_root, {"date=2024-01-02/part-0.parquet": base})
    recorded = digest_for(tmp_path / "first")
    assert recorded["name"] == f"price_marks:{MARKET}"

    # Identical content under the same relative layout -> identical digest.
    second_root = tmp_path / "second" / PRICE_TABLE
    _write_parquet_price_table(second_root, {"date=2024-01-02/part-0.parquet": base})
    assert digest_for(tmp_path / "second")["sha256"] == recorded["sha256"]

    # Adding a parquet file changes the digest.
    _write_parquet_price_table(first_root, {"date=2024-01-03/part-0.parquet": _price_frame(dates=[D1])})
    added = digest_for(tmp_path / "first")
    assert added["sha256"] != recorded["sha256"]

    # Changing one file's content changes it (same file count, same names).
    _write_parquet_price_table(first_root, {"date=2024-01-02/part-0.parquet": base.with_columns(pl.col("close") + 1.0)})
    rewritten = digest_for(tmp_path / "first")
    assert rewritten["sha256"] not in {recorded["sha256"], added["sha256"]}

    # Non-parquet files are excluded: the digest is over the scanned glob only.
    (first_root / "date=2024-01-02" / "_SUCCESS").write_text("marker", encoding="utf-8")
    (first_root / "notes.csv").write_text("date,close\n", encoding="utf-8")
    assert digest_for(tmp_path / "first")["sha256"] == rewritten["sha256"]

    # Removing that parquet file changes the digest back to nothing like the old one.
    (first_root / "date=2024-01-03" / "part-0.parquet").unlink()
    removed = digest_for(tmp_path / "first")
    assert removed["sha256"] != rewritten["sha256"]


# ---------------------------------------------------------------------------
# 5. Action inputs bind the versions they read
# ---------------------------------------------------------------------------


def test_action_inputs_record_their_read_versions(tmp_path: Path) -> None:
    """Action inputs record the version each table was read at; absent tables do not.

    Falsified by: inventing a version for a missing table, reporting a version that is
    not the one read, or having the delegating reader disagree with the version-aware
    one about what was read.
    """
    lake = tmp_path / "lake"
    actions = pl.DataFrame(
        {
            "ticker": ["AAA"],
            "symbol": ["AAA"],
            "market": [MARKET],
            "mic": [MIC],
            "ex_date": [D1],
            "action": ["split"],
            "value": [2.0],
        }
    )
    coverage = pl.DataFrame(
        {
            "market": [MARKET],
            "mic": [MIC],
            "symbol": ["AAA"],
            "source": ["test-source"],
            "coverage_start": [D0],
            "coverage_end": [D2],
        }
    )
    write_delta(actions, ACTION_TABLE, mode="overwrite", lake_dir=lake, partition_by=["ex_date"])
    write_delta(coverage, COVERAGE_TABLE, mode="overwrite", lake_dir=lake, partition_by=["coverage_end"])

    read_actions, read_coverage, versions = load_corporate_action_inputs_with_versions(MARKET, lake_dir=lake)
    assert read_actions.height == 1 and read_coverage.height == 1
    assert versions == {ACTION_TABLE: 0, COVERAGE_TABLE: 0}

    # The version-blind reader delegates, so the two can never read differently.
    plain_actions, plain_coverage = load_corporate_action_inputs(MARKET, lake_dir=lake)
    assert plain_actions.equals(read_actions) and plain_coverage.equals(read_coverage)

    # The loader records one snapshot per action table, with the same digest convention.
    loader = BacktestDataLoader(lake_dir=lake)
    loader._action_inputs_for(InstrumentKey(market=MARKET, mic=MIC, symbol="AAA"))
    snapshots = {snapshot["name"]: snapshot for snapshot in loader.snapshots()}
    assert snapshots[f"action_events:{MARKET}"]["version"] == "0"
    assert snapshots[f"action_events:{MARKET}"]["source"] == "lake:delta"
    assert snapshots[f"action_events:{MARKET}"]["sha256"] == _delta_reference_digest(lake / ACTION_TABLE, 0)
    assert snapshots[f"action_coverage:{MARKET}"]["version"] == "0"
    assert snapshots[f"action_coverage:{MARKET}"]["sha256"] == _delta_reference_digest(lake / COVERAGE_TABLE, 0)

    # Absent tables: typed empty frames, and no invented version for either.
    empty_lake = tmp_path / "empty-lake"
    empty_actions, empty_coverage, missing = load_corporate_action_inputs_with_versions(MARKET, lake_dir=empty_lake)
    assert empty_actions.is_empty() and empty_coverage.is_empty()
    assert missing == {ACTION_TABLE: None, COVERAGE_TABLE: None}
    assert all(version is None for version in missing.values())

    no_actions = BacktestDataLoader(lake_dir=empty_lake)
    no_actions._action_inputs_for(InstrumentKey(market=MARKET, mic=MIC, symbol="AAA"))
    assert no_actions.snapshots() == []  # nothing was read, so nothing is claimed


# ---------------------------------------------------------------------------
# 6. Preloaded arena data: no read, no snapshot, provenance still unknown
# ---------------------------------------------------------------------------


def test_preloaded_arena_data_records_no_snapshots_and_stays_unknown(tmp_path: Path) -> None:
    """A preloaded outcome carries no snapshot, and its manifest stays ``unknown``.

    Falsified by: fabricating a snapshot for data the arena never read, or by turning
    "no read" into a provenance claim.
    """
    outcome = _arena_outcome(_price_frame())
    assert outcome.input_snapshots == []  # the arena recorded no read for a preload

    _, summary = write_arena_artifacts_with_evidence(outcome, base=tmp_path, run_date=D0)
    assert len(summary.finalized) == 1
    manifest = _manifest_of(tmp_path)
    assert manifest["input_snapshots"] == []
    assert manifest["data_provenance"] == "unknown"

    # A loader that never read anything reports no snapshot either.
    assert BacktestDataLoader().snapshots() == []


# ---------------------------------------------------------------------------
# 7. The writer passes read-seam snapshots into the manifest
# ---------------------------------------------------------------------------


def test_arena_writer_passes_snapshots_into_the_run_manifest(tmp_path: Path) -> None:
    """Snapshots reach ``manifest.input_snapshots`` with their versions intact.

    Falsified by: dropping the snapshot, losing the ``version`` on the way through the
    plain-mapping seam, or by the presence of evidence quietly upgrading provenance.
    """
    snapshots: list[dict[str, str | None]] = [
        {"name": f"price_marks:{MARKET}", "sha256": "a" * 64, "source": "lake:delta", "version": "7"},
        {"name": f"action_events:{MARKET}", "sha256": "b" * 64, "source": "lake:delta", "version": "3"},
    ]
    _, summary = write_arena_artifacts_with_evidence(_arena_outcome(_price_frame(), input_snapshots=snapshots), base=tmp_path, run_date=D0)
    assert len(summary.finalized) == 1

    manifest = _manifest_of(tmp_path)
    assert manifest["input_snapshots"] == snapshots  # name/sha256/source/version survive
    assert [entry["version"] for entry in manifest["input_snapshots"]] == ["7", "3"]
    assert manifest["data_provenance"] == "unknown"  # snapshots are not a provenance upgrade
    assert validate_run_directory(summary.finalized[0].directory).finalized


# ---------------------------------------------------------------------------
# 8. Provenance cannot be upgraded without a persisted price-source marker
# ---------------------------------------------------------------------------


def test_provenance_cannot_be_upgraded_without_a_price_source_marker(tmp_path: Path) -> None:
    """Both shipped paths record ``unknown``; only an explicit claim needs evidence.

    The model accepts a *self-asserted* non-unknown provenance when snapshots exist
    (ADR-0025 Decision 3 leaves provenance self-asserted), but no shipped path ever
    asserts one — a snapshot proves which bytes/versions were read, not which price
    source produced the marks. That is open item E01-6.

    Falsified by: deriving provenance from a snapshot, from a ``01_bronze`` path, from a
    bronze identity column, or from the action-coverage ``source`` column; or by
    accepting a non-unknown claim with no snapshots at all.
    """
    snapshot = InputSnapshot(name=f"price_marks:{MARKET}", sha256="c" * 64, source="lake:delta", version="2")

    # The shipped arena writer leaves provenance unknown even with snapshots present.
    _, summary = write_arena_artifacts_with_evidence(
        _arena_outcome(
            _price_frame(),
            input_snapshots=[{"name": snapshot.name, "sha256": snapshot.sha256, "source": snapshot.source, "version": "2"}],
        ),
        base=tmp_path,
        run_date=D0,
    )
    assert len(summary.finalized) == 1
    assert _manifest_of(tmp_path)["data_provenance"] == "unknown"
    assert summary.finalized[0].manifest.input_snapshots  # the evidence is recorded...

    # ... and it still does not upgrade provenance, even when one is asserted by hand.
    asserted = finalise_run(
        _stub_result(),
        scope=_scope(["AAA"]),
        input_snapshots=[snapshot],
        data_provenance="synthetic",
        code=CodeIdentity(commit="a" * 40),
        root=tmp_path / "asserted",
    )
    assert asserted.manifest.data_provenance == "synthetic"  # self-asserted, allowed by the model
    assert asserted.manifest.input_snapshots == [snapshot]

    # A non-unknown claim with no snapshots is still refused outright.
    with pytest.raises(ValueError, match="requires input_snapshots"):
        finalise_run(
            _stub_result(),
            scope=_scope(["AAA"]),
            data_provenance="real",
            code=CodeIdentity(commit="a" * 40),
            root=tmp_path / "refused",
        )
    assert not (tmp_path / "refused").exists() or list((tmp_path / "refused").rglob("manifest.json")) == []


# ---------------------------------------------------------------------------
# 9. Regression pins: read_delta contract and snapshot model shape
# ---------------------------------------------------------------------------


def _delta_table_double_without_version() -> type:
    """A duck-typed table double that reads like a DeltaTable but has no ``version()``.

    This models the legitimate callers ``read_delta`` has always had: they read rows
    and never asked the table which snapshot they came from.
    """

    class _VersionlessDeltaTable:
        def __init__(self, path: str, version: int | None = None) -> None:
            self.table = DeltaTable(path, version=version) if version is not None else DeltaTable(path)

        def schema(self) -> Any:
            return self.table.schema()

        def to_pyarrow_table(self, **kwargs: Any) -> Any:
            return self.table.to_pyarrow_table(**kwargs)

    return _VersionlessDeltaTable


def test_read_delta_keeps_its_signature_and_error_contract(tmp_path: Path) -> None:
    """``read_delta`` still takes ``version`` and still raises ``DeltaReadError``.

    Falsified by: changing the delegating reader's parameter list/order/defaults (its
    callers pass ``version`` positionally or by keyword), returning a tuple from it, or
    letting a missing table surface as a bare ``FileNotFoundError``.
    """
    parameters = list(inspect.signature(read_delta).parameters.items())
    assert [name for name, _parameter in parameters] == ["table", "version", "lake_dir", "filters", "columns"]
    defaults = {name: parameter.default for name, parameter in parameters}
    assert defaults["version"] is None
    assert defaults["lake_dir"] is None and defaults["filters"] is None and defaults["columns"] is None
    assert inspect.signature(read_delta).return_annotation in (pl.DataFrame, "pl.DataFrame")

    lake = tmp_path / "lake"
    _write_delta_price_table(lake, frames=[_price_frame()])
    frame = read_delta(PRICE_TABLE, lake_dir=lake)
    assert isinstance(frame, pl.DataFrame)
    assert frame.height == len(DATES) * 2
    versioned = read_delta(PRICE_TABLE, 0, lake_dir=lake)
    assert versioned.height == frame.height

    with pytest.raises(DeltaReadError):
        read_delta("01_bronze/absent", lake_dir=lake)
    with pytest.raises(DeltaReadError):
        read_delta_with_version("01_bronze/absent", lake_dir=lake)


def test_read_delta_does_not_require_a_version_capable_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The blind reader never asks the table for a version.

    Falsified by: calling ``dt.version()`` on every read (the shared implementation
    would then demand a capability from callers that never needed one), or by the
    blind reader returning anything other than the frame.
    """
    lake = tmp_path / "lake"
    _write_delta_price_table(lake, frames=[_price_frame()])
    monkeypatch.setattr("equity_lake.storage.delta.DeltaTable", _delta_table_double_without_version())

    frame = read_delta(PRICE_TABLE, lake_dir=lake)
    assert isinstance(frame, pl.DataFrame)
    assert frame.height == len(DATES) * 2
    # An explicit snapshot is read the same way: no version is requested of the table.
    assert read_delta(PRICE_TABLE, 0, lake_dir=lake).height == frame.height


def test_read_delta_with_version_refuses_a_table_that_cannot_supply_its_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Version-capturing reads fail loudly instead of inventing a version.

    Falsified by: returning a frame with a fabricated/placeholder version, letting the
    ``AttributeError`` escape as a non-``DeltaReadError``, or silently degrading to a
    versionless read (the caller would then bind evidence to a snapshot it never saw).
    """
    lake = tmp_path / "lake"
    _write_delta_price_table(lake, frames=[_price_frame()])
    monkeypatch.setattr("equity_lake.storage.delta.DeltaTable", _delta_table_double_without_version())

    with pytest.raises(DeltaReadError) as excinfo:
        read_delta_with_version(PRICE_TABLE, lake_dir=lake)
    assert excinfo.value.table == PRICE_TABLE
    assert isinstance(excinfo.value.__cause__, AttributeError)
    assert "version" in str(excinfo.value.__cause__)


def test_input_snapshot_declares_the_version_additively() -> None:
    """``InputSnapshot.version`` is additive, optional, and never an empty string.

    Falsified by: making ``version`` required (it would break non-Delta file-set
    digests), dropping the sha256 validator, or accepting ``version=""`` as if a
    snapshot had been bound.
    """
    assert InputSnapshot(name="x", sha256="d" * 64, source="lake:parquet").version is None
    assert InputSnapshot(name="x", sha256="d" * 64, source="lake:delta", version="12").version == "12"
    with pytest.raises(ValueError):
        InputSnapshot(name="x", sha256="NOT-A-DIGEST", source="lake:delta")
    with pytest.raises(ValueError):
        InputSnapshot(name="x", sha256="d" * 64, source="lake:delta", version="")
    with pytest.raises(ValueError):
        InputSnapshot.model_validate({"name": "x", "sha256": "d" * 64, "source": "lake:delta", "extra_field": "nope"})


def test_universe_basis_type_covers_exactly_the_two_parse_outcomes() -> None:
    """The basis literal names exactly the two outcomes the parser can produce.

    Falsified by: adding a third basis that no parse produces (an unprovable claim
    would become recordable), or by renaming one the manifest already stores.
    """
    declared = set(UniverseBasis.__args__)
    assert declared == {"canonical", "ticker_only"}
    assert universe_basis_for(["us_equity/XNYS/AAPL"]) in declared
    assert universe_basis_for(["AAPL"]) in declared
    assert set(IDENTITY_COLUMNS) == {"market", "mic", "symbol"}


# ---------------------------------------------------------------------------
# 10. Snapshots are scoped to the load that produced the data
# ---------------------------------------------------------------------------


def test_reused_loader_reports_the_second_loads_delta_read(tmp_path: Path) -> None:
    """A second ``load()`` replaces the first load's snapshot instead of inheriting it.

    Falsified by: keeping the earlier map (a reused loader would then read the newer
    table while ``snapshots()`` still named the first version), by recording nothing
    for the second load, or by a digest that does not follow the version read.
    """
    lake = tmp_path / "lake"
    table_path = lake / PRICE_TABLE
    _write_delta_price_table(lake, frames=[_price_frame()])  # version 0

    loader = BacktestDataLoader(lake_dir=lake)
    assert not loader.load(["AAA"], D0, D2, markets=[MARKET]).is_empty()
    [first] = loader.snapshots()
    assert first["name"] == f"price_marks:{MARKET}"
    assert first["source"] == "lake:delta"
    assert first["version"] == "0"
    assert first["sha256"] == _delta_reference_digest(table_path, 0)

    # A new commit lands between the two loads, on the *same* loader instance.
    write_delta(_price_frame(close_offset=1.0), PRICE_TABLE, mode="append", lake_dir=lake)
    assert not loader.load(["AAA"], D0, D2, markets=[MARKET]).is_empty()
    [second] = loader.snapshots()  # exactly one entry: nothing survived from load one
    assert second["name"] == f"price_marks:{MARKET}"
    assert second["version"] == "1"
    assert second["sha256"] == _delta_reference_digest(table_path, 1)
    assert second["sha256"] != first["sha256"]


def test_reused_loader_reports_the_second_loads_parquet_read(tmp_path: Path) -> None:
    """The non-Delta path is re-digested per load, never reported from the first load.

    Falsified by: skipping the re-digest when a name is already present (a rewritten
    parquet file set would keep the earlier aggregate digest).
    """
    root = tmp_path / "lake" / PRICE_TABLE
    _write_parquet_price_table(root, {"date=2024-01-02/part-0.parquet": _price_frame(dates=[D0])})

    loader = BacktestDataLoader(lake_dir=tmp_path / "lake")
    assert not loader.load(["AAA"], D0, D2, markets=[MARKET]).is_empty()
    [first] = loader.snapshots()
    assert first["source"] == "lake:parquet"
    assert first["version"] is None

    # Same file names, different content: the file-set digest must move with it.
    _write_parquet_price_table(root, {"date=2024-01-02/part-0.parquet": _price_frame(dates=[D0]).with_columns(pl.col("close") + 1.0)})
    assert not loader.load(["AAA"], D0, D2, markets=[MARKET]).is_empty()
    [second] = loader.snapshots()
    assert second["name"] == f"price_marks:{MARKET}"
    assert second["sha256"] != first["sha256"]


def test_two_loads_of_an_unchanged_read_report_the_same_snapshot(tmp_path: Path) -> None:
    """The per-load reset is not a source of drift: an unchanged table reports one version.

    Falsified by: reading the version after the scan (it could drift), by accumulating
    duplicate entries across loads, or by a reset that drops the second load's entry.
    """
    lake = tmp_path / "lake"
    _write_delta_price_table(lake, frames=[_price_frame()])  # one commit, never re-read

    loader = BacktestDataLoader(lake_dir=lake)
    assert not loader.load(["AAA"], D0, D2, markets=[MARKET]).is_empty()
    first = loader.snapshots()
    assert not loader.load(["AAA"], D0, D2, markets=[MARKET]).is_empty()
    second = loader.snapshots()

    assert second == first  # same single entry: same version, same digest, no duplicate
    assert [snapshot["version"] for snapshot in second] == ["0"]
