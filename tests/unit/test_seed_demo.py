"""Tests for devtools/seed_demo (synthetic path; the real path needs network)."""

from __future__ import annotations

import pytest

from equity_lake.devtools.seed_demo import DEMO_UNIVERSE, resolve_universe, seed_demo  # type: ignore[import-untyped]

pytestmark = pytest.mark.slow


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
    from equity_lake.storage.delta import read_delta  # type: ignore[import-untyped]

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
    assert set(coverage["coverage_schema_version"].to_list()) == {2}
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
