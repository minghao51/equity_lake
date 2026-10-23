"""Synthetic private-watchlist, state-machine, and all-market journey contracts."""

from __future__ import annotations

import json
import stat
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from equity_lake.core.instruments import InstrumentKey

tracking = import_module("equity_lake.signals.tracking")

runner = CliRunner()
SESSION = date(2024, 2, 1)
CLOCK = datetime(2024, 2, 2, 17, 30, tzinfo=UTC)
MARKETS = {
    "us_equity": ("us", "NASDAQ", "XNAS"),
    "cn_ashare": ("cn", "SSE", "XSHG"),
    "hk_sg_equity": ("hk", "HKEX", "XHKG"),
    "jpx_equity": ("jpx", "TSE", "XTKS"),
    "krx_equity": ("krx", "KRX", "XKRX"),
}


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


def _json_output(output: str) -> Any:
    """Parse the final CLI JSON document after informational config logs."""
    start = output.find("{")
    if start < 0:
        raise AssertionError("CLI output has no JSON document")
    return json.loads(output[start:])


def _instrument(market: str, mic: str, symbol: str = "SHARED") -> InstrumentKey:
    return InstrumentKey(market=market, mic=mic, symbol=symbol)


def _candidate(instrument: InstrumentKey, *, action: str = "BUY", session: date = SESSION) -> Any:
    return tracking.TrackingCandidate(
        instrument=instrument,
        session=session,
        source="price_sma20",
        action=cast(Any, action),
        rule_version=tracking.TRACKING_RULE_VERSION,
        reasoning="synthetic split-adjusted SMA20 threshold crossing",
        price=103.0,
        sma20=100.0,
        deviation=0.03,
        threshold=0.02 if action == "BUY" else -0.01,
    )


def _scan_report(
    instrument: InstrumentKey,
    outcome: Any,
    *,
    suffix: int,
    completed_at: datetime | None = None,
    config_fingerprint: str = "a" * 64,
) -> Any:
    now = completed_at or datetime(2024, 2, 1, 22, 0, tzinfo=UTC) + timedelta(seconds=suffix)
    return tracking.TrackingScanReport(
        run_id=f"{suffix:032x}",
        watchlist="Synthetic",
        config_fingerprint=config_fingerprint,
        config_provenance="synthetic",
        started_at=now - timedelta(seconds=1),
        completed_at=now,
        status="success" if outcome.status in {"signal", "no_condition"} else "partial",
        evaluations=[
            tracking.TrackingEvaluation(
                instrument=instrument,
                expected_session=outcome.candidate.session if outcome.candidate else SESSION,
                evaluated_at=now,
                status="success",
                rules=[outcome],
            )
        ],
    )


@pytest.mark.parametrize(
    ("clock", "quiet"),
    [
        (datetime(2024, 3, 10, 1, 59, tzinfo=UTC), False),  # 20:59 EST
        (datetime(2024, 3, 10, 2, 0, tzinfo=UTC), True),  # 21:00 EST
        (datetime(2024, 3, 10, 11, 59, tzinfo=UTC), True),  # 07:59 EDT
        (datetime(2024, 3, 10, 12, 0, tzinfo=UTC), False),  # 08:00 EDT
        (datetime(2024, 11, 3, 0, 59, tzinfo=UTC), False),  # 20:59 EDT
        (datetime(2024, 11, 3, 1, 0, tzinfo=UTC), True),  # 21:00 EDT
        (datetime(2024, 11, 3, 12, 59, tzinfo=UTC), True),  # 07:59 EST
        (datetime(2024, 11, 3, 13, 0, tzinfo=UTC), False),  # 08:00 EST
    ],
)
def test_quiet_hours_boundaries_are_dst_aware(clock: datetime, quiet: bool) -> None:
    assert tracking.in_quiet_hours(clock) is quiet


def test_private_watchlist_crud_validates_identity_and_keeps_file_private(tmp_path: Path) -> None:
    config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "private" / "watchlist.yaml"
    data_dir = tmp_path / "aux"

    result = tracking.add_instrument(
        market="hk_sg_equity",
        mic="XHKG",
        symbol="SHARED",
        groups=["growth"],
        tags=["review"],
        notes="private research note",
        zone_low=80.0,
        zone_high=120.0,
        path=watchlist,
        ticker_config_path=config,
        data_dir=data_dir,
    )
    assert result.instruments[0].instrument == _instrument("hk_sg_equity", "XHKG")
    assert result.instruments[0].held is False
    assert stat.S_IMODE(watchlist.stat().st_mode) == 0o600
    assert stat.S_IMODE(watchlist.parent.stat().st_mode) == 0o700

    updated = tracking.update_instrument(
        _instrument("hk_sg_equity", "XHKG"),
        held=True,
        groups=["core"],
        tags=["watch", "earnings"],
        notes="updated private note",
        set_notes=True,
        zone_low=90.0,
        path=watchlist,
        ticker_config_path=config,
    )
    item = updated.instruments[0]
    assert item.held is True
    assert item.groups == ["core"]
    assert item.tags == ["watch", "earnings"]
    assert item.notes == "updated private note"
    assert item.price_zone is not None and (item.price_zone.low, item.price_zone.high) == (90.0, 120.0)

    settings = tracking.update_watchlist_settings(cooldown_minutes=15, path=watchlist, ticker_config_path=config)
    assert settings.cooldown_minutes == 15
    removed = tracking.remove_instrument(_instrument("hk_sg_equity", "XHKG"), path=watchlist, data_dir=data_dir, ticker_config_path=config)
    assert removed.instruments == []
    assert not tracking.tracking_db_path(data_dir).exists()


