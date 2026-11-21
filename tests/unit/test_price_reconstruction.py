"""ADR-0029 Part I — raw-price reconstruction at the ingest write boundary.

The vendor basis is split-back-adjusted while the lake's adjusted read
(`with_price_adjustment`, ADR-0015) assumes raw prices, so a real-root price write
reconstructs raw prices first and **refuses** when the split actions cannot be proven.
Every test names the mutation it falsifies: dropping the inverse transform, dropping any
one refusal, or dropping the real-root gate changes an assertion here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from equity_lake.core import paths  # type: ignore[import-untyped]
from equity_lake.core.polars_utils import FrameLike, ensure_polars  # type: ignore[import-untyped]
from equity_lake.ingestion import orchestrator  # type: ignore[import-untyped]
from equity_lake.ingestion.corporate_actions import corporate_actions_table  # type: ignore[import-untyped]
from equity_lake.ingestion.price_reconstruction import (  # type: ignore[import-untyped]
    SplitReconstructionError,
    reconstruct_vendor_prices,
)
from equity_lake.ingestion.writers import upsert_dataset  # type: ignore[import-untyped]

SPLIT_EX_DATE = date(2024, 6, 10)
SPLIT_VALUE = 0.5  # lake convention old/new for a 2-for-1 split

_ACTION_SCHEMA: dict[str, Any] = {
    "ticker": pl.String,
    "ex_date": pl.Date,
    "action": pl.String,
    "value": pl.Float64,
    "source": pl.String,
    "ingested_at": pl.Datetime("us"),
    "source_available_at": pl.Datetime("us", "UTC"),
}


def _empty_actions() -> pl.DataFrame:
    """A typed zero-row action frame (an empty vendor list, not a missing column set)."""
    return pl.DataFrame(schema=_ACTION_SCHEMA)


def _actions(rows: list[dict[str, object]]) -> pl.DataFrame:
    stamped = [
        {
            "source": "yahoo",
            "ingested_at": datetime(2026, 11, 17, 9, 0),
            "source_available_at": None,
            **row,
        }
        for row in rows
    ]
    return pl.DataFrame(stamped, schema_overrides=_ACTION_SCHEMA)


def _vendor_frame() -> pl.DataFrame:
    """Vendor rows for a 2-for-1 split: pre-split OHLC is halved and volume doubled.

    Raw basis is close 1000/volume 1000 before ``SPLIT_EX_DATE`` and 500/2000 after it.
    The vendor series is continuous at 500 because the vendor back-adjusts — that
    continuity is exactly what makes an unstamped write silently double-adjusted.
    """
    return pl.DataFrame(
        {
            "ticker": ["AAA", "AAA"],
            "date": [date(2024, 6, 7), date(2024, 6, 10)],
            "open": [500.0, 500.0],
            "high": [510.0, 510.0],
            "low": [490.0, 490.0],
            "close": [500.0, 500.0],
            "volume": [2000, 2000],
            "adj_close": [499.0, 500.0],
            "market": ["us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS"],
            "symbol": ["AAA", "AAA"],
        }
    )


def _split_actions(**overrides: object) -> pl.DataFrame:
    row: dict[str, object] = {"ticker": "AAA", "ex_date": SPLIT_EX_DATE, "action": "split", "value": SPLIT_VALUE}
    row.update(overrides)
    return _actions([row])


class TestSplitSeamReconstruction:
    def test_pre_split_rows_are_re_inflated_and_post_split_rows_are_not(self) -> None:
        """The raw basis carries the split step the vendor removed.

        Falsified by: storing the vendor frame as fetched (the pre-split close stays
        500 and `with_price_adjustment` then halves it a second time).
        """
        reconstructed = reconstruct_vendor_prices(_vendor_frame(), actions=_split_actions())
        assert reconstructed["close"].to_list() == [1000.0, 500.0]
        assert reconstructed["open"].to_list() == [1000.0, 500.0]
        assert reconstructed["high"].to_list() == [1020.0, 510.0]
        assert reconstructed["low"].to_list() == [980.0, 490.0]

    def test_volume_is_inverted_against_ohlc(self) -> None:
        """Volume is divided by the back-adjustment factor, not multiplied by it.

        Falsified by: scaling volume the same way as OHLC (raw volume would be 500).
        """
        reconstructed = reconstruct_vendor_prices(_vendor_frame(), actions=_split_actions())
        assert reconstructed["volume"].to_list() == [1000, 2000]

    def test_reapplying_the_adjustment_reproduces_the_vendor_basis(self) -> None:
        """ADR-0029 decision 7(ii): the transform is the exact inverse of the read.

        Falsified by: an off-by-one on the ex-date boundary or an inverted ratio — either
        way `reconstructed * value` would no longer reproduce the vendor close.
        """
        vendor = _vendor_frame()
        reconstructed = reconstruct_vendor_prices(vendor, actions=_split_actions())
        factors = [SPLIT_VALUE if day < SPLIT_EX_DATE else 1.0 for day in reconstructed["date"].to_list()]
        recovered = [close * factor for close, factor in zip(reconstructed["close"].to_list(), factors, strict=True)]
        for recovered_close, vendor_close in zip(recovered, vendor["close"].to_list(), strict=True):
            assert abs(recovered_close - vendor_close) <= 1e-9 * abs(vendor_close)

    def test_the_ex_date_row_is_not_adjusted(self) -> None:
        """A split's own session is already post-split at the vendor.

        Falsified by: using `ex_date >= row date` instead of strictly after it, which
        would inflate the ex-date row by the ratio as well.
        """
        ex_date_only = pl.DataFrame({"ticker": ["AAA"], "date": [SPLIT_EX_DATE], "open": [500.0], "close": [500.0], "volume": [2000]})
        reconstructed = reconstruct_vendor_prices(ex_date_only, actions=_split_actions())
        assert reconstructed["close"].to_list() == [500.0]
        assert reconstructed["volume"].to_list() == [2000]

    def test_adj_close_and_column_order_are_untouched(self) -> None:
        """decisions 4 and 6: `adj_close` as fetched, no new column, no stamp.

        Falsified by: adjusting or dropping `adj_close`, or stamping a
        `price_adjustment_version` that the adjusted read then refuses.
        """
        vendor = _vendor_frame()
        reconstructed = reconstruct_vendor_prices(vendor, actions=_split_actions())
        assert reconstructed.columns == vendor.columns
        assert reconstructed["adj_close"].to_list() == vendor["adj_close"].to_list()
        assert "price_adjustment_version" not in reconstructed.columns

    def test_a_ticker_without_splits_is_returned_unchanged(self) -> None:
        """No split is a valid outcome; dividends alone leave prices alone."""
        vendor = _vendor_frame()
        dividends_only = _actions([{"ticker": "AAA", "ex_date": date(2026, 8, 1), "action": "dividend", "value": 0.25}])
        reconstructed = reconstruct_vendor_prices(vendor, actions=dividends_only)
        assert reconstructed.equals(vendor)

    def test_reconstruction_is_deterministic(self) -> None:
        """decision 3: the transform is a pure function of (prices, actions)."""
        first = reconstruct_vendor_prices(_vendor_frame(), actions=_split_actions())
        second = reconstruct_vendor_prices(_vendor_frame(), actions=_split_actions())
        assert first.equals(second)

    def test_multi_ticker_frames_apply_only_their_own_splits(self) -> None:
        """A split on one ticker never rescales a sibling instrument.

        Falsified by: a frame-wide cumulative product instead of a per-ticker one.
        """
        vendor = pl.concat(
            [
                _vendor_frame(),
                pl.DataFrame(
                    {
                        "ticker": ["BBB", "BBB"],
                        "date": [date(2024, 6, 7), date(2024, 6, 10)],
                        "open": [20.0, 20.0],
                        "high": [20.0, 20.0],
                        "low": [20.0, 20.0],
                        "close": [20.0, 20.0],
                        "volume": [100, 100],
                        "adj_close": [20.0, 20.0],
                        "market": ["us_equity", "us_equity"],
                        "mic": ["XNYS", "XNYS"],
                        "symbol": ["BBB", "BBB"],
                    }
                ),
            ]
        )
        actions = _actions(
            [
                {"ticker": "AAA", "ex_date": SPLIT_EX_DATE, "action": "split", "value": SPLIT_VALUE},
                {"ticker": "BBB", "ex_date": date(2026, 1, 5), "action": "dividend", "value": 0.2},
            ]
        )
        reconstructed = reconstruct_vendor_prices(vendor, actions=actions)
        assert reconstructed.filter(pl.col("ticker") == "BBB")["close"].to_list() == [20.0, 20.0]
        assert reconstructed.filter(pl.col("ticker") == "AAA")["close"].to_list() == [1000.0, 500.0]


class TestFailClosedRefusals:
    """Each refusal is a named reason, because they need different operator actions."""

    def test_an_absent_action_store_refuses(self) -> None:
        """Falsified by: treating an empty action read as "no splits".

        A missing store is indistinguishable from a store whose splits never arrived, so
        the write must not proceed (ADR-0029 decision 2).
        """
        with pytest.raises(SplitReconstructionError) as excinfo:
            reconstruct_vendor_prices(_vendor_frame(), actions=pl.DataFrame(schema=_ACTION_SCHEMA))
        assert excinfo.value.reason == "action_store_missing"
        assert "equity corporate-actions" in str(excinfo.value)

    def test_a_ticker_missing_from_the_action_store_refuses(self) -> None:
        """A ticker with no stored action at all is unproven, not split-free.

        Falsified by: checking only that the store exists, which would let an unqueried
        ticker through with a factor of 1.
        """
        actions = _actions([{"ticker": "BBB", "ex_date": date(2026, 8, 1), "action": "dividend", "value": 0.25}])
        with pytest.raises(SplitReconstructionError) as excinfo:
            reconstruct_vendor_prices(_vendor_frame(), actions=actions)
        assert excinfo.value.reason == "action_scope_incomplete"
        assert "AAA" in str(excinfo.value)

    def test_an_older_action_store_than_the_price_window_refuses(self) -> None:
        """A watermark below the rows being written leaves a later split unproven.

        Falsified by: dropping the watermark comparison, which would reconstruct a window
        the action store never reached and silently keep the vendor's back-adjustment.
        """
        actions = _split_actions()
        later = _vendor_frame().with_columns(pl.lit(date(2026, 3, 2)).alias("date"))
        with pytest.raises(SplitReconstructionError) as excinfo:
            reconstruct_vendor_prices(later, actions=actions)
        assert excinfo.value.reason == "action_window_uncovered"
        assert "2026-03-02" in str(excinfo.value)

    @pytest.mark.parametrize("bad_value", [1.0, 0.0, -0.5, float("nan")])
    def test_an_unusable_split_ratio_refuses(self, bad_value: float) -> None:
        """A ratio that cannot be inverted must not silently become a no-op or a sign flip.

        Falsified by: dropping the finite/positive/not-1 validation.
        """
        with pytest.raises(SplitReconstructionError) as excinfo:
            reconstruct_vendor_prices(_vendor_frame(), actions=_split_actions(value=bad_value))
        assert excinfo.value.reason == "action_invalid"

    def test_a_reverse_split_ratio_is_accepted(self) -> None:
        """The lake stores old/new, so a reverse split is > 1 and is legitimate."""
        reverse = _split_actions(value=10.0)  # 1-for-10
        reconstructed = reconstruct_vendor_prices(_vendor_frame(), actions=reverse)
        assert reconstructed["close"].to_list() == [50.0, 500.0]


class TestWriteBoundaryWiring:
    """The transform is gated on the real-source root, never on the demo root."""

    def _capture(self, monkeypatch: pytest.MonkeyPatch) -> list[pl.DataFrame]:
        seen: list[pl.DataFrame] = []

        def _fake_upsert(df: FrameLike, *_args: object, **_kwargs: object) -> bool:
            seen.append(ensure_polars(df))
            return True

        monkeypatch.setattr(orchestrator, "upsert_dataset", _fake_upsert)
        return seen

    def test_a_real_root_write_is_reconstructed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falsified by: dropping the reconstruction call from `_write_market`.

        The written frame must be the raw basis, not the vendor basis.
        """
        seen = self._capture(monkeypatch)
        canonical = tmp_path / "lake"
        real = tmp_path / "lake_real"
        monkeypatch.setattr(paths, "LAKE_DIR", canonical)
        monkeypatch.setattr(orchestrator, "LAKE_DIR", canonical)
        monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", canonical)

        actions = _split_actions()
        assert upsert_dataset(actions, corporate_actions_table("us_equity", "silver"), date(2024, 6, 10), partition_by=["ex_date"], lake_dir=real)

        assert orchestrator._write_market(_vendor_frame(), "us_equity", date(2024, 6, 10), True, real) is True
        assert seen[-1]["close"].to_list() == [1000.0, 500.0]

    def test_the_demo_root_write_is_never_re_inflated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Decision 8: demo rows are never re-inflated by this transform.

        Falsified by: applying the transform unconditionally, which would rescale the
        synthetic demo lake's own rows.
        """
        seen = self._capture(monkeypatch)
        monkeypatch.setattr(paths, "LAKE_DIR", tmp_path / "lake")
        monkeypatch.setattr(orchestrator, "LAKE_DIR", tmp_path / "lake")

        assert orchestrator._write_market(_vendor_frame(), "us_equity", date(2024, 6, 10), True, None) is True
        assert seen[-1]["close"].to_list() == [500.0, 500.0]

    def test_a_real_root_write_refuses_when_the_actions_are_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The refusal reaches the write boundary, so the CLI exits non-zero.

        Falsified by: swallowing `SplitReconstructionError` inside `_write_market`.
        """
        seen = self._capture(monkeypatch)
        canonical = tmp_path / "lake"
        real = tmp_path / "lake_real"
        monkeypatch.setattr(paths, "LAKE_DIR", canonical)
        monkeypatch.setattr(orchestrator, "LAKE_DIR", canonical)
        monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", canonical)

        with pytest.raises(SplitReconstructionError) as excinfo:
            orchestrator._write_market(_vendor_frame(), "us_equity", date(2024, 6, 10), True, real)
        assert excinfo.value.reason == "action_store_missing"
        assert seen == []  # nothing reached the writer
        assert not real.exists()  # and nothing was created


