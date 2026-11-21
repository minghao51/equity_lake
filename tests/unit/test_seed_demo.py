"""Tests for devtools/seed_demo (synthetic path; the real path needs network)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from equity_lake.core.availability import (
    ACTION_COVERAGE_SCHEMA_VERSION,  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
)
from equity_lake.devtools.seed_demo import DEMO_UNIVERSE, resolve_universe, seed_demo  # type: ignore[import-untyped]

pytestmark = pytest.mark.slow

_TABLE = "01_bronze/market_data/us_equity"
# Venue-closed weekdays inside a rolling 2y seed window (observed Independence Day
# 2026-07-03 and Labor Day 2026-09-07) — the dates R2/F11 found bars on.
_KNOWN_CLOSURES = (date(2026, 7, 3), date(2026, 9, 7))


def _seeded_dates(tmp_path: Path) -> list[date]:
    """Seed a 2y single-ticker sample lake and return its distinct Bronze dates."""
    from equity_lake.storage.delta import read_delta  # type: ignore[import-untyped]

    seed_demo(years=2, tickers=["AAPL"], seed=17, lake_dir=tmp_path)
    return sorted(set(read_delta(_TABLE, lake_dir=tmp_path)["date"].to_list()))


def test_resolve_explicit_tickers_uppercased_and_filtered() -> None:
    assert resolve_universe(["aapl", "  msft  ", ""]) == ["AAPL", "MSFT"]


def test_resolve_default_universe_nonempty() -> None:
    out = resolve_universe(None)
    # resolves the config `demo` group (50) or the built-in default
    assert len(out) >= 20
    assert all(isinstance(t, str) and t == t.upper() for t in out)


def test_seed_demo_synthetic_writes_and_summarizes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    summary = seed_demo(years=1, tickers=["AAPL", "MSFT", "GOOGL"], seed=7, lake_dir=tmp_path)
    assert summary["source"] == "synthetic"
    assert summary["tickers"] == 3
    assert summary["days"] > 0
    assert summary["rows"] == summary["days"] * 3
    assert summary["coverage_proofs"] == 3


def test_seed_demo_data_is_readable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from equity_lake.storage.delta import read_delta

    seed_demo(years=1, tickers=["AAPL"], seed=1, lake_dir=tmp_path)
    df = read_delta("01_bronze/market_data/us_equity", lake_dir=tmp_path)
    assert df.height > 0
    assert {"ticker", "date", "open", "high", "low", "close", "volume"}.issubset(df.columns)


def test_seed_demo_stamps_canonical_identity(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Seeded Bronze rows must satisfy the same identity contract real ingestion writes."""
    from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, normalize_identity_frame  # type: ignore[import-untyped]
    from equity_lake.storage.delta import read_delta

    seed_demo(years=1, tickers=["AAPL", "MSFT"], seed=3, lake_dir=tmp_path)
    df = read_delta("01_bronze/market_data/us_equity", lake_dir=tmp_path)

    identities = df.select("market", "mic", "symbol", "identity_schema_version", "identity_provenance").unique()
    assert set(identities["market"].to_list()) == {"us_equity"}
    assert set(identities["mic"].to_list()) == {"XNAS"}
    assert set(identities["symbol"].to_list()) == {"AAPL", "MSFT"}
    assert set(identities["identity_schema_version"].to_list()) == {IDENTITY_SCHEMA_VERSION}
    assert set(identities["identity_provenance"].to_list()) == {"current_ticker_config"}
    # A strict consumer must accept the seeded rows without a provenance override.
    assert normalize_identity_frame(df).height == df.height


def test_seed_demo_writes_action_coverage_proofs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """ADR-0015 requires a coverage proof; empty action data alone is not proof."""
    from equity_lake.ingestion.corporate_actions import corporate_action_coverage_table, load_corporate_action_inputs  # type: ignore[import-untyped]

    seed_demo(years=1, tickers=["AAPL", "MSFT"], seed=5, lake_dir=tmp_path)
    actions, coverage = load_corporate_action_inputs("us_equity", lake_dir=tmp_path)

    assert actions.is_empty()  # synthetic prices are split-free by construction
    assert coverage.height == 2
    assert set(coverage["source"].to_list()) == {"synthetic_demo_seed"}
    assert set(coverage["coverage_status"].to_list()) == {"complete"}
    assert set(coverage["coverage_schema_version"].to_list()) == {ACTION_COVERAGE_SCHEMA_VERSION}
    assert (tmp_path / corporate_action_coverage_table("us_equity")).exists()


