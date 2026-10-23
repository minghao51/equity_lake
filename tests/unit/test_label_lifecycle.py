"""Regression tests for the ADR-0014 label lifecycle.

Covers multi-session finalization against real temporary Delta tables, weekend
and exchange-holiday next-session behavior, missing next-session bars staying
pending, as-of eligibility, idempotent replay, label-only update safety, and
the bounded report/repair path.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from runpy import run_path
from typing import cast

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.core.availability import (  # type: ignore[import-untyped]
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
    AvailabilityContractError,
)
from equity_lake.core.instruments import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    InstrumentKey,
)
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION  # type: ignore[import-untyped]
from equity_lake.features.labels import (  # type: ignore[import-untyped]
    AVAILABILITY_COLUMN,
    TARGET_COLUMN,
    apply_label_repair,
    filter_as_of_available,
    finalize_preceding_labels,
    scan_label_repair,
)
from equity_lake.storage.delta import DeltaMergeError, read_delta, update_delta_columns  # type: ignore[import-untyped]

_pit_fixtures = run_path(str(Path(__file__).with_name("_pit_fixtures.py")))
with_synthetic_feature_proof = _pit_fixtures["with_synthetic_feature_proof"]

# ---------------------------------------------------------------------------
# Temporary-lake helpers (real Delta writes, no mocked row counts)
# ---------------------------------------------------------------------------


def _weekdays_ending(end: date, n: int) -> list[date]:
    from equity_lake.core.calendar import is_trading_day  # type: ignore[import-untyped]

    days: list[date] = []
    current = end
    while len(days) < n:
        if current.weekday() < 5 and is_trading_day("us_equity", current):
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


def _price_frame(ticker: str, days: list[date]) -> pl.DataFrame:
    import math

    close = [100.0 + 8 * math.sin(i / 2.5) + i * 0.05 for i in range(len(days))]
    return pl.DataFrame(
        {
            "market": ["us_equity"] * len(days),
            "mic": ["XNAS"] * len(days),
            "symbol": [ticker] * len(days),
            "identity_schema_version": [1] * len(days),
            "ticker": [ticker] * len(days),
            "date": days,
            "open": [value - 0.5 for value in close],
            "high": [value + 1.0 for value in close],
            "low": [value - 1.0 for value in close],
            "close": close,
            "volume": [1_000_000.0 + (i % 10) * 1_000 for i in range(len(days))],
        }
    )


def _prepare_lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frames: list[pl.DataFrame]) -> None:
    import exchange_calendars as xc

    from equity_lake.core.availability import ActionCoverageProof  # pyright: ignore[reportMissingImports] -- source import verified by uv
    from equity_lake.ingestion.corporate_actions import corporate_action_coverage_table  # type: ignore[import-untyped]
    from equity_lake.storage.delta import merge_delta

    market_dir = tmp_path / "01_bronze" / "market_data" / "us_equity"
    market_dir.mkdir(parents=True, exist_ok=True)
    write_deltalake(str(market_dir), pl.concat(frames).to_arrow(), mode="append")
    for module in ("equity_lake.features.engineering", "equity_lake.storage.delta", "equity_lake.features.labels"):
        monkeypatch.setattr(f"{module}.LAKE_DIR", tmp_path)

    calendar = xc.get_calendar("XNAS")
    proofs = []
    for frame in frames:
        for identity in frame.select("market", "mic", "symbol").unique().to_dicts():
            dates = frame.filter(pl.col("symbol") == identity["symbol"])["date"].to_list()
            coverage_start = min(dates)
            for coverage_end in dates:
                close = calendar.session_close(coverage_end.isoformat()).to_pydatetime()
                proofs.append(
                    ActionCoverageProof(
                        **identity,
                        source="synthetic-test-complete-window",
                        coverage_start=coverage_start,
                        coverage_end=coverage_end,
                        source_available_at=close - timedelta(minutes=1),
                        source_valid_until=close,
                    ).model_dump()
                )
    merge_delta(
        pl.DataFrame(proofs),
        corporate_action_coverage_table("us_equity"),
        key_columns=["market", "mic", "symbol", "source", "coverage_start", "coverage_end"],
        partition_by=["coverage_end"],
        lake_dir=tmp_path,
    )


def _run_job(day: date, tickers: list[str]) -> tuple[pl.DataFrame, dict[str, object]]:
    from equity_lake.features import run_feature_job  # type: ignore[import-untyped]

    diagnostics: dict[str, object] = {}
    frame = run_feature_job(
        tickers=tickers,
        output_start_date=day,
        output_end_date=day,
        diagnostics=diagnostics,
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker) for ticker in tickers],
    )
    return frame, diagnostics


def _gold(tmp_path: Path) -> pl.DataFrame:
    return cast(pl.DataFrame, read_delta("03_gold/features", lake_dir=tmp_path)).sort(["market", "mic", "symbol", "date"])


def _row(frame: pl.DataFrame, ticker: str, day: date) -> dict[str, object]:
    mine = frame.filter((pl.col("ticker") == ticker) & (pl.col("date") == day))
    assert mine.height == 1, f"expected one row for {ticker} {day}, got {mine.height}"
    return mine.row(0, named=True)


def _seed_row(ticker: str, day: date, close: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": [ticker],
            "identity_schema_version": [1],
            "ticker": [ticker],
            "date": [day],
            "close": [close],
            TARGET_COLUMN: pl.Series([None], dtype=pl.Float64),
            AVAILABILITY_COLUMN: pl.Series([None], dtype=pl.Date),
        }
    )


# ---------------------------------------------------------------------------
# Three EOD sessions finalize prior labels; newest stays pending
# ---------------------------------------------------------------------------


def test_multi_session_finalization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    days = _weekdays_ending(date(2024, 3, 6), 80)
    sessions = days[-3:]
    frames = [
        _price_frame("AAPL", days),
        _price_frame("HALT", days[:-1]),  # no bar on the final session
    ]
    _prepare_lake(tmp_path, monkeypatch, frames)
    s0, s1, s2 = sessions

    _run_job(s0, ["AAPL", "HALT"])
    _run_job(s1, ["AAPL", "HALT"])
    _, diagnostics = _run_job(s2, ["AAPL", "HALT"])

    gold = _gold(tmp_path)
    aapl = gold.filter(pl.col("ticker") == "AAPL").sort("date")
    assert aapl["date"].to_list() == [s0, s1, s2]

    source = _price_frame("AAPL", days)
    close = dict(zip(source["date"].to_list(), source["close"].to_list(), strict=True))

    # Prior sessions finalized with the next observed session's return and date.
    assert _row(gold, "AAPL", s0)[TARGET_COLUMN] == pytest.approx(close[s1] / close[s0] - 1)
    assert _row(gold, "AAPL", s0)[AVAILABILITY_COLUMN] == s1
    assert _row(gold, "AAPL", s1)[AVAILABILITY_COLUMN] == s2
    # Newest session has no next observation yet: pending, never fabricated.
    assert _row(gold, "AAPL", s2)[TARGET_COLUMN] is None
    assert _row(gold, "AAPL", s2)[AVAILABILITY_COLUMN] is None

    # Missing next-session bar for HALT leaves s1 pending and is reported.
    assert _row(gold, "HALT", s0)[AVAILABILITY_COLUMN] == s1
    assert _row(gold, "HALT", s1)[TARGET_COLUMN] is None
    assert _row(gold, "HALT", s1)[AVAILABILITY_COLUMN] is None
    labels = diagnostics["label_finalization"]
    assert isinstance(labels, dict)
    assert labels["pending_keys"] == [{"market": "us_equity", "mic": "XNAS", "symbol": "HALT", "ticker": "HALT", "date": str(s1)}]


# ---------------------------------------------------------------------------
# Next session is the next *exchange session*, not the next calendar day
# ---------------------------------------------------------------------------


def test_weekend_next_session_finalizes_friday_on_monday(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monday = date(2024, 3, 4)
    days = _weekdays_ending(monday, 80)
    friday = days[-2]
    assert friday.weekday() == 4 and days[-1] == monday
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])

    _run_job(friday, ["AAPL"])
    _run_job(monday, ["AAPL"])

    gold = _gold(tmp_path)
    source = _price_frame("AAPL", days)
    close = dict(zip(source["date"].to_list(), source["close"].to_list(), strict=True))
    assert _row(gold, "AAPL", friday)[AVAILABILITY_COLUMN] == monday
    assert _row(gold, "AAPL", friday)[TARGET_COLUMN] == pytest.approx(close[monday] / close[friday] - 1)


def test_exchange_holiday_next_session_uses_next_observed_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.core.calendar import is_trading_day

    holiday = date(2024, 1, 15)  # MLK Day
    assert is_trading_day("us_equity", holiday) is False

    tuesday = date(2024, 1, 16)
    days = [day for day in _weekdays_ending(tuesday, 80) if day != holiday]
    friday = days[-2]
    assert friday == date(2024, 1, 12) and days[-1] == tuesday
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])

    _run_job(friday, ["AAPL"])
    _run_job(tuesday, ["AAPL"])

    gold = _gold(tmp_path)
    source = _price_frame("AAPL", days)
    close = dict(zip(source["date"].to_list(), source["close"].to_list(), strict=True))
    assert _row(gold, "AAPL", friday)[AVAILABILITY_COLUMN] == tuesday
    assert _row(gold, "AAPL", friday)[TARGET_COLUMN] == pytest.approx(close[tuesday] / close[friday] - 1)


# ---------------------------------------------------------------------------
# Rerun / replay idempotency and label-only update safety
# ---------------------------------------------------------------------------


def test_rerun_is_idempotent_and_preserves_prior_feature_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0, s1 = days[-2], days[-1]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])

    _run_job(s0, ["AAPL"])
    _run_job(s1, ["AAPL"])
    after_first = _gold(tmp_path)
    d0_first = _row(after_first, "AAPL", s0)
    schema_first = after_first.schema

    # Replay s1 (re-finalizes s0) and s0 (rewrites the pending row).
    _run_job(s1, ["AAPL"])
    _run_job(s0, ["AAPL"])
    after_replay = _gold(tmp_path)

    assert after_replay.height == after_first.height
    assert after_replay.select("ticker", "date").n_unique() == after_replay.height
    assert after_replay.schema == schema_first
    assert _row(after_replay, "AAPL", s0) == d0_first


def test_label_only_update_touches_no_feature_columns_or_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0, s1 = days[-2], days[-1]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])

    _run_job(s0, ["AAPL"])
    before = _gold(tmp_path)
    schema_before = before.schema
    feature_columns = [column for column in before.columns if column not in (TARGET_COLUMN, AVAILABILITY_COLUMN)]
    snapshot_before = before.select(feature_columns).sort(["ticker", "date"])

    _run_job(s1, ["AAPL"])
    after = _gold(tmp_path)

    assert after.height == before.height + 1  # only the new s1 row
    assert after.schema == schema_before
    prior_after = after.filter(pl.col("date") == s0).select(feature_columns)
    prior_before = before.filter(pl.col("date") == s0).select(feature_columns)
    assert prior_after.equals(prior_before)
    assert snapshot_before["rsi_14"].null_count() == prior_after["rsi_14"].null_count()


def test_update_delta_columns_never_inserts_unmatched_and_rejects_missing_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    table = "03_gold/features"
    existing = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 3, 1)],
            "close": [100.0],
            "next_day_return": pl.Series([None], dtype=pl.Float64),
            "label_available_date": pl.Series([None], dtype=pl.Date),
        }
    )
    from equity_lake.storage.delta import delta_table_path

    write_deltalake(str(delta_table_path(table, tmp_path)), existing.to_arrow(), mode="append", partition_by=["date"])

    source = pl.DataFrame(
        {
            "ticker": ["AAPL", "GHOST"],
            "date": [date(2024, 3, 1), date(2024, 3, 1)],
            "next_day_return": [0.02, 0.99],
            "label_available_date": [date(2024, 3, 4), date(2024, 3, 4)],
        }
    )
    metrics = update_delta_columns(source, table, update_columns=["next_day_return", "label_available_date"], lake_dir=tmp_path)
    assert metrics["num_target_rows_inserted"] == 0
    assert metrics["num_target_rows_updated"] == 1
    result = read_delta(table, lake_dir=tmp_path)
    assert result.height == 1
    assert "GHOST" not in result["ticker"].to_list()

    with pytest.raises(DeltaMergeError):
        update_delta_columns(source, "03_gold/missing", update_columns=["next_day_return"], lake_dir=tmp_path)
    with pytest.raises(DeltaMergeError):
        update_delta_columns(source, table, update_columns=["not_a_column"], lake_dir=tmp_path)


# ---------------------------------------------------------------------------
# As-of eligibility
# ---------------------------------------------------------------------------


def test_filter_as_of_available_excludes_later_and_unknown_availability() -> None:
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
            TARGET_COLUMN: [0.01, -0.02, 0.03],
            AVAILABILITY_COLUMN: [date(2024, 1, 3), date(2024, 1, 2), None],
        }
    )
    strict = filter_as_of_available(frame, date(2024, 1, 2))
    assert strict["date"].to_list() == [date(2024, 1, 2)]

    # Legacy frame with no availability column is strict-excluded by default.
    legacy = frame.drop(AVAILABILITY_COLUMN)
    assert filter_as_of_available(legacy, date(2024, 1, 2)).is_empty()
    # The documented tolerance hook is opt-in and not used by training.
    assert filter_as_of_available(legacy, date(2024, 1, 2), strict=False).height == 3


def test_feature_loader_enforces_as_of_cutoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.ml.feature_loader import FeatureLoader  # type: ignore[import-untyped]

    gold_dir = tmp_path / "03_gold" / "features"
    gold_dir.parent.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        {
            "market": ["us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS"],
            "symbol": ["AAPL", "AAPL"],
            "identity_schema_version": [1, 1],
            "ticker": ["AAPL", "AAPL"],
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "close": [100.0, 101.0],
            "feature_schema_version": [FEATURE_SCHEMA_VERSION, FEATURE_SCHEMA_VERSION],
            "rsi_14": [50.0, 51.0],
            TARGET_COLUMN: [0.01, 0.02],
            AVAILABILITY_COLUMN: [date(2024, 1, 10), date(2024, 1, 3)],
        }
    )
    frame = with_synthetic_feature_proof(frame, InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"))
    write_deltalake(str(gold_dir), frame.to_arrow(), mode="append", partition_by=["date"])
    monkeypatch.setattr("equity_lake.ml.feature_loader.GOLD_FEATURES_DIR", gold_dir)

    loader = FeatureLoader()
    try:
        unfiltered = loader.load_features("AAPL", date(2024, 1, 1), date(2024, 1, 31))
        assert unfiltered.height == 2
        strict = loader.load_features("AAPL", date(2024, 1, 1), date(2024, 1, 31), require_available_by=date(2024, 1, 5))
        assert strict["date"].dt.date().to_list() == [date(2024, 1, 3)]
    finally:
        loader.close()


def test_feature_loader_marks_legacy_source_expiry_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.ml.feature_loader import FeatureLoader

    gold_dir = tmp_path / "03_gold" / "features"
    gold_dir.parent.mkdir(parents=True, exist_ok=True)
    legacy = pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "identity_schema_version": [1],
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 2)],
            "feature_schema_version": [FEATURE_SCHEMA_VERSION],
            "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION],
            "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION],
            "action_coverage": pl.Series([1], dtype=pl.Int8),
            "action_source_age_days": pl.Series([0], dtype=pl.Int32),
        }
    )
    write_deltalake(str(gold_dir), legacy.to_arrow(), mode="append", partition_by=["date"])
    monkeypatch.setattr("equity_lake.ml.feature_loader.GOLD_FEATURES_DIR", gold_dir)
    loader = FeatureLoader()
    try:
        with pytest.raises(AvailabilityContractError, match="source-availability proof is unavailable"):
            loader.load_features("AAPL", date(2024, 1, 1), date(2024, 1, 31))
    finally:
        loader.close()


def test_model_feature_list_excludes_availability_metadata() -> None:
    from equity_lake.ml.forecasting import NON_FEATURE_COLUMNS, PriceForecaster  # type: ignore[import-untyped]

    assert AVAILABILITY_COLUMN in NON_FEATURE_COLUMNS
    frame = pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "identity_schema_version": [1],
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 2)],
            "rsi_14": [50.0],
            TARGET_COLUMN: [None],
            AVAILABILITY_COLUMN: [None],
        }
    )
    frame = with_synthetic_feature_proof(frame, InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"))
    forecaster = PriceForecaster(model_dir="/tmp")
    try:
        features = forecaster._get_feature_columns(frame)
    finally:
        forecaster.close()
    assert AVAILABILITY_COLUMN not in features
    assert TARGET_COLUMN not in features
    assert "rsi_14" in features
    assert "action_coverage" in features
    assert "action_source_age_days" in features
    assert "action_source_valid_until" not in features
    assert "action_source_available_at" not in features
    assert "action_coverage_start" not in features
    assert "action_coverage_end" not in features


# ---------------------------------------------------------------------------
# Dry-run and report-first repair
# ---------------------------------------------------------------------------


def test_dry_run_finalization_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.features.labels import add_label_availability

    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0, s1 = days[-2], days[-1]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])
    _run_job(s0, ["AAPL"])
    before = _gold(tmp_path)

    # Reconstruct the s1 computed frame and dry-run the preceding finalization.
    frame = _price_frame("AAPL", days).sort(["ticker", "date"])
    features = add_label_availability(frame)
    features = features.with_columns((pl.col("close").shift(-1).over("ticker") / pl.col("close") - 1).alias(TARGET_COLUMN))
    result = finalize_preceding_labels(features, s1, dry_run=True)
    assert result.eligible >= 1
    assert result.finalized == 0
    assert _gold(tmp_path).equals(before)

    # The real (non-dry-run) path does finalize the s0 label.
    _run_job(s1, ["AAPL"])
    assert _row(_gold(tmp_path), "AAPL", s0)[TARGET_COLUMN] is not None


def test_scan_and_repair_report_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0, s1 = days[-2], days[-1]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])

    # Persist only the s0 row with a null target (simulates a legacy unlabeled row).
    source = _price_frame("AAPL", days)
    close = dict(zip(source["date"].to_list(), source["close"].to_list(), strict=True))
    gold_dir = tmp_path / "03_gold" / "features"
    gold_dir.mkdir(parents=True, exist_ok=True)
    seed = _seed_row("AAPL", s0, close[s0])
    write_deltalake(str(gold_dir), seed.to_arrow(), mode="append", partition_by=["date"])

    plan = scan_label_repair(start=s0, end=s0, tickers=["AAPL"], lake_dir=tmp_path)
    assert plan.finalizable_count == 1
    assert plan.pending_count == 0

    expected = close[s1] / close[s0] - 1

    updated = apply_label_repair(plan, lake_dir=tmp_path)
    assert updated == 1
    repaired = _row(_gold(tmp_path), "AAPL", s0)
    assert repaired[TARGET_COLUMN] == pytest.approx(expected)
    assert repaired[AVAILABILITY_COLUMN] == s1

    # Idempotent: the repaired row is no longer unlabeled.
    assert scan_label_repair(start=s0, end=s0, tickers=["AAPL"], lake_dir=tmp_path).finalizable_count == 0


def test_scan_reports_pending_when_next_bar_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0 = days[-2]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days[:-1])])  # ends at s0
    gold_dir = tmp_path / "03_gold" / "features"
    gold_dir.mkdir(parents=True, exist_ok=True)
    seed = _seed_row("AAPL", s0, 100.0)
    write_deltalake(str(gold_dir), seed.to_arrow(), mode="append", partition_by=["date"])

    plan = scan_label_repair(start=s0, end=s0, tickers=["AAPL"], lake_dir=tmp_path)
    assert plan.finalizable_count == 0
    assert plan.pending_count == 1
    assert apply_label_repair(plan, lake_dir=tmp_path) == 0


def test_cli_label_repair_is_report_only_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]

    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0, s1 = days[-2], days[-1]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])
    gold_dir = tmp_path / "03_gold" / "features"
    gold_dir.mkdir(parents=True, exist_ok=True)
    seed = _seed_row("AAPL", s0, 100.0)
    write_deltalake(str(gold_dir), seed.to_arrow(), mode="append", partition_by=["date"])

    runner = CliRunner()
    help_result = runner.invoke(app, ["label-repair", "--help"])
    assert help_result.exit_code == 0
    assert "report-only" in help_result.stdout.lower() or "report only" in help_result.stdout.lower()

    report = runner.invoke(app, ["label-repair", "--start", str(s0), "--end", str(s0), "--tickers", "AAPL"])
    assert report.exit_code == 0
    assert "finalizable" in report.stdout
    assert _row(_gold(tmp_path), "AAPL", s0)[TARGET_COLUMN] is None  # no write

    repaired = runner.invoke(app, ["label-repair", "--start", str(s0), "--end", str(s0), "--tickers", "AAPL", "--repair"])
    assert repaired.exit_code == 0
    assert _row(_gold(tmp_path), "AAPL", s0)[AVAILABILITY_COLUMN] == s1


# ---------------------------------------------------------------------------
# Review-warning regression pins (W03 PR2 reviewer pass)
# ---------------------------------------------------------------------------


def test_filter_as_of_available_strict_false_keeps_unknown_availability_rows() -> None:
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "AAPL"],
            "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
            TARGET_COLUMN: [0.01, -0.02, 0.03],
            AVAILABILITY_COLUMN: [date(2024, 1, 3), None, date(2024, 1, 2)],
        }
    )
    # Tolerance hook keeps unknown-availability rows even when the column is
    # present, matching the absent-column branch; later availability stays out.
    tolerant = filter_as_of_available(frame, date(2024, 1, 2), strict=False)
    assert tolerant["date"].to_list() == [date(2024, 1, 2), date(2024, 1, 3)]
    # Strict (the default) still excludes both the later and the unknown rows.
    assert filter_as_of_available(frame, date(2024, 1, 2))["date"].to_list() == [date(2024, 1, 3)]


def test_replay_recomputing_finalized_label_logs_divergence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from structlog.testing import capture_logs

    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0, s1 = days[-2], days[-1]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])

    _run_job(s0, ["AAPL"])
    _run_job(s1, ["AAPL"])
    finalized_before = _row(_gold(tmp_path), "AAPL", s0)[TARGET_COLUMN]

    # A source-data revision changes s1's prices, so replaying s1 recomputes a
    # DIFFERENT non-null target for s0 (review warning 3): the rewrite must at
    # least surface the divergence instead of silently overwriting. All four
    # price columns scale together so the OHLC validation still passes.
    revised = _price_frame("AAPL", days).with_columns(
        *[
            pl.when(pl.col("date") == s1).then(pl.col(column) * 1.05).otherwise(pl.col(column)).alias(column)
            for column in ("open", "high", "low", "close")
        ]
    )
    market_dir = tmp_path / "01_bronze" / "market_data" / "us_equity"
    write_deltalake(str(market_dir), revised.to_arrow(), mode="overwrite")

    with capture_logs() as logs:
        _run_job(s1, ["AAPL"])

    divergence = [entry for entry in logs if entry.get("event") in ("label_finalization_divergence", "label_replay_divergence")]
    assert divergence and divergence[0]["count"] == 1
    assert _row(_gold(tmp_path), "AAPL", s0)[TARGET_COLUMN] != finalized_before


def test_scan_reports_availability_unknown_legacy_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    days = _weekdays_ending(date(2024, 3, 6), 80)
    s0 = days[-2]
    _prepare_lake(tmp_path, monkeypatch, [_price_frame("AAPL", days)])
    gold_dir = tmp_path / "03_gold" / "features"
    gold_dir.mkdir(parents=True, exist_ok=True)
    # Legacy row: target finalized, availability metadata never written.
    seed = _seed_row("AAPL", s0, 100.0).with_columns(pl.lit(0.0125).alias(TARGET_COLUMN))
    write_deltalake(str(gold_dir), seed.to_arrow(), mode="append", partition_by=["date"])

    plan = scan_label_repair(start=s0, end=s0, tickers=["AAPL"], lake_dir=tmp_path)
    assert plan.availability_unknown_count == 1
    assert plan.availability_unknown["date"].to_list() == [s0]
    assert plan.finalizable_count == 0
    # Report-only: the bounded label repair never touches these rows.
    assert apply_label_repair(plan, lake_dir=tmp_path) == 0
