"""Tests for corporate-actions ingestion (ADR-0011, Wave B1).

Network is mocked at the ``yf.Ticker`` boundary; lake writes go to tmp dirs
via the call-time path resolution in :func:`equity_lake.core.paths.corporate_actions_dir`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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


def _actions_pandas(rows: list[tuple[date, float, float]], *, split_column: str = "Stock Splits") -> pd.DataFrame:
    """(date, dividends, splits) rows in yfinance's actions layout.

    Production shape: installed yfinance (>= 0.2.5x) emits ``Stock Splits``;
    ``split_column="Splits"`` builds the legacy layout for the compat test.
    """
    return pd.DataFrame(
        {"Dividends": [d for _, d, _ in rows], split_column: [s for _, _, s in rows]},
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

    def test_vendor_stock_splits_layout_recognized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """F13/W01: split-only events in the production ``Stock Splits`` layout
        produce a split row with the inverted ratio (pre-W01: zero rows)."""
        factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2020, 9, 1), 0.0, 4.0)])})
        fetcher = _make_fetcher(monkeypatch, factory, ["AAPL"])
        out = fetcher.fetch_corporate_actions()
        assert out.height == 1
        assert out["action"].to_list() == ["split"]
        assert out["value"].to_list() == pytest.approx([0.25])

    def test_legacy_splits_layout_still_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2020, 9, 1), 0.0, 4.0)], split_column="Splits")})
        fetcher = _make_fetcher(monkeypatch, factory, ["AAPL"])
        out = fetcher.fetch_corporate_actions()
        assert out.height == 1
        assert out["action"].to_list() == ["split"]

    def test_simultaneous_dividend_and_split_yields_two_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """F14/W01: a same-day dividend + split expands to two lake events —
        one row per (ticker, ex_date, action); the split no longer overrides
        the dividend."""
        factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2024, 6, 1), 0.50, 2.0)])})
        fetcher = _make_fetcher(monkeypatch, factory, ["AAPL"])
        out = fetcher.fetch_corporate_actions().sort("action")
        assert out.height == 2
        assert out["action"].to_list() == ["dividend", "split"]
        assert out["value"].to_list() == pytest.approx([0.50, 0.5])
        assert out["ex_date"].to_list() == [date(2024, 6, 1)] * 2

    def test_ingested_at_is_naive_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """W01: ``ingested_at`` follows the documented naive-UTC convention
        (CORPORATE_ACTION_SCHEMA), not machine-local time."""
        factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2024, 6, 1), 0.25, 0.0)])})
        fetcher = _make_fetcher(monkeypatch, factory, ["AAPL"])
        out = fetcher.fetch_corporate_actions()
        assert out.schema["ingested_at"] == pl.Datetime("us")
        now_utc = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        ingested = out["ingested_at"][0]
        assert ingested.tzinfo is None
        assert abs((ingested - now_utc).total_seconds()) < 5

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

            def fetch_corporate_actions(self, since: date | None = None, since_by_ticker: dict[str, date | None] | None = None) -> pl.DataFrame:
                assert since is None  # per-ticker watermarks only; first run = full history
                assert since_by_ticker == {"AAPL": None}
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

        # Second run: the per-ticker watermark bounds the fetch; overlapping rows dedupe.
        class _IncrementalFetcher:
            tickers = ["AAPL"]

            def fetch_corporate_actions(self, since: date | None = None, since_by_ticker: dict[str, date | None] | None = None) -> pl.DataFrame:
                assert since is None  # the bound is per ticker, not market-global
                assert since_by_ticker == {"AAPL": date(2024, 6, 1)}
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

            def fetch_corporate_actions(self, since: date | None = None, since_by_ticker: dict[str, date | None] | None = None) -> pl.DataFrame:
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

            def fetch_corporate_actions(self, since: date | None = None, since_by_ticker: dict[str, date | None] | None = None) -> pl.DataFrame:
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

            def fetch_corporate_actions(self, since: date | None = None, since_by_ticker: dict[str, date | None] | None = None) -> pl.DataFrame:
                return pl.DataFrame(
                    [("AAPL", date(2024, 6, 1), "split", 0.0, "yahoo", datetime.now().replace(microsecond=0))],
                    schema=CORPORATE_ACTION_SCHEMA,
                    orient="row",
                )

        outcome = ingest_corporate_actions("us_equity", fetcher=_Fetcher())
        assert not outcome["ok"]
        assert not (tmp_actions_lake / "02_silver" / "corporate_actions" / "us_equity").exists()


# ---------------------------------------------------------------------------
# W03 PR4 / F14: per-ticker scoped watermarks (vendor-shaped yfinance mocks
# through the real fetcher -> quality gate -> Delta write path)
# ---------------------------------------------------------------------------


class TestPerTickerWatermarks:
    """F14 overlap portion (W03 PR4): the fetch bound is each ticker's own
    max stored ``ex_date`` — never a market-global watermark."""

    def test_new_ticker_full_history_not_suppressed_by_other_tickers_event(self, tmp_actions_lake: object, monkeypatch: pytest.MonkeyPatch) -> None:
        """A newly scoped ticker fetches its own full history; MSFT's 2020/2024
        events are older than the market-global max (2026-09-01) that the
        pre-PR4 watermark would have used, so they must still land."""
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        seed_factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2026, 9, 1), 0.25, 0.0)])})
        first = ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, seed_factory, ["AAPL"]))
        assert first["ok"] and first["fetched"] == 1

        run2_factory = _FakeTickerFactory(
            {
                "AAPL": _actions_pandas([(date(2026, 9, 1), 0.25, 0.0)]),
                "MSFT": _actions_pandas([(date(2020, 9, 1), 0.0, 4.0), (date(2024, 6, 1), 0.30, 0.0)]),
            }
        )
        second = ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, run2_factory, ["AAPL", "MSFT"]))
        assert second["ok"]
        assert second["since_by_ticker"] == {"AAPL": date(2026, 9, 1), "MSFT": None}

        silver = read_delta("02_silver/corporate_actions/us_equity").sort(["ticker", "ex_date", "action"])
        assert silver.height == 3
        msft = silver.filter(pl.col("ticker") == "MSFT")
        assert msft["ex_date"].to_list() == [date(2020, 9, 1), date(2024, 6, 1)]
        assert msft["action"].to_list() == ["split", "dividend"]

    def test_late_event_within_ticker_window_is_picked_up(self, tmp_actions_lake: object, monkeypatch: pytest.MonkeyPatch) -> None:
        """A late older event for a known ticker, dated above *its own*
        watermark (2024-01-01) but far below another ticker's recent event
        (2026-09-01), is picked up; the stored value lands via the natural key."""
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        seed_factory = _FakeTickerFactory(
            {
                "AAPL": _actions_pandas([(date(2024, 1, 1), 0.25, 0.0)]),
                "MSFT": _actions_pandas([(date(2026, 9, 1), 1.0, 0.0)]),
            }
        )
        ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, seed_factory, ["AAPL", "MSFT"]))

        late_factory = _FakeTickerFactory(
            {
                "AAPL": _actions_pandas([(date(2024, 1, 1), 0.25, 0.0), (date(2024, 6, 1), 0.30, 0.0)]),
                "MSFT": _actions_pandas([(date(2026, 9, 1), 1.0, 0.0)]),
            }
        )
        second = ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, late_factory, ["AAPL", "MSFT"]))
        assert second["ok"] and second["fetched"] == 1

        silver = read_delta("02_silver/corporate_actions/us_equity").sort(["ticker", "ex_date", "action"])
        assert silver.height == 3
        aapl = silver.filter(pl.col("ticker") == "AAPL").sort("ex_date")
        assert aapl["value"].to_list() == pytest.approx([0.25, 0.30])
        # Stable natural keys, no duplicates.
        assert silver.select("ticker", "ex_date", "action").unique().height == 3

    def test_correction_below_ticker_watermark_is_not_redetected(self, tmp_actions_lake: object, monkeypatch: pytest.MonkeyPatch) -> None:
        """Honest provider boundary (F14): the per-ticker watermark is
        exclusive — a vendor correction at or below the ticker's own max
        stored ``ex_date`` is *not* re-detected. Pinned as documented
        behavior, not an endorsement; no unlimited correction detection."""
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        seed_factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2024, 1, 1), 0.25, 0.0), (date(2024, 6, 1), 0.50, 0.0)])})
        ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, seed_factory, ["AAPL"]))

        corrected_factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2024, 1, 1), 0.35, 0.0), (date(2024, 6, 1), 0.50, 0.0)])})
        second = ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, corrected_factory, ["AAPL"]))
        assert second["ok"] and second["fetched"] == 0  # both events are <= the watermark

        silver = read_delta("02_silver/corporate_actions/us_equity").sort("ex_date")
        assert silver["value"].to_list() == pytest.approx([0.25, 0.50])  # stored values unchanged

    def test_overlapping_rerun_is_idempotent(self, tmp_actions_lake: object, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reruns with overlapping fetch windows produce the identical table:
        stable (ticker, ex_date, action) keys, no duplicate rows, unchanged
        values, and the per-ticker watermarks persist across runs."""
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions, max_stored_ex_dates

        seed_factory = _FakeTickerFactory(
            {
                "AAPL": _actions_pandas([(date(2024, 1, 1), 0.25, 0.0)]),
                "MSFT": _actions_pandas([(date(2026, 9, 1), 1.0, 0.0)]),
            }
        )
        ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, seed_factory, ["AAPL", "MSFT"]))

        late_factory = _FakeTickerFactory(
            {
                "AAPL": _actions_pandas([(date(2024, 1, 1), 0.25, 0.0), (date(2024, 6, 1), 0.30, 0.0)]),
                "MSFT": _actions_pandas([(date(2026, 9, 1), 1.0, 0.0)]),
            }
        )
        ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, late_factory, ["AAPL", "MSFT"]))

        # Per-ticker watermarks persist per ticker (the market-global notion is gone).
        assert max_stored_ex_dates("us_equity") == {"AAPL": date(2024, 6, 1), "MSFT": date(2026, 9, 1)}

        before = read_delta("02_silver/corporate_actions/us_equity").sort(["ticker", "ex_date", "action"])
        rerun = ingest_corporate_actions("us_equity", fetcher=_make_fetcher(monkeypatch, late_factory, ["AAPL", "MSFT"]))
        assert rerun["ok"] and rerun["fetched"] == 0  # every event is <= its ticker's watermark

        after = read_delta("02_silver/corporate_actions/us_equity").sort(["ticker", "ex_date", "action"])
        assert after.height == before.height == 3
        assert after.select("ticker", "ex_date", "action", "value").equals(before.select("ticker", "ex_date", "action", "value"))

    def test_natural_key_upsert_overwrites_cleanly(self, tmp_actions_lake: object) -> None:
        """When a fetched frame re-delivers an already-stored natural key with
        a corrected value (e.g. an out-of-band redelivery inside the window),
        the upsert overwrites it — one row per (ticker, ex_date, action)."""
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        class _Fetcher:
            tickers = ["AAPL"]

            def __init__(self, rows: list[tuple[str, date, str, float]]) -> None:
                self._rows = rows

            def fetch_corporate_actions(self, since: date | None = None, since_by_ticker: dict[str, date | None] | None = None) -> pl.DataFrame:
                return _ca_frame(self._rows)

        assert ingest_corporate_actions("us_equity", fetcher=_Fetcher([("AAPL", date(2024, 6, 1), "dividend", 0.30)]))["ok"]
        # Redelivery with a corrected value plus a same-day split.
        outcome = ingest_corporate_actions(
            "us_equity",
            fetcher=_Fetcher([("AAPL", date(2024, 6, 1), "dividend", 0.35), ("AAPL", date(2024, 6, 1), "split", 0.5)]),
        )
        assert outcome["ok"] and outcome["fetched"] == 2

        silver = read_delta("02_silver/corporate_actions/us_equity").sort("action")
        assert silver.height == 2
        assert silver["action"].to_list() == ["dividend", "split"]
        assert silver["value"].to_list() == pytest.approx([0.35, 0.5])  # corrected value overwrote 0.30


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


