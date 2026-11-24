"""Stage 1: the explicitly scoped feature runner (real-root capable).

The feature path used to be canonical-root-only: ``FeatureEngineer._setup_views``
read ``LAKE_DIR`` and the corporate-action read was market-wide, so a subset run
against the 11-ticker real action store hit the ticker-only identity resolver
(``corporate action identity is unverified``). These tests pin the closed gap:

* ``FeatureEngineer(lake_dir=...)`` reads exactly one explicit root for prices
  **and** the action store;
* :func:`scope_action_inputs` scopes both action frames per instrument, so a
  subset read is coherent while the all-market resolver is untouched;
* the three run-level refusals (strict vs weak-only evidence, weak vs
  strict-only evidence, a window past the action coverage bound) and an
  instrument with no proof;
* a dry run writes nothing at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.core.availability import (
    ActionCoverageProof,
    AvailabilityContractError,
    VendorEventCoverageProof,
)
from equity_lake.core.instruments import InstrumentKey
from equity_lake.features import run_feature_job, scope_action_inputs
from equity_lake.ingestion.corporate_actions import coverage_frame_for_write

AAPL = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
JPM = InstrumentKey(market="us_equity", mic="XNYS", symbol="JPM")
WEAK = "vendor_events_no_clock"
STRICT = "source_clock"


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _price_frame(days: list[date]) -> pl.DataFrame:
    """An oscillating AAPL series (a monotonic one leaves RSI undefined)."""
    import math

    close = [100.0 + 8 * math.sin(index / 2.5) + index * 0.05 for index in range(len(days))]
    return pl.DataFrame(
        {
            "market": ["us_equity"] * len(days),
            "mic": ["XNAS"] * len(days),
            "symbol": ["AAPL"] * len(days),
            "ticker": ["AAPL"] * len(days),
            "date": days,
            "open": [value - 0.5 for value in close],
            "high": [value + 1.0 for value in close],
            "low": [value - 1.0 for value in close],
            "close": close,
            "volume": [1_000_000.0 + (index % 10) * 1_000 for index in range(len(days))],
        },
        schema_overrides={"date": pl.Date},
    )


def _action_frame(days: list[date], *, tickers: tuple[str, ...]) -> pl.DataFrame:
    """One split per ticker on the same session; the vendor supplies no clock."""
    return pl.DataFrame(
        {
            "ticker": list(tickers),
            "ex_date": [days[60]] * len(tickers),
            "action": ["split"] * len(tickers),
            "value": [0.5] * len(tickers),
            "source": ["yahoo"] * len(tickers),
            "ingested_at": [datetime.combine(days[0], datetime.min.time())] * len(tickers),
            "source_available_at": [None] * len(tickers),
        },
        schema_overrides={"ex_date": pl.Date, "ingested_at": pl.Datetime("us"), "source_available_at": pl.Datetime("us", "UTC")},
    )


def _weak_proof(*, start: date, end: date) -> VendorEventCoverageProof:
    return VendorEventCoverageProof(market="us_equity", mic="XNAS", symbol="AAPL", source="yahoo", coverage_start=start, coverage_end=end)


def _strict_proof(*, start: date, end: date) -> ActionCoverageProof:
    return ActionCoverageProof(
        market="us_equity",
        mic="XNAS",
        symbol="AAPL",
        source="yahoo",
        coverage_start=start,
        coverage_end=end,
        source_available_at=datetime.combine(start, datetime.min.time(), tzinfo=UTC),
        source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
    )


def build_lake(
    root: Path,
    *,
    proof: VendorEventCoverageProof | ActionCoverageProof | None = None,
    action_tickers: tuple[str, ...] = ("AAPL", "JPM"),
    price_days: int = 90,
) -> list[date]:
    """Write bronze prices, silver actions and one coverage proof under *root*."""
    days = _business_days(date(2024, 1, 1), price_days)
    market_dir = root / "01_bronze" / "market_data" / "us_equity"
    market_dir.mkdir(parents=True)
    write_deltalake(str(market_dir), _price_frame(days).to_arrow(), mode="append")
    actions_dir = root / "02_silver" / "corporate_actions" / "us_equity"
    actions_dir.mkdir(parents=True)
    write_deltalake(str(actions_dir), _action_frame(days, tickers=action_tickers).to_arrow(), mode="append")
    if proof is not None:
        coverage_dir = root / "02_silver" / "corporate_action_coverage" / "us_equity"
        coverage_dir.mkdir(parents=True)
        write_deltalake(str(coverage_dir), coverage_frame_for_write([proof]).to_arrow(), mode="append")
    return days


def test_engineer_reads_only_the_explicit_lake_root(tmp_path: Path) -> None:
    """Requirement 1: price sources come from the selected root, never ``LAKE_DIR``."""
    from equity_lake.features.engineering import FeatureEngineer

    root = tmp_path / "lake_scope"
    build_lake(root, proof=_weak_proof(start=date(2024, 1, 1), end=date(2024, 6, 30)))

    engineer = FeatureEngineer(lake_dir=root)
    try:
        assert engineer.lake_dir == root
        assert engineer.market_sources == [("us_equity", root / "01_bronze" / "market_data" / "us_equity")]
    finally:
        engineer.close()


def test_action_fallback_read_uses_the_explicit_lake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 1: the all-market action read (no ``action_inputs``) shares the root."""
    from equity_lake.features.engineering import FeatureEngineer
    from equity_lake.ingestion import corporate_actions as corporate_actions_module

    root = tmp_path / "lake_scope"
    days = build_lake(
        root,
        proof=_weak_proof(start=date(2024, 1, 1), end=date(2024, 6, 30)),
        action_tickers=("AAPL",),
    )
    recorded: list[Path | None] = []
    original = corporate_actions_module.load_corporate_action_inputs

    def _spy(market: str, *, lake_dir: Path | None = None) -> tuple[pl.DataFrame, pl.DataFrame]:
        recorded.append(lake_dir)
        return cast("tuple[pl.DataFrame, pl.DataFrame]", original(market, lake_dir=lake_dir))

    monkeypatch.setattr(corporate_actions_module, "load_corporate_action_inputs", _spy)

    engineer = FeatureEngineer(lake_dir=root)
    try:
        frame = engineer.generate_features(
            tickers=["AAPL"],
            start_date=days[0],
            end_date=days[-1],
            compute_target=False,
            instruments=[AAPL],
            coverage_basis=WEAK,
        )
    finally:
        engineer.close()

    assert frame.height > 0
    assert recorded == [root], "the corporate-action read did not use the selected root"


