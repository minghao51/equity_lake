"""Offline, synthetic coverage for read-only tracking observations."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import uuid4

import polars as pl
import pytest
import typer
from typer.testing import CliRunner

import equity_lake.signals.readiness as readiness
from equity_lake.cli.commands import tracking
from equity_lake.core.instruments import InstrumentKey
from equity_lake.signals.models import Signal, SignalScanOutcome, SignalScanReport
from equity_lake.storage.delta import update_delta_columns, write_delta


def _write_configs(tmp_path: Path) -> tuple[Path, Path]:
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "schema_version: 1\nname: Synthetic\ninstruments:\n"
        "  - instrument: {market: us_equity, mic: XNAS, symbol: AAPL}\n"
        "  - instrument: {market: us_equity, mic: XNAS, symbol: MSFT}\n",
        encoding="utf-8",
    )
    ticker_config = tmp_path / "tickers.yaml"
    ticker_config.write_text(
        "markets:\n  us:\n    currency: USD\n    tickers:\n"
        "      - {symbol: AAPL, name: Apple, exchange: NASDAQ, sector: Technology, active: true}\n"
        "      - {symbol: MSFT, name: Microsoft, exchange: NASDAQ, sector: Technology, active: true}\n",
        encoding="utf-8",
    )
    return watchlist, ticker_config


def _signal(
    ticker: str,
    action: Literal["BUY", "SELL", "HOLD"],
    signal_type: Literal["backtest", "sentiment", "ml"],
    reasoning: str,
) -> Signal:
    return Signal(
        ticker=ticker,
        date=date(2024, 12, 2),
        signal_type=signal_type,
        action=action,
        confidence=61.0,
        reasoning=reasoning,
        metadata={"price": 100.0, "unapproved_private_value": "must not be exposed"},
        market="us_equity",
        mic="XNAS",
    )


def _scan_report() -> SignalScanReport:
    return SignalScanReport(
        run_id=uuid4().hex,
        target_date=date(2024, 12, 2),
        watchlist="Synthetic",
        tickers=["AAPL", "MSFT"],
        instruments=[
            InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"),
            InstrumentKey(market="us_equity", mic="XNAS", symbol="MSFT"),
        ],
        generators=["backtest", "sentiment"],
        status="partial",
        outcomes=[
            SignalScanOutcome(ticker="AAPL", market="us_equity", mic="XNAS", generator="backtest", status="signal"),
            SignalScanOutcome(ticker="AAPL", market="us_equity", mic="XNAS", generator="sentiment", status="signal"),
            SignalScanOutcome(ticker="MSFT", market="us_equity", mic="XNAS", generator="backtest", status="unavailable"),
        ],
        signals=[
            _signal("AAPL", "BUY", "backtest", "price above configured SMA state"),
            _signal("AAPL", "SELL", "sentiment", "negative sentiment state"),
        ],
    )


def _history_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 12, 2), date(2024, 11, 29)],
            "signal_type": ["backtest", "ml"],
            "action": ["BUY", "SELL"],
            "confidence": [61.0, 55.0],
            "reasoning": ["observed price condition", "observed model candidate"],
            "market": ["us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS"],
            "identity_schema_version": [1, 1],
            "price": [100.0, 90.0],
        }
    )


def test_readiness_reports_observed_age_coverage_and_unknown_policy_without_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist, ticker_config = _write_configs(tmp_path)
    data_dir = tmp_path / "data"
    calls: list[str] = []

    def observe_table(path: Path, table: str, _as_of: date, **_kwargs: object) -> readiness.TableObservation:
        del path
        calls.append(table)
        aapl = ("us_equity", "XNAS", "AAPL")
        msft = ("us_equity", "XNAS", "MSFT")
        if table.endswith("us_equity"):
            return readiness.TableObservation("available", {aapl: date(2024, 12, 2)})
        return readiness.TableObservation("available", {aapl: date(2024, 12, 1), msft: date(2024, 12, 2)})

    monkeypatch.setattr(readiness, "_read_table_dates", observe_table)
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("available", _history_frame()))
    (data_dir / "signal_scans").mkdir(parents=True)
    (data_dir / "signal_scans" / "2024-12-02.json").write_text(_scan_report().model_dump_json(), encoding="utf-8")
    other_watchlist = _scan_report().model_copy(update={"watchlist": "Other"})
    (data_dir / "signal_scans" / "other.json").write_text(other_watchlist.model_dump_json(), encoding="utf-8")

    report = readiness.build_tracking_readiness(
        watchlist_path=watchlist,
        ticker_config_path=ticker_config,
        as_of=date(2024, 12, 4),
        data_dir=data_dir,
        lake_dir=tmp_path / "lake",
    )

    assert [row.ticker for row in report.tickers] == ["AAPL", "MSFT"]
    assert report.tickers[0].latest_price_session == date(2024, 12, 2)
    assert report.tickers[0].price_age_days == 2
    assert report.tickers[0].feature_age_days == 3
    assert report.tickers[0].latest_scan_report_statuses == ["partial"]
    assert report.tickers[0].latest_scan_outcomes == ["signal", "signal"]
    assert len(report.scan_reports) == 1
    assert report.tickers[1].price_status == "no_rows_as_of"
    assert report.tickers[1].signal_history_rows == 1
    assert report.tickers[1].latest_signal_history_date == date(2024, 11, 29)
    assert report.freshness_limit.startswith("unset/unknown")
    assert "before identity filters" in report.signal_history_validation_scope
    assert report.inference_status.startswith("not evaluated")
    assert "unavailable/not configured" in report.delivery_status
    assert len(calls) == 2
    assert readiness.conflicts_by_ticker_date(report.scan_reports[0].signals)[0][0].action == "BUY"
    assert "unapproved_private_value" not in str(report.to_dict())
    assert not (data_dir / "signals").exists()
    assert {p.name for p in (data_dir / "signal_scans").iterdir()} == {"2024-12-02.json", "other.json"}


def test_missing_and_corrupt_tables_are_not_reported_as_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist, ticker_config = _write_configs(tmp_path)
    observations = iter(
        [
            readiness.TableObservation("missing", {}),
            readiness.TableObservation("corrupt", {("us_equity", "XNAS", "AAPL"): date(2024, 12, 2)}),
        ]
    )
    monkeypatch.setattr(readiness, "_read_table_dates", lambda *_args, **_kwargs: next(observations))
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("corrupt", None))

    report = readiness.build_tracking_readiness(
        watchlist_path=watchlist,
        ticker_config_path=ticker_config,
        as_of=date(2024, 12, 4),
        data_dir=tmp_path / "empty-data",
        lake_dir=tmp_path / "lake",
    )

    assert report.tickers[0].price_status == "missing"
    assert report.tickers[0].feature_status == "corrupt"
    assert report.tickers[0].latest_feature_session == date(2024, 12, 2)
    assert report.tickers[1].feature_status == "corrupt"
    assert report.signal_history_status == "corrupt"
    assert report.delivery_status.startswith("unavailable/not configured")


def test_table_reader_distinguishes_absent_from_corrupt_delta_without_migration(tmp_path: Path) -> None:
    missing = readiness._read_table_dates(tmp_path / "absent", "01_bronze/market_data/us_equity", date(2024, 12, 4))
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    empty = readiness._read_table_dates(empty_path, "01_bronze/market_data/us_equity", date(2024, 12, 4))
    corrupt_path = tmp_path / "corrupt"
    (corrupt_path / "_delta_log").mkdir(parents=True)

    corrupt = readiness._read_table_dates(corrupt_path, "01_bronze/market_data/us_equity", date(2024, 12, 4))

    assert missing.state == "missing"
    assert empty.state == "missing"
    assert corrupt.state == "corrupt"
    assert list(corrupt_path.iterdir()) == [corrupt_path / "_delta_log"]


def test_table_reader_counts_null_and_invalid_required_keys_before_cutoff(tmp_path: Path) -> None:
    table_path = tmp_path / "legacy-table"
    table_path.mkdir()
    pl.DataFrame(
        {
            "ticker": ["AAPL", None, "MSFT", " "],
            "date": ["2024-12-01", "2024-12-02", None, "not-a-date"],
        }
    ).write_parquet(table_path / "part.parquet")

    result = readiness._read_table_dates(table_path, "01_bronze/market_data/us_equity", date(2024, 12, 1))

    assert result.state == "corrupt"
    assert result.latest_by_identity == {}


def test_unrecognized_and_dangling_storage_entries_fail_closed(tmp_path: Path) -> None:
    table_path = tmp_path / "unrecognized-table"
    table_path.mkdir()
    pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 12, 2)]}).write_parquet(table_path / "part.parquet")
    (table_path / "unexpected.csv").write_text("not a table", encoding="utf-8")
    history_path = tmp_path / "data" / "signals"
    history_path.mkdir(parents=True)
    _history_frame().write_parquet(history_path / "part.parquet")
    (history_path / "unexpected.csv").write_text("not a table", encoding="utf-8")
    dangling_table = tmp_path / "dangling-table"
    dangling_table.symlink_to(tmp_path / "missing-table-target")
    dangling_history = tmp_path / "dangling-data" / "signals"
    dangling_history.parent.mkdir()
    dangling_history.symlink_to(tmp_path / "missing-history-target")
    empty_history_path = tmp_path / "empty-data" / "signals"
    empty_history_path.mkdir(parents=True)
    unrecognized_scan_path = tmp_path / "unrecognized-scans" / "signal_scans"
    unrecognized_scan_path.mkdir(parents=True)
    (unrecognized_scan_path / "unexpected.csv").write_text("not a report", encoding="utf-8")

    table_result = readiness._read_table_dates(table_path, "01_bronze/market_data/us_equity", date(2024, 12, 4))
    history_result = readiness._read_history_frame(tmp_path / "data")
    dangling_table_result = readiness._read_table_dates(dangling_table, "01_bronze/market_data/us_equity", date(2024, 12, 4))
    dangling_history_result = readiness._read_history_frame(dangling_history.parent)
    empty_history_result = readiness._read_history_frame(empty_history_path.parent)
    unrecognized_scan_result = readiness._read_scan_reports(date(2024, 12, 4), unrecognized_scan_path.parent)

    assert table_result.state == "corrupt"
    assert history_result == ("corrupt", None)
    assert dangling_table_result.state == "corrupt"
    assert dangling_history_result == ("corrupt", None)
    assert empty_history_result == ("missing", None)
    assert unrecognized_scan_result.state == "corrupt"
    assert unrecognized_scan_result.invalid_reports == 1
    assert (table_path / "unexpected.csv").is_file()
    assert (history_path / "unexpected.csv").is_file()


def test_empty_scan_directory_is_missing(tmp_path: Path) -> None:
    scan_dir = tmp_path / "data" / "signal_scans"
    scan_dir.mkdir(parents=True)

    result = readiness._read_scan_reports(date(2024, 12, 4), tmp_path / "data")

    assert result.state == "missing"
    assert result.reports == []
    assert result.invalid_reports == 0


def test_corrupt_scan_report_is_distinct_from_empty_history(tmp_path: Path) -> None:
    scan_dir = tmp_path / "data" / "signal_scans"
    scan_dir.mkdir(parents=True)
    (scan_dir / "broken.json").write_text("{not-json", encoding="utf-8")
    (scan_dir / "valid.json").write_text(_scan_report().model_dump_json(), encoding="utf-8")

    result = readiness._read_scan_reports(date(2024, 12, 4), tmp_path / "data")

    assert result.state == "corrupt"
    assert result.invalid_reports == 1
    assert len(result.reports) == 1
    assert result.reports[0].outcomes_by_identity["us_equity/XNAS/MSFT"] == ["unavailable"]


def test_invalid_history_keys_outside_selection_are_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL", None, "MSFT", "AAPL", "AAPL"],
            "date": ["2024-12-10", "2024-12-10", "2024-12-10", "not-a-date", "2024-12-10"],
            "signal_type": ["invalid", "ml", "sentiment", "backtest", None],
            "action": ["BUY"] * 5,
            "confidence": [60.0] * 5,
            "reasoning": ["synthetic"] * 5,
        }
    )
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("available", frame))

    result = readiness.read_signal_history_readonly(target_date=date(2024, 12, 1), tickers={"ONLY"}, data_dir=tmp_path)

    assert result.state == "corrupt"
    assert result.invalid_rows == 4
    assert result.total_rows == 0
    assert result.signals == []
    assert "before identity filters" in result.validation_scope
    assert "unknown legacy identities" in result.validation_scope


def test_unselected_payload_fields_are_not_claimed_as_fully_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": ["2024-12-10"],
            "signal_type": ["backtest"],
            "action": ["NOT_AN_ACTION"],
            "confidence": [101.0],
            "reasoning": ["synthetic"],
        }
    )
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("available", frame))

    result = readiness.read_signal_history_readonly(as_of=date(2024, 12, 1), data_dir=tmp_path)

    assert result.state == "available"
    assert result.invalid_rows == 0
    assert result.total_rows == 0
    assert "only for displayed rows" in result.validation_scope


def test_legacy_history_identity_remains_visible_but_never_joins_a_managed_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pl.DataFrame(
        {
            "ticker": ["SHARED"],
            "date": ["2024-12-02"],
            "signal_type": ["backtest"],
            "action": ["BUY"],
            "confidence": [70.0],
            "reasoning": ["legacy row"],
        }
    )
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("available", frame))

    result = readiness.read_signal_history_readonly(
        as_of=date(2024, 12, 2),
        identities={("us_equity", "XNAS", "SHARED")},
        data_dir=tmp_path,
    )

    assert result.state == "unknown_identity"
    assert result.unknown_identity_rows == 1
    assert result.signals[0].identity_status == "unknown_legacy"
    assert result.signals[0].market is None and result.signals[0].mic is None
    assert result.count_by_identity == {}
    assert result.latest_by_identity == {}


def test_read_history_is_date_bounded_ordered_and_does_not_migrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("available", _history_frame()))

    result = readiness.read_signal_history_readonly(target_date=date(2024, 12, 2), limit=1, data_dir=tmp_path)

    assert result.state == "available"
    assert result.total_rows == 1
    assert result.signals[0].ticker == "AAPL"
    assert result.signals[0].date == date(2024, 12, 2)
    assert result.truncated is False
    assert not (tmp_path / "signals").exists()


def test_signal_history_cli_reports_null_dates_observationally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", None, "MSFT"],
            "date": ["2024-12-02", None, "2024-12-03", "not-a-date"],
            "signal_type": ["backtest", "sentiment", "ml", "backtest"],
            "action": ["BUY", "SELL", "HOLD", "BUY"],
            "confidence": [61.0, 55.0, 50.0, 60.0],
            "reasoning": ["observed", "null date", "null ticker", "unparseable date"],
        }
    )
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("available", frame))
    monkeypatch.setattr(readiness, "DATA_DIR", data_dir)
    monkeypatch.setattr(tracking, "import_module", lambda name: readiness if name == "equity_lake.signals.readiness" else pytest.fail(name))

    app = typer.Typer(add_completion=False)
    signal_app = typer.Typer(add_completion=False)
    signal_app.command("history")(tracking.signal_history)
    app.add_typer(signal_app, name="signal")
    result = CliRunner().invoke(app, ["signal", "history", "--date", "2024-12-02", "--limit", "5"])

    assert result.exit_code == 1
    observation = json.loads(result.stdout)
    assert observation["state"] == "corrupt"
    assert observation["invalid_rows"] == 3
    assert observation["total_rows"] == 1
    assert observation["signals"][0]["ticker"] == "AAPL"
    assert "only for displayed rows" in observation["validation_scope"]
    assert not data_dir.exists()


def test_history_as_of_order_is_newest_first_and_reports_display_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("available", _history_frame()))

    result = readiness.read_signal_history_readonly(as_of=date(2024, 12, 2), limit=1, data_dir=tmp_path)

    assert result.total_rows == 2
    assert result.truncated is True
    assert result.signals[0].ticker == "AAPL"
    assert result.signals[0].date == date(2024, 12, 2)


def test_existing_delta_history_is_read_without_migration_or_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history_path = tmp_path / "signals"
    (history_path / "_delta_log" / "_sidecars").mkdir(parents=True)
    (history_path / "_delta_log" / "_staged_commits").mkdir()
    (history_path / "date=2024-12-02").mkdir()
    (history_path / "_delta_log" / "00000000000000000000.json").write_text("{}", encoding="utf-8")
    (history_path / "_delta_log" / "_started_00000000000000000001").touch()
    (history_path / "_delta_log" / "_committed_00000000000000000001").touch()
    (history_path / "_delta_log" / "_last_checkpoint").write_text("{}", encoding="utf-8")
    (history_path / "_delta_log" / "_sidecars" / "checkpoint.parquet").touch()
    (history_path / "_delta_log" / "_staged_commits" / "00000000000000000001.json").write_text("{}", encoding="utf-8")
    (history_path / "date=2024-12-02" / "part-000.parquet").touch()
    (history_path / ".hidden-library-file").touch()
    reads: list[tuple[str, Path]] = []

    def read_delta(table: str, *, lake_dir: Path, **_kwargs: object) -> pl.DataFrame:
        reads.append((table, lake_dir))
        return _history_frame()

    monkeypatch.setattr(readiness.DeltaTable, "is_deltatable", lambda _path: True)
    monkeypatch.setattr(readiness, "read_delta", read_delta)

    state, frame = readiness._read_history_frame(tmp_path)

    assert state == "available"
    assert frame is not None and frame.height == 2
    assert reads == [("signals", tmp_path)]
    assert (history_path / "_delta_log" / "00000000000000000000.json").is_file()
    assert (history_path / "date=2024-12-02" / "part-000.parquet").is_file()


def test_unknown_files_beside_delta_state_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    table_path = tmp_path / "table"
    (table_path / "_delta_log").mkdir(parents=True)
    (table_path / "_delta_log" / "00000000000000000000.json").write_text("{}", encoding="utf-8")
    (table_path / "date=2024-12-02").mkdir()
    (table_path / "date=2024-12-02" / "part-000.parquet").touch()
    (table_path / "unexpected.csv").write_text("not a table", encoding="utf-8")
    history_path = tmp_path / "data" / "signals"
    (history_path / "_delta_log").mkdir(parents=True)
    (history_path / "_delta_log" / "00000000000000000000.json").write_text("{}", encoding="utf-8")
    (history_path / "part-000.parquet").touch()
    (history_path / "unexpected.csv").write_text("not a table", encoding="utf-8")
    monkeypatch.setattr(readiness.DeltaTable, "is_deltatable", lambda _path: True)
    monkeypatch.setattr(readiness, "read_delta", lambda *_args, **_kwargs: pytest.fail("corrupt layout must not be read"))

    table_result = readiness._read_table_dates(table_path, "01_bronze/market_data/us_equity", date(2024, 12, 4))
    history_result = readiness._read_history_frame(history_path.parent)

    assert table_result.state == "corrupt"
    assert history_result == ("corrupt", None)
    assert (table_path / "unexpected.csv").is_file()
    assert (history_path / "unexpected.csv").is_file()


def test_history_missing_and_read_error_remain_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("missing", None))
    missing = readiness.read_signal_history_readonly(as_of=date(2024, 12, 2), data_dir=tmp_path)
    monkeypatch.setattr(readiness, "_read_history_frame", lambda _data_dir: ("corrupt", None))
    corrupt = readiness.read_signal_history_readonly(as_of=date(2024, 12, 2), data_dir=tmp_path)

    assert missing.state == "missing"
    assert corrupt.state == "corrupt"
    assert missing.signals == corrupt.signals == []


def test_explicit_canonical_identity_remains_unambiguous_across_markets(tmp_path: Path) -> None:
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "schema_version: 1\nname: Synthetic\ninstruments:\n  - instrument: {market: us_equity, mic: XNAS, symbol: AAPL}\n",
        encoding="utf-8",
    )
    ticker_config = tmp_path / "tickers.yaml"
    ticker_config.write_text(
        "markets:\n  us:\n    currency: USD\n    tickers:\n"
        "      - {symbol: AAPL, name: Apple, exchange: NASDAQ, sector: Technology, active: true}\n"
        "  cn:\n    currency: CNY\n    tickers:\n"
        "      - {symbol: AAPL, name: Duplicate, exchange: XSHG, sector: Technology, active: true}\n",
        encoding="utf-8",
    )

    report = readiness.build_tracking_readiness(
        watchlist_path=watchlist,
        ticker_config_path=ticker_config,
        as_of=date(2024, 12, 4),
        data_dir=tmp_path / "data",
        lake_dir=tmp_path / "lake",
    )

    assert [(row.market, row.mic, row.ticker) for row in report.tickers] == [("us_equity", "XNAS", "AAPL")]


def _feature_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS"],
            "symbol": ["AAPL", "MSFT"],
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 12, 2), date(2024, 12, 2)],
            "rsi_14": [51.0, 52.0],
        }
    )


def test_doctor_reports_arrow_string_view_drift_without_reading_data_pages(tmp_path: Path) -> None:
    """F10/R1: the read-schema drift is visible instead of surfacing as an exception."""
    watchlist, ticker_config = _write_configs(tmp_path)
    lake = tmp_path / "lake"
    assert write_delta(_feature_rows(), "03_gold/features", lake_dir=lake)
    update_delta_columns(
        pl.DataFrame(
            [{"market": "us_equity", "mic": "XNAS", "symbol": "AAPL", "date": date(2024, 12, 2), "rsi_14": 55.0}],
            schema_overrides={"rsi_14": pl.Float64},
        ),
        "03_gold/features",
        update_columns=["rsi_14"],
        key_columns=["market", "mic", "symbol", "date"],
        lake_dir=lake,
    )

    report = readiness.build_tracking_readiness(
        watchlist_path=watchlist,
        ticker_config_path=ticker_config,
        as_of=date(2024, 12, 4),
        data_dir=tmp_path / "data",
        lake_dir=lake,
    )

    by_table = {row.table: row for row in report.storage_schema}
    features = by_table["03_gold/features"]
    assert features.state == "drifted"
    assert {"ticker", "symbol", "market", "mic"}.issubset(set(features.string_view_columns))
    assert features.scanned_files >= 1
    # Absent tables are reported as missing, never as clean.
    assert by_table["01_bronze/market_data/us_equity"].state == "missing"
    assert by_table["04_platinum/predictions"].state == "missing"
    assert "storage_schema" in report.to_dict()


def test_doctor_reports_a_clean_table_as_clean(tmp_path: Path) -> None:
    """A table that has never been merge-rewritten keeps large_string columns."""
    watchlist, ticker_config = _write_configs(tmp_path)
    lake = tmp_path / "lake"
    assert write_delta(_feature_rows(), "03_gold/features", lake_dir=lake)

    report = readiness.build_tracking_readiness(
        watchlist_path=watchlist,
        ticker_config_path=ticker_config,
        as_of=date(2024, 12, 4),
        data_dir=tmp_path / "data",
        lake_dir=lake,
    )

    by_table = {row.table: row for row in report.storage_schema}
    assert by_table["03_gold/features"].state == "clean"
    assert by_table["03_gold/features"].string_view_columns == []


def _bronze_price_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS"],
            "symbol": ["AAPL", "MSFT"],
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 12, 2), date(2024, 12, 2)],
            "close": [100.0, 200.0],
            "identity_provenance": ["current_ticker_config", "current_ticker_config"],
        }
    )


def _prediction_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "ticker": ["AAPL"],
            "date": [date(2024, 12, 2)],
            "direction": ["up"],
            "probability": [0.61],
            "model_version": ["synthetic-model"],
        }
    )


def _coverage_rows(sources: list[str], start: date, end: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity"] * len(sources),
            "mic": ["XNAS"] * len(sources),
            "symbol": ["AAPL"] * len(sources),
            "source": sources,
            "coverage_start": [start] * len(sources),
            "coverage_end": [end] * len(sources),
        }
    )


def _marked_feature_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity", "us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS", "XNAS"],
            "symbol": ["AAPL", "MSFT", "NVDA"],
            "ticker": ["AAPL", "MSFT", "NVDA"],
            "date": [date(2024, 12, 2), date(2024, 12, 2), date(2024, 11, 29)],
            "action_coverage_source": ["synthetic_demo_seed", "synthetic_demo_seed", "vendor_actions"],
        }
    )


def _unmarked_feature_rows() -> pl.DataFrame:
    return _marked_feature_rows().with_columns(pl.lit("vendor_actions").alias("action_coverage_source"))


def test_doctor_reports_synthetic_markers_from_the_tables_that_carry_them(tmp_path: Path) -> None:
    """R7: the synthetic lake is visible from markers that already exist, read-only."""
    watchlist, ticker_config = _write_configs(tmp_path)
    lake = tmp_path / "lake"
    assert write_delta(
        _coverage_rows(["synthetic_demo_seed", "synthetic_demo_seed"], date(2023, 10, 30), date(2026, 10, 28)),
        "02_silver/corporate_action_coverage/us_equity",
        lake_dir=lake,
        partition_by=["coverage_end"],
    )
    assert write_delta(_marked_feature_rows(), "03_gold/features", lake_dir=lake)
    before = sorted(str(path) for path in lake.rglob("*"))

    report = readiness.build_tracking_readiness(
        watchlist_path=watchlist,
        ticker_config_path=ticker_config,
        as_of=date(2024, 12, 4),
        data_dir=tmp_path / "data",
        lake_dir=lake,
    )

    observation = report.synthetic_provenance
    assert observation.state == "synthetic"
    assert observation.marked_rows == 4
    assert observation.rows == 5
    assert observation.earliest_marked_date == date(2023, 10, 30)
    assert observation.latest_marked_date == date(2026, 10, 28)
    by_table = {row.table: row for row in observation.tables}
    coverage = by_table["02_silver/corporate_action_coverage/us_equity"]
    assert (coverage.state, coverage.column, coverage.rows, coverage.marked_rows) == ("marked", "source", 2, 2)
    features = by_table["03_gold/features"]
    assert (features.state, features.column, features.rows, features.marked_rows) == ("marked", "action_coverage_source", 3, 2)
    assert by_table["02_silver/corporate_action_coverage/cn_ashare"].state == "missing"
    assert "current_ticker_config" in observation.bronze_provenance
    assert "synthetic_demo_seed" in observation.reason
    assert "synthetic_provenance" in report.to_dict()
    # Read-only: the observation creates, moves, and rewrites nothing.
    assert sorted(str(path) for path in lake.rglob("*")) == before


def test_doctor_never_reports_an_unmarked_populated_lake_as_real(tmp_path: Path) -> None:
    """R7: data without a marker is `unknown`; only a positive marker proves synthetic."""
    watchlist, ticker_config = _write_configs(tmp_path)
    lake = tmp_path / "lake"
    assert write_delta(
        _coverage_rows(["vendor_actions"], date(2023, 10, 30), date(2026, 10, 28)),
        "02_silver/corporate_action_coverage/us_equity",
        lake_dir=lake,
        partition_by=["coverage_end"],
    )
    assert write_delta(_unmarked_feature_rows(), "03_gold/features", lake_dir=lake)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.rows == 4
    assert observation.marked_rows == 0
    assert observation.state == "unknown"
    assert observation.state != "real"
    assert observation.earliest_marked_date is None
    assert "cannot prove real ingestion" in observation.reason
    assert {row.state for row in observation.tables} >= {"unmarked", "missing"}


def test_doctor_reports_a_bronze_only_lake_as_unknown_not_empty(tmp_path: Path) -> None:
    """R7: a real ingest leaves Bronze rows before coverage/Gold; `empty` must not hide them."""
    lake = tmp_path / "lake"
    assert write_delta(_bronze_price_rows(), "01_bronze/market_data/us_equity", lake_dir=lake)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "unknown"
    assert observation.state != "empty"
    assert observation.state != "real"
    assert (observation.rows, observation.marked_rows) == (2, 0)
    by_table = {row.table: row for row in observation.tables}
    bronze = by_table["01_bronze/market_data/us_equity"]
    assert (bronze.state, bronze.column, bronze.rows, bronze.marked_rows) == ("unmarked", "identity_provenance", 2, 0)
    assert by_table["02_silver/corporate_action_coverage/us_equity"].state == "missing"
    assert by_table["03_gold/features"].state == "missing"
    assert by_table["04_platinum/predictions"].state == "missing"
    assert "Bronze" in observation.reason
    assert "01_bronze/market_data/us_equity" in observation.reason
    assert "cannot prove real ingestion" in observation.reason


def test_doctor_counts_a_row_only_predictions_table_as_rows(tmp_path: Path) -> None:
    """R7: predictions carry no marker; rows there alone are `unknown`, never `empty`."""
    lake = tmp_path / "lake"
    assert write_delta(_prediction_rows(), "04_platinum/predictions", lake_dir=lake)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "unknown"
    assert observation.state != "empty"
    assert observation.rows == 1
    predictions = {row.table: row for row in observation.tables}["04_platinum/predictions"]
    # Row-only: no marker column is invented, and the state stays unmarked.
    assert (predictions.state, predictions.column, predictions.rows, predictions.marked_rows) == ("unmarked", "", 1, 0)
    assert "04_platinum/predictions" in observation.reason


def test_doctor_reports_an_empty_lake_as_empty(tmp_path: Path) -> None:
    """R7: no rows in any observed table is `empty`, not `real` and not `synthetic`."""
    observation = readiness.observe_synthetic_provenance(lake_dir=tmp_path / "absent-lake")

    assert observation.state == "empty"
    assert (observation.rows, observation.marked_rows) == (0, 0)
    assert observation.earliest_marked_date is None
    assert {row.state for row in observation.tables} == {"missing"}
    assert "no rows" in observation.reason


def test_doctor_reports_present_but_empty_directories_as_empty(tmp_path: Path) -> None:
    """R7: created-but-rowless table directories hold nothing to delete."""
    lake = tmp_path / "lake"
    for relative in (
        "01_bronze/market_data/us_equity",
        "02_silver/corporate_action_coverage/us_equity",
        "03_gold/features",
        "04_platinum/predictions",
    ):
        (lake / relative).mkdir(parents=True)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "empty"
    assert observation.rows == 0
    assert {row.state for row in observation.tables} >= {"empty"}


def test_doctor_reports_an_unreadable_marked_table_as_unknown(tmp_path: Path) -> None:
    """R7: a partial read is `unknown`; an absent marker must not be inferred as real."""
    lake = tmp_path / "lake"
    gold = lake / "03_gold/features"
    gold.mkdir(parents=True)
    (gold / "unexpected.txt").write_text("not a delta table", encoding="utf-8")

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "unknown"
    assert "cannot read" in observation.reason
    assert [row.state for row in observation.tables if row.table == "03_gold/features"] == ["unreadable"]


def test_synthetic_marker_still_wins_when_bronze_rows_coexist(tmp_path: Path) -> None:
    """R7: the seeded lake writes Bronze rows too; the existing marker still wins."""
    lake = tmp_path / "lake"
    assert write_delta(_bronze_price_rows(), "01_bronze/market_data/us_equity", lake_dir=lake)
    assert write_delta(
        _coverage_rows(["synthetic_demo_seed"], date(2023, 10, 30), date(2026, 10, 28)),
        "02_silver/corporate_action_coverage/us_equity",
        lake_dir=lake,
        partition_by=["coverage_end"],
    )
    assert write_delta(_marked_feature_rows(), "03_gold/features", lake_dir=lake)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "synthetic"
    assert observation.marked_rows == 3
    assert observation.rows == 6
    by_table = {row.table: row for row in observation.tables}
    # Bronze contributes rows but never a synthetic mark.
    assert (by_table["01_bronze/market_data/us_equity"].rows, by_table["01_bronze/market_data/us_equity"].marked_rows) == (2, 0)


def _news_sentiment_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "article_id": ["a1", "a2"],
            "date": [date(2024, 12, 2), date(2024, 12, 12)],
            "sentiment_score": [0.4, -0.2],
        }
    )


def _macro_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series": ["CPIAUCSL", "GDPC1"],
            "date": [date(2024, 12, 2), date(2024, 12, 12)],
            "value": [310.0, 23000.0],
        }
    )


def test_doctor_counts_an_unenumerated_silver_dataset_as_rows(tmp_path: Path) -> None:
    """R7/D3: `empty` covers every Delta dataset a reset deletes, not 12 known ones.

    A lake whose only rows live in `02_silver/news_sentiment` used to report `empty`,
    which let `make reset-lake` delete them; the dataset is now discovered generically.
    """
    lake = tmp_path / "lake"
    assert write_delta(_news_sentiment_rows(), "02_silver/news_sentiment", lake_dir=lake)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "unknown"
    assert observation.state != "empty"
    assert (observation.rows, observation.marked_rows) == (2, 0)
    news = {row.table: row for row in observation.tables}["02_silver/news_sentiment"]
    # Discovered generically: row-only, no marker column invented.
    assert (news.state, news.column, news.rows, news.marked_rows) == ("unmarked", "", 2, 0)
    assert "02_silver/news_sentiment" in observation.reason
    assert "cannot prove real ingestion" in observation.reason


def test_doctor_never_reports_empty_when_non_delta_data_files_remain(tmp_path: Path) -> None:
    """R7/D3: a bare-Parquet table under 02_silver has rows the reset would delete.

    It carries no `_delta_log`, so its rows cannot be counted; the lake must be
    `unknown` rather than `empty`.
    """
    lake = tmp_path / "lake"
    stray = lake / "02_silver/news_sentiment"
    stray.mkdir(parents=True)
    (stray / "part-0.parquet").write_bytes(b"not-a-real-parquet-but-a-data-file")

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "unknown"
    assert observation.state != "empty"
    assert (observation.rows, observation.marked_rows) == (0, 0)
    assert "02_silver/news_sentiment/part-0.parquet" in observation.reason
    assert "cannot be counted" in observation.reason


def test_doctor_counts_an_unenumerated_bronze_dataset_as_rows(tmp_path: Path) -> None:
    """R7/D3: `01_bronze/macro` rows are only deleted if counted; they are counted."""
    lake = tmp_path / "lake"
    assert write_delta(_macro_rows(), "01_bronze/macro", lake_dir=lake)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "unknown"
    assert observation.state != "empty"
    assert (observation.rows, observation.marked_rows) == (2, 0)
    macro = {row.table: row for row in observation.tables}["01_bronze/macro"]
    assert (macro.state, macro.column, macro.rows) == ("unmarked", "", 2)
    assert "01_bronze/macro" in observation.reason
    # Macro rows are not Bronze *price* rows: the reason must not claim that label.
    assert "identity_provenance" not in observation.reason


def test_synthetic_marker_still_wins_beside_unenumerated_datasets(tmp_path: Path) -> None:
    """A seeded-then-partially-ingested lake stays resettable (marker precedence)."""
    lake = tmp_path / "lake"
    assert write_delta(_news_sentiment_rows(), "02_silver/news_sentiment", lake_dir=lake)
    assert write_delta(_macro_rows(), "01_bronze/macro", lake_dir=lake)
    assert write_delta(
        _coverage_rows(["synthetic_demo_seed"], date(2023, 10, 30), date(2026, 10, 28)),
        "02_silver/corporate_action_coverage/us_equity",
        lake_dir=lake,
        partition_by=["coverage_end"],
    )

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "synthetic"
    assert observation.rows == 5
    assert observation.marked_rows == 1


def test_doctor_caps_the_reported_table_list_and_keeps_exact_totals(tmp_path: Path) -> None:
    """A lake with many datasets cannot flood the report; the row totals stay exact."""
    lake = tmp_path / "lake"
    extra = 2
    for index in range(readiness.MAX_REPORTED_TABLES + extra):
        assert write_delta(_macro_rows(), f"01_bronze/discovered_{index:02d}", lake_dir=lake)

    observation = readiness.observe_synthetic_provenance(lake_dir=lake)

    assert observation.state == "unknown"
    assert observation.tables_truncated is True
    assert len(observation.tables) == readiness.MAX_REPORTED_TABLES
    # Exact, even though the per-table list is a slice.
    assert observation.rows == (readiness.MAX_REPORTED_TABLES + extra) * 2
    assert "not listed" in observation.reason


def test_synthetic_marker_tables_match_the_canonical_writers() -> None:
    """The marker tables must be the Silver/Gold tables the writers actually write."""
    from equity_lake.ingestion.corporate_actions import corporate_action_coverage_table

    for market in ("us_equity", "cn_ashare", "hk_sg_equity", "jpx_equity", "krx_equity"):
        assert readiness.COVERAGE_TABLE_TEMPLATE.format(market=market) == corporate_action_coverage_table(market)
    assert readiness.GOLD_FEATURES_TABLE == "03_gold/features"
    assert readiness.BRONZE_PRICE_TABLE_TEMPLATE.format(market="us_equity") == "01_bronze/market_data/us_equity"
    assert readiness.PREDICTIONS_TABLE == "04_platinum/predictions"
    assert readiness.BRONZE_MARKER_COLUMN == "identity_provenance"
    assert readiness.SYNTHETIC_MARKER == "synthetic_demo_seed"
    # The `empty` scope is the reset artifact set, minus the row-free models directory.
    assert readiness.RESET_DATASET_TREES == ("01_bronze", "02_silver", "03_gold", "04_platinum/predictions")
