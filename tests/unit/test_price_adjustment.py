"""Tests for read-time corporate-action price adjustment (ADR-0011, Wave A2).

All oracles are hand-computed: adjustment factors multiply OHLC backwards in
time so that day-over-day returns across an ex-date become continuous.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal, cast

import polars as pl
import pytest

from equity_lake.core.availability import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ActionCoverageProof,
    AvailabilityContractError,
)
from equity_lake.storage.lake_reader import factor_snapshot  # type: ignore[import-untyped]
from equity_lake.storage.lake_reader import with_price_adjustment as _strict_with_price_adjustment

_SOURCE_TIME = datetime(2023, 12, 29, 20, 0, tzinfo=UTC)


def _prices(rows: list[tuple[str, date, float, float, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity"] * len(rows),
            "mic": ["XNYS"] * len(rows),
            "symbol": [row[0] for row in rows],
            "ticker": [row[0] for row in rows],
            "date": [row[1] for row in rows],
            "open": [row[2] for row in rows],
            "high": [row[3] for row in rows],
            "low": [row[4] for row in rows],
            "close": [row[5] for row in rows],
            "volume": [row[6] for row in rows],
        },
        schema_overrides={"date": pl.Date},
    )


def _actions(rows: list[tuple[str, date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity"] * len(rows),
            "mic": ["XNYS"] * len(rows),
            "symbol": [row[0] for row in rows],
            "ticker": [row[0] for row in rows],
            "ex_date": [row[1] for row in rows],
            "action": [row[2] for row in rows],
            "value": [row[3] for row in rows],
            "source_available_at": [_SOURCE_TIME] * len(rows),
        },
        schema_overrides={"ex_date": pl.Date},
    )


def _ticker_only_action(symbol: str, ex_date: date, *, source: str = "synthetic-test-complete-window") -> pl.DataFrame:
    """Match the production corporate-action columns, which carry no MIC identity."""
    return pl.DataFrame(
        {
            "ticker": [symbol],
            "ex_date": [ex_date],
            "action": ["split"],
            "value": [0.5],
            "source": [source],
            "ingested_at": [datetime(2024, 1, 2)],
            "source_available_at": [_SOURCE_TIME],
        },
        schema_overrides={"ex_date": pl.Date},
    )


def _coverage_proof(mic: str, symbol: str, start: date, end: date, *, source: str = "synthetic-test-complete-window") -> ActionCoverageProof:
    return ActionCoverageProof(
        market="us_equity",
        mic=mic,
        symbol=symbol,
        source=source,
        coverage_start=start,
        coverage_end=end,
        source_available_at=_SOURCE_TIME,
        source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
    )


def _coverage(prices: pl.DataFrame, as_of: date | None) -> list[ActionCoverageProof]:
    if prices.is_empty():
        return []
    decision_date = as_of or max(prices["date"].to_list())
    return [
        ActionCoverageProof(
            market=market,
            mic=mic,
            symbol=symbol,
            source="synthetic-test-complete-window",
            coverage_start=min(prices.filter((pl.col("market") == market) & (pl.col("mic") == mic) & (pl.col("symbol") == symbol))["date"]),
            coverage_end=decision_date,
            source_available_at=_SOURCE_TIME,
            source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
        )
        for market, mic, symbol in prices.select("market", "mic", "symbol").unique().iter_rows()
    ]


def with_price_adjustment(
    prices: pl.DataFrame,
    actions: pl.DataFrame,
    *,
    method: Literal["split_only", "total_return"] = "split_only",
    as_of: date | None = None,
) -> pl.DataFrame:
    """Use explicit synthetic coverage fixtures with the production strict API."""
    return cast(pl.DataFrame, _strict_with_price_adjustment(prices, actions, coverage=_coverage(prices, as_of), method=method, as_of=as_of))


D01, D02, D03, D04, D05 = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)


class TestSplitAdjustment:
    def test_two_for_one_split_fixes_return(self) -> None:
        # Close 100.0 then a 2-for-1 split (ratio 0.5); post-split close 52.0.
        prices = _prices([("A", D01, 100.0, 102.0, 99.0, 100.0, 1_000.0), ("A", D02, 52.0, 53.0, 51.0, 52.0, 2_000.0)])
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions)
        # Pre-split row scaled by 0.5 -> close 50.0; post-split untouched.
        assert out["close"].to_list() == pytest.approx([50.0, 52.0])
        # Raw data showed a -48% "crash"; adjusted series is +4%.
        raw_return = 52.0 / 100.0 - 1
        adjusted_return = 52.0 / 50.0 - 1
        assert adjusted_return == pytest.approx(0.04)
        assert raw_return != pytest.approx(adjusted_return, abs=1e-6)

    def test_chained_splits_product_of_ratios(self) -> None:
        # 2-for-1 on D03, then 3-for-1 on D05.
        prices = _prices(
            [
                ("A", D01, 600.0, 600.0, 600.0, 600.0, 1.0),
                ("A", D03, 300.0, 300.0, 300.0, 300.0, 1.0),
                ("A", D05, 100.0, 100.0, 100.0, 100.0, 1.0),
            ]
        )
        actions = _actions([("A", D03, "split", 0.5), ("A", D05, "split", 1.0 / 3.0)])
        out = with_price_adjustment(prices, actions)
        # Before first split: 0.5 * (1/3); between: 1/3; after: 1.
        assert out["close"].to_list() == pytest.approx([100.0, 100.0, 100.0])

    def test_reverse_split_ratio_above_one(self) -> None:
        # 1-for-10 reverse split: value = old/new share ratio = 10.0; stored
        # price jumps 1 -> 10. Pre-split row scales ×10 so the jump vanishes.
        prices = _prices([("A", D01, 1.0, 1.0, 1.0, 1.0, 10_000.0), ("A", D02, 10.0, 10.0, 10.0, 10.0, 1_000.0)])
        actions = _actions([("A", D02, "split", 10.0)])
        out = with_price_adjustment(prices, actions)
        assert out["close"].to_list() == pytest.approx([10.0, 10.0])

    def test_split_only_ignores_dividends(self) -> None:
        prices = _prices([("A", D01, 25.0, 25.0, 25.0, 25.0, 1.0), ("A", D02, 25.0, 25.0, 25.0, 25.0, 1.0)])
        actions = _actions([("A", D02, "dividend", 0.5)])
        out = with_price_adjustment(prices, actions, method="split_only")
        assert out["close"].to_list() == pytest.approx([25.0, 25.0])


class TestDividendAdjustment:
    def test_total_return_mode_is_rejected(self) -> None:
        prices = _prices([("A", D01, 25.0, 25.0, 25.0, 25.0, 1.0)])
        actions = _actions([("A", D02, "dividend", 0.5)])
        with pytest.raises(ValueError, match="split-only"):
            with_price_adjustment(prices, actions, method="total_return")


class TestGuardsAndSemantics:
    def test_as_of_hides_later_events(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0), ("A", D02, 100.0, 100.0, 100.0, 100.0, 1.0)])
        actions = _actions([("A", D04, "split", 0.5)])
        out = with_price_adjustment(prices, actions, as_of=D02)
        assert out["close"].to_list() == pytest.approx([100.0, 100.0])

    def test_as_of_keeps_earlier_events(self) -> None:
        prices = _prices(
            [
                ("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0),
                ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0),
                ("A", D03, 50.0, 50.0, 50.0, 50.0, 1.0),
            ]
        )
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions, as_of=D03)
        assert out["close"].to_list() == pytest.approx([50.0, 50.0, 50.0])

    def test_unknown_method_raises(self) -> None:
        prices = _prices([("A", D01, 1.0, 1.0, 1.0, 1.0, 1.0)])
        actions = _actions([("A", D02, "split", 0.5)])
        with pytest.raises(ValueError, match="Unknown adjustment method"):
            invalid_method: Any = "pandas"
            _strict_with_price_adjustment(prices, actions, coverage=_coverage(prices, None), method=invalid_method)

    def test_missing_coverage_fails_closed(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0)])
        with pytest.raises(AvailabilityContractError, match="coverage is unknown"):
            _strict_with_price_adjustment(prices, _actions([]))

    def test_missing_source_validity_is_unavailable(self) -> None:
        prices = _prices([("A", D02, 100.0, 100.0, 100.0, 100.0, 1.0)])
        proof = _coverage(prices, None)[0].model_dump()
        proof.pop("source_valid_until")
        with pytest.raises(AvailabilityContractError, match="source_valid_until"):
            _strict_with_price_adjustment(prices, _actions([]), coverage=pl.DataFrame([proof]))

    def test_action_source_must_match_coverage_proof(self) -> None:
        prices = _prices([("A", D02, 100.0, 100.0, 100.0, 100.0, 1.0)])
        actions = _actions([("A", D02, "split", 0.5)]).with_columns(pl.lit("yahoo").alias("source"))
        with pytest.raises(AvailabilityContractError, match="does not match its coverage proof"):
            _strict_with_price_adjustment(prices, actions, coverage=_coverage(prices, None))

    @pytest.mark.parametrize("mic", ["XNYS", "XNAS"])
    def test_ticker_only_action_rejects_mic_collision_outside_price_scope(self, mic: str) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0)]).with_columns(pl.lit(mic).alias("mic"))
        proofs = [_coverage_proof("XNYS", "A", D01, D02), _coverage_proof("XNAS", "A", D01, D02)]
        with pytest.raises(AvailabilityContractError, match="identity is ambiguous"):
            _strict_with_price_adjustment(prices, _ticker_only_action("A", D02), coverage=proofs, as_of=D02)

    def test_ticker_only_action_resolves_unique_source_proof(self) -> None:
        prices = _prices([("UNIQUE", D01, 100.0, 100.0, 100.0, 100.0, 1.0)])
        proof = _coverage_proof("XNYS", "UNIQUE", D01, D02)
        out = _strict_with_price_adjustment(prices, _ticker_only_action("UNIQUE", D02), coverage=[proof], as_of=D02)
        assert out["close"].to_list() == pytest.approx([50.0])

    def test_duplicate_same_identity_proofs_do_not_create_ambiguity(self) -> None:
        prices = _prices([("UNIQUE", D01, 100.0, 100.0, 100.0, 100.0, 1.0)])
        proof = _coverage_proof("XNYS", "UNIQUE", D01, D02)
        out = _strict_with_price_adjustment(prices, _ticker_only_action("UNIQUE", D02), coverage=[proof, proof], as_of=D02)
        assert out["close"].to_list() == pytest.approx([50.0])

    def test_ticker_only_event_cannot_borrow_other_source_window(self) -> None:
        prices = _prices([("A", D02, 100.0, 100.0, 100.0, 100.0, 1.0)])
        requested_proof = _coverage_proof("XNYS", "A", D02, D02)
        other_window = _coverage_proof("XNYS", "A", D01, D01, source="other-provider")
        with pytest.raises(AvailabilityContractError, match="outside the requested coverage window"):
            _strict_with_price_adjustment(
                prices,
                _ticker_only_action("A", D01, source="other-provider"),
                coverage=[requested_proof, other_window],
                as_of=D02,
            )

    def test_source_expiry_must_reach_decision_session_close(self) -> None:
        prices = _prices([("A", D02, 100.0, 100.0, 100.0, 100.0, 1.0)])
        proof = _coverage(prices, None)[0].model_dump()
        proof["source_valid_until"] = datetime(2024, 1, 2, 20, 0, tzinfo=UTC)
        with pytest.raises(AvailabilityContractError, match="valid through cutoff"):
            _strict_with_price_adjustment(prices, _actions([]), coverage=pl.DataFrame([proof]))

    def test_source_issued_expiry_replaces_arbitrary_age_limit(self) -> None:
        old_day = date(2024, 2, 1)
        prices = _prices([("A", old_day, 100.0, 100.0, 100.0, 100.0, 1.0)])
        proof = _coverage(prices, None)
        out = _strict_with_price_adjustment(prices, _actions([]), coverage=proof)
        assert out["action_source_age_days"][0] > 7
        assert out["action_source_available_at"][0] == proof[0].source_available_at
        assert out["action_source_valid_until"][0] == proof[0].source_valid_until
        assert out["action_coverage_start"][0] == proof[0].coverage_start
        assert out["action_coverage_end"][0] == proof[0].coverage_end

    def test_empty_actions_passthrough(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0)])
        actions = _actions([])
        out = with_price_adjustment(prices, actions)
        assert out["close"].to_list() == prices["close"].to_list()
        assert out["action_coverage"].to_list() == [1]

    def test_no_ohlc_fails_closed(self) -> None:
        prices = pl.DataFrame({"market": ["us_equity"], "mic": ["XNYS"], "symbol": ["A"], "ticker": ["A"], "date": [D01], "volume": [1.0]})
        with pytest.raises(AvailabilityContractError, match="no OHLC"):
            _strict_with_price_adjustment(prices, _actions([]), coverage=_coverage(prices, None))

    def test_volume_scales_and_adj_close_is_untouched(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 5_000.0)]).with_columns(pl.lit(95.0).alias("adj_close"))
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions, as_of=D02)
        assert out["volume"].to_list() == [10_000.0]
        assert out["adj_close"].to_list() == [95.0]
        assert out["close"].to_list() == pytest.approx([50.0])

    def test_multi_ticker_isolation(self) -> None:
        prices = _prices(
            [
                ("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0),
                ("B", D01, 30.0, 30.0, 30.0, 30.0, 1.0),
                ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0),
                ("B", D02, 30.0, 30.0, 30.0, 30.0, 1.0),
            ]
        )
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions)
        by_ticker = {t: c for t, c in zip(out["ticker"], out["close"], strict=True)}
        assert by_ticker["A"] == pytest.approx(50.0)
        assert by_ticker["B"] == pytest.approx(30.0)  # untouched by A's split

    def test_unsorted_inputs_produce_correct_result(self) -> None:
        prices = _prices(
            [
                ("A", D03, 50.0, 50.0, 50.0, 50.0, 1.0),
                ("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0),
                ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0),
            ]
        )
        actions = _actions([("A", D03, "split", 0.5), ("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions)
        # Row order preserved: D03, D01, D02 -> factors 1.0, 0.25, 0.5.
        assert out["close"].to_list() == pytest.approx([50.0, 25.0, 25.0])

    def test_missing_price_identity_fails_closed(self) -> None:
        prices = pl.DataFrame({"date": [D01], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "ticker": ["A"]})
        with pytest.raises(AvailabilityContractError, match="price identity is unknown"):
            _strict_with_price_adjustment(prices, _actions([]), coverage=_coverage(_prices([("A", D01, 1, 1, 1, 1, 1)]), None))

    def test_missing_action_column_raises(self) -> None:
        prices = _prices([("A", D01, 1.0, 1.0, 1.0, 1.0, 1.0)])
        with pytest.raises(ValueError, match="ex_date"):
            with_price_adjustment(prices, pl.DataFrame({"ticker": ["A"], "action": ["split"], "value": [0.5]}))

    def test_unknown_action_rows_fail_closed(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0), ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0)])
        actions = _actions([("A", D02, "merger", 0.5)])
        with pytest.raises(AvailabilityContractError, match="unknown corporate action"):
            with_price_adjustment(prices, actions)

    def test_empty_prices_passthrough(self) -> None:
        prices = _prices([])
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions)
        assert out.is_empty()

    def test_row_order_preserved(self) -> None:
        prices = _prices(
            [
                ("B", D02, 1.0, 1.0, 1.0, 1.0, 1.0),
                ("A", D02, 1.0, 1.0, 1.0, 1.0, 1.0),
                ("B", D01, 1.0, 1.0, 1.0, 1.0, 1.0),
                ("A", D01, 2.0, 2.0, 2.0, 2.0, 1.0),
            ]
        )
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions)
        assert out["ticker"].to_list() == ["B", "A", "B", "A"]
        assert out["close"].to_list() == pytest.approx([1.0, 1.0, 1.0, 1.0])


class TestFactorSnapshot:
    def test_product_of_splits_up_to_date(self) -> None:
        actions = _actions(
            [
                ("A", D02, "split", 0.5),
                ("A", D05, "split", 1.0 / 3.0),
                ("A", D05, "dividend", 0.5),  # never enters a split snapshot
                ("B", D03, "split", 10.0),
            ]
        )
        out = factor_snapshot(actions, D04)
        assert out.sort("ticker")["ticker"].to_list() == ["A", "B"]
        assert out.sort("ticker")["factor"].to_list() == pytest.approx([0.5, 10.0])
        later = factor_snapshot(actions, D05)
        assert later.filter(pl.col("ticker") == "A")["factor"].to_list() == pytest.approx([0.5 * (1.0 / 3.0)])

    def test_missing_action_column_raises(self) -> None:
        with pytest.raises(ValueError, match="ex_date"):
            factor_snapshot(pl.DataFrame({"ticker": ["A"]}), D01)

    def test_unknown_ticker_absent(self) -> None:
        actions = _actions([("A", D02, "split", 0.5)])
        assert factor_snapshot(actions, D04)["ticker"].to_list() == ["A"]
