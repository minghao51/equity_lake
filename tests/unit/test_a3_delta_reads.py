"""Regression guard for A3: monitoring/signals/dashboard route through lake_reader.duckdb_scan_for."""

from __future__ import annotations

from pathlib import Path

import pytest

from equity_lake.core.paths import US_EQUITY_DIR, US_NEWS_DIR


class TestA3CallSitesUseLakeReader:
    def test_backtest_signal_generator_uses_lake_reader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[Path] = []

        def fake_scan(p: Path) -> str:
            calls.append(p)
            return "read_parquet('/dev/null')"

        monkeypatch.setattr("equity_lake.signals.generators.backtest.duckdb_scan_for", fake_scan)
        from equity_lake.signals.generators.backtest import BacktestSignalGenerator

        BacktestSignalGenerator({})
        assert calls and calls[0] == US_EQUITY_DIR

    def test_sentiment_signal_generator_uses_lake_reader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[Path] = []

        def fake_scan(p: Path) -> str:
            calls.append(p)
            return "read_parquet('/dev/null')"

        monkeypatch.setattr("equity_lake.signals.generators.sentiment.duckdb_scan_for", fake_scan)
        from equity_lake.signals.generators.sentiment import SentimentSignalGenerator

        SentimentSignalGenerator({})
        assert calls and calls[0] == US_NEWS_DIR