def test_private_watchlist_rejects_ambiguous_mic_and_malformed_yaml(tmp_path: Path) -> None:
    config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "private.yaml"

    with pytest.raises(tracking.TrackingError, match="ambiguous across configured MICs"):
        tracking.add_instrument(market="hk_sg_equity", symbol="SHARED", path=watchlist, ticker_config_path=config)
    assert not watchlist.exists()

    watchlist.write_text("schema_version: 99\ninstruments: []\n", encoding="utf-8")
    with pytest.raises(tracking.TrackingError, match="private watchlist is invalid"):
        tracking.load_private_watchlist(watchlist, config)


def test_legacy_watchlist_migration_requires_one_exact_identity(tmp_path: Path) -> None:
    ambiguous_config = _ticker_config(tmp_path / "ambiguous-tickers.yaml")
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("name: Legacy\ntickers: [SHARED]\ngroups:\n  core: [SHARED]\n", encoding="utf-8")
    with pytest.raises(tracking.TrackingError, match="does not have one exact active source-config identity"):
        tracking.migrate_legacy_watchlist(source=legacy, ticker_config_path=ambiguous_config, destination=tmp_path / "private.yaml")

    unique_config = tmp_path / "unique-tickers.yaml"
    unique_config.write_text(
        yaml.safe_dump(
            {
                "markets": {
                    "us": {
                        "currency": "USD",
                        "tickers": [{"symbol": "SHARED", "name": "Synthetic", "exchange": "NASDAQ", "sector": "Test"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    migrated = tracking.migrate_legacy_watchlist(source=legacy, ticker_config_path=unique_config, destination=tmp_path / "migrated.yaml")
    assert migrated.instruments[0].instrument == _instrument("us_equity", "XNAS")
    assert migrated.instruments[0].groups == ["core"]
    with pytest.raises(tracking.TrackingError, match="already exists"):
        tracking.migrate_legacy_watchlist(source=legacy, ticker_config_path=unique_config, destination=tmp_path / "migrated.yaml")


def test_delta_read_error_does_not_fall_back_to_uncommitted_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    delta = import_module("equity_lake.storage.delta")
    instrument = _instrument("us_equity", "XNAS")
    table_path = tmp_path / "price-table"
    (table_path / "_delta_log").mkdir(parents=True)
    (table_path / "uncommitted.parquet").touch()
    monkeypatch.setattr(tracking, "delta_table_path", lambda _table, _lake: table_path)

    def corrupt_delta(table: str, *, lake_dir: Path) -> pl.DataFrame:
        del lake_dir
        raise delta.DeltaReadError(table, RuntimeError("synthetic corrupt Delta snapshot"))

    monkeypatch.setattr(tracking, "read_delta", corrupt_delta)
    monkeypatch.setattr(tracking.pl, "read_parquet", lambda *_args, **_kwargs: pytest.fail("never bypass a corrupt Delta log"))

    with pytest.raises(delta.DeltaReadError):
        tracking._read_price_table(instrument, tmp_path)


def test_price_rule_requires_canonical_exact_session_twenty_venue_bars_and_split_adjustment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import exchange_calendars as xc

    corporate_actions = import_module("equity_lake.ingestion.corporate_actions")

    instrument = _instrument("us_equity", "XNAS")
    expected = date(2024, 1, 31)
    sessions = xc.get_calendar("XNAS").sessions_in_range((expected - timedelta(days=50)).isoformat(), expected.isoformat())[-20:]
    session_dates = [date.fromisoformat(str(session)[:10]) for session in sessions]
    frame = pl.DataFrame(
        {
            "market": [instrument.market] * len(session_dates),
            "mic": [instrument.mic] * len(session_dates),
            "symbol": [instrument.symbol] * len(session_dates),
            "date": session_dates,
            "close": [100.0] * (len(session_dates) - 1) + [103.0],
        }
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tracking, "_latest_completed_session", lambda _instrument, _now: (expected, True))
    monkeypatch.setattr(tracking, "_read_price_table", lambda _instrument, _lake: frame)
    monkeypatch.setattr(corporate_actions, "load_corporate_action_inputs", lambda *_args, **_kwargs: (pl.DataFrame(), pl.DataFrame()))

    def adjust(prices: pl.DataFrame, _actions: pl.DataFrame, *, coverage: pl.DataFrame, method: str, as_of: date) -> pl.DataFrame:
        calls.append({"coverage": coverage, "method": method, "as_of": as_of})
        return prices

    monkeypatch.setattr(tracking, "with_price_adjustment", adjust)
    outcome = tracking._price_rule(instrument, CLOCK, tmp_path)
    assert outcome.status == "signal"
    assert outcome.candidate is not None and outcome.candidate.action == "BUY"
    assert calls[0]["method"] == "split_only" and calls[0]["as_of"] == expected

    missing_latest = frame.filter(pl.col("date") != pl.lit(expected))
    monkeypatch.setattr(tracking, "_read_price_table", lambda _instrument, _lake: missing_latest)
    assert tracking._price_rule(instrument, CLOCK, tmp_path).status == "unavailable"

    short_history = frame.tail(19)
    monkeypatch.setattr(tracking, "_read_price_table", lambda _instrument, _lake: short_history)
    assert tracking._price_rule(instrument, CLOCK, tmp_path).status == "unavailable"


def test_all_supported_instrument_calendars_resolve_open_closed_and_preclose_sessions() -> None:
    from zoneinfo import ZoneInfo

    instruments = [
        _instrument("us_equity", "XNAS"),
        _instrument("cn_ashare", "XSHG"),
        _instrument("hk_sg_equity", "XHKG"),
        _instrument("hk_sg_equity", "XSES"),
        _instrument("jpx_equity", "XTKS"),
        _instrument("krx_equity", "XKRX"),
    ]
    for instrument in instruments:
        zone = ZoneInfo(tracking.PRICE_MARKETS[instrument.market].timezone)
        before_close = datetime.combine(SESSION, datetime.min.time().replace(hour=9), tzinfo=zone)
        expected_before, open_before = tracking._latest_completed_session(instrument, before_close)
        assert expected_before == date(2024, 1, 31) and open_before is True
        after_close = datetime.combine(SESSION, datetime.min.time().replace(hour=18), tzinfo=zone)
        expected_after, open_after = tracking._latest_completed_session(instrument, after_close)
        assert expected_after == SESSION and open_after is True
        weekend = datetime.combine(date(2024, 2, 4), datetime.min.time().replace(hour=15), tzinfo=zone)
        expected_weekend, open_weekend = tracking._latest_completed_session(instrument, weekend)
        assert expected_weekend == date(2024, 2, 2) and open_weekend is False


def test_cooldown_suppresses_a_change_without_losing_it(tmp_path: Path) -> None:
    instrument = _instrument("us_equity", "XNAS")
    first_at = datetime(2024, 2, 1, 22, 0, tzinfo=UTC)
    first = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=_candidate(instrument))
    initial = tracking._persist_scan(_scan_report(instrument, first, suffix=20, completed_at=first_at), 15, tmp_path)

    changed_candidate = _candidate(instrument, action="SELL")
    changed = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=changed_candidate)
    suppressed = tracking._persist_scan(_scan_report(instrument, changed, suffix=21, completed_at=first_at + timedelta(minutes=5)), 15, tmp_path)
    emitted = tracking._persist_scan(_scan_report(instrument, changed, suffix=22, completed_at=first_at + timedelta(minutes=16)), 15, tmp_path)

    assert len(initial.events) == 1
    assert suppressed.events == []
    assert len(emitted.events) == 1
    assert emitted.events[0].action == "SELL" and emitted.events[0].transition == 2


def test_delivery_recovers_ambiguous_max_attempt_outbox_without_resending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    instrument = _instrument("us_equity", "XNAS")
    outcome = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=_candidate(instrument))
    created = tracking._persist_scan(_scan_report(instrument, outcome, suffix=76), 0, tmp_path)
    assert len(created.events) == 1

    connection = tracking._open_database(tmp_path, writable=True)
    assert connection is not None
    try:
        connection.execute(
            "UPDATE outbox SET attempts=?,status='pending' WHERE event_id=?",
            (tracking.MAX_DELIVERY_ATTEMPTS, created.events[0].event_id),
        )
    finally:
        connection.close()
    monkeypatch.setattr(tracking, "_event_is_current", lambda *_args, **_kwargs: pytest.fail("ambiguous event must not be resent"))

    result = tracking.deliver_pending(enabled=True, sender=lambda _payload: pytest.fail("sender must not be called"), now=CLOCK, data_dir=tmp_path)
    snapshot = tracking.private_tracking_snapshot(now=CLOCK, data_dir=tmp_path)

    assert result.status == "completed"
    assert result.attempted == 0 and result.sent == 0 and result.failed == 1 and result.pending == 0
    assert len(snapshot.recent_events) == 1
    assert snapshot.recent_events[0].status == "failed"
    assert snapshot.recent_events[0].attempts == tracking.MAX_DELIVERY_ATTEMPTS
    assert snapshot.recent_events[0].error_code == "ambiguous_outcome_after_max_attempts"


def test_removing_an_instrument_keeps_history_and_expires_pending_events(tmp_path: Path) -> None:
    config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "watchlist.yaml"
    data_dir = tmp_path / "aux"
    instrument = _instrument("us_equity", "XNAS")
    tracking.add_instrument(market=instrument.market, mic=instrument.mic, symbol=instrument.symbol, path=watchlist, ticker_config_path=config)
    outcome = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=_candidate(instrument))
    first = tracking._persist_scan(_scan_report(instrument, outcome, suffix=77), 0, data_dir)

    tracking.remove_instrument(instrument, path=watchlist, ticker_config_path=config, data_dir=data_dir)
    snapshot = tracking.private_tracking_snapshot(now=CLOCK, watchlist_file=watchlist, ticker_config_path=config, data_dir=data_dir)

    assert len(first.events) == 1
    assert snapshot.watchlist.instruments == []
    assert len(snapshot.recent_reports) == 1
    assert len(snapshot.recent_events) == 1
    assert snapshot.recent_events[0].status == "expired"
    assert snapshot.recent_events[0].error_code == "watchlist_removed"


def test_digest_reports_pending_opportunities_failures_and_no_evaluation_warning_in_quiet_hours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instrument = _instrument("us_equity", "XNAS")
    candidate = _candidate(instrument)
    signal = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=candidate)
    failed = tracking.TrackingRuleOutcome(source="ml", status="error", detail="synthetic strict inference failure")
    started_at = datetime(2024, 2, 1, 21, 59, tzinfo=UTC)
    completed_at = started_at + timedelta(seconds=1)
    partial = tracking.TrackingScanReport(
        run_id="a" * 32,
        watchlist="Synthetic",
        config_fingerprint="a" * 64,
        config_provenance="synthetic",
        started_at=started_at,
        completed_at=completed_at,
        status="partial",
        evaluations=[
            tracking.TrackingEvaluation(
                instrument=instrument,
                expected_session=SESSION,
                evaluated_at=completed_at,
                status="partial",
                rules=[signal, failed],
            )
        ],
    )
    created = tracking._persist_scan(partial, 0, tmp_path)
    closed_at = completed_at + timedelta(minutes=1)
    not_evaluated = tracking.TrackingRuleOutcome(source="price_sma20", status="not_scheduled", detail="venue is closed")
    closed_report = tracking.TrackingScanReport(
        run_id="b" * 32,
        watchlist="Synthetic",
        config_fingerprint="a" * 64,
        config_provenance="synthetic",
        started_at=closed_at - timedelta(seconds=1),
        completed_at=closed_at,
        status="not_scheduled",
        evaluations=[
            tracking.TrackingEvaluation(
                instrument=instrument,
                expected_session=SESSION,
                evaluated_at=closed_at,
                status="not_scheduled",
                rules=[not_evaluated],
            )
        ],
    )
    tracking._persist_scan(closed_report, 0, tmp_path)

    quiet_clock = datetime(2024, 2, 3, 3, 0, tzinfo=UTC)
    monkeypatch.setattr(tracking, "PRIVATE_WATCHLIST_PATH", tmp_path / "no-default-watchlist.yaml")
    digest = tracking.build_digest(now=quiet_clock, data_dir=tmp_path)
    assert tracking.in_quiet_hours(quiet_clock)
    assert [event.event_id for event in digest.opportunities] == [created.events[0].event_id]
    assert [outcome.status for outcome in digest.failures] == ["error"]
    assert digest.stale_run_warning is not None and "No successful private tracking scan" in digest.stale_run_warning
    assert digest.delivery_counts["pending"] == 1


def test_event_state_is_idempotent_and_only_neutral_evaluation_rearms(tmp_path: Path) -> None:
    instrument = _instrument("us_equity", "XNAS")
    candidate = _candidate(instrument)
    signal = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=candidate)

    first = tracking._persist_scan(_scan_report(instrument, signal, suffix=1), 0, tmp_path)
    repeated = tracking._persist_scan(_scan_report(instrument, signal, suffix=2), 0, tmp_path)
    unavailable = tracking.TrackingRuleOutcome(source="price_sma20", status="unavailable", detail="stale or corrupt input")
    tracking._persist_scan(_scan_report(instrument, unavailable, suffix=3), 0, tmp_path)
    errored = tracking.TrackingRuleOutcome(source="price_sma20", status="error", detail="synthetic evaluation error")
    tracking._persist_scan(_scan_report(instrument, errored, suffix=4), 0, tmp_path)
    closed = tracking.TrackingRuleOutcome(source="price_sma20", status="not_scheduled", detail="venue closed")
    tracking._persist_scan(_scan_report(instrument, closed, suffix=5), 0, tmp_path)
    still_active = tracking._persist_scan(_scan_report(instrument, signal, suffix=6), 0, tmp_path)
    neutral = tracking.TrackingRuleOutcome(source="price_sma20", status="no_condition", detail="complete neutral evaluation")
    tracking._persist_scan(_scan_report(instrument, neutral, suffix=7), 0, tmp_path)
    rearmed_candidate = _candidate(instrument, session=SESSION + timedelta(days=1))
    rearmed = tracking._persist_scan(
        _scan_report(instrument, tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=rearmed_candidate), suffix=8),
        0,
        tmp_path,
    )

    assert len(first.events) == 1
    assert repeated.events == []
    assert still_active.events == []
    assert len(rearmed.events) == 1 and rearmed.events[0].transition == 2
    snapshot = tracking.private_tracking_snapshot(now=CLOCK, data_dir=tmp_path)
    assert len(snapshot.recent_events) == 2
    assert {event.event.event_id for event in snapshot.recent_events} == {event.event_id for event in [first.events[0], rearmed.events[0]]}


