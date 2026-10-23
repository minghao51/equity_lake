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

import equity_lake.signals.readiness as readiness  # type: ignore[import-untyped]
from equity_lake.cli.commands import tracking  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.models import Signal, SignalScanOutcome, SignalScanReport  # type: ignore[import-untyped]


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
    )

    assert [(row.market, row.mic, row.ticker) for row in report.tickers] == [("us_equity", "XNAS", "AAPL")]
