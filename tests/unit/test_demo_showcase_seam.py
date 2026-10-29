"""Seam test: a demo-seeded lake must be backtestable through ``lake_dir``.

This covers the gap that let ``equity demo seed`` write Bronze rows no strict
consumer could read. Two contracts are both required before the arena can
produce a single decision: canonical identity on the price rows (ADR-0016) and a
source-scoped action coverage proof (ADR-0015). Previously every demo-seeded run
failed closed and no test exercised the seed -> loader -> arena path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from equity_lake.backtesting.arena import run_arena  # type: ignore[import-untyped]
from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE  # type: ignore[import-untyped]
from equity_lake.devtools.seed_demo import seed_demo  # type: ignore[import-untyped]
from equity_lake.devtools.seeding import trailing_business_days  # type: ignore[import-untyped]

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed"),
]

_TICKERS = ["AAPL", "MSFT"]


def test_demo_seeded_lake_is_backtestable_via_lake_dir(tmp_path: Path) -> None:
    """Seeded prices + coverage proofs let the arena read a non-canonical lake root."""
    seed_demo(years=2, tickers=_TICKERS, seed=11, lake_dir=tmp_path)

    days = trailing_business_days(2 * 365)
    start_date, end_date = days[-200], days[-20]

    outcome = run_arena(
        _TICKERS,
        start_date,
        end_date,
        markets=("us_equity",),
        strategies=["momentum"],
        cost_regimes=["zero"],
        lake_dir=tmp_path,
    )

    assert len(outcome.runs) == 1, "a demo-seeded lake must clear the point-in-time gate"
    assert outcome.runs[0].strategy == "momentum"
    assert not outcome.benchmark.is_empty(), "benchmark is built from the same shared load"
    assert outcome.data.height > 0


def test_arena_without_lake_override_reports_actionable_empty_load(tmp_path: Path) -> None:
    """An unseeded lake root fails loudly with the remedy, never a silent empty run."""
    days = trailing_business_days(365)
    with pytest.raises(ValueError, match="equity demo seed"):
        run_arena(_TICKERS, days[-10], days[-1], markets=("us_equity",), lake_dir=tmp_path / "empty")
