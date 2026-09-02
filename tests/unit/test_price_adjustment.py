"""Tests for read-time corporate-action price adjustment (ADR-0011, Wave A2).

All oracles are hand-computed: adjustment factors multiply OHLC backwards in
time so that day-over-day returns across an ex-date become continuous.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from equity_lake.storage.lake_reader import factor_snapshot, with_price_adjustment


def _prices(rows: list[tuple[str, date, float, float, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "ticker": pl.String,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
        orient="row",
    )


def _actions(rows: list[tuple[str, date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"ticker": pl.String, "ex_date": pl.Date, "action": pl.String, "value": pl.Float64},
        orient="row",
    )


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
    def test_total_return_step_uses_prev_close(self) -> None:
        # $0.50 dividend with prior close 25.00 -> step 1 - 0.02 = 0.98.
        prices = _prices(
            [
                ("A", D01, 25.0, 25.0, 25.0, 25.0, 1.0),
                ("A", D02, 25.0, 25.0, 25.0, 25.0, 1.0),  # ex-date row: uses D01 close
                ("A", D03, 25.0, 25.0, 25.0, 25.0, 1.0),
            ]
        )
        actions = _actions([("A", D02, "dividend", 0.5)])
        out = with_price_adjustment(prices, actions, method="total_return")
        assert out["close"].to_list() == pytest.approx([25.0 * 0.98, 25.0, 25.0])

    def test_ex_date_own_close_never_feeds_step(self) -> None:
        # If the ex-date close differed from prev close, the step must use prev.
        prices = _prices(
            [
                ("A", D01, 20.0, 20.0, 20.0, 20.0, 1.0),
                ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0),  # ex-date: dividend 10
            ]
        )
        actions = _actions([("A", D02, "dividend", 10.0)])
        out = with_price_adjustment(prices, actions, method="total_return")
        # step = 1 - 10/20 = 0.5 (from D01 close), NOT 1 - 10/50 = 0.8.
        assert out["close"].to_list() == pytest.approx([10.0, 50.0])

    def test_dividend_without_prior_close_dropped(self) -> None:
        prices = _prices([("A", D02, 25.0, 25.0, 25.0, 25.0, 1.0)])
        actions = _actions([("A", D02, "dividend", 0.5)])
        out = with_price_adjustment(prices, actions, method="total_return")
        assert out["close"].to_list() == pytest.approx([25.0])

    def test_same_day_split_and_dividend_compose(self) -> None:
        # Split and dividend on the SAME ex-date: steps must multiply, not
        # compete for the asof match.
        prices = _prices(
            [
                ("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0),
                ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0),  # split 0.5 AND dividend $1 on this date
                ("A", D03, 50.0, 50.0, 50.0, 50.0, 1.0),
            ]
        )
        actions = _actions([("A", D02, "split", 0.5), ("A", D02, "dividend", 1.0)])
        out = with_price_adjustment(prices, actions, method="total_return")
        # Combined step for pre-event rows: 0.5 * (1 - 1/100) = 0.495.
        assert out["close"].to_list() == pytest.approx([49.5, 50.0, 50.0])

    def test_split_and_dividend_compose(self) -> None:
        prices = _prices(
            [
                ("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0),
                ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0),  # split ex-date
                ("A", D03, 50.0, 50.0, 50.0, 50.0, 1.0),  # dividend ex-date ($1, prev close 50 -> 0.98)
                ("A", D04, 50.0, 50.0, 50.0, 50.0, 1.0),
            ]
        )
        actions = _actions([("A", D02, "split", 0.5), ("A", D03, "dividend", 1.0)])
        out = with_price_adjustment(prices, actions, method="total_return")
        # D01: split 0.5 * dividend 0.98; D02: dividend 0.98; rest 1.0.
        assert out["close"].to_list() == pytest.approx([49.0, 49.0, 50.0, 50.0])


class TestGuardsAndSemantics:
    def test_as_of_hides_later_events(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0), ("A", D04, 50.0, 50.0, 50.0, 50.0, 1.0)])
        actions = _actions([("A", D04, "split", 0.5)])
        out = with_price_adjustment(prices, actions, as_of=D02)
        assert out["close"].to_list() == pytest.approx([100.0, 50.0])

    def test_as_of_keeps_earlier_events(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0), ("A", D04, 50.0, 50.0, 50.0, 50.0, 1.0)])
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions, as_of=D03)
        assert out["close"].to_list() == pytest.approx([50.0, 50.0])

    def test_unknown_method_raises(self) -> None:
        prices = _prices([("A", D01, 1.0, 1.0, 1.0, 1.0, 1.0)])
        actions = _actions([("A", D02, "split", 0.5)])
        with pytest.raises(ValueError, match="Unknown adjustment method"):
            with_price_adjustment(prices, actions, method="pandas")  # type: ignore[arg-type]

    def test_empty_actions_passthrough(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0)])
        actions = _actions([])
        out = with_price_adjustment(prices, actions)
        assert out.equals(prices)

    def test_no_ohlc_passthrough(self) -> None:
        prices = pl.DataFrame({"ticker": ["A"], "date": [D01], "volume": [1.0]})
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions)
        assert out.equals(prices)

    def test_volume_and_adj_close_untouched(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 5_000.0)]).with_columns(pl.lit(95.0).alias("adj_close"))
        actions = _actions([("A", D02, "split", 0.5)])
        out = with_price_adjustment(prices, actions)
        assert out["volume"].to_list() == [5_000.0]
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

    def test_missing_price_column_raises(self) -> None:
        prices = pl.DataFrame({"date": [D01], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})
        actions = _actions([("A", D02, "split", 0.5)])
        with pytest.raises(ValueError, match="ticker"):
            with_price_adjustment(prices, actions)

    def test_missing_action_column_raises(self) -> None:
        prices = _prices([("A", D01, 1.0, 1.0, 1.0, 1.0, 1.0)])
        with pytest.raises(ValueError, match="ex_date"):
            with_price_adjustment(prices, pl.DataFrame({"ticker": ["A"], "action": ["split"], "value": [0.5]}))

    def test_unknown_action_rows_ignored(self) -> None:
        prices = _prices([("A", D01, 100.0, 100.0, 100.0, 100.0, 1.0), ("A", D02, 50.0, 50.0, 50.0, 50.0, 1.0)])
        actions = _actions([("A", D02, "merger", 0.5)])
        out = with_price_adjustment(prices, actions)
        assert out["close"].to_list() == pytest.approx([100.0, 50.0])

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
