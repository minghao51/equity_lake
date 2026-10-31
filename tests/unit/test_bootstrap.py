"""Smoke test for cli.bootstrap sample-data generation (Delta write path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from deltalake import DeltaTable

from equity_lake.cli.bootstrap import SAMPLE_TICKERS, cmd_sample


class TestCmdSampleWrite:
    def test_synthetic_generation_writes_delta_tables(self, tmp_path: Path) -> None:
        # Point MARKET_DIRS at non-existent paths so _try_load_real_data short-circuits
        # to None, forcing the synthetic-generation branch.
        fake_dirs = {market: tmp_path / f"absent_{market}" for market in SAMPLE_TICKERS}
        with patch("equity_lake.cli.bootstrap.MARKET_DIRS", fake_dirs):
            cmd_sample(days=3, output_dir=str(tmp_path / "sample"), seed=42)

        sample_root = tmp_path / "sample"
        for market in SAMPLE_TICKERS:
            market_path = sample_root / market
            assert market_path.exists(), f"{market} delta table missing"
            assert DeltaTable.is_deltatable(str(market_path)), f"{market} is not a Delta table"

    def test_delta_tables_are_queryable(self, tmp_path: Path) -> None:
        import duckdb

        fake_dirs = {market: tmp_path / f"absent_{market}" for market in SAMPLE_TICKERS}
        with patch("equity_lake.cli.bootstrap.MARKET_DIRS", fake_dirs):
            cmd_sample(days=2, output_dir=str(tmp_path / "sample"), seed=7)

        con = duckdb.connect(":memory:")
        con.execute("INSTALL delta; LOAD delta;")
        df = con.execute(f"SELECT COUNT(*) AS n FROM delta_scan('{tmp_path / 'sample' / 'us_equity'}')").pl()
        assert int(df["n"][0]) > 0

    def test_synthetic_dates_follow_each_market_calendar(self, tmp_path: Path) -> None:
        """Each written market gets its own exchange sessions, not the Mon–Fri union (R2/F11)."""
        from equity_lake.core.calendar import trading_days_between  # type: ignore[import-untyped]
        from equity_lake.storage.delta import read_delta

        fake_dirs = {market: tmp_path / f"absent_{market}" for market in SAMPLE_TICKERS}
        with patch("equity_lake.cli.bootstrap.MARKET_DIRS", fake_dirs):
            cmd_sample(days=20, output_dir=str(tmp_path / "sample"), seed=42)

        for market in SAMPLE_TICKERS:
            frame = read_delta(market, lake_dir=tmp_path / "sample")
            dates = sorted(set(frame["date"].to_list()))
            assert dates, f"{market} has no rows"
            # Intersection semantics: no row may claim a venue-closed session.
            assert dates == trading_days_between(market, dates[0], dates[-1])
            # The widened window is large enough to still deliver `days` sessions.
            assert len(dates) == 20

    def test_mixed_venue_sample_skips_single_venue_sessions(self, tmp_path: Path) -> None:
        """hk_sg rows carry no MIC, so a date one venue is closed must not be seeded (R2)."""
        from datetime import date  # noqa: PLC0415

        from equity_lake.core.calendar import is_instrument_trading_day, trading_days_between
        from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
        from equity_lake.storage.delta import read_delta

        fake_dirs = {market: tmp_path / f"absent_{market}" for market in SAMPLE_TICKERS}
        with patch("equity_lake.cli.bootstrap.MARKET_DIRS", fake_dirs):
            cmd_sample(days=250, output_dir=str(tmp_path / "sample"), seed=42)

        dates = sorted(set(read_delta("hk_sg_equity", lake_dir=tmp_path / "sample")["date"].to_list()))
        # 2026-08-10 is Singapore National Day (XHKG trades) and 2026-07-01 is an
        # XHKG holiday (XSES trades): both are inside a 250-session window here.
        for single_venue in (date(2026, 8, 10), date(2026, 7, 1)):
            assert single_venue not in dates
        assert dates == trading_days_between("hk_sg_equity", dates[0], dates[-1])
        # Every seeded date is an open session for BOTH venues.
        for mic in ("XHKG", "XSES"):
            instrument = InstrumentKey(market="hk_sg_equity", mic=mic, symbol="TEST")
            assert [day for day in dates if not is_instrument_trading_day(instrument, day)] == []
