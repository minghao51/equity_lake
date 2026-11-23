"""RS-R01: the scale harness refuses ceilings, keeps parity and never invents a counter.

Every test here uses a tiny slice of the frozen three-year design so the suite stays fast; the
*measured* 5/50/200 points are produced by ``python -m equity_lake.ml.r01_scale`` inside the
frozen ceilings and recorded under ``docs/research/``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from equity_lake.backtesting.protocol import ProtocolError  # type: ignore[import-untyped]
from equity_lake.ml.r01_controls import freeze_r01_protocol  # type: ignore[import-untyped]
from equity_lake.ml.r01_scale import (  # type: ignore[import-untyped]
    MINIMUM_UNIVERSE,
    SCALE_WARMUP_SESSIONS,
    ScaleCeilings,
    ScaleError,
    economics_parity,
    feature_parity,
    run_scale,
    run_scale_point,
    scale_panel,
    scale_pipeline,
    scale_universe,
    shipped_pool,
    three_year_sessions,
)

RECORDED_AT = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)
TINY_SESSIONS = three_year_sessions()[:80]


def test_an_over_budget_scale_point_refuses() -> None:
    """A point beyond its frozen per-size ceiling refuses instead of continuing."""
    with pytest.raises(ScaleError) as excinfo:
        run_scale_point(5, sessions=TINY_SESSIONS, ceilings=ScaleCeilings(per_size_seconds=1e-9))
    assert "beyond the frozen per-size ceiling" in str(excinfo.value)


def test_an_rss_ceiling_breach_refuses() -> None:
    with pytest.raises(ScaleError) as excinfo:
        run_scale_point(5, sessions=TINY_SESSIONS, peak_rss_reader=lambda: 10**13)
    assert "beyond the frozen ceiling" in str(excinfo.value)


def test_an_unmonitorable_rss_aborts_rather_than_guessing() -> None:
    def unavailable() -> int:
        raise ScaleError("peak RSS cannot be monitored on this host")

    with pytest.raises(ScaleError):
        run_scale_point(5, sessions=TINY_SESSIONS, peak_rss_reader=unavailable)


def test_an_unmeasurable_host_ram_aborts() -> None:
    def unavailable() -> int:
        raise ScaleError("host RAM cannot be measured")

    with pytest.raises(ScaleError):
        run_scale_point(5, sessions=TINY_SESSIONS, host_ram_reader=unavailable)


def test_unavailable_io_counters_carry_a_reason_and_never_zeros() -> None:
    reason = "the host exposes no /proc/self/io, so I/O counters are unavailable (never reported as zero)"
    point = run_scale_point(5, sessions=TINY_SESSIONS, io_reader=lambda: {"available": False, "reason": reason})
    assert point["io"]["available"] is False
    assert point["io"]["reason"] == reason
    assert "read_bytes" not in point["io"] and "write_bytes" not in point["io"]


def test_a_measured_point_records_every_required_field() -> None:
    point = run_scale_point(5, sessions=TINY_SESSIONS)
    for field in (
        "wall_seconds",
        "peak_rss_bytes",
        "rss_ceiling_bytes",
        "io",
        "instruments",
        "sessions",
        "warmup_sessions",
        "decision_sessions",
        "minimum_universe",
        "long_decisions",
        "parity",
        "estimand",
    ):
        assert field in point
    assert point["instruments"] == 5
    assert point["warmup_sessions"] == SCALE_WARMUP_SESSIONS
    assert point["minimum_universe"] == MINIMUM_UNIVERSE
    assert point["estimand"].startswith("mean_per_session_net_return")
    assert point["censored"] is False
    assert point["io"]["available"] in (True, False)
    if not point["io"]["available"]:
        assert point["io"]["reason"]


def test_shared_instruments_keep_identical_fixtures_across_scale_sizes() -> None:
    names = [symbol for _market, symbol in scale_universe(5)]
    five = feature_parity(scale_panel(5, sessions=TINY_SESSIONS), instruments=names)
    eight = feature_parity(scale_panel(8, sessions=TINY_SESSIONS), instruments=names)
    assert five == eight


def test_the_vectorised_economics_matches_the_reference_path() -> None:
    panel = scale_panel(5, sessions=TINY_SESSIONS)
    measured = scale_pipeline(panel)
    parity = economics_parity(panel, measured["decisions"])
    assert parity["equal_within_1e12"] is True


def test_the_scale_universe_is_distinct_instruments_and_reaches_two_hundred() -> None:
    pool = shipped_pool()
    assert len(pool) == len(set(pool))
    assert len(pool) == 104
    for size in (5, 50, 200):
        universe = scale_universe(size)
        assert len(universe) == size
        assert len(set(universe)) == size
    assert scale_universe(5) == pool[:5]
    assert scale_universe(200)[:104] == pool
    assert scale_universe(200)[-1][1].startswith("SCALE")


def test_a_measured_point_records_the_fixture_diagnostics() -> None:
    point = run_scale_point(5, sessions=TINY_SESSIONS)
    diagnostics = point["fixture_diagnostics"]
    assert diagnostics["degenerate_paths"] == 0
    assert diagnostics["price_floor"] > 0.0
    assert "not interpretable" in point["economic_note"]


def test_an_unfrozen_protocol_refuses_the_scale_sweep(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError) as excinfo:
        run_scale(protocol_root=tmp_path, sizes=[5])
    assert "assumptions are absent" in str(excinfo.value)


def test_a_size_beyond_the_frozen_budget_refuses(tmp_path: Path) -> None:
    freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    with pytest.raises(ScaleError) as excinfo:
        run_scale(protocol_root=tmp_path, sizes=[201])
    assert "exceed the frozen resource budget" in str(excinfo.value)


def test_the_sweep_censors_after_a_ceiling_breach_instead_of_continuing(tmp_path: Path) -> None:
    freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)

    def breaching(size: int, **kwargs: object) -> dict[str, object]:
        return {
            "size": size,
            "wall_seconds": 10**6,
            "peak_rss_bytes": 1024,
            "host_ram_bytes": 8 * 1024**3,
            "io": {"available": True, "read_bytes": 0, "write_bytes": 0},
            "parity": {"features": "a", "technical_decisions": "b"},
        }

    payload = run_scale(protocol_root=tmp_path, sizes=[5, 50], subprocess_isolation=False, in_process=breaching)
    assert payload["verification_status"] == "censored"
    assert payload["points"] == []
    assert [row["size"] for row in payload["censored"]] == [5, 50]
    assert all("ceiling" in str(row["reason"]) for row in payload["censored"])


def test_a_complete_sweep_records_verified_scale_evidence(tmp_path: Path) -> None:
    freeze_r01_protocol(recorded_at=RECORDED_AT, root=tmp_path)
    payload = run_scale(
        protocol_root=tmp_path,
        sizes=[5, 8],
        subprocess_isolation=False,
        in_process=lambda size, **kwargs: run_scale_point(size, sessions=TINY_SESSIONS, **kwargs),
    )
    assert payload["verification_status"] == "verified"
    assert len(payload["points"]) == 2
    assert payload["parity"]["shared_instrument_features_equal"] is True
    assert payload["parity"]["shared_instrument_decisions_equal"] is True
