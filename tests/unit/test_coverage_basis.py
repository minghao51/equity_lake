"""ADR-0032: the ``vendor_events_no_clock`` coverage basis.

The weak basis is opt-in and explicitly weaker: it asserts vendor-event completeness
and carries no clock at all. These tests pin the four properties the act of
acceptance turned into obligations (decision 8): the strict basis stays the default
and refuses weak proofs, one read admits one basis, legacy rows still read as
``source_clock``, and the demo lake's behaviour is unchanged. The landed real slice is
re-verified read-only, by the reciprocal shape used when it was built.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from equity_lake.core.availability import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ACTION_COVERAGE_SCHEMA_VERSION,
    AVAILABILITY_SCHEMA_VERSION,
    ActionCoverageProof,
    AvailabilityContractError,
    CoverageBasis,
    VendorEventCoverageProof,
    action_coverage_feature_fields,
    parse_coverage_proof,
    validate_action_coverage_for_decision,
    validate_feature_action_coverage,
    validate_weak_feature_action_coverage,
)
from equity_lake.core.instruments import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    InstrumentKey,
)
from equity_lake.core.schemas import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    FEATURE_SCHEMA_VERSION,
    SYNTHETIC_COVERAGE_SOURCE,
)
from equity_lake.storage.lake_reader import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    with_price_adjustment,
)

AAPL = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
SOURCE_TIME = datetime(2024, 6, 3, 15, tzinfo=UTC)
WEAK_BASIS = "vendor_events_no_clock"
STRICT_BASIS = "source_clock"
#: The landed real slice (ADR-0028/0030); ad-hoc local data, absent in a clean clone.
REAL_ROOT = Path("data/lake_real")
REAL_WINDOW_END = date(2026, 8, 20)


def _prices(rows: list[tuple[date, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity"] * len(rows),
            "mic": ["XNAS"] * len(rows),
            "symbol": ["AAPL"] * len(rows),
            "ticker": ["AAPL"] * len(rows),
            "date": [row[0] for row in rows],
            "open": [row[1] for row in rows],
            "high": [row[1] + 1 for row in rows],
            "low": [row[1] - 1 for row in rows],
            "close": [row[1] for row in rows],
            "volume": [row[2] for row in rows],
        },
        schema_overrides={"date": pl.Date},
    )


def _actions(*, clock: bool, source: str = "yahoo") -> pl.DataFrame:
    """A 2-for-1 split (lake ratio 0.5) on 2024-06-03, optionally with a clock."""
    return pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "ex_date": [date(2024, 6, 3)],
            "action": ["split"],
            "value": [0.5],
            "source": [source],
            "source_available_at": [SOURCE_TIME if clock else None],
        },
        schema_overrides={"ex_date": pl.Date},
    )


def _strict_proof(*, source: str = "yahoo") -> ActionCoverageProof:
    return ActionCoverageProof(
        market="us_equity",
        mic="XNAS",
        symbol="AAPL",
        source=source,
        coverage_start=date(2024, 6, 1),
        coverage_end=date(2024, 6, 5),
        source_available_at=SOURCE_TIME,
        source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
    )


def _weak_proof(*, source: str = "yahoo", start: date = date(2024, 6, 1), end: date = date(2024, 6, 5)) -> VendorEventCoverageProof:
    return VendorEventCoverageProof(
        market="us_equity",
        mic="XNAS",
        symbol="AAPL",
        source=source,
        coverage_start=start,
        coverage_end=end,
    )


def _weak_feature_row(proof: VendorEventCoverageProof | None = None, *, decision: date = date(2024, 6, 3)) -> dict[str, object]:
    issued = proof or _weak_proof()
    return {
        "market": issued.market,
        "mic": issued.mic,
        "symbol": issued.symbol,
        "date": decision,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "action_coverage": 1,
        "action_source_age_days": None,
        **action_coverage_feature_fields(issued),
    }


def _strict_feature_row(*, decision: date = date(2024, 6, 3), basis: str | None = STRICT_BASIS) -> dict[str, object]:
    row: dict[str, object] = {
        "market": "us_equity",
        "mic": "XNAS",
        "symbol": "AAPL",
        "date": decision,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "action_coverage": 1,
        "action_source_age_days": 0,
        **action_coverage_feature_fields(_strict_proof()),
    }
    if basis is None:
        row.pop("action_coverage_basis")
    elif basis != STRICT_BASIS:
        row["action_coverage_basis"] = basis
    return row


class TestWeakProofModel:
    def test_weak_proof_round_trips_and_carries_no_clock(self) -> None:
        proof = _weak_proof()
        assert proof.coverage_basis == WEAK_BASIS
        assert proof.coverage_status == "vendor_events_complete"
        assert proof.coverage_schema_version == 3
        row = proof.as_row()
        assert "source_available_at" not in row
        assert "source_valid_until" not in row
        assert parse_coverage_proof(row) == proof

    def test_legacy_v2_row_implies_the_strict_basis(self) -> None:
        legacy = {
            "market": "us_equity",
            "mic": "XNAS",
            "symbol": "AAPL",
            "source": "synthetic-test-complete-window",
            "coverage_start": date(2024, 6, 1),
            "coverage_end": date(2024, 6, 5),
            "source_available_at": SOURCE_TIME,
            "source_valid_until": datetime(2030, 1, 1, tzinfo=UTC),
            "coverage_status": "complete",
            "coverage_schema_version": 2,
        }
        parsed = parse_coverage_proof(legacy)
        assert isinstance(parsed, ActionCoverageProof)
        assert parsed.coverage_basis == STRICT_BASIS
        assert parsed.coverage_schema_version == 2

    def test_strict_proof_defaults_to_the_strict_basis_and_current_version(self) -> None:
        proof = _strict_proof()
        assert proof.coverage_basis == STRICT_BASIS
        assert proof.coverage_schema_version == ACTION_COVERAGE_SCHEMA_VERSION


class TestStrictConsumersRefuseTheWeakBasis:
    def test_decision_validator_refuses_a_weak_proof(self) -> None:
        with pytest.raises(AvailabilityContractError, match=WEAK_BASIS):
            validate_action_coverage_for_decision(cast(ActionCoverageProof, _weak_proof()), AAPL, date(2024, 6, 3))

    def test_strict_feature_validator_refuses_a_weak_row(self) -> None:
        with pytest.raises(AvailabilityContractError, match=WEAK_BASIS):
            validate_feature_action_coverage(_weak_feature_row())

    def test_v5_row_without_a_basis_stamp_refuses(self) -> None:
        with pytest.raises(AvailabilityContractError, match="basis stamp is missing"):
            validate_feature_action_coverage(_strict_feature_row(basis=None))

    def test_legacy_v4_row_without_a_basis_still_reads_as_strict(self) -> None:
        row = _strict_feature_row(basis=None)
        row["availability_schema_version"] = 4
        proof = validate_feature_action_coverage(row)
        assert proof.coverage_basis == STRICT_BASIS

    def test_strict_default_read_refuses_weak_coverage(self) -> None:
        with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
            with_price_adjustment(_prices([(date(2024, 6, 2), 100.0, 1_000.0)]), _actions(clock=False), coverage=[_weak_proof()])


class TestWeakRead:
    def test_weak_read_stamps_nulls_and_basis_and_keeps_the_arithmetic(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0), (date(2024, 6, 3), 52.0, 2_000.0)])
        weak = with_price_adjustment(
            prices,
            _actions(clock=False).select("ticker", "ex_date", "action", "value", "source"),
            coverage=[_weak_proof()],
            coverage_basis=WEAK_BASIS,
            as_of=date(2024, 6, 3),
        )
        assert weak["close"].to_list() == pytest.approx([50.0, 52.0])
        assert weak["volume"].to_list() == pytest.approx([2_000.0, 2_000.0])
        assert weak["action_coverage_basis"].unique().to_list() == [WEAK_BASIS]
        assert weak["action_coverage_status"].unique().to_list() == ["vendor_events_complete"]
        assert weak["action_coverage_schema_version"].unique().to_list() == [ACTION_COVERAGE_SCHEMA_VERSION]
        assert weak["availability_schema_version"].unique().to_list() == [AVAILABILITY_SCHEMA_VERSION]
        assert weak["action_source_available_at"].null_count() == weak.height
        assert weak["action_source_valid_until"].null_count() == weak.height
        assert weak["action_source_age_days"].null_count() == weak.height

    def test_weak_and_strict_reads_agree_on_the_numbers(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0), (date(2024, 6, 3), 52.0, 2_000.0)])
        columns = ("open", "high", "low", "close", "volume")
        weak = with_price_adjustment(
            prices, _actions(clock=False), coverage=[_weak_proof()], coverage_basis=WEAK_BASIS, as_of=date(2024, 6, 3)
        ).select(columns)
        strict = with_price_adjustment(prices, _actions(clock=True), coverage=[_strict_proof()], as_of=date(2024, 6, 3)).select(columns)
        assert weak.equals(strict)

    def test_mixed_basis_coverage_refuses_under_either_basis(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0)])
        mixed = [_strict_proof(), _weak_proof()]
        with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
            with_price_adjustment(prices, _actions(clock=True), coverage=mixed, as_of=date(2024, 6, 2))
        with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
            with_price_adjustment(prices, _actions(clock=False), coverage=mixed, coverage_basis=WEAK_BASIS, as_of=date(2024, 6, 2))

    def test_weak_read_refuses_coverage_that_does_not_reach_the_window(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0), (date(2024, 6, 5), 60.0, 1_000.0)])
        with pytest.raises(AvailabilityContractError, match="no source coverage proof"):
            with_price_adjustment(
                prices,
                _actions(clock=False),
                coverage=[_weak_proof(end=date(2024, 6, 3))],
                coverage_basis=WEAK_BASIS,
                as_of=date(2024, 6, 5),
            )

    def test_weak_read_refuses_a_clocked_proof(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0)])
        with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
            with_price_adjustment(prices, _actions(clock=True), coverage=[_strict_proof()], coverage_basis=WEAK_BASIS, as_of=date(2024, 6, 2))

    def test_synthetic_rows_refuse_a_vendor_proof(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0)]).with_columns(pl.lit("synthetic").alias("price_provenance"))
        with pytest.raises(AvailabilityContractError, match="one read admits one root"):
            with_price_adjustment(prices, _actions(clock=False), coverage=[_weak_proof()], coverage_basis=WEAK_BASIS, as_of=date(2024, 6, 2))

    def test_real_rows_refuse_the_synthetic_demo_proof(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0)]).with_columns(pl.lit("unknown").alias("price_provenance"))
        with pytest.raises(AvailabilityContractError, match="one read admits one root"):
            with_price_adjustment(
                prices,
                _actions(clock=True),
                coverage=[_strict_proof(source=SYNTHETIC_COVERAGE_SOURCE)],
                as_of=date(2024, 6, 2),
            )

    def test_demo_lake_strict_read_is_unchanged(self) -> None:
        prices = _prices([(date(2024, 6, 2), 100.0, 1_000.0)]).with_columns(pl.lit("synthetic").alias("price_provenance"))
        proof = _strict_proof(source=SYNTHETIC_COVERAGE_SOURCE)
        demo_actions = _actions(clock=True, source=SYNTHETIC_COVERAGE_SOURCE)
        baseline = with_price_adjustment(prices, demo_actions, coverage=[proof], as_of=date(2024, 6, 3))
        explicit = with_price_adjustment(prices, demo_actions, coverage=[proof], as_of=date(2024, 6, 3), coverage_basis=STRICT_BASIS)
        assert baseline.equals(explicit)
        assert baseline["action_coverage_basis"].unique().to_list() == [STRICT_BASIS]
        assert baseline["action_source_age_days"].null_count() == 0


class TestWeakFeatureContract:
    def test_weak_feature_row_validates_only_against_the_weak_contract(self) -> None:
        row = _weak_feature_row()
        proof = validate_weak_feature_action_coverage(row)
        assert proof.coverage_basis == WEAK_BASIS
        with pytest.raises(AvailabilityContractError):
            validate_feature_action_coverage(row)

    def test_weak_feature_row_must_not_carry_a_clock_or_age(self) -> None:
        for column, value in (
            ("action_source_available_at", SOURCE_TIME),
            ("action_source_valid_until", SOURCE_TIME),
            ("action_source_age_days", 3),
        ):
            row = _weak_feature_row()
            row[column] = value
            with pytest.raises(AvailabilityContractError, match=column):
                validate_weak_feature_action_coverage(row)

    def test_weak_feature_row_must_cover_its_decision_date(self) -> None:
        with pytest.raises(AvailabilityContractError, match="does not cover decision date"):
            validate_weak_feature_action_coverage(_weak_feature_row(_weak_proof(end=date(2024, 6, 2)), decision=date(2024, 6, 3)))


class TestClocklessFetchIssuesWeakProofs:
    """The fetch seam (ADR-0032 decision 6): vendor list window, no synthesized clock."""

    @staticmethod
    def _fetcher(monkeypatch: pytest.MonkeyPatch, *, configured: bool = True) -> Any:
        import pandas as pd

        from equity_lake.sources.base import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
            YFinanceBaseFetcher,
        )

        class _Meta:
            def __init__(self, symbol: str) -> None:
                self._symbol = symbol

            def instrument_key(self, market: str) -> InstrumentKey:
                return InstrumentKey(market=market, mic="XNAS", symbol=self._symbol)

        class _Config:
            def get_ticker_metadata(self, symbol: str, market: str | None = None) -> _Meta | None:
                return _Meta(symbol) if configured else None

        class _Ticker:
            actions = pd.DataFrame(
                {"Dividends": [0.25, 0.0], "Stock Splits": [0.0, 4.0]},
                index=pd.Index([date(2024, 6, 1), date(2020, 9, 1)], name="Date"),
            )

        class _Factory:
            def __call__(self, ticker: str) -> _Ticker:
                return _Ticker()

        class _Fetcher(YFinanceBaseFetcher):
            market = "us_equity"

        monkeypatch.setattr("equity_lake.sources.base.yf.Ticker", _Factory())
        return _Fetcher(
            tickers=["AAPL"],
            retry_attempts=1,
            retry_delay=0.0,
            ticker_config=cast(Any, _Config()) if configured else None,
        )

    def test_proof_window_is_the_vendor_list_and_has_no_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fetcher = self._fetcher(monkeypatch)
        events = fetcher.fetch_corporate_actions(since_by_ticker={"AAPL": date(2024, 1, 1)})
        # The watermark filters what is written, never the vendor list the proof names.
        assert events["ex_date"].to_list() == [date(2024, 6, 1)]
        proofs = fetcher.action_coverage_proofs
        assert len(proofs) == 1
        assert proofs[0].coverage_start == date(2020, 9, 1)
        assert proofs[0].coverage_end == date(2024, 6, 1)
        assert proofs[0].source == "yahoo"
        assert "source_available_at" not in proofs[0].as_row()

    def test_unavailable_identity_yields_no_proof(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fetcher = self._fetcher(monkeypatch, configured=False)
        assert fetcher.fetch_corporate_actions().height == 2
        assert fetcher.action_coverage_proofs == ()

    def test_ingest_persists_weak_coverage_with_typed_null_clocks(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from equity_lake.ingestion.corporate_actions import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
            corporate_action_coverage_table,
            ingest_corporate_actions,
        )
        from equity_lake.storage.delta import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
            read_delta,
        )

        fetcher = self._fetcher(monkeypatch)
        outcome = ingest_corporate_actions("us_equity", fetcher=fetcher, lake_dir=tmp_path)
        assert outcome["ok"] is True
        assert outcome["coverage_written"] == 1
        stored = read_delta(corporate_action_coverage_table("us_equity"), lake_dir=tmp_path)
        assert stored.height == 1
        assert stored["coverage_basis"].to_list() == [WEAK_BASIS]
        assert stored["coverage_status"].to_list() == ["vendor_events_complete"]
        assert stored["coverage_schema_version"].to_list() == [ACTION_COVERAGE_SCHEMA_VERSION]
        assert stored["source_available_at"].null_count() == 1
        assert stored["source_valid_until"].null_count() == 1


@pytest.mark.skipif(not (REAL_ROOT / "01_bronze/market_data/us_equity").exists(), reason="landed real slice is local-only")
class TestLandedSliceReciprocalVerification:
    """Read-only: the landed raw rows re-adjust to the vendor's own split semantics."""

    @staticmethod
    def _loaded() -> tuple[pl.DataFrame, pl.DataFrame]:
        from equity_lake.storage.delta import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
            read_delta,
        )

        prices = read_delta("01_bronze/market_data/us_equity", lake_dir=REAL_ROOT.resolve())
        actions = read_delta("02_silver/corporate_actions/us_equity", lake_dir=REAL_ROOT.resolve())
        return prices, actions

    def _adjusted(self) -> pl.DataFrame:
        from equity_lake.storage.delta import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
            read_delta,
        )

        prices, actions = self._loaded()
        # The widened root is heterogeneous by design (ADBE ends 2005, BABA starts 2023,
        # GLD has no actions). A single read needs one coherent window, so verify the
        # original five-ticker window here; the per-ticker widening is verified separately.
        tickers = ["AAPL", "MSFT", "NVDA", "JPM", "KO"]
        prices = prices.filter(pl.col("ticker").is_in(tickers) & (pl.col("date") <= REAL_WINDOW_END))
        stored_coverage = read_delta("02_silver/corporate_action_coverage/us_equity", lake_dir=REAL_ROOT.resolve()).to_dicts()
        coverage_by_symbol = {row["symbol"]: parse_coverage_proof(row) for row in stored_coverage}
        proofs = []
        for identity in prices.select("market", "mic", "symbol").unique().sort("symbol").to_dicts():
            stored = coverage_by_symbol.get(identity["symbol"])
            if stored is not None:
                # GLD's proof is the proven-empty one; ADBE/BABA/... carry their weak proof.
                proofs.append(stored)
                continue
            events = actions.filter(pl.col("ticker") == identity["symbol"])
            proofs.append(
                VendorEventCoverageProof(
                    market=identity["market"],
                    mic=identity["mic"],
                    symbol=identity["symbol"],
                    source="yahoo",
                    coverage_start=cast(date, events["ex_date"].min()),
                    coverage_end=cast(date, events["ex_date"].max()),
                )
            )
        return cast(
            pl.DataFrame,
            with_price_adjustment(
                prices,
                # A per-instrument read scopes its own events: ticker-only rows are
                # identity-resolved against the full proof set, so an out-of-scope
                # event would otherwise refuse the whole read.
                actions.filter(pl.col("ticker").is_in(prices["symbol"].unique().to_list())).select("ticker", "ex_date", "action", "value", "source"),
                coverage=proofs,
                coverage_basis=WEAK_BASIS,
                as_of=REAL_WINDOW_END,
            ),
        )

    def test_whole_slice_adjusts_and_is_weak_stamped(self) -> None:
        prices, _ = self._loaded()
        window = prices.filter(pl.col("ticker").is_in(["AAPL", "MSFT", "NVDA", "JPM", "KO"]) & (pl.col("date") <= REAL_WINDOW_END))
        adjusted = self._adjusted()
        # The landed root is a widening target, so totals are data, not a constant:
        # every row in the verified window must adjust exactly once, no duplicates.
        assert adjusted.height == window.height
        assert not adjusted.select(pl.struct("market", "mic", "symbol", "date").is_duplicated().any()).item()
        assert adjusted["action_coverage_basis"].unique().to_list() == [WEAK_BASIS]
        assert adjusted["action_source_age_days"].null_count() == adjusted.height
        assert adjusted["action_source_available_at"].null_count() == adjusted.height

    def test_split_seams_are_exactly_the_stored_ratios(self) -> None:
        adjusted = self._adjusted()
        prices, _ = self._loaded()
        nvda_adjusted = adjusted.filter(pl.col("ticker") == "NVDA").sort("date")
        nvda_raw = prices.filter(pl.col("ticker") == "NVDA").sort("date")
        before_date, after_date = date(2024, 6, 7), date(2024, 6, 10)
        raw_before = nvda_raw.filter(pl.col("date") == before_date).row(-1, named=True)
        raw_after = nvda_raw.filter(pl.col("date") == after_date).row(-1, named=True)
        adjusted_before = nvda_adjusted.filter(pl.col("date") == before_date).row(-1, named=True)
        adjusted_after = nvda_adjusted.filter(pl.col("date") == after_date).row(-1, named=True)
        # Stored raw basis has the seam the vendor's back-adjustment removed; the weak
        # read restores it by exactly the stored 0.1 ratio, and the return across the
        # seam matches the raw series' own return (no 10x step is left).
        assert raw_before["close"] == pytest.approx(1208.88, rel=1e-6)
        assert adjusted_before["close"] == pytest.approx(raw_before["close"] * 0.1, rel=1e-6)
        assert adjusted_after["close"] == pytest.approx(raw_after["close"], rel=1e-6)
        assert adjusted_after["close"] / adjusted_before["close"] == pytest.approx(raw_after["close"] / (raw_before["close"] * 0.1), rel=1e-9)
        # The seam is gone: the adjusted return is an ordinary daily move, not the
        # 10x step the raw basis still carries across the ex-date.
        assert not 0.09 < adjusted_after["close"] / adjusted_before["close"] < 0.11
        assert adjusted_before["volume"] == pytest.approx(raw_before["volume"] / 0.1, rel=1e-6)
        assert adjusted_after["volume"] == pytest.approx(raw_after["volume"], rel=1e-6)