def test_seed_demo_rejects_unconfigured_symbol(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A bare ticker is not identity: an unknown symbol must fail closed, never guess a venue."""
    with pytest.raises(ValueError, match="no configured exchange identity"):
        seed_demo(years=1, tickers=["AAA"], seed=1, lake_dir=tmp_path)
    assert not (tmp_path / "01_bronze").exists()


def test_seed_demo_is_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = seed_demo(years=1, tickers=["AAPL"], seed=1, lake_dir=tmp_path)
    second = seed_demo(years=1, tickers=["AAPL"], seed=1, lake_dir=tmp_path)
    assert first["rows"] == second["rows"]  # overwrite, no duplication


def test_demo_universe_matches_config_size() -> None:
    # The built-in default and the config `demo` group are intentionally the same 50.
    assert len(DEMO_UNIVERSE) == 50


# ---------------------------------------------------------------------------
# Exchange calendar (R2/F11): a seeded lake never asserts a closed session
# ---------------------------------------------------------------------------


def test_seeded_dates_follow_exchange_calendar(tmp_path: Path) -> None:
    from equity_lake.core.calendar import is_trading_day  # type: ignore[import-untyped]

    dates = _seeded_dates(tmp_path)
    # Calendar sanity for the known closures the defect used to seed.
    assert [day for day in _KNOWN_CLOSURES if is_trading_day("us_equity", day)] == []
    in_window = [day for day in _KNOWN_CLOSURES if dates[0] <= day <= dates[-1]]
    assert in_window, f"the rolling 2y seed window {dates[0]}..{dates[-1]} must cover a known closure"
    assert [day for day in dates if not is_trading_day("us_equity", day)] == []
    assert set(in_window).isdisjoint(dates)


def test_seeded_window_has_no_missing_sessions(tmp_path: Path) -> None:
    from equity_lake.core.calendar import trading_days_between

    dates = _seeded_dates(tmp_path)
    # Nothing extra (closed sessions) and nothing missing: the seeded date set is
    # exactly the exchange sessions of its own [min, max] window.
    assert dates == trading_days_between("us_equity", dates[0], dates[-1])


def test_label_chain_finalizes_on_next_open_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two consecutive seeded sessions straddling a closure finalize on the next OPEN session.

    ADR-0014 defines the label horizon as the next **observed** session. A seeded
    bar on a closed session made ``2026-07-02`` finalize against ``2026-07-03``
    (observed Independence Day) and re-derived session lists disagreed with the
    stored rows afterwards.
    """
    import polars as pl

    from equity_lake.core.calendar import is_trading_day
    from equity_lake.core.instruments import InstrumentKey
    from equity_lake.features import run_feature_job  # type: ignore[import-untyped]
    from equity_lake.features.labels import AVAILABILITY_COLUMN  # type: ignore[import-untyped]
    from equity_lake.storage.delta import read_delta

    closure = date(2026, 7, 3)
    assert is_trading_day("us_equity", closure) is False
    before, after = date(2026, 7, 2), date(2026, 7, 6)

    seed_demo(years=2, tickers=["AAPL"], seed=13, lake_dir=tmp_path)
    # Same scoped fixture as tests/unit/test_label_lifecycle.py: point the feature
    # read/write seams at the seeded temporary lake.
    for module in ("equity_lake.features.engineering", "equity_lake.features.labels", "equity_lake.storage.delta"):
        monkeypatch.setattr(f"{module}.LAKE_DIR", tmp_path)

    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
    for day in (before, after):
        run_feature_job(tickers=["AAPL"], output_start_date=day, output_end_date=day, instruments=[instrument])

    gold = read_delta("03_gold/features", lake_dir=tmp_path)
    row = gold.filter(pl.col("date") == before)
    assert row.height == 1
    assert row[AVAILABILITY_COLUMN][0] == after


# ---------------------------------------------------------------------------
# Price-origin claim (E01-6 stage 2 / ADR-0027 D1, §4)
# ---------------------------------------------------------------------------


def test_seeded_rows_carry_a_recomputable_synthetic_origin_claim(tmp_path: Path) -> None:
    """The generator asserts `synthetic`, bound to the batch it actually produced.

    The digest is recomputed here from the **stored** rows with hand-written evidence
    parameters, so this passes only if the seeder recorded the same generator revision,
    seed, profile, identity/session scope and batch digest (ADR-0027 §4.2). It also pins
    that action-coverage provenance is not reused as price origin.

    Falsified by: storing the coverage label or a config hash as ``price_source_sha256``,
    claiming a different profile/seed/revision than it generated with, or stamping only
    some rows.
    """
    from equity_lake.core.price_provenance import (  # type: ignore[import-untyped]
        generator_evidence_digest,
        generator_evidence_payload,
        price_batch_digest,
    )
    from equity_lake.devtools.seed_demo import DEMO_PRICE_SOURCE
    from equity_lake.storage.delta import read_delta

    seed_demo(years=1, tickers=["AAPL", "MSFT"], seed=17, lake_dir=tmp_path)
    frame = read_delta(_TABLE, lake_dir=tmp_path)

    assert frame["price_provenance"].unique().to_list() == ["synthetic"]
    assert frame["price_provenance_version"].unique().to_list() == [1]
    assert frame["price_source"].unique().to_list() == [DEMO_PRICE_SOURCE] == ["generator:synthetic_demo_seed@1"]
    assert frame["price_source_sha256"].null_count() == 0

    identities = sorted({f"{market}|{mic}|{symbol}" for market, mic, symbol in frame.select("market", "mic", "symbol").unique().iter_rows()})
    expected = generator_evidence_digest(
        generator_evidence_payload(
            generator="synthetic_demo_seed",
            generator_revision="1",
            identities=identities,
            parameters={"days": frame["date"].n_unique(), "profile": "demo_v1", "seed": 17},
            price_batch_sha256=price_batch_digest(frame),
            sessions_start=min(frame["date"].to_list()),
            sessions_end=max(frame["date"].to_list()),
        )
    )
    assert frame["price_source_sha256"].unique().to_list() == [expected]
    assert frame["price_source_sha256"].unique().to_list() != ["synthetic_demo_seed"]  # not the coverage label
