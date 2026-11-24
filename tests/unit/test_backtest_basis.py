"""Stage 2a: one lake root and one ADR-0032 coverage basis per backtest read.

The backtest path used to be canonical-root/strict-only. These tests pin the
integration: the loader and the engine expose ``lake_dir`` and ``coverage_basis``
(defaults unchanged: canonical root, ``source_clock``), the weak
``vendor_events_no_clock`` basis reaches every ``with_price_adjustment`` call, the
basis the read admitted is bound into the ``action_coverage:<market>`` snapshot and
therefore the run manifest, and the bases are never mixed or inferred.

Every fixture is written under ``tmp_path`` only: nothing under ``data/`` is read or
written and no provider/network call is made. Each test states the rule it pins, so a
mutation of that one gate has a named test to fail.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest
from pydantic import ValidationError

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE, VectorBacktestEngine
from equity_lake.backtesting.evidence import (
    RUN_PROTOCOL_V4,
    CostApplication,
    InputSnapshot,
    RunManifest,
    RunScope,
)
from equity_lake.backtesting.strategy.base import BaseStrategy
from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ActionCoverageProof,
    AvailabilityContractError,
    CoverageBasis,
    VendorEventCoverageProof,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    InstrumentKey,
)
from equity_lake.ingestion.corporate_actions import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    corporate_action_coverage_table,
    corporate_actions_table,
    load_corporate_action_inputs,
)
from equity_lake.storage.delta import merge_delta, write_delta
from equity_lake.storage.lake_reader import with_price_adjustment

MARKET = "us_equity"
MIC = "XNYS"
SYMBOL = "AAPL"
AAPL = InstrumentKey(market=MARKET, mic=MIC, symbol=SYMBOL)
WEAK: CoverageBasis = "vendor_events_no_clock"
STRICT: CoverageBasis = "source_clock"
PRICE_TABLE = "01_bronze/market_data/us_equity"
DATES = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8)]
#: Raw (vendor) closes with a 2-for-1 split whose ex-date is DATES[3]: the three
#: pre-split bars are halved by the adjusted read, the two post-split bars are not.
RAW_CLOSES = [200.0, 202.0, 204.0, 102.0, 103.0]
SPLIT_EX_DATE = DATES[3]
NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _write_prices(lake: Path) -> None:
    rows = [
        {
            "market": MARKET,
            "mic": MIC,
            "symbol": SYMBOL,
            "ticker": SYMBOL,
            "date": day,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000.0,
        }
        for day, close in zip(DATES, RAW_CLOSES, strict=True)
    ]
    write_delta(pl.DataFrame(rows), PRICE_TABLE, mode="overwrite", lake_dir=lake)


def _write_actions(lake: Path, *, basis: CoverageBasis) -> None:
    """Write the split event and one coverage proof on *basis* (weak has no clock)."""
    actions = pl.DataFrame(
        {
            "ticker": [SYMBOL],
            "ex_date": [SPLIT_EX_DATE],
            "action": ["split"],
            "value": [0.5],
            "source": ["yahoo"],
            "source_available_at": [datetime(2024, 1, 1, 21, tzinfo=UTC) if basis == STRICT else None],
        },
        schema_overrides={"ex_date": pl.Date, "source_available_at": pl.Datetime("us", "UTC")},
    )
    merge_delta(
        actions,
        corporate_actions_table(MARKET, "silver"),
        key_columns=["ticker", "ex_date", "action"],
        lake_dir=lake,
        partition_by=["ex_date"],
    )
    if basis == STRICT:
        proof: ActionCoverageProof | VendorEventCoverageProof = ActionCoverageProof(
            market=MARKET,
            mic=MIC,
            symbol=SYMBOL,
            source="yahoo",
            coverage_start=DATES[0],
            coverage_end=DATES[-1],
            source_available_at=NOW,
            source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
        )
    else:
        proof = VendorEventCoverageProof(
            market=MARKET,
            mic=MIC,
            symbol=SYMBOL,
            source="yahoo",
            coverage_start=DATES[0],
            coverage_end=DATES[-1],
        )
    merge_delta(
        pl.DataFrame([proof.model_dump()]),
        corporate_action_coverage_table(MARKET),
        key_columns=["market", "mic", "symbol", "source", "coverage_start", "coverage_end"],
        lake_dir=lake,
        partition_by=["coverage_end"],
    )


def _lake(tmp_path: Path, *, basis: CoverageBasis) -> Path:
    """A tiny one-instrument lake whose coverage proof is issued on *basis*."""
    lake = tmp_path / f"lake-{basis}"
    _write_prices(lake)
    _write_actions(lake, basis=basis)
    return lake


def _raw_prices() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "market": MARKET,
                "mic": MIC,
                "symbol": SYMBOL,
                "ticker": SYMBOL,
                "date": day,
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000.0,
            }
            for day, close in zip(DATES, RAW_CLOSES, strict=True)
        ]
    )


class _BuyAndHold(BaseStrategy):
    """One instrument, always fully invested: a deterministic weak-basis run."""

    def initialize(self, data: pl.DataFrame) -> None:
        self._data = data

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        return data.select("date", "market", "mic", "symbol").unique().with_columns(pl.lit(1.0).alias("weight"))


def _engine(**kwargs: Any) -> VectorBacktestEngine:
    return VectorBacktestEngine(
        strategy=_BuyAndHold(),
        tickers=[AAPL],
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=1_000.0,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Basis refusals at the read and construction seams
# ---------------------------------------------------------------------------


def test_strict_basis_refuses_weak_only_evidence(tmp_path: Path) -> None:
    """The default strict read refuses a lake whose only proofs are weak.

    Falsified by: threading the weak basis implicitly (a strict read would then
    succeed), or by excluding every session and reporting an empty load as success.
    """
    loader = BacktestDataLoader(lake_dir=_lake(tmp_path, basis=WEAK))  # strict default
    with pytest.raises(AvailabilityContractError, match="one read admits one basis") as refused:
        loader.load([AAPL], DATES[0], DATES[-1], markets=[MARKET])
    assert "vendor_events_no_clock" in str(refused.value)


def test_weak_basis_refuses_strict_only_evidence(tmp_path: Path) -> None:
    """The explicit weak read refuses a lake whose only proofs are strict.

    Falsified by: letting the weak flag reinterpret a strict proof (a basis is never
    inferred in either direction).
    """
    loader = BacktestDataLoader(lake_dir=_lake(tmp_path, basis=STRICT), coverage_basis=WEAK)
    with pytest.raises(AvailabilityContractError, match="one read admits one basis") as refused:
        loader.load([AAPL], DATES[0], DATES[-1], markets=[MARKET])
    assert "source_clock" in str(refused.value)


def test_unknown_basis_refuses_at_construction() -> None:
    """Neither the engine nor the loader accepts an unnameable basis."""
    with pytest.raises(ValueError, match="Unknown coverage basis"):
        _engine(coverage_basis=cast(CoverageBasis, "guessed_clock"))
    with pytest.raises(ValueError, match="Unknown coverage basis"):
        BacktestDataLoader(coverage_basis=cast(CoverageBasis, "guessed_clock"))


def test_an_unknown_recorded_context_basis_refuses() -> None:
    """A preloaded point-in-time context whose recorded basis is unknown refuses.

    The basis is read from the context stamp alone; an unknown value is never
    defaulted to strict (that would infer a basis the writer never recorded).
    """
    payload = json.dumps({"actions": [], "coverage": [], "coverage_basis": "guessed_clock"})
    with pytest.raises(AvailabilityContractError, match="unknown coverage basis") as refused:
        VectorBacktestEngine._decode_action_context(payload)
    assert "guessed_clock" in str(refused.value)


def test_a_preloaded_frame_is_never_reinterpreted_on_another_basis(tmp_path: Path) -> None:
    """A strict engine refuses a frame the weak loader read (basis is not inferred).

    Falsified by: reading the recorded context basis and adopting it instead of
    refusing, which would let a strict run silently consume weak evidence.
    """
    weak_loader = BacktestDataLoader(lake_dir=_lake(tmp_path, basis=WEAK), coverage_basis=WEAK)
    prepared = weak_loader.load([AAPL], DATES[0], DATES[-1], markets=[MARKET])
    assert not prepared.is_empty()

    with pytest.raises(AvailabilityContractError, match="one run admits one basis") as refused:
        _engine(preloaded_data=prepared).run()
    assert "vendor_events_no_clock" in str(refused.value)


def test_defaults_stay_canonical_root_and_strict_basis() -> None:
    """The integration changes no default: no root override, ``source_clock`` basis."""
    engine = _engine()
    assert engine.lake_dir is None
    assert engine.coverage_basis == STRICT
    loader = BacktestDataLoader()
    assert loader.lake_dir is None
    assert loader.coverage_basis == STRICT


# ---------------------------------------------------------------------------
# 2. The basis a read admitted is bound into the snapshot and the manifest
# ---------------------------------------------------------------------------


def _scope() -> RunScope:
    return RunScope(
        universe=[AAPL.display_key],
        start_date=DATES[0],
        end_date=DATES[-1],
        strategies=["momentum"],
        costs=[CostApplication(regime="zero", fee_ratio=0.0, tax_ratio=0.0)],
        initial_cash=1_000.0,
        parameters={},
    )


def _manifest(snapshots: list[InputSnapshot]) -> RunManifest:
    scope = _scope()
    return RunManifest(
        run_id=scope.run_id(),
        run_dir="run-2024-01-02",
        created_at=NOW,
        finalized_at=NOW,
        protocol=RUN_PROTOCOL_V4,
        start_date=scope.start_date,
        end_date=scope.end_date,
        universe=scope.universe,
        universe_basis="canonical",
        strategies=scope.strategies,
        cost_regimes=["zero"],
        initial_cash=scope.initial_cash,
        parameters={},
        costs=scope.costs,
        input_snapshots=snapshots,
    )


def _coverage_snapshot(market: str, basis: CoverageBasis) -> InputSnapshot:
    return InputSnapshot(name=f"action_coverage:{market}", sha256="a" * 64, source="lake:delta", version="0", coverage_basis=basis)


def test_the_action_coverage_snapshot_records_the_basis_it_read(tmp_path: Path) -> None:
    """A weak read stamps its basis on the ``action_coverage:<market>`` snapshot.

    Falsified by: recording the basis only in memory (the manifest would then carry
    no basis at all) or by stamping it on every snapshot indiscriminately.
    """
    loader = BacktestDataLoader(lake_dir=_lake(tmp_path, basis=WEAK), coverage_basis=WEAK)
    assert not loader.load([AAPL], DATES[0], DATES[-1], markets=[MARKET]).is_empty()
    snapshots = {snapshot["name"]: snapshot for snapshot in loader.snapshots()}
    assert snapshots[f"action_coverage:{MARKET}"]["coverage_basis"] == WEAK
    assert "coverage_basis" not in snapshots[f"action_events:{MARKET}"]
    assert "coverage_basis" not in snapshots[f"price_marks:{MARKET}"]


def test_a_manifest_records_one_basis_and_refuses_a_mixed_scope() -> None:
    """A manifest binds the basis it read, and a scope mixing bases refuses.

    ADR-0032 decision 9. Falsified by: accepting a manifest whose snapshots record two
    bases (a reader would have no rule to pick one), or by dropping the stamp from the
    serialized manifest.
    """
    manifest = _manifest([_coverage_snapshot(MARKET, WEAK)])
    assert manifest.input_snapshots[0].coverage_basis == WEAK
    assert json.loads(manifest.model_dump_json())["input_snapshots"][0]["coverage_basis"] == WEAK

    # A snapshot with no basis keeps the exact pre-existing shape (no new null key).
    plain = InputSnapshot(name=f"price_marks:{MARKET}", sha256="b" * 64, source="lake:delta", version="1")
    assert "coverage_basis" not in plain.model_dump()

    with pytest.raises(ValidationError, match="mixing bases"):
        _manifest([_coverage_snapshot(MARKET, STRICT), _coverage_snapshot("cn_ashare", WEAK)])


def test_a_basis_may_only_be_recorded_on_a_coverage_snapshot() -> None:
    """A basis on a price/event snapshot refuses: those inputs carry no proofs."""
    with pytest.raises(ValidationError, match="only be recorded on an"):
        InputSnapshot(name=f"price_marks:{MARKET}", sha256="c" * 64, source="lake:delta", coverage_basis=WEAK)


# ---------------------------------------------------------------------------
# 3. A weak-basis backtest runs and reproduces the direct adjusted read
# ---------------------------------------------------------------------------


def test_weak_basis_read_matches_the_direct_adjusted_read(tmp_path: Path) -> None:
    """The loader's weak read reproduces ``with_price_adjustment(..., weak)`` exactly.

    The reference is computed directly from the same raw bars, events and coverage
    proof. The loader publishes a **split-neutral return index** (``_point_in_time_marks``:
    each as-of mark chain starts at the first decision's close, before the split is
    known), so the two series agree up to that documented constant rebase — never up
    to a different set of split factors. A loader that dropped the basis argument
    would one of: refuse (strict default) or produce a different rebase.
    """
    lake = _lake(tmp_path, basis=WEAK)
    loader = BacktestDataLoader(lake_dir=lake, coverage_basis=WEAK)
    loaded = loader.load([AAPL], DATES[0], DATES[-1], markets=[MARKET]).sort("date")
    assert not loaded.is_empty()
    assert loaded["action_coverage"].to_list() == [1] * len(DATES)

    actions, coverage = load_corporate_action_inputs(MARKET, lake_dir=lake)
    direct = with_price_adjustment(
        _raw_prices(),
        actions,
        coverage=coverage,
        method="split_only",
        as_of=DATES[-1],
        coverage_basis=WEAK,
    ).sort("date")
    # One exact scale step at the split: three halved bars, then the post-split bars.
    assert direct["close"].to_list() == pytest.approx([100.0, 101.0, 102.0, 102.0, 103.0])

    # The index base is the first decision's *unadjusted* close (the split is not yet
    # known there), so the two series are equal after removing that one constant.
    rebase = loaded["close"][0] / direct["close"][0]
    assert rebase == pytest.approx(RAW_CLOSES[0] / direct["close"][0])
    assert (loaded["close"] / rebase).to_list() == pytest.approx(direct["close"].to_list())
    # Volume is deliberately the raw pass-through mark (the engine trades the split-neutral
    # price index, never a share count), so only the price basis is compared here.
    assert loaded["volume"].to_list() == pytest.approx([1_000.0] * len(DATES))

    # The strict read of the same lake refuses, so the equality is not a fallback.
    with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
        BacktestDataLoader(lake_dir=lake).load([AAPL], DATES[0], DATES[-1], markets=[MARKET])


@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")
def test_weak_basis_backtest_runs_end_to_end(tmp_path: Path) -> None:
    """A weak-basis engine run over the synthetic fixture completes and is stamped.

    Falsified by: the engine constructing its loader without the requested basis
    (the strict default would refuse the weak lake and ``run()`` would fail).
    """
    lake = _lake(tmp_path, basis=WEAK)
    engine = _engine(lake_dir=lake, coverage_basis=WEAK)
    result = engine.run()

    assert engine.lake_dir == lake.resolve()
    assert engine.coverage_basis == WEAK
    assert result.equity_curve.len() == len(DATES)
    assert engine.loaded_universe_size == 1
    assert engine.loaded_identities == [AAPL.display_key]
    snapshots = {snapshot["name"]: snapshot for snapshot in cast(BacktestDataLoader, engine.data_loader).snapshots()}
    assert snapshots[f"action_coverage:{MARKET}"]["coverage_basis"] == WEAK


# ---------------------------------------------------------------------------
# 4. The flat CLI entry point exposes the root and the basis
# ---------------------------------------------------------------------------


class TestBacktestCli:
    def test_help_lists_lake_dir_and_coverage_basis(self) -> None:
        from typer.testing import CliRunner

        from equity_lake.cli.__main__ import app

        result = CliRunner().invoke(app, ["backtest", "--help"], env={"COLUMNS": "220"})
        assert result.exit_code == 0
        for flag in ("--lake-dir", "--coverage-basis"):
            assert flag in result.stdout
        assert "vendor_events_no_clock" in result.stdout
        assert "source_clock" in result.stdout

    def test_an_unknown_basis_exits_non_zero(self) -> None:
        from typer.testing import CliRunner

        from equity_lake.cli.__main__ import app

        result = CliRunner().invoke(
            app,
            ["backtest", "--start-date", "2024-01-01", "--end-date", "2024-02-01", "--coverage-basis", "guessed_clock"],
        )
        assert result.exit_code == 1
        assert "Unknown --coverage-basis" in result.stdout

    def test_a_lake_root_outside_data_exits_non_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from typer.testing import CliRunner

        import equity_lake.core.paths as paths
        from equity_lake.cli.__main__ import app

        monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
        outside = tmp_path.parent / "elsewhere"
        result = CliRunner().invoke(
            app,
            ["backtest", "--start-date", "2024-01-01", "--end-date", "2024-02-01", "--lake-dir", str(outside)],
        )
        assert result.exit_code == 1
        assert "--lake-dir rejected" in result.stdout