class TestFeatureWriterRouting:
    """ADR-0032 decision 8: weak rows pass the weak contract, never the strict one."""

    @staticmethod
    def _frame(*, basis: str | None, age: int | None) -> pl.DataFrame:
        from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION

        proof: ActionCoverageProof | VendorEventCoverageProof = _weak_proof() if basis == WEAK_BASIS else _strict_proof()
        fields = action_coverage_feature_fields(proof)
        row: dict[str, object] = {
            "market": "us_equity",
            "mic": "XNAS",
            "symbol": "AAPL",
            "ticker": "AAPL",
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "price_adjustment_version": "split-only-v1",
            "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
            "action_coverage": 1,
            "action_source_age_days": age,
            **fields,
            "date": date(2024, 6, 3),
        }
        if basis is None:
            row.pop("action_coverage_basis")
        return pl.DataFrame([row], schema_overrides={"date": pl.Date})

    def test_strict_frame_passes_the_writer(self) -> None:
        from equity_lake.ingestion.writers import validate_schema  # type: ignore[import-untyped]

        assert validate_schema(self._frame(basis=STRICT_BASIS, age=0), "03_gold/features") is True

    def test_weak_frame_passes_the_weak_contract(self) -> None:
        from equity_lake.ingestion.writers import validate_schema

        assert validate_schema(self._frame(basis=WEAK_BASIS, age=None), "03_gold/features") is True

    def test_mixed_basis_frame_is_refused(self) -> None:
        from equity_lake.ingestion.writers import validate_schema

        mixed = pl.concat([self._frame(basis=STRICT_BASIS, age=0), self._frame(basis=WEAK_BASIS, age=None)])
        assert validate_schema(mixed, "03_gold/features") is False

    def test_unstamped_v5_frame_is_refused(self) -> None:
        from equity_lake.ingestion.writers import validate_schema

        assert validate_schema(self._frame(basis=None, age=0), "03_gold/features") is False