@pytest.mark.parametrize("mode", ["v1_direction", "v2_meta_label"])
def test_ml_neutral_policy_family_rearms_only_exact_mode_and_config(mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the production ML rule adapter with trusted synthetic metadata, not model training."""
    forecasting = import_module("equity_lake.ml.forecasting")
    instrument = _instrument("us_equity", "XNAS")
    selected_fingerprint = "a" * 64
    unrelated_fingerprint = "b" * 64
    state: dict[str, Any] = {"model_version": "model-v1", "probability": 0.8, "should_execute": True, "no_candidate": False}

    class FakeForecaster:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def predict(self, *_args: Any) -> dict[str, Any]:
            if state["no_candidate"]:
                raise forecasting.NoCandidateConditionError("synthetic v2 candidate-none")
            result = {
                "model_version": state["model_version"],
                "model_age_days": 1,
                "feature_schema_compatibility": "known",
                "adjustment_compatibility": "known",
                "identity_schema_version": tracking.IDENTITY_SCHEMA_VERSION,
                "threshold_source": "configured_fallback" if mode == "v2_meta_label" else "default",
                "probability": state["probability"],
                "execution_probability": state["probability"],
                "threshold_used": 0.6,
                "should_execute": state["should_execute"],
                "candidate_action": "BUY",
            }
            return result

        def close(self) -> None:
            pass

    monkeypatch.setattr(forecasting, "PriceForecaster", FakeForecaster)
    ml_config = {"mode": mode, "min_confidence": 60, "meta_label_threshold": 0.6}

    def evaluate(fingerprint: str = selected_fingerprint) -> Any:
        return tracking._ml_rule(
            instrument,
            SESSION,
            ml_config,
            fingerprint=fingerprint,
            provenance="explicit",
        )

    def persist(outcome: Any, suffix: int, fingerprint: str = selected_fingerprint) -> Any:
        return tracking._persist_scan(
            _scan_report(instrument, outcome, suffix=suffix, config_fingerprint=fingerprint),
            0,
            tmp_path,
        )

    first = persist(evaluate(), 31)
    assert len(first.events) == 1 and first.events[0].model_version == "model-v1"

    # A model replacement is a distinct actionable event, even with the same action.
    state.update(model_version="model-v2", probability=0.8, should_execute=True)
    model_changed = persist(evaluate(), 32)
    assert len(model_changed.events) == 1
    assert model_changed.events[0].model_version == "model-v2"
    assert model_changed.events[0].rule_version != first.events[0].rule_version

    # Other config families, stale/error/missing-model, and closed-session states are not neutral.
    state.update(model_version="model-v1", probability=0.8, should_execute=True)
    assert persist(evaluate(), 33).events == []
    state.update(probability=0.5, should_execute=False)
    unrelated_neutral = persist(evaluate(unrelated_fingerprint), 34, unrelated_fingerprint)
    assert unrelated_neutral.events == []
    state.update(probability=0.8, should_execute=True)
    assert persist(evaluate(), 35).events == []
    for index, status in enumerate(("unavailable", "error", "missing_model", "not_scheduled"), start=36):
        unavailable = tracking._bind_rule_provenance(
            tracking.TrackingRuleOutcome(source="ml", status=status, detail=f"{status} is not neutral"),
            fingerprint=selected_fingerprint,
            provenance="explicit",
            mode=mode,
        )
        assert persist(unavailable, index).events == []
        assert persist(evaluate(), index + 10).events == []

    # Valid metadata supports mode-appropriate no-candidate outcomes: a low-confidence
    # v1 prediction, or a v2 candidate rejected by its effective execution threshold.
    state.update(model_version="model-v2", probability=0.5, should_execute=False)
    neutral = evaluate()
    assert neutral.status == "no_condition" and neutral.model_version == "model-v2"
    assert persist(neutral, 50).events == []
    state.update(probability=0.8, should_execute=True)
    rearmed = persist(evaluate(), 51)
    assert len(rearmed.events) == 1 and rearmed.events[0].transition == 2
    assert rearmed.events[0].model_version == "model-v2"

    if mode == "v2_meta_label":
        # The actual NoCandidateConditionError catch has no model version. Its stable
        # policy-family neutral still rearms only this exact selected mode/config.
        state["no_candidate"] = True
        candidate_none = evaluate()
        assert candidate_none.status == "no_condition" and candidate_none.model_version == "candidate-none"
        assert persist(candidate_none, 52).events == []
        state["no_candidate"] = False
        after_candidate_none = persist(evaluate(), 53)
        assert len(after_candidate_none.events) == 1 and after_candidate_none.events[0].transition == 3


def test_delivery_revalidation_binds_config_rule_model_and_latest_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.signals.config import load_signal_config  # pyright: ignore[reportMissingImports] -- source import verified by uv

    source_config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "watchlist.yaml"
    instrument = _instrument("us_equity", "XNAS")
    tracking.add_instrument(market=instrument.market, mic=instrument.mic, symbol=instrument.symbol, path=watchlist, ticker_config_path=source_config)
    selected_config = tmp_path / "selected.yaml"
    selected_config.write_text(
        "backtest: {enabled: false}\nsentiment: {enabled: false}\nml: {enabled: true, mode: v1_direction, buy_probability_threshold: 0.6}\n",
        encoding="utf-8",
    )
    changed_config = tmp_path / "changed.yaml"
    changed_config.write_text(
        "backtest: {enabled: false}\nsentiment: {enabled: false}\nml: {enabled: true, mode: v1_direction, buy_probability_threshold: 0.7}\n",
        encoding="utf-8",
    )
    fingerprint = tracking._config_fingerprint(load_signal_config(selected_config))
    state: dict[str, Any] = {"model": "model-v1", "rule": "ml-v1_direction-v1", "action": "BUY", "session": SESSION}

    def outcome_for_current() -> Any:
        candidate = tracking.TrackingCandidate(
            instrument=instrument,
            session=state["session"],
            source="ml",
            action=cast(Any, state["action"]),
            rule_version=state["rule"],
            model_version=state["model"],
            reasoning="synthetic candidate",
            probability_score=0.8,
        )
        return tracking._bind_rule_provenance(
            tracking.TrackingRuleOutcome(source="ml", status="signal", candidate=candidate),
            fingerprint=fingerprint,
            provenance="explicit",
        )

    monkeypatch.setattr(tracking, "_ml_rule", lambda *_args, **_kwargs: outcome_for_current())
    monkeypatch.setattr(tracking, "_latest_completed_session", lambda _instrument, _now: (SESSION, True))
    current_outcome = outcome_for_current()
    candidate = current_outcome.candidate
    assert candidate is not None
    event = tracking.TrackingEvent(
        event_id="b" * 64,
        instrument=instrument,
        session=SESSION,
        source="ml",
        action="BUY",
        rule_version=candidate.rule_version,
        config_fingerprint=fingerprint,
        config_provenance="explicit",
        model_version="model-v1",
        transition=1,
        reason="synthetic candidate",
        created_at=CLOCK,
    )

    assert tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, selected_config)
    assert not tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, None)
    assert not tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, changed_config)

    state["session"] = SESSION + timedelta(days=1)
    assert not tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, selected_config)
    state.update(session=SESSION, model="model-v2")
    assert not tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, selected_config)
    state.update(model="model-v1", rule="other-rule")
    assert not tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, selected_config)
    state.update(rule="ml-v1_direction-v1", action="SELL")
    assert not tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, selected_config)
    state.update(action="BUY")
    assert tracking._event_is_current(event, CLOCK, watchlist, source_config, None, tmp_path, selected_config)


def test_c10_all_market_cli_scan_retry_delivery_and_private_api_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    api_main = import_module("equity_lake.api.main")
    tracking_router = import_module("equity_lake.api.routers.tracking")
    unified_app = import_module("equity_lake.cli.__main__").app
    signal_config = import_module("equity_lake.signals.config")

    source_config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "private" / "watchlist.yaml"
    data_dir = tmp_path / "aux"
    lake_dir = tmp_path / "synthetic-lake"
    runner_cli = CliRunner()
    instruments = [
        _instrument("us_equity", "XNAS"),
        _instrument("cn_ashare", "XSHG"),
        _instrument("hk_sg_equity", "XHKG"),
        _instrument("hk_sg_equity", "XSES"),
        _instrument("jpx_equity", "XTKS"),
        _instrument("krx_equity", "XKRX"),
    ]

    for index, instrument in enumerate(instruments):
        args = [
            "tracking",
            "add",
            "--market",
            instrument.market,
            "--mic",
            instrument.mic,
            "--symbol",
            instrument.symbol,
            "--group",
            "synthetic",
            "--tag",
            "offline-test",
            "--watchlist",
            str(watchlist),
            "--ticker-config",
            str(source_config),
            "--data-dir",
            str(data_dir),
        ]
        if index == 0:
            args.extend(["--notes", "private note must not leave the local view"])
        if index == 2:
            args.append("--held")
        added = runner_cli.invoke(unified_app, args)
        assert added.exit_code == 0, added.output

    validated = tracking.load_private_watchlist(watchlist, source_config)
    assert {item.instrument.tuple for item in validated.instruments} == {item.tuple for item in instruments}
    assert {item.instrument.market for item in validated.instruments} == set(MARKETS)

    monkeypatch.setattr(tracking, "_latest_completed_session", lambda _instrument, _now: (SESSION, True))
    monkeypatch.setattr(
        signal_config,
        "load_signal_config",
        lambda _path=None: import_module("equity_lake.signals.models").SignalConfig(
            backtest={"enabled": False}, sentiment={"enabled": False}, ml={"enabled": False}
        ),
    )
    readiness = runner_cli.invoke(
        unified_app,
        [
            "tracking",
            "readiness",
            "--as-of",
            "2024-02-02T17:30:00-05:00",
            "--watchlist",
            str(watchlist),
            "--ticker-config",
            str(source_config),
            "--data-dir",
            str(data_dir),
        ],
    )
    assert readiness.exit_code == 0, readiness.output
    readiness_report = _json_output(readiness.stdout)
    assert len(readiness_report["readiness"]) == 6
    assert {item["status"] for item in readiness_report["readiness"]} == {"unavailable"}
    assert readiness_report["stale_run_warning"]

    def price_rule(instrument: InstrumentKey, _now: datetime, _lake_dir: Path) -> Any:
        action = "SELL" if instrument.mic == "XHKG" else "BUY"
        candidate = _candidate(instrument, action=action)
        return tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=candidate)

    monkeypatch.setattr(tracking, "_price_rule", price_rule)
    scan_args = [
        "tracking",
        "scan",
        "--as-of",
        "2024-02-02T17:30:00-05:00",
        "--watchlist",
        str(watchlist),
        "--ticker-config",
        str(source_config),
        "--lake-dir",
        str(lake_dir),
        "--data-dir",
        str(data_dir),
    ]
    scanned = runner_cli.invoke(unified_app, scan_args)
    assert scanned.exit_code == 0, scanned.output
    first_report = _json_output(scanned.stdout)
    assert first_report["status"] == "success"
    assert len(first_report["events"]) == 6
    assert {item["instrument"]["market"] for item in first_report["evaluations"]} == set(MARKETS)

    repeated = runner_cli.invoke(unified_app, scan_args)
    assert repeated.exit_code == 0, repeated.output
    assert _json_output(repeated.stdout)["events"] == []

    monkeypatch.setattr(tracking, "_send_http", lambda *_args, **_kwargs: pytest.fail("default delivery must remain disabled"))
    disabled_cli = runner_cli.invoke(unified_app, ["tracking", "deliver", "--data-dir", str(data_dir)])
    assert disabled_cli.exit_code == 0, disabled_cli.output
    disabled_summary = _json_output(disabled_cli.stdout)
    assert disabled_summary["status"] == "disabled" and disabled_summary["pending"] == 6
    schedule = runner_cli.invoke(unified_app, ["tracking", "schedule"])
    assert schedule.exit_code == 0
    assert _json_output(schedule.stdout)["scheduler_activated"] is False

    monkeypatch.setattr(tracking, "_event_is_current", lambda *_args, **_kwargs: True)
    send_attempts: dict[str, int] = {}
    delivered_payloads: list[dict[str, Any]] = []

    def mock_sender(payload: Any) -> None:
        count = send_attempts.get(payload.event_id, 0)
        send_attempts[payload.event_id] = count + 1
        if count == 0:
            raise OSError("synthetic offline failure with no retained exception text")
        delivered_payloads.append(payload.model_dump(mode="json"))

    summary = tracking.deliver_pending(
        enabled=True,
        sender=mock_sender,
        now=CLOCK,
        watchlist_file=watchlist,
        ticker_config_path=source_config,
        lake_dir=lake_dir,
        data_dir=data_dir,
    )
    assert (summary.attempted, summary.sent, summary.failed, summary.pending) == (12, 6, 0, 0)
    assert len(delivered_payloads) == 6
    expected_payload_fields = {"event_id", "market", "mic", "symbol", "session", "source", "action", "rule_version", "reason"}
    assert all(set(payload) == expected_payload_fields for payload in delivered_payloads)
    assert "private note must not leave the local view" not in json.dumps(delivered_payloads)
    assert set(send_attempts.values()) == {2}

    snapshot = tracking.private_tracking_snapshot(now=CLOCK, watchlist_file=watchlist, ticker_config_path=source_config, data_dir=data_dir)
    assert len(snapshot.recent_events) == 6
    assert all(item.status == "sent" and item.attempts == 2 for item in snapshot.recent_events)
    assert snapshot.watchlist.instruments[0].notes == "private note must not leave the local view"

    monkeypatch.setattr(tracking_router, "_snapshot", lambda: snapshot)
    client = TestClient(api_main.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 51234))
    response = client.get("/tracking/private")
    assert response.status_code == 200
    assert response.json()["watchlist"]["instruments"][0]["notes"] == "private note must not leave the local view"
    forwarded = client.get("/tracking/private", headers={"x-forwarded-for": "192.0.2.50"})
    assert forwarded.status_code == 403


def test_delivery_retries_are_bounded_and_persist_only_sanitized_failure_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    instrument = _instrument("us_equity", "XNAS")
    outcome = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=_candidate(instrument))
    tracking._persist_scan(_scan_report(instrument, outcome, suffix=88), 0, tmp_path)
    monkeypatch.setattr(tracking, "_event_is_current", lambda *_args, **_kwargs: True)
    calls = 0

    def fail(_payload: Any) -> None:
        nonlocal calls
        calls += 1
        raise OSError("sensitive webhook URL and response body")

    summary = tracking.deliver_pending(enabled=True, sender=fail, now=CLOCK, data_dir=tmp_path)
    snapshot = tracking.private_tracking_snapshot(now=CLOCK, data_dir=tmp_path)
    assert calls == tracking.MAX_DELIVERY_ATTEMPTS
    assert summary.attempted == tracking.MAX_DELIVERY_ATTEMPTS and summary.failed == 1
    assert snapshot.recent_events[0].status == "failed"
    assert snapshot.recent_events[0].attempts == tracking.MAX_DELIVERY_ATTEMPTS
    assert snapshot.recent_events[0].error_code == "retry_limit_or_send_failure"
    assert "sensitive webhook URL" not in snapshot.model_dump_json()


def test_public_signal_projection_whitelists_metadata_and_marks_legacy_identity_unknown() -> None:
    public_signals = import_module("equity_lake.api.routers.signals")
    canonical = public_signals._public_signal_projection(
        {
            "ticker": "SHARED",
            "date": "2024-02-01",
            "market": "us_equity",
            "mic": "XNAS",
            "identity_schema_version": 1,
            "metadata": {"strategy": "sma", "notes": "private", "held": True, "secret": "not-public"},
        }
    )
    assert canonical["identity_status"] == "canonical"
    assert canonical["metadata"] == {"strategy": "sma"}
    legacy = public_signals._public_signal_projection({"ticker": "SHARED", "metadata": {"notes": "private"}})
    assert legacy["identity_status"] == "unknown_legacy"
    assert legacy["market"] is None and legacy["mic"] is None
    assert legacy["metadata"] == {}


def test_dry_run_quiet_hours_and_stale_revalidation_are_non_delivering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unified_app = import_module("equity_lake.cli.__main__").app

    watchlist = tmp_path / "not-created.yaml"
    data_dir = tmp_path / "aux"
    dry_run = runner.invoke(
        unified_app,
        ["tracking", "scan", "--dry-run", "--watchlist", str(watchlist), "--data-dir", str(data_dir)],
    )
    assert dry_run.exit_code == 0
    expected_plan = {
        "status": "planned",
        "reason": "dry_run",
        "reads": False,
        "inference": False,
        "persistence": False,
        "delivery": False,
    }
    assert json.loads(dry_run.stdout) == expected_plan
    assert not watchlist.exists() and not data_dir.exists()

    instrument = _instrument("us_equity", "XNAS")
    outcome = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=_candidate(instrument))
    tracking._persist_scan(_scan_report(instrument, outcome, suffix=99), 0, data_dir)

    def sender(_payload: Any) -> None:
        pytest.fail("quiet or stale event must not be delivered")

    quiet = tracking.deliver_pending(
        enabled=True,
        sender=sender,
        now=datetime(2024, 2, 3, 3, 0, tzinfo=UTC),
        data_dir=data_dir,
    )
    assert quiet.status == "quiet_hours" and quiet.pending == 1

    monkeypatch.setattr(tracking, "_event_is_current", lambda *_args, **_kwargs: False)
    stale = tracking.deliver_pending(enabled=True, sender=sender, now=CLOCK, data_dir=data_dir)
    assert stale.expired == 1 and stale.sent == 0 and stale.pending == 0
    disabled = tracking.deliver_pending(enabled=False, sender=sender, now=CLOCK, data_dir=data_dir)
    assert disabled.status == "disabled"


def test_removal_during_scan_cannot_create_a_late_pending_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "watchlist.yaml"
    data_dir = tmp_path / "aux"
    instrument = _instrument("us_equity", "XNAS")
    tracking.add_instrument(market=instrument.market, mic=instrument.mic, symbol=instrument.symbol, path=watchlist, ticker_config_path=config)
    signal_config = import_module("equity_lake.signals.config")
    monkeypatch.setattr(
        signal_config,
        "load_signal_config",
        lambda _path=None: import_module("equity_lake.signals.models").SignalConfig(
            backtest={"enabled": False}, sentiment={"enabled": False}, ml={"enabled": False}
        ),
    )
    monkeypatch.setattr(tracking, "_latest_completed_session", lambda _instrument, _now: (SESSION, True))
    entered = Event()
    release = Event()

    def paused_price_rule(selected: InstrumentKey, _now: datetime, _lake_dir: Path) -> Any:
        assert selected == instrument
        entered.set()
        assert release.wait(5)
        return tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=_candidate(instrument))

    monkeypatch.setattr(tracking, "_price_rule", paused_price_rule)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan = pool.submit(
            tracking.scan_private_watchlist,
            now=CLOCK,
            watchlist_file=watchlist,
            ticker_config_path=config,
            data_dir=data_dir,
        )
        assert entered.wait(5)
        tracking.remove_instrument(instrument, path=watchlist, ticker_config_path=config, data_dir=data_dir)
        release.set()
        report = scan.result(timeout=5)

    snapshot = tracking.private_tracking_snapshot(now=CLOCK, watchlist_file=watchlist, ticker_config_path=config, data_dir=data_dir)
    assert report.events == []
    assert report.status == "partial"
    assert any("removed before event persistence" in issue for issue in report.issues)
    assert snapshot.watchlist.instruments == []
    assert snapshot.recent_events == []


def test_removal_waits_for_an_in_flight_delivery_reservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = tmp_path / "watchlist.yaml"
    data_dir = tmp_path / "aux"
    instrument = _instrument("us_equity", "XNAS")
    tracking.add_instrument(market=instrument.market, mic=instrument.mic, symbol=instrument.symbol, path=watchlist, ticker_config_path=config)
    outcome = tracking.TrackingRuleOutcome(source="price_sma20", status="signal", candidate=_candidate(instrument))
    tracking._persist_scan(_scan_report(instrument, outcome, suffix=101), 0, data_dir)
    monkeypatch.setattr(tracking, "_event_is_current", lambda *_args, **_kwargs: True)
    entered = Event()
    release = Event()
    removal_started = Event()

    def paused_sender(_payload: Any) -> None:
        entered.set()
        assert release.wait(5)

    def remove() -> Any:
        removal_started.set()
        return tracking.remove_instrument(instrument, path=watchlist, ticker_config_path=config, data_dir=data_dir)

    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(
            tracking.deliver_pending,
            enabled=True,
            sender=paused_sender,
            now=CLOCK,
            watchlist_file=watchlist,
            ticker_config_path=config,
            data_dir=data_dir,
        )
        assert entered.wait(5)
        removal = pool.submit(remove)
        assert removal_started.wait(5)
        assert not removal.done()
        release.set()
        summary = delivery.result(timeout=5)
        removal.result(timeout=5)

    snapshot = tracking.private_tracking_snapshot(now=CLOCK, watchlist_file=watchlist, ticker_config_path=config, data_dir=data_dir)
    assert summary.sent == 1 and summary.pending == 0
    assert snapshot.watchlist.instruments == []
    assert snapshot.recent_events[0].status == "sent"
