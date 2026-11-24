"""Range-widening path: one request per ticker, clamped to its own evidence.

The path exists because ``backfill`` is per trading day (~10k requests for forty
years). These tests pin the two things that make it safe: the write goes through the
same boundary as the daily pipeline, and no ticker is widened past its coverage.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from equity_lake.ingestion.backfill import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    widen_history_range,
)


def _coverage(rows: list[tuple[str, date, date]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [row[0] for row in rows],
            "coverage_start": [row[1] for row in rows],
            "coverage_end": [row[2] for row in rows],
        },
        schema_overrides={"coverage_start": pl.Date, "coverage_end": pl.Date},
    )


def _actions(rows: list[tuple[str, date]]) -> pl.DataFrame:
    return pl.DataFrame({"ticker": [row[0] for row in rows], "ex_date": [row[1] for row in rows]}, schema_overrides={"ex_date": pl.Date})


def test_each_ticker_is_clamped_to_its_coverage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from equity_lake.ingestion import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
        corporate_actions,
        orchestrator,
        router,
    )

    coverage = _coverage([("AAPL", date(1987, 5, 11), date(2026, 11, 12)), ("ADBE", date(1987, 3, 12), date(2005, 5, 24))])
    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", lambda market, lake_dir=None: (_actions([]), coverage))

    ranges: list[tuple[str, date, date]] = []
    writes: list[tuple[str, date, bool]] = []

    class _Fetcher:
        def __init__(self, ticker: str) -> None:
            self.ticker = ticker

        def fetch_range(self, start: date, end: date) -> pl.DataFrame:
            ranges.append((self.ticker, start, end))
            return pl.DataFrame({"ticker": [self.ticker], "date": [start]})

        def fetch_vendor_action_list(self, ticker: str) -> pl.DataFrame:
            return pl.DataFrame(
                {"ticker": [ticker], "ex_date": [date(2023, 12, 20)], "action": ["dividend"], "value": [0.1]},
                schema_overrides={"ex_date": pl.Date},
            )

    def _factory(**kwargs: Any) -> _Fetcher:
        return _Fetcher(kwargs["explicit_tickers"][0])

    monkeypatch.setattr(router, "MARKET_REGISTRY", {"us_equity": _factory})

    def _write(df: pl.DataFrame, market: str, trading_date: date, dry_run: bool, lake_dir: Path | None = None) -> bool:
        writes.append((market, trading_date, dry_run))
        return True

    monkeypatch.setattr(orchestrator, "_write_market", _write)

    outcome = widen_history_range(
        market="us_equity",
        tickers=["AAPL", "ADBE", "GLD"],
        start_date=date(1980, 1, 1),
        end_date=date(2026, 11, 18),
        lake_dir=tmp_path,
        dry_run=True,
    )

    assert ranges == [
        ("AAPL", date(1987, 5, 11), date(2026, 11, 12)),
        ("ADBE", date(1987, 3, 12), date(2005, 5, 24)),
    ]
    assert writes == [("us_equity", date(2026, 11, 12), True), ("us_equity", date(2005, 5, 24), True)]
    assert outcome["tickers"]["GLD"] == {"ok": False, "reason": "actions_not_ingested", "rows": 0}
    assert outcome["ok"] is False  # the uncovered ticker is reported, never guessed


def test_no_write_when_the_window_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from equity_lake.ingestion import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
        corporate_actions,
        orchestrator,
        router,
    )

    coverage = _coverage([("AAPL", date(2020, 1, 1), date(2020, 12, 31))])
    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", lambda market, lake_dir=None: (_actions([]), coverage))
    monkeypatch.setattr(router, "MARKET_REGISTRY", {"us_equity": lambda **kwargs: pytest.fail("no fetch expected")})
    monkeypatch.setattr(orchestrator, "_write_market", lambda *args, **kwargs: pytest.fail("no write expected"))

    outcome = widen_history_range(
        market="us_equity",
        tickers=["AAPL"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        lake_dir=tmp_path,
    )
    assert outcome["tickers"]["AAPL"] == {"ok": False, "reason": "empty_window", "rows": 0}


def _empty_frames() -> tuple[pl.DataFrame, pl.DataFrame]:
    actions = pl.DataFrame(
        schema={
            "ticker": pl.String,
            "ex_date": pl.Date,
            "action": pl.String,
            "value": pl.Float64,
            "source": pl.String,
            "ingested_at": pl.Datetime("us"),
            "source_available_at": pl.Datetime("us", "UTC"),
        }
    )
    coverage = pl.DataFrame({"symbol": pl.String, "coverage_start": pl.Date, "coverage_end": pl.Date})
    return actions, coverage


class _Meta:
    def __init__(self, symbol: str) -> None:
        self._symbol = symbol

    def instrument_key(self, market: str) -> Any:
        from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
            InstrumentKey,
        )

        return InstrumentKey(market=market, mic="ARCX", symbol=self._symbol)


class _Config:
    def get_ticker_metadata(self, symbol: str, market: str | None = None) -> _Meta | None:
        return _Meta(symbol)


def test_an_empty_vendor_list_is_proven_then_widened(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from equity_lake.ingestion import backfill as backfill_module
    from equity_lake.ingestion import corporate_actions, orchestrator, router, writers

    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", lambda market, lake_dir=None: _empty_frames())

    class _Fetcher:
        source_name = "yahoo"

        def fetch_vendor_action_list(self, ticker: str) -> pl.DataFrame | None:
            return None  # the fetch succeeded and listed no actions

        def fetch_range(self, start: date, end: date) -> pl.DataFrame:
            return pl.DataFrame({"ticker": ["GLD"], "date": [start]})

    monkeypatch.setattr(router, "MARKET_REGISTRY", {"us_equity": lambda **kwargs: _Fetcher()})
    proof_writes: list[pl.DataFrame] = []

    def _upsert(df: pl.DataFrame, table: str, trading_date: date, **kwargs: object) -> bool:
        proof_writes.append(df)
        return True

    monkeypatch.setattr(writers, "upsert_dataset", _upsert)
    price_writes: list[pl.DataFrame] = []

    def _write(df: pl.DataFrame, market: str, trading_date: date, dry_run: bool, lake_dir: Path | None = None) -> bool:
        price_writes.append(df)
        return True

    monkeypatch.setattr(orchestrator, "_write_market", _write)

    outcome = backfill_module.widen_history_range(
        market="us_equity",
        tickers=["GLD"],
        start_date=date(2004, 1, 1),
        end_date=date(2026, 11, 18),
        lake_dir=tmp_path,
        ticker_config=cast(Any, _Config()),
    )
    assert outcome["ok"] is True
    assert len(proof_writes) == 1
    proof = proof_writes[0]
    assert proof["coverage_basis"].to_list() == ["vendor_events_no_clock"]
    assert proof["source_available_at"].null_count() == 1
    assert proof["coverage_start"].to_list() == [date(2004, 1, 1)]
    assert proof["coverage_end"].to_list() == [date(2026, 11, 18)]
    assert len(price_writes) == 1


def test_a_failed_vendor_fetch_never_proves_emptiness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from equity_lake.ingestion import backfill as backfill_module
    from equity_lake.ingestion import corporate_actions, orchestrator, router, writers

    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", lambda market, lake_dir=None: _empty_frames())

    class _Fetcher:
        source_name = "yahoo"

        def fetch_vendor_action_list(self, ticker: str) -> pl.DataFrame | None:
            raise RuntimeError("provider unavailable")

        def fetch_range(self, start: date, end: date) -> pl.DataFrame:
            return pl.DataFrame({"ticker": ["GLD"], "date": [start]})

    monkeypatch.setattr(router, "MARKET_REGISTRY", {"us_equity": lambda **kwargs: _Fetcher()})
    monkeypatch.setattr(writers, "upsert_dataset", lambda *args, **kwargs: pytest.fail("no proof may be written"))
    monkeypatch.setattr(orchestrator, "_write_market", lambda *args, **kwargs: pytest.fail("no price may be written"))

    outcome = backfill_module.widen_history_range(
        market="us_equity",
        tickers=["GLD"],
        start_date=date(2004, 1, 1),
        end_date=date(2026, 11, 18),
        lake_dir=tmp_path,
        ticker_config=cast(Any, _Config()),
    )
    assert outcome["ok"] is False
    assert "action_fetch_failed" in str(outcome["tickers"]["GLD"]["reason"])