class TestFeaturePathOptIn:
    """ADR-0032 decision 8: the feature path opts in explicitly; the default stays strict."""

    @staticmethod
    def _fixture(*, weak_actions: bool, weak_coverage: bool) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, date, date]:
        from equity_lake.core.availability import ActionCoverageProof, VendorEventCoverageProof

        start = date(2024, 1, 2)
        days = [start + timedelta(days=offset) for offset in range(120) if (start + timedelta(days=offset)).weekday() < 5][:90]
        split_day = days[60]
        prices = pl.DataFrame(
            {
                "market": ["us_equity"] * len(days),
                "mic": ["XNAS"] * len(days),
                "symbol": ["AAPL"] * len(days),
                "ticker": ["AAPL"] * len(days),
                "date": days,
                # Raw basis: the split leaves a seam (100 -> 50) the adjusted read repairs.
                "close": [100.0 + i * 0.1 if day < split_day else 50.0 + i * 0.05 for i, day in enumerate(days)],
                "open": [100.0 + i * 0.1 if day < split_day else 50.0 + i * 0.05 for i, day in enumerate(days)],
                "high": [101.0 + i * 0.1 if day < split_day else 51.0 + i * 0.05 for i, day in enumerate(days)],
                "low": [99.0 + i * 0.1 if day < split_day else 49.0 + i * 0.05 for i, day in enumerate(days)],
                "volume": [1_000.0] * len(days),
            },
            schema_overrides={"date": pl.Date},
        )
        actions = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "ex_date": [split_day],
                "action": ["split"],
                "value": [0.5],
                "source": ["yahoo"],
                "source_available_at": [None if weak_actions else datetime.combine(start, datetime.min.time(), tzinfo=UTC)],
            },
            schema_overrides={"ex_date": pl.Date},
        )
        if weak_coverage:
            proofs: list[ActionCoverageProof | VendorEventCoverageProof] = [
                VendorEventCoverageProof(market="us_equity", mic="XNAS", symbol="AAPL", source="yahoo", coverage_start=days[0], coverage_end=days[-1])
            ]
        else:
            proofs = [
                ActionCoverageProof(
                    market="us_equity",
                    mic="XNAS",
                    symbol="AAPL",
                    source="yahoo",
                    coverage_start=days[0],
                    coverage_end=days[-1],
                    source_available_at=datetime.combine(days[0], datetime.min.time(), tzinfo=UTC),
                    source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
                )
            ]
        coverage = pl.DataFrame([proof.as_row() for proof in proofs])
        return prices, actions, coverage, days[0], days[-1]

    def _compute(self, basis: CoverageBasis, *, weak_actions: bool, weak_coverage: bool) -> pl.DataFrame:
        from equity_lake.features.engineering import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
            FeatureEngineer,
        )

        prices, actions, coverage, _, _ = self._fixture(weak_actions=weak_actions, weak_coverage=weak_coverage)
        engineer = FeatureEngineer()
        try:
            return cast(
                pl.DataFrame,
                engineer._compute_point_in_time_technical(  # noqa: SLF001 - the opt-in lives here
                    prices,
                    AAPL,
                    actions,
                    coverage,
                    compute_target=False,
                    coverage_basis=basis,
                ),
            )
        finally:
            engineer.close()

    def test_weak_basis_feature_rows_are_weak_stamped_and_writer_acceptable(self) -> None:
        from equity_lake.ingestion.writers import validate_schema

        out = self._compute(WEAK_BASIS, weak_actions=True, weak_coverage=True)
        assert out["action_coverage_basis"].unique().to_list() == [WEAK_BASIS]
        assert out["action_coverage_status"].unique().to_list() == ["vendor_events_complete"]
        assert out["action_source_age_days"].null_count() == out.height
        assert out["action_source_available_at"].null_count() == out.height
        assert validate_schema(out, "03_gold/features") is True

    def test_strict_default_refuses_weak_coverage(self) -> None:
        with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
            self._compute(STRICT_BASIS, weak_actions=False, weak_coverage=True)

    def test_weak_basis_refuses_strict_coverage(self) -> None:
        with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
            self._compute(WEAK_BASIS, weak_actions=True, weak_coverage=False)

    def test_strict_default_on_strict_coverage_is_unchanged(self) -> None:
        out = self._compute(STRICT_BASIS, weak_actions=False, weak_coverage=False)
        assert out["action_coverage_basis"].unique().to_list() == [STRICT_BASIS]
        assert out["action_coverage_status"].unique().to_list() == ["complete"]
        assert out["action_source_age_days"].null_count() == 0


class TestCorporateActionsIngestWiring:
    """The factory path must carry the ticker config, or no proof can resolve."""

    def test_factory_path_receives_the_current_ticker_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from equity_lake.core.schemas import CORPORATE_ACTION_SCHEMA
        from equity_lake.ingestion import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
            router,
        )
        from equity_lake.ingestion.corporate_actions import ingest_corporate_actions

        captured: dict[str, object] = {}

        class _Fetcher:
            tickers = ["AAPL"]
            action_coverage_proofs: tuple[object, ...] = ()

            def fetch_corporate_actions(self, **kwargs: object) -> pl.DataFrame:
                return pl.DataFrame(schema=CORPORATE_ACTION_SCHEMA)

        def _factory(**kwargs: object) -> _Fetcher:
            captured.update(kwargs)
            return _Fetcher()

        monkeypatch.setattr(router, "MARKET_REGISTRY", {"us_equity": _factory})
        outcome = ingest_corporate_actions("us_equity", tickers=["AAPL"], lake_dir=tmp_path)
        assert outcome["ok"] is True
        assert captured["ticker_config"] is not None
