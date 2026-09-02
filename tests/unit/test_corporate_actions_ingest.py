"""Tests for corporate-actions ingestion (ADR-0011, Wave B1).

Network is mocked at the ``yf.Ticker`` boundary; lake writes go to tmp dirs
via the call-time path resolution in :func:`equity_lake.core.paths.corporate_actions_dir`.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import polars as pl
import pytest

from equity_lake.core.schemas import CORPORATE_ACTION_SCHEMA
from equity_lake.storage.delta import merge_delta, read_delta

# ---------------------------------------------------------------------------
# Fetcher mapping (mocked yfinance)
# ---------------------------------------------------------------------------


class _FakeActions:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    @property
    def actions(self) -> pd.DataFrame:
        return self._frame


class _FakeTickerFactory:
    """Returns fake ``yf.Ticker`` objects keyed by ticker."""

    def __init__(self, frames: dict[str, pd.DataFrame], errors: set[str] | None = None) -> None:
        self.frames = frames
        self.errors = errors or set()
        self.requested: list[str] = []

    def __call__(self, ticker: str) -> _FakeActions:
        self.requested.append(ticker)
        if ticker in self.errors:
            raise OSError("network down")
        return _FakeActions(self.frames.get(ticker, pd.DataFrame()))


def _actions_pandas(rows: list[tuple[date, float, float]]) -> pd.DataFrame:
    """(date, dividends, splits) rows in yfinance's actions layout."""
    return pd.DataFrame(
        {"Dividends": [d for _, d, _ in rows], "Splits": [s for _, _, s in rows]},
        index=pd.Index([d for d, _, _ in rows], name="Date"),
    )


def _make_fetcher(monkeypatch: pytest.MonkeyPatch, factory: _FakeTickerFactory, tickers: list[str]):
    from equity_lake.sources.base import YFinanceBaseFetcher

    monkeypatch.setattr("equity_lake.sources.base.yf.Ticker", factory)
    return YFinanceBaseFetcher(tickers=tickers, retry_attempts=1, retry_delay=0.0)


