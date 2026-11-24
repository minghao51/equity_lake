"""Hand-math oracles for the derived total-return series (ADR-0037).

These oracles *are* the specification (residual plan §2.10, ADR-0037 decision 4): every
expected value below is computed by hand from the closed-form factors, never taken from a
vendor series. No network call exists anywhere in this module.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from equity_lake.storage.total_return import (
    NO_SOURCE_CLOCK,
    TOTAL_RETURN_BASIS,
    TotalReturnSeries,
    derive_total_return,
)

RESEARCH = "vendor_events_no_clock"


def _prices(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    """Raw prices as the lake stores them: unadjusted closes keyed by (ticker, session)."""
    return pl.DataFrame(
        {
            "market": ["us_equity"] * len(rows),
            "mic": ["XNYS"] * len(rows),
            "symbol": [row[0] for row in rows],
            "ticker": [row[0] for row in rows],
            "date": [row[1] for row in rows],
            "close": [row[2] for row in rows],
        },
        schema_overrides={"date": pl.Date},
    )


def _actions(rows: list[tuple[str, date, str, float]]) -> pl.DataFrame:
    """Stored corporate actions, including the always-null source clock (ADR-0032)."""
    return pl.DataFrame(
        {
            "ticker": [row[0] for row in rows],
            "ex_date": [row[1] for row in rows],
            "action": [row[2] for row in rows],
            "value": [row[3] for row in rows],
            "source": ["yfinance"] * len(rows),
            "source_available_at": [None] * len(rows),
        },
        schema_overrides={"ex_date": pl.Date, "source_available_at": pl.Datetime("us", "UTC")},
    )


def _levels(result: TotalReturnSeries) -> list[float]:
    return result.frame["total_return_close"].to_list()


def _index(result: TotalReturnSeries) -> list[float]:
    return result.frame["total_return_index"].to_list()


def test_oracle_dividend_offsetting_the_price_drop_is_zero_total_return() -> None:
    """Oracle (i): 100 -> 90 with a 10 cash distribution at that session is 0%, not -10%/+10%."""
    prices = _prices([("AAA", date(2024, 1, 2), 100.0), ("AAA", date(2024, 1, 3), 90.0)])
    actions = _actions([("AAA", date(2024, 1, 3), "dividend", 10.0)])

    result = derive_total_return(prices, actions, basis=RESEARCH)

    # Hand math: 90 * (1 + 10/90) = 100 at the ex-session; the base session is unscaled.
    assert _levels(result) == pytest.approx([100.0, 100.0], rel=1e-12)
    assert _index(result) == pytest.approx([100.0, 100.0], rel=1e-12)
    gross_total_return = _levels(result)[1] / _levels(result)[0] - 1.0
    assert gross_total_return == pytest.approx(0.0, abs=1e-12)
    # The two wrong answers the oracle exists to exclude.
    assert gross_total_return != pytest.approx(-0.10, abs=1e-6)
    assert gross_total_return != pytest.approx(0.10, abs=1e-6)
    assert result.dividend_reinvestments == 1
    assert result.split_adjustments == 0


def test_oracle_two_for_one_split_changes_no_wealth() -> None:
    """Oracle (ii): a 2:1 split leaves the derived series flat across the split session."""
    prices = _prices([("AAA", date(2024, 6, 7), 100.0), ("AAA", date(2024, 6, 10), 50.0)])
    # Lake convention is old/new, so a 2-for-1 split is stored as 0.5 (ADR-0011/0029).
    actions = _actions([("AAA", date(2024, 6, 10), "split", 0.5)])

    result = derive_total_return(prices, actions, basis=RESEARCH)

    # Hand math: the pre-split session is multiplied by 0.5, the split session is untouched.
    assert _levels(result) == pytest.approx([50.0, 50.0], rel=1e-12)
    assert _index(result) == pytest.approx([100.0, 100.0], rel=1e-12)
    assert result.split_adjustments == 1
    assert result.dividend_reinvestments == 0

    # Without the stored split the same prices carry the phantom -50% seam.
    unsplit = derive_total_return(prices, _actions([]), basis=RESEARCH)
    assert _levels(unsplit) == pytest.approx([100.0, 50.0], rel=1e-12)


def test_oracle_three_dividends_match_the_closed_form_series() -> None:
    """Oracle (iii): three reinvestments at 1.1 each give 110 / 145.2 / 173.03 by hand."""
    prices = _prices(
        [
            ("AAA", date(2024, 1, 2), 100.0),
            ("AAA", date(2024, 1, 3), 120.0),
            ("AAA", date(2024, 1, 4), 130.0),
        ]
    )
    actions = _actions(
        [
            ("AAA", date(2024, 1, 2), "dividend", 10.0),  # 1 + 10/100  = 1.1
            ("AAA", date(2024, 1, 3), "dividend", 12.0),  # 1 + 12/120  = 1.1
            ("AAA", date(2024, 1, 4), "dividend", 13.0),  # 1 + 13/130  = 1.1
        ]
    )

    result = derive_total_return(prices, actions, basis=RESEARCH)

    # Hand math, cumulative factors 1.1 / 1.21 / 1.331:
    #   100 * 1.1 = 110.00, 120 * 1.21 = 145.20, 130 * 1.331 = 173.03
    #   index base 110 -> 100, 145.2/110*100 = 132.0, 173.03/110*100 = 157.3
    assert _levels(result) == pytest.approx([110.0, 145.2, 173.03], rel=1e-12)
    assert _index(result) == pytest.approx([100.0, 132.0, 157.3], rel=1e-12)
    assert result.dividend_reinvestments == 3
    # Session-over-session returns: 110 -> 145.2 is +32%, 145.2 -> 173.03 is +19.17...%
    assert _levels(result)[1] / _levels(result)[0] - 1.0 == pytest.approx(0.32, rel=1e-12)
    assert _levels(result)[2] / _levels(result)[1] - 1.0 == pytest.approx(173.03 / 145.2 - 1.0, rel=1e-12)


def test_oracle_four_for_one_split_and_same_session_dividend_resolve_split_first() -> None:
    """Oracle (iv): a 4:1 split plus a same-session dividend resolves split first, then dividend."""
    prices = _prices([("AAA", date(2024, 6, 7), 400.0), ("AAA", date(2024, 6, 10), 100.0)])
    actions = _actions(
        [
            ("AAA", date(2024, 6, 10), "split", 0.25),  # 4-for-1, stored old/new
            ("AAA", date(2024, 6, 10), "dividend", 2.0),
        ]
    )

    result = derive_total_return(prices, actions, basis=RESEARCH)

    # Hand math: 400 * 0.25 = 100 (split scales only the earlier session) and
    # 100 * (1 + 2/100) = 102 -> a +2% gross total return across the seam.
    assert _levels(result) == pytest.approx([100.0, 102.0], rel=1e-12)
    assert _index(result) == pytest.approx([100.0, 102.0], rel=1e-12)
    gross_total_return = _levels(result)[1] / _levels(result)[0] - 1.0
    assert gross_total_return == pytest.approx(0.02, rel=1e-12)
    assert (result.split_adjustments, result.dividend_reinvestments) == (1, 1)

    # Applying the dividend to the PRE-split close instead (1 + 2/400) would give 100.5,
    # and scaling the split's own session would give 25.5; neither may happen.
    assert _levels(result)[1] != pytest.approx(100.5, abs=1e-9)
    assert _levels(result)[1] != pytest.approx(25.5, abs=1e-9)


def test_series_is_stamped_research_only_and_declares_the_absent_clock() -> None:
    """The basis is impossible to miss: a row stamp plus metadata naming the missing clock."""
    prices = _prices([("AAA", date(2024, 1, 2), 100.0), ("AAA", date(2024, 1, 3), 110.0)])
    result = derive_total_return(prices, _actions([]), basis=RESEARCH)

    assert set(result.frame["total_return_basis"].to_list()) == {TOTAL_RETURN_BASIS}
    assert result.basis == "vendor_events_no_clock"
    assert result.clock_basis == NO_SOURCE_CLOCK == "none_declared"
    assert (result.dividend_reinvestments, result.split_adjustments) == (0, 0)
    # Every input column and the input order are preserved.
    assert result.frame.columns[: len(prices.columns)] == prices.columns
    assert result.frame.select("ticker", "date", "close").equals(prices.select("ticker", "date", "close"))


def test_derivation_is_deterministic_and_input_order_independent() -> None:
    """Same (prices, actions) in, same numbers out — regardless of the row order handed in."""
    prices = _prices(
        [
            ("AAA", date(2024, 1, 2), 100.0),
            ("AAA", date(2024, 1, 4), 130.0),
            ("AAA", date(2024, 1, 3), 120.0),
            ("BBB", date(2024, 1, 2), 40.0),
            ("BBB", date(2024, 1, 3), 20.0),
        ]
    )
    actions = _actions(
        [
            ("AAA", date(2024, 1, 3), "dividend", 12.0),
            ("BBB", date(2024, 1, 3), "split", 0.5),
        ]
    )

    first = derive_total_return(prices, actions, basis=RESEARCH)
    shuffled = derive_total_return(prices.reverse(), actions.reverse(), basis=RESEARCH)

    def _keyed(result: TotalReturnSeries) -> tuple[list[tuple[str, date]], list[float]]:
        keys = list(zip(result.frame["ticker"].to_list(), result.frame["date"].to_list(), strict=True))
        order = sorted(range(len(keys)), key=lambda position: keys[position])
        return [keys[position] for position in order], [_levels(result)[position] for position in order]

    first_keys, first_levels = _keyed(first)
    shuffled_keys, shuffled_levels = _keyed(shuffled)
    assert first_keys == shuffled_keys
    assert shuffled_levels == pytest.approx(first_levels, rel=1e-12)
    # The base-100 index is anchored per instrument at its first session in the frame.
    assert first.frame.filter(pl.col("ticker") == "BBB")["total_return_index"].to_list() == pytest.approx([100.0, 100.0], rel=1e-12)