class TestProvenEmptyActionScope:
    """ADR-0033: an empty vendor list is evidence only as a covering weak proof."""

    @staticmethod
    def _proof(*, start: date = date(2024, 6, 1), end: date = date(2024, 6, 30)) -> pl.DataFrame:
        from equity_lake.core.availability import VendorEventCoverageProof

        proof = VendorEventCoverageProof(
            market="us_equity",
            mic="XNAS",
            symbol="AAA",
            source="yahoo",
            coverage_start=start,
            coverage_end=end,
        )
        return pl.DataFrame([proof.as_row()])

    def test_covering_weak_proof_passes_and_prices_are_unchanged(self) -> None:
        frame = _vendor_frame()
        reconstructed = reconstruct_vendor_prices(frame, actions=_empty_actions(), coverage=self._proof())
        # No splits in the evidence means the vendor series needs no re-inflation.
        assert reconstructed.equals(frame)

    def test_a_proof_that_does_not_reach_the_rows_still_refuses(self) -> None:
        with pytest.raises(SplitReconstructionError, match="action_store_missing"):
            reconstruct_vendor_prices(_vendor_frame(), actions=_empty_actions(), coverage=self._proof(end=date(2024, 6, 8)))

    def test_empty_actions_without_any_proof_still_refuses(self) -> None:
        with pytest.raises(SplitReconstructionError, match="action_store_missing"):
            reconstruct_vendor_prices(_vendor_frame(), actions=_empty_actions())

    def test_a_strict_clock_proof_cannot_prove_emptiness(self) -> None:
        from equity_lake.core.availability import (
            ActionCoverageProof,  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
        )

        strict = ActionCoverageProof(
            market="us_equity",
            mic="XNAS",
            symbol="AAA",
            source="yahoo",
            coverage_start=date(2024, 6, 1),
            coverage_end=date(2024, 6, 30),
            source_available_at=datetime(2024, 6, 1, tzinfo=UTC),
            source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(SplitReconstructionError, match="action_store_missing"):
            reconstruct_vendor_prices(_vendor_frame(), actions=_actions([]), coverage=pl.DataFrame([strict.as_row()]))
