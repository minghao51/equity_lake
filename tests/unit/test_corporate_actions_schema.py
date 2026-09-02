"""Corporate-actions contract tests (Wave A1, ADR-0011).

Covers the ``CorporateActionSchema`` pointblank contract (each violation
fails naming its step), the empty-frame pass-through, and the writer wiring
that routes corporate-actions datasets to dedupe keys, column checks, and the
``corporate_action`` quality schema.
"""

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from equity_lake.core.schemas import CORPORATE_ACTION_COLUMNS
from equity_lake.ingestion import writers
from equity_lake.validation.schemas import SCHEMA_REGISTRY, CorporateActionSchema

_CORPORATE_ACTION_MARKETS = ("corporate_actions", "01_bronze/corporate_actions", "02_silver/corporate_actions")


def _valid_actions(n: int = 3) -> pl.DataFrame:
    rows = [
        {
            "ticker": "AAPL",
            "ex_date": date(2020, 8, 31),
            "action": "split",
            "value": 0.25,
            "source": "yahoo",
            "ingested_at": datetime(2026, 6, 2, 12, 0),
        },
        {
            "ticker": "AAPL",
            "ex_date": date(2024, 2, 9),
            "action": "dividend",
            "value": 0.25,
            "source": "yahoo",
            "ingested_at": datetime(2026, 6, 2, 12, 0),
        },
        {
            "ticker": "MSFT",
            "ex_date": date(2024, 3, 14),
            "action": "dividend",
            "value": 0.83,
            "source": "yahoo",
            "ingested_at": datetime(2026, 6, 2, 12, 0),
        },
    ]
    return pl.DataFrame(rows[:n])


class TestCorporateActionSchema:
    def test_valid_frame_passes(self) -> None:
        out = CorporateActionSchema.validate(_valid_actions())
        assert out.height == 3

    def test_empty_frame_passes(self) -> None:
        """Empty frames pass without interrogation (PointblankSchema base behavior)."""
        out = CorporateActionSchema.validate(_valid_actions().head(0))
        assert out.is_empty()

    def test_registry_entry(self) -> None:
        assert SCHEMA_REGISTRY["corporate_action"] is CorporateActionSchema

    @pytest.mark.parametrize("column", ["ticker", "ex_date", "action", "value"])
    def test_null_required_column_fails_naming_step(self, column: str) -> None:
        df = _valid_actions(1).with_columns(pl.lit(None).alias(column))
        with pytest.raises(ValueError, match=column):
            CorporateActionSchema.validate(df)

    def test_unknown_action_fails_naming_step(self) -> None:
        df = _valid_actions(1).with_columns(pl.lit("merger").alias("action"))
        with pytest.raises(ValueError, match="set of `dividend`, `split`"):
            CorporateActionSchema.validate(df)

    def test_negative_dividend_fails_naming_step(self) -> None:
        df = _valid_actions(1).with_columns(pl.lit(-0.25).alias("value"))
        with pytest.raises(ValueError, match="`value` should be >= `0`"):
            CorporateActionSchema.validate(df)

    def test_split_with_zero_value_fails_naming_step(self) -> None:
        """A split ratio of 0 passes value >= 0 but fails the split-positivity step."""
        df = _valid_actions(1).with_columns(pl.lit("split").alias("action"), pl.lit(0.0).alias("value"))
        with pytest.raises(ValueError, match="Split rows must have a strictly positive ratio"):
            CorporateActionSchema.validate(df)

    def test_future_ex_date_fails_naming_step(self) -> None:
        df = _valid_actions(1).with_columns(pl.lit(date.today() + timedelta(days=1)).alias("ex_date"))
        with pytest.raises(ValueError, match="ex_date must not be in the future"):
            CorporateActionSchema.validate(df)

    def test_duplicate_composite_key_fails_naming_step(self) -> None:
        df = pl.concat([_valid_actions(1), _valid_actions(1)])
        assert df.height == 2
        with pytest.raises(ValueError, match=r"\(ticker, ex_date, action\) rows must be unique"):
            CorporateActionSchema.validate(df)


class TestCorporateActionsWriterWiring:
    """The three routable names share dedupe keys, column checks, and quality schema."""

    def test_dedupe_key_columns(self) -> None:
        for market in _CORPORATE_ACTION_MARKETS:
            assert writers._dedupe_key_columns(market) == ["ticker", "ex_date", "action"], market

    def test_quality_data_type(self) -> None:
        for market in _CORPORATE_ACTION_MARKETS:
            assert writers._quality_data_type(market) == "corporate_action", market

    def test_validate_schema_required_columns(self) -> None:
        df = _valid_actions()
        for market in _CORPORATE_ACTION_MARKETS:
            assert writers.validate_schema(df, market), market
            for col in CORPORATE_ACTION_COLUMNS:
                assert not writers.validate_schema(df.drop(col), market), f"{market} missing {col}"

    def test_validate_schema_rejects_all_null_required_column(self) -> None:
        df = _valid_actions(1).with_columns(pl.lit(None).alias("value"))
        assert not writers.validate_schema(df, "corporate_actions")