def test_subset_read_scopes_action_inputs_per_instrument(tmp_path: Path) -> None:
    """Requirement 2: the 11-ticker-style store is scoped, not widened.

    The store holds an out-of-scope ticker (JPM) whose ticker-only event has no
    coverage proof — the evidence shape that makes the identity resolver refuse
    a market-wide read. Scoping per instrument keeps AAPL readable and excludes
    the unproven event instead of widening the resolver.
    """
    root = tmp_path / "lake_scope"
    build_lake(root, proof=_weak_proof(start=date(2024, 1, 1), end=date(2024, 6, 30)))

    from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs

    stored_actions, _coverage = load_corporate_action_inputs("us_equity", lake_dir=root)
    assert set(stored_actions["ticker"].to_list()) == {"AAPL", "JPM"}

    action_inputs = scope_action_inputs(
        instruments=[AAPL],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 6, 28),
        lake_dir=root,
        coverage_basis=WEAK,
    )

    assert list(action_inputs) == [AAPL.tuple]
    actions, coverage = action_inputs[AAPL.tuple]
    assert actions["ticker"].unique().to_list() == ["AAPL"]
    assert coverage["symbol"].to_list() == ["AAPL"]
    assert coverage["coverage_basis"].to_list() == [WEAK]


def test_instrument_without_coverage_proof_refuses(tmp_path: Path) -> None:
    """A ticker with action rows but no proof cannot establish action coverage."""
    root = tmp_path / "lake_scope"
    build_lake(root, proof=_weak_proof(start=date(2024, 1, 1), end=date(2024, 6, 30)))

    with pytest.raises(AvailabilityContractError, match="no corporate-action coverage proof is recorded"):
        scope_action_inputs(
            instruments=[JPM],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 6, 28),
            lake_dir=root,
            coverage_basis=WEAK,
        )


