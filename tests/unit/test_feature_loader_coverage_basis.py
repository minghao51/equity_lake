"""R02 reader contract: weak-basis opt-in, strict default, mixed-basis refusal.

The real-root variant is read-only and skips when the authorized slice is absent;
the unit variants build their own synthetic weak Gold table through the production
merge seam, so the assertions exercise the real read path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from runpy import run_path
from typing import Any, cast

import polars as pl
import pytest

from equity_lake.core.availability import AvailabilityContractError, CoverageBasis
from equity_lake.core.instruments import InstrumentKey
from equity_lake.core.paths import PROJECT_ROOT
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION
from equity_lake.ml.feature_loader import FeatureLoader

_fixtures = run_path(str(Path(__file__).with_name("_weak_basis_fixtures.py")))
us_equity_session_dates = _fixtures["us_equity_session_dates"]
weak_gold_frame = _fixtures["weak_gold_frame"]
with_synthetic_feature_proof = _fixtures["with_synthetic_feature_proof"]
write_gold_lake = _fixtures["write_gold_lake"]
WEAK_BASIS: str = _fixtures["WEAK_BASIS"]

_AAPL = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
_MSFT = InstrumentKey(market="us_equity", mic="XNAS", symbol="MSFT")
_DATES = [session for session in us_equity_session_dates(date(2024, 1, 2), 60) if session <= date(2024, 9, 30)]
#: Anchored to the checkout on purpose: ``tests/conftest.py`` redirects every
#: ``DATA_DIR``-derived path into a temporary runtime root, so ``REAL_LAKE_DIR``
#: is not the authorized slice inside a test process. The read below is read-only.
REAL_ROOT = PROJECT_ROOT / "data" / "lake_real"


def _weak_lake(tmp_path: Path) -> Path:
    write_gold_lake(tmp_path, [weak_gold_frame(_AAPL, _DATES, seed=11)])
    return tmp_path


def test_weak_opt_in_reads_weak_rows(tmp_path: Path) -> None:
    """The explicit opt-in admits vendor-stamped rows and stamps the basis it read."""
    root = _weak_lake(tmp_path)

    loader = FeatureLoader(lake_dir=root, coverage_basis="vendor_events_no_clock")
    frame = loader.load_features(_AAPL, _DATES[0], _DATES[-1])

    assert frame.height == len(_DATES)
    assert set(frame["action_coverage_basis"].to_list()) == {WEAK_BASIS}
    assert frame["action_source_age_days"].null_count() == frame.height
    assert loader.features_dir() == root / "03_gold" / "features"


def test_strict_default_refuses_weak_rows(tmp_path: Path) -> None:
    """The unchanged default refuses weak rows: no fallback, no inference, no upgrade."""
    root = _weak_lake(tmp_path)

    with pytest.raises(AvailabilityContractError) as refusal:
        FeatureLoader(lake_dir=root).load_features(_AAPL, _DATES[0], _DATES[-1])

    message = str(refusal.value)
    assert "source_clock" in message
    assert WEAK_BASIS in message
    assert "one read admits one basis" in message


def test_the_default_reader_is_still_strict() -> None:
    """Construction defaults are unchanged by the reader change (ADR-0034 decision 1)."""
    loader = FeatureLoader()

    assert loader.coverage_basis == "source_clock"
    assert loader.lake_dir is None


def test_unknown_basis_is_refused() -> None:
    """A basis outside the closed vocabulary is refused, not coerced."""
    with pytest.raises(ValueError, match="Unknown coverage basis"):
        FeatureLoader(coverage_basis=cast(CoverageBasis, "event_time"))


def test_mixed_bases_refuse_under_either_request(tmp_path: Path) -> None:
    """One instrument carrying two bases refuses: neither side may reinterpret the other."""
    strict_rows = with_synthetic_feature_proof(
        pl.DataFrame(
            {
                "date": _DATES[:2],
                "close": [100.0, 100.5],
                "rsi_14": [50.0, 51.0],
                "roc_20": [0.01, 0.02],
                "feature_schema_version": [FEATURE_SCHEMA_VERSION, FEATURE_SCHEMA_VERSION],
            }
        ),
        _AAPL,
    )
    weak_rows = weak_gold_frame(_AAPL, _DATES[2:], seed=13)
    write_gold_lake(tmp_path, [strict_rows, weak_rows])
    combined = pl.concat([strict_rows.select("date", "action_coverage_basis"), weak_rows.select("date", "action_coverage_basis")])
    bases = set(combined["action_coverage_basis"].to_list())
    assert bases == {"source_clock", WEAK_BASIS}, "the fixture must actually carry two bases"

    for requested in ("source_clock", "vendor_events_no_clock"):
        loader = FeatureLoader(lake_dir=tmp_path, coverage_basis=cast(CoverageBasis, requested))
        with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
            loader.load_features(_AAPL, _DATES[0], _DATES[-1])


def test_weak_opt_in_refuses_a_strict_only_frame(tmp_path: Path) -> None:
    """The weak basis does not silently read strict rows either: the refusal is symmetric."""
    strict_rows = with_synthetic_feature_proof(
        pl.DataFrame(
            {
                "date": _DATES[:3],
                "close": [100.0, 101.0, 102.0],
                "rsi_14": [50.0, 51.0, 52.0],
                "roc_20": [0.0, 0.1, 0.2],
                "feature_schema_version": [FEATURE_SCHEMA_VERSION] * 3,
            }
        ),
        _MSFT,
    )
    write_gold_lake(tmp_path, [strict_rows])

    loader = FeatureLoader(lake_dir=tmp_path, coverage_basis="vendor_events_no_clock")
    with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
        loader.load_features(_MSFT, _DATES[0], _DATES[-1])


def _real_root_frame(*, basis: str) -> Any:
    loader = FeatureLoader(lake_dir=REAL_ROOT, coverage_basis=cast(CoverageBasis, basis))
    return loader.load_features(_AAPL, date(2024, 1, 2), date(2024, 9, 30))


@pytest.mark.skipif(not (REAL_ROOT / "03_gold" / "features").exists(), reason="the authorized real slice is not present")
def test_weak_opt_in_reads_the_real_root_read_only() -> None:
    """The authorized real slice is readable on the weak basis and refused on the strict one."""
    before = sorted(path.stat().st_mtime_ns for path in (REAL_ROOT / "03_gold" / "features").rglob("*") if path.is_file())

    frame = _real_root_frame(basis="vendor_events_no_clock")

    assert frame.height > 0
    assert set(frame["action_coverage_basis"].to_list()) == {WEAK_BASIS}
    assert frame["date"].max() <= date(2024, 9, 30)
    with pytest.raises(AvailabilityContractError):
        _real_root_frame(basis="source_clock")
    after = sorted(path.stat().st_mtime_ns for path in (REAL_ROOT / "03_gold" / "features").rglob("*") if path.is_file())
    assert before == after, "the real-root read must not write"