class TestFetchCorporateActions:
    def test_maps_dividends_and_inverts_split_multiplier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # yfinance splits: 2.0 = 2-for-1 → lake ratio 0.5.
        factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2024, 6, 1), 0.25, 0.0), (date(2020, 9, 1), 0.0, 4.0)])})
        fetcher = _make_fetcher(monkeypatch, factory, ["AAPL"])
        out = fetcher.fetch_corporate_actions()
        assert out.columns == list(CORPORATE_ACTION_SCHEMA)
        out = out.sort("ex_date")
        assert out["action"].to_list() == ["split", "dividend"]
        assert out["value"].to_list() == pytest.approx([0.25, 0.25])
        assert out["source"].unique().to_list() == ["yahoo"]
        assert out["ingested_at"].is_not_null().all()

    def test_since_is_exclusive_lower_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = _FakeTickerFactory({"A": _actions_pandas([(date(2023, 1, 1), 0.1, 0.0), (date(2024, 1, 1), 0.2, 0.0)])})
        fetcher = _make_fetcher(monkeypatch, factory, ["A"])
        out = fetcher.fetch_corporate_actions(since=date(2023, 6, 1))
        assert out["ex_date"].to_list() == [date(2024, 1, 1)]

    def test_ticker_errors_are_isolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = _FakeTickerFactory(
            {"GOOD": _actions_pandas([(date(2024, 1, 1), 0.5, 0.0)])},
            errors={"BAD"},
        )
        fetcher = _make_fetcher(monkeypatch, factory, ["BAD", "GOOD"])
        out = fetcher.fetch_corporate_actions()
        assert out["ticker"].unique().to_list() == ["GOOD"]

    def test_no_actions_yields_typed_empty_frame(self, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = _FakeTickerFactory({"A": pd.DataFrame()})
        fetcher = _make_fetcher(monkeypatch, factory, ["A"])
        out = fetcher.fetch_corporate_actions()
        assert out.is_empty()
        assert out.columns == list(CORPORATE_ACTION_SCHEMA)

    def test_zero_rows_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # yfinance sometimes emits 0.0 dividend rows; they are not events.
        factory = _FakeTickerFactory({"A": _actions_pandas([(date(2024, 1, 1), 0.0, 0.0)])})
        fetcher = _make_fetcher(monkeypatch, factory, ["A"])
        assert fetcher.fetch_corporate_actions().is_empty()


# ---------------------------------------------------------------------------
# Watermark + end-to-end ingest (tmp lake)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_actions_lake(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> object:
    """Point the corporate-actions dataset roots and the delta lake at a tmp dir."""
    tmp = tmp_path_factory.mktemp("lake")
    import equity_lake.core.paths as paths
    import equity_lake.storage.delta as delta

    monkeypatch.setattr(paths, "BRONZE_CORPORATE_ACTIONS_DIR", tmp / "01_bronze" / "corporate_actions")
    monkeypatch.setattr(paths, "SILVER_CORPORATE_ACTIONS_DIR", tmp / "02_silver" / "corporate_actions")
    monkeypatch.setattr(delta, "LAKE_DIR", tmp)
    return tmp


class TestWatermarkAndIngest:
    def test_max_stored_ex_date_missing_table(self, tmp_actions_lake: object) -> None:
        from equity_lake.ingestion.corporate_actions import max_stored_ex_date

        assert max_stored_ex_date("us_equity") is None

    def test_ingest_end_to_end_idempotent(self, tmp_actions_lake: object, monkeypatch: pytest.MonkeyPatch) -> None:
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions, max_stored_ex_date

        class _Fetcher:
            tickers = ["AAPL"]

            def fetch_corporate_actions(self, since: date | None = None) -> pl.DataFrame:
                assert since is None  # first run: full history
                return pl.DataFrame(
                    [
                        ("AAPL", date(2024, 6, 1), "dividend", 0.25, "yahoo", datetime.now().replace(microsecond=0)),
                        ("AAPL", date(2020, 9, 1), "split", 0.25, "yahoo", datetime.now().replace(microsecond=0)),
                    ],
                    schema=CORPORATE_ACTION_SCHEMA,
                    orient="row",
                )

        first = ingest_corporate_actions("us_equity", fetcher=_Fetcher())
        assert first["ok"] and first["fetched"] == 2

        bronze = read_delta("01_bronze/corporate_actions/us_equity")
        silver = read_delta("02_silver/corporate_actions/us_equity")
        assert bronze.height == 2 and silver.height == 2
        assert max_stored_ex_date("us_equity") == date(2024, 6, 1)

        # Second run: watermark bounds the fetch; overlapping rows dedupe.
        class _IncrementalFetcher:
            tickers = ["AAPL"]

            def fetch_corporate_actions(self, since: date | None = None) -> pl.DataFrame:
                assert since == date(2024, 6, 1)
                return pl.DataFrame(
                    [("AAPL", date(2024, 6, 1), "dividend", 0.25, "yahoo", datetime.now().replace(microsecond=0))],
                    schema=CORPORATE_ACTION_SCHEMA,
                    orient="row",
                )

        second = ingest_corporate_actions("us_equity", fetcher=_IncrementalFetcher())
        assert second["ok"]
        assert read_delta("01_bronze/corporate_actions/us_equity").height == 2

    def test_ingest_partitioned_by_ex_date(self, tmp_actions_lake: object) -> None:
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        class _Fetcher:
            tickers = ["AAPL"]

            def fetch_corporate_actions(self, since: date | None = None) -> pl.DataFrame:
                return pl.DataFrame(
                    [
                        ("AAPL", date(2024, 6, 1), "dividend", 0.25, "yahoo", datetime.now().replace(microsecond=0)),
                        ("AAPL", date(2020, 9, 1), "split", 0.25, "yahoo", datetime.now().replace(microsecond=0)),
                    ],
                    schema=CORPORATE_ACTION_SCHEMA,
                    orient="row",
                )

        assert ingest_corporate_actions("us_equity", fetcher=_Fetcher())["ok"]
        part_dirs = {
            p.name.split("=")[1]
            for p in (tmp_actions_lake / "01_bronze" / "corporate_actions" / "us_equity").iterdir()
            if p.name.startswith("ex_date=")
        }
        assert part_dirs == {"2020-09-01", "2024-06-01"}

    def test_dry_run_writes_nothing(self, tmp_actions_lake: object) -> None:
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        class _Fetcher:
            tickers = ["AAPL"]

            def fetch_corporate_actions(self, since: date | None = None) -> pl.DataFrame:
                return pl.DataFrame(
                    [("AAPL", date(2024, 6, 1), "dividend", 0.25, "yahoo", datetime.now().replace(microsecond=0))],
                    schema=CORPORATE_ACTION_SCHEMA,
                    orient="row",
                )

        outcome = ingest_corporate_actions("us_equity", fetcher=_Fetcher(), dry_run=True)
        assert outcome["ok"] and outcome["fetched"] == 1
        assert not (tmp_actions_lake / "01_bronze" / "corporate_actions" / "us_equity").exists()

    def test_unsupported_market_rejected(self, tmp_actions_lake: object) -> None:
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        outcome = ingest_corporate_actions("us_news")
        assert not outcome["ok"]

    def test_quality_gate_blocks_bad_rows(self, tmp_actions_lake: object) -> None:
        # A split with value <= 0 violates the pointblank schema: nothing lands.
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        class _Fetcher:
            tickers = ["AAPL"]

            def fetch_corporate_actions(self, since: date | None = None) -> pl.DataFrame:
                return pl.DataFrame(
                    [("AAPL", date(2024, 6, 1), "split", 0.0, "yahoo", datetime.now().replace(microsecond=0))],
                    schema=CORPORATE_ACTION_SCHEMA,
                    orient="row",
                )

        outcome = ingest_corporate_actions("us_equity", fetcher=_Fetcher())
        assert not outcome["ok"]
        assert not (tmp_actions_lake / "02_silver" / "corporate_actions" / "us_equity").exists()


# ---------------------------------------------------------------------------
# Merge idempotency on the ex_date-partitioned table (direct delta layer)
# ---------------------------------------------------------------------------


def _ca_frame(rows: list[tuple[str, date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [(*r, "yahoo", datetime.now().replace(microsecond=0)) for r in rows],
        schema=CORPORATE_ACTION_SCHEMA,
        orient="row",
    )


class TestDeltaLayer:
    def test_merge_idempotent_on_event_key(self, tmp_path: object) -> None:
        df = _ca_frame([("A", date(2024, 1, 1), "split", 0.5)])
        table = "01_bronze/corporate_actions/us_equity"
        assert merge_delta(df, table, key_columns=["ticker", "ex_date", "action"], lake_dir=tmp_path, partition_by=["ex_date"])
        assert merge_delta(df, table, key_columns=["ticker", "ex_date", "action"], lake_dir=tmp_path, partition_by=["ex_date"])
        assert read_delta(table, lake_dir=tmp_path).height == 1
