"""All-market private tracking journey using real calendars, writers, and Delta reads."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import exchange_calendars as xc
import polars as pl
import pytest
import yaml
from deltalake import DeltaTable
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ActionCoverageProof,
    decision_session_close,
)
from equity_lake.core.instruments import InstrumentKey  # pyright: ignore[reportMissingImports] -- source import verified by uv
from equity_lake.core.schemas import CORPORATE_ACTION_SCHEMA  # pyright: ignore[reportMissingImports] -- source import verified by uv

tracking = import_module("equity_lake.signals.tracking")
runner = CliRunner()
NOW = datetime(2024, 2, 2, 13, 0, tzinfo=UTC)
MIC_CALENDARS = {"XNAS": "XNAS", "XSHG": "XSHG", "XHKG": "XHKG", "XSES": "XSES", "XTKS": "JPX", "XKRX": "XKRX"}
INSTRUMENTS = (
    InstrumentKey(market="us_equity", mic="XNAS", symbol="SHARED"),
    InstrumentKey(market="cn_ashare", mic="XSHG", symbol="SHARED"),
    InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="SHARED"),
    InstrumentKey(market="hk_sg_equity", mic="XSES", symbol="SHARED"),
    InstrumentKey(market="jpx_equity", mic="XTKS", symbol="SHARED"),
    InstrumentKey(market="krx_equity", mic="XKRX", symbol="SHARED"),
)


def _ticker_config(path: Path) -> Path:
    markets: dict[str, Any] = {}
    for key, exchange in (
        ("us", "NASDAQ"),
        ("cn", "SSE"),
        ("hk", "HKEX"),
        ("sg", "SGX"),
        ("jpx", "TSE"),
        ("krx", "KRX"),
    ):
        markets[key] = {
            "currency": "XXX",
            "tickers": [{"symbol": "SHARED", "name": "Synthetic", "exchange": exchange, "sector": "Test", "active": True}],
        }
    path.write_text(yaml.safe_dump({"markets": markets}, sort_keys=False), encoding="utf-8")
    return path


def _json_document(output: str) -> Any:
    assert output.lstrip().startswith("{"), "CLI diagnostics must not prefix the JSON document"
    return json.loads(output)


def _price_row(instrument: InstrumentKey, session: date, close: float) -> dict[str, Any]:
    return {
        "market": instrument.market,
        "mic": instrument.mic,
        "symbol": instrument.symbol,
        "ticker": instrument.symbol,
        "date": session,
        "open": 100.0,
        "high": max(100.0, close),
        "low": min(100.0, close),
        "close": close,
        "volume": 1000,
    }


def _write_prices(
    instruments: tuple[InstrumentKey, ...],
    sessions_by_identity: dict[tuple[str, str, str], date],
    closes_by_identity: dict[tuple[str, str, str], float],
    *,
    lake_dir: Path,
) -> None:
    from equity_lake.ingestion.writers import upsert_dataset  # pyright: ignore[reportMissingImports] -- source import verified by uv

    grouped: dict[str, list[dict[str, Any]]] = {}
    for instrument in instruments:
        target = sessions_by_identity[instrument.tuple]
        close = closes_by_identity[instrument.tuple]
        calendar = xc.get_calendar(MIC_CALENDARS[instrument.mic])
        sessions = calendar.sessions_in_range((target - timedelta(days=100)).isoformat(), target.isoformat())[-20:]
        dates = [date.fromisoformat(str(session)[:10]) for session in sessions]
        assert len(dates) == 20 and dates[-1] == target
        grouped.setdefault(instrument.market, []).extend(
            _price_row(instrument, session, close if index == len(dates) - 1 else 100.0) for index, session in enumerate(dates)
        )

    for market, rows in grouped.items():
        frame = pl.DataFrame(rows)
        assert upsert_dataset(
            frame,
            f"01_bronze/market_data/{market}",
            max(cast(date, row["date"]) for row in rows),
            partition_by=["date"],
        ), f"pointblank-validated price fixture write failed for {market} at {lake_dir}"


def _write_coverage_and_empty_actions(
    instruments: tuple[InstrumentKey, ...], sessions_by_identity: dict[tuple[str, str, str], date], lake_dir: Path
) -> None:
    corporate_actions = import_module("equity_lake.ingestion.corporate_actions")
    delta = import_module("equity_lake.storage.delta")
    from equity_lake.ingestion.writers import upsert_dataset  # pyright: ignore[reportMissingImports] -- source import verified by uv

    proofs: list[dict[str, Any]] = []
    for instrument in instruments:
        target = sessions_by_identity[instrument.tuple]
        calendar = xc.get_calendar(MIC_CALENDARS[instrument.mic])
        sessions = calendar.sessions_in_range((target - timedelta(days=100)).isoformat(), target.isoformat())[-20:]
        first_session = date.fromisoformat(str(sessions[0])[:10])
        proof = ActionCoverageProof(
            market=instrument.market,
            mic=instrument.mic,
            symbol=instrument.symbol,
            source="synthetic-source-proof",
            coverage_start=first_session,
            coverage_end=target,
            source_available_at=datetime(2024, 1, 1, tzinfo=UTC),
            source_valid_until=decision_session_close(instrument, target),
        )
        assert proof.source_valid_until == decision_session_close(instrument, target)
        proofs.append(proof.as_row())

    coverage_by_market: dict[str, list[dict[str, Any]]] = {}
    for row in proofs:
        coverage_by_market.setdefault(cast(str, row["market"]), []).append(row)
    for market, rows in coverage_by_market.items():
        table = corporate_actions.corporate_action_coverage_table(market)
        assert upsert_dataset(
            pl.DataFrame(rows),
            table,
            max(cast(date, row["coverage_end"]) for row in rows),
            partition_by=["coverage_end"],
        ), f"pointblank-validated action coverage write failed for {market}"

    empty_action_schema = pl.DataFrame(schema=CORPORATE_ACTION_SCHEMA).to_arrow().schema
    for market in sorted({instrument.market for instrument in instruments}):
        action_table = corporate_actions.corporate_actions_table(market, "silver")
        action_path = delta.delta_table_path(action_table, lake_dir)
        DeltaTable.create(action_path, schema=empty_action_schema, partition_by=["ex_date"])
        assert DeltaTable.is_deltatable(str(action_path))


def test_private_tracking_all_market_real_delta_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Add → readiness → exact-session scan → event → mocked retry → private projections."""
    delta = import_module("equity_lake.storage.delta")
    monkeypatch.setattr(delta, "LAKE_DIR", tmp_path / "synthetic-lake")
    lake_dir = tmp_path / "synthetic-lake"
    source_config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "private" / "watchlist.yaml"
    signal_config = tmp_path / "signals.yaml"
    signal_config.write_text(
        "backtest: {enabled: false}\nsentiment: {enabled: false}\nml: {enabled: false}\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / "auxiliary"
    cli = import_module("equity_lake.cli.__main__").app

    sessions_by_identity: dict[tuple[str, str, str], date] = {}
    closes_by_identity = {instrument.tuple: 103.0 for instrument in INSTRUMENTS}
    for instrument in INSTRUMENTS:
        target, is_open_today = tracking._latest_completed_session(instrument, NOW)
        assert is_open_today and target is not None
        sessions_by_identity[instrument.tuple] = target

    _write_prices(INSTRUMENTS, sessions_by_identity, closes_by_identity, lake_dir=lake_dir)
    _write_coverage_and_empty_actions(INSTRUMENTS, sessions_by_identity, lake_dir)

    for index, instrument in enumerate(INSTRUMENTS):
        args = [
            "tracking",
            "add",
            "--market",
            instrument.market,
            "--mic",
            instrument.mic,
            "--symbol",
            instrument.symbol,
            "--watchlist",
            str(watchlist),
            "--ticker-config",
            str(source_config),
            "--data-dir",
            str(data_dir),
        ]
        if index == 0:
            args.extend(["--notes", "private journey note"])
        added = runner.invoke(cli, args)
        assert added.exit_code == 0, added.output

    corporate_actions = import_module("equity_lake.ingestion.corporate_actions")
    for market in sorted({instrument.market for instrument in INSTRUMENTS}):
        actions, coverage = corporate_actions.load_corporate_action_inputs(market, lake_dir=lake_dir)
        assert actions.is_empty(), f"expected an actual empty Silver action Delta table for {market}"
        expected_count = sum(instrument.market == market for instrument in INSTRUMENTS)
        assert coverage.height == expected_count

    ready = runner.invoke(
        cli,
        [
            "tracking",
            "readiness",
            "--as-of",
            NOW.isoformat(),
            "--watchlist",
            str(watchlist),
            "--ticker-config",
            str(source_config),
            "--data-dir",
            str(data_dir),
            "--lake-dir",
            str(lake_dir),
        ],
    )
    assert ready.exit_code == 0, ready.output
    readiness = _json_document(ready.stdout)
    assert len(readiness["readiness"]) == len(INSTRUMENTS)
    assert {row["status"] for row in readiness["readiness"]} == {"signal"}
    assert {tuple(row["instrument"][key] for key in ("market", "mic", "symbol")) for row in readiness["readiness"]} == {
        instrument.tuple for instrument in INSTRUMENTS
    }

    def scan(clock: datetime = NOW) -> dict[str, Any]:
        result = runner.invoke(
            cli,
            [
                "tracking",
                "scan",
                "--as-of",
                clock.isoformat(),
                "--watchlist",
                str(watchlist),
                "--ticker-config",
                str(source_config),
                "--config",
                str(signal_config),
                "--lake-dir",
                str(lake_dir),
                "--data-dir",
                str(data_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        return cast(dict[str, Any], _json_document(result.stdout))

    first = scan()
    assert first["status"] == "success" and len(first["events"]) == len(INSTRUMENTS)
    assert {tuple(event["instrument"][key] for key in ("market", "mic", "symbol")) for event in first["events"]} == {
        instrument.tuple for instrument in INSTRUMENTS
    }
    assert scan()["events"] == []

    us = INSTRUMENTS[0]
    us_session = sessions_by_identity[us.tuple]
    _write_prices((us,), sessions_by_identity, {**closes_by_identity, us.tuple: 98.0}, lake_dir=lake_dir)
    changed = scan()
    assert len(changed["events"]) == 1 and changed["events"][0]["action"] == "SELL"
    assert changed["events"][0]["transition"] == 2
    assert scan()["events"] == []

    _write_prices((us,), sessions_by_identity, {**closes_by_identity, us.tuple: 100.0}, lake_dir=lake_dir)
    assert scan()["events"] == []  # successful neutral re-arms, but is not itself an event
    _write_prices((us,), sessions_by_identity, closes_by_identity, lake_dir=lake_dir)
    rearmed = scan()
    assert len(rearmed["events"]) == 1
    assert rearmed["events"][0]["action"] == "BUY" and rearmed["events"][0]["transition"] == 3

    us_table = delta.delta_table_path("01_bronze/market_data/us_equity", lake_dir)
    DeltaTable(us_table).delete(predicate=f"date = '{us_session.isoformat()}' AND mic = '{us.mic}' AND symbol = '{us.symbol}'")
    stale = scan()
    assert stale["status"] == "partial" and stale["events"] == []
    _write_prices((us,), sessions_by_identity, closes_by_identity, lake_dir=lake_dir)
    assert scan()["events"] == []  # stale data did not clear active BUY state

    closed_clock = datetime(2024, 2, 4, 13, 0, tzinfo=UTC)
    closed = runner.invoke(
        cli,
        [
            "tracking",
            "scan",
            "--as-of",
            closed_clock.isoformat(),
            "--watchlist",
            str(watchlist),
            "--ticker-config",
            str(source_config),
            "--config",
            str(signal_config),
            "--lake-dir",
            str(lake_dir),
            "--data-dir",
            str(data_dir),
        ],
    )
    assert closed.exit_code == 1
    closed_report = _json_document(closed.stdout)
    assert closed_report["status"] == "not_scheduled" and closed_report["events"] == []
    assert scan()["events"] == []  # a closed session also leaves the active state unchanged

    # A source-reported error remains a failure, not a successful neutral. The adapter
    # receives complete synthetic trusted metadata and no real model is trained/loaded.
    forecasting = import_module("equity_lake.ml.forecasting")
    identity_schema_version = import_module("equity_lake.core.instruments").IDENTITY_SCHEMA_VERSION
    error_config = tmp_path / "ml-error.yaml"
    error_config.write_text(
        "backtest: {enabled: false}\nsentiment: {enabled: false}\n"
        "ml: {enabled: true, mode: v1_direction, buy_probability_threshold: 0.6, min_confidence: 60}\n",
        encoding="utf-8",
    )

    class ExplicitMetadataForecaster:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def predict(self, *_args: Any) -> dict[str, Any]:
            return {
                "model_version": "synthetic-trusted-model-v1",
                "model_age_days": 1,
                "feature_schema_compatibility": "known",
                "adjustment_compatibility": "known",
                "identity_schema_version": identity_schema_version,
                "threshold_source": "default",
                "probability": float("nan"),
            }

        def close(self) -> None:
            pass

    monkeypatch.setattr(forecasting, "PriceForecaster", ExplicitMetadataForecaster)
    error_data_dir = tmp_path / "error-outcomes"
    error_scan = tracking.scan_private_watchlist(
        now=NOW,
        watchlist=tracking.PrivateWatchlist(instruments=[tracking.TrackedInstrument(instrument=us)]),
        lake_dir=lake_dir,
        data_dir=error_data_dir,
        signal_config_path=error_config,
    )
    us_outcomes = next(item for item in error_scan.evaluations if item.instrument == us).rules
    assert next(outcome for outcome in us_outcomes if outcome.source == "ml").status == "error"
    monkeypatch.setattr(tracking, "PRIVATE_WATCHLIST_PATH", tmp_path / "no-default-watchlist.yaml")
    error_digest = tracking.build_digest(now=NOW, data_dir=error_data_dir)
    assert any(outcome.status == "error" for outcome in error_digest.failures)
    assert error_digest.stale_run_warning is not None

    attempts: dict[str, int] = {}
    sent_payloads: list[dict[str, Any]] = []

    def mock_sender(payload: Any) -> None:
        attempts[payload.event_id] = attempts.get(payload.event_id, 0) + 1
        if attempts[payload.event_id] == 1:
            raise OSError("synthetic offline retry; no exception text is retained")
        sent_payloads.append(payload.model_dump(mode="json"))

    delivered = tracking.deliver_pending(
        enabled=True,
        sender=mock_sender,
        now=NOW,
        watchlist_file=watchlist,
        ticker_config_path=source_config,
        signal_config_path=signal_config,
        lake_dir=lake_dir,
        data_dir=data_dir,
    )
    assert delivered.sent == len(INSTRUMENTS) + 1
    assert delivered.expired == 1  # the changed SELL no longer matches the current BUY
    assert delivered.attempted == 2 * (len(INSTRUMENTS) + 1)
    assert len(sent_payloads) == len(INSTRUMENTS) + 1
    assert set(attempts.values()) == {2}
    assert "private journey note" not in json.dumps(sent_payloads)

    snapshot = tracking.private_tracking_snapshot(
        now=NOW,
        watchlist_file=watchlist,
        ticker_config_path=source_config,
        data_dir=data_dir,
        lake_dir=lake_dir,
    )
    assert snapshot.watchlist.instruments[0].notes == "private journey note"
    assert all(row.status in {"sent", "expired"} for row in snapshot.recent_events)
    assert {row.status for row in snapshot.recent_events} == {"sent", "expired"}

    api_main = import_module("equity_lake.api.main")
    tracking_router = import_module("equity_lake.api.routers.tracking")
    monkeypatch.setattr(tracking_router, "_snapshot", lambda: snapshot)
    client = TestClient(api_main.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 51234))
    response = client.get("/tracking/private")
    assert response.status_code == 200
    assert response.json()["watchlist"]["instruments"][0]["notes"] == "private journey note"
    assert client.get("/tracking/private", headers={"x-forwarded-for": "192.0.2.50"}).status_code == 403

    pytest.importorskip("streamlit", reason="private tracking UI projection requires the optional dashboard group")
    streamlit_app = import_module("equity_lake.dashboard.streamlit_app")
    ui_calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(streamlit_app.st, "get_option", lambda _name: "localhost", raising=False)
    for name in ("title", "caption", "subheader", "dataframe", "info", "warning", "error"):
        monkeypatch.setattr(streamlit_app.st, name, lambda *args, _name=name, **_kwargs: ui_calls.append((_name, args)), raising=False)
    real_import = streamlit_app.import_module
    monkeypatch.setattr(
        streamlit_app,
        "import_module",
        lambda name: SimpleNamespace(private_tracking_snapshot=lambda: snapshot) if name == "equity_lake.signals.tracking" else real_import(name),
    )
    streamlit_app.render_private_tracking_preview()
    assert any(name == "dataframe" and "private journey note" in repr(args) for name, args in ui_calls)
    assert any(name == "dataframe" and "delivery" in repr(args) for name, args in ui_calls)

    # Same ticker text is distinct by market/MIC; HK's 2024-07-01 holiday does not close SG.
    holiday_clock = datetime(2024, 7, 1, 15, 0, tzinfo=UTC)
    hk = next(item for item in INSTRUMENTS if item.mic == "XHKG")
    sg = next(item for item in INSTRUMENTS if item.mic == "XSES")
    hk_session, hk_open = tracking._latest_completed_session(hk, holiday_clock)
    sg_session, sg_open = tracking._latest_completed_session(sg, holiday_clock)
    assert hk_open is False and hk_session == date(2024, 6, 28)
    assert sg_open is True and sg_session == date(2024, 7, 1)
    assert hk.symbol == sg.symbol and hk.tuple != sg.tuple