def test_strict_basis_refuses_weak_only_coverage(tmp_path: Path) -> None:
    """Refusal 1: the strict default never accepts weak-only evidence (ADR-0032)."""
    root = tmp_path / "lake_scope"
    build_lake(root, proof=_weak_proof(start=date(2024, 1, 1), end=date(2024, 6, 30)))

    with pytest.raises(AvailabilityContractError, match="point-in-time source_clock coverage is unavailable"):
        scope_action_inputs(
            instruments=[AAPL],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 6, 28),
            lake_dir=root,
            coverage_basis=STRICT,
        )


def test_weak_basis_refuses_strict_only_coverage(tmp_path: Path) -> None:
    """Refusal 2: the weak opt-in never reinterprets clocked evidence as weak."""
    root = tmp_path / "lake_scope"
    build_lake(root, proof=_strict_proof(start=date(2024, 1, 1), end=date(2024, 6, 30)))

    with pytest.raises(AvailabilityContractError, match="vendor_events_no_clock coverage is unavailable"):
        scope_action_inputs(
            instruments=[AAPL],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 6, 28),
            lake_dir=root,
            coverage_basis=WEAK,
        )


def test_window_past_the_action_coverage_bound_refuses(tmp_path: Path) -> None:
    """Refusal 3: a window past the watermark refuses instead of clamping."""
    root = tmp_path / "lake_scope"
    days = build_lake(
        root,
        proof=_weak_proof(start=date(2024, 1, 1), end=date(2024, 3, 29)),
        price_days=90,
    )

    with pytest.raises(AvailabilityContractError, match="beyond the action coverage bound 2024-03-29"):
        scope_action_inputs(
            instruments=[AAPL],
            start_date=date(2024, 1, 2),
            end_date=days[-1],
            lake_dir=root,
            coverage_basis=WEAK,
        )


def test_window_before_the_coverage_start_refuses(tmp_path: Path) -> None:
    """Refusal 4: a window that ends before the evidence starts refuses."""
    root = tmp_path / "lake_scope"
    days = build_lake(root, proof=_weak_proof(start=date(2024, 4, 1), end=date(2024, 6, 30)))

    with pytest.raises(AvailabilityContractError, match="after the requested window ends"):
        scope_action_inputs(
            instruments=[AAPL],
            start_date=days[0],
            end_date=date(2024, 1, 5),
            lake_dir=root,
            coverage_basis=WEAK,
        )


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """A dry run computes the scoped frame and persists nothing under the root."""
    root = tmp_path / "lake_scope"
    days = build_lake(root, proof=_weak_proof(start=date(2024, 1, 1), end=date(2024, 6, 30)))
    before = sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())

    action_inputs = scope_action_inputs(
        instruments=[AAPL],
        start_date=days[-1],
        end_date=days[-1],
        lake_dir=root,
        coverage_basis=WEAK,
    )
    diagnostics: dict[str, object] = {}
    frame = run_feature_job(
        tickers=["AAPL"],
        instruments=[AAPL],
        output_start_date=days[-1],
        output_end_date=days[-1],
        compute_target=True,
        diagnostics=diagnostics,
        coverage_basis=WEAK,
        lake_dir=root,
        action_inputs=action_inputs,
        dry_run=True,
    )

    assert frame.height == 1
    assert frame["action_coverage_basis"].unique().to_list() == [WEAK]
    assert diagnostics["produced"] == ["AAPL"]
    assert not (root / "03_gold").exists(), "a dry run created the Gold table"
    after = sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
    assert after == before, "a dry run changed files under the lake root"