class TestSimultaneousEventRoundTrip:
    """W01/F13/F14: vendor-shaped simultaneous events survive the full
    fetch → quality gate → Delta write/read → price-adjustment path."""

    def test_same_day_split_and_dividend_round_trip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        """Vendor-shaped simultaneous events survive fetch → quality gate →
        Delta write/read → price-adjustment. The write goes through
        ``upsert_dataset`` so the pointblank ``corporate_action`` schema gates
        the two-event frame exactly as production does (review finding)."""
        from unittest.mock import patch

        from equity_lake.ingestion.writers import upsert_dataset
        from equity_lake.storage.lake_reader import with_price_adjustment

        factory = _FakeTickerFactory({"AAPL": _actions_pandas([(date(2024, 6, 3), 0.50, 2.0)])})
        fetcher = _make_fetcher(monkeypatch, factory, ["AAPL"])
        events = fetcher.fetch_corporate_actions()
        assert events.height == 2
        assert events.schema["ingested_at"] == pl.Datetime("us")

        table = "02_silver/corporate_actions/us_equity"
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            assert upsert_dataset(events, table, date.today(), partition_by=["ex_date"])
        stored = read_delta(table, lake_dir=tmp_path).sort("action")
        assert stored.height == 2
        assert stored["action"].to_list() == ["dividend", "split"]

        # Both events are visible to the read-time adjustment helper (ADR-0011):
        # the split fixes the return across the ex-date, the dividend stays intact.
        prices = pl.DataFrame(
            {
                "ticker": ["AAPL"] * 2,
                "date": [date(2024, 6, 2), date(2024, 6, 3)],
                "open": [100.0, 51.0],
                "high": [101.0, 52.0],
                "low": [99.0, 50.0],
                "close": [100.0, 51.5],
                "volume": [1_000.0, 2_000.0],
            }
        )
        adjusted = with_price_adjustment(prices, stored.select("ticker", "ex_date", "action", "value"))
        assert adjusted["close"].to_list() == pytest.approx([50.0, 51.5])

    def test_future_ex_date_blocks_whole_batch_current_behavior(self, tmp_actions_lake: object) -> None:
        """Pinned behavior (W01): the pointblank schema rejects any future
        ex_date and the all-or-nothing quality gate discards the whole batch,
        including valid historical rows. W03 PR4 kept this rejection policy
        (it landed per-ticker watermarks only) — this test documents the
        current contract, not an endorsement."""
        from datetime import timedelta

        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        class _Fetcher:
            tickers = ["AAPL"]

            def fetch_corporate_actions(self, since: date | None = None, since_by_ticker: dict[str, date | None] | None = None) -> pl.DataFrame:
                return pl.DataFrame(
                    [
                        ("AAPL", date.today() - timedelta(days=30), "dividend", 0.25, "yahoo", datetime.now().replace(microsecond=0)),
                        ("AAPL", date.today() + timedelta(days=30), "split", 0.5, "yahoo", datetime.now().replace(microsecond=0)),
                    ],
                    schema=CORPORATE_ACTION_SCHEMA,
                    orient="row",
                )

        outcome = ingest_corporate_actions("us_equity", fetcher=_Fetcher())
        assert not outcome["ok"]
        assert not (tmp_actions_lake / "02_silver" / "corporate_actions" / "us_equity").exists()
