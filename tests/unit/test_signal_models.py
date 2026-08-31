"""Test signal data models."""

from datetime import date

import pytest

from equity_lake.signals.models import Signal, SignalConfig, Watchlist


def test_signal_creation_valid():
    """Test creating a valid signal."""
    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 1),
        signal_type="backtest",
        action="BUY",
        confidence=75.0,
        reasoning="Momentum strategy entered long",
        metadata={"strategy": "momentum", "win_rate": 0.65},
    )
    assert signal.ticker == "AAPL"
    assert signal.action == "BUY"
    assert signal.confidence == 75.0


def test_signal_confidence_validation():
    """Test that confidence out of range raises error."""
    with pytest.raises(ValueError, match="Confidence must be 0-100"):
        Signal(
            ticker="AAPL",
            date=date(2024, 12, 1),
            signal_type="backtest",
            action="BUY",
            confidence=150.0,  # Invalid
            reasoning="Test",
            metadata={},
        )


def test_watchlist_simple_list():
    """Test watchlist with simple ticker list."""
    watchlist = Watchlist(name="My Portfolio", tickers=["AAPL", "GOOGL", "MSFT"])
    assert len(watchlist.tickers) == 3
    assert "AAPL" in watchlist.tickers


def test_watchlist_with_groups():
    """Test watchlist with grouped tickers."""
    watchlist = Watchlist(
        name="Tech Portfolio",
        tickers=["AAPL", "TSLA"],
        groups={"mega_tech": ["GOOGL", "MSFT"], "ev": ["RIVN"]},
    )
    # Groups should be merged into main tickers list
    assert len(watchlist.tickers) == 5
    assert "GOOGL" in watchlist.tickers
    assert "RIVN" in watchlist.tickers


def test_signal_config_generator_enabled():
    """Test checking if generator is enabled."""
    config = SignalConfig(
        backtest={"enabled": True},
        sentiment={"enabled": False, "buy_threshold": 0.5},
        ml={"enabled": True, "model_dir": "models"},
    )
    assert config.is_generator_enabled("backtest")
    assert not config.is_generator_enabled("sentiment")
    assert config.is_generator_enabled("ml")


def test_watchlist_validate_against_tickers_returns_unknown_entries():
    """Test watchlist validation returns unconfigured tickers without raising."""
    watchlist = Watchlist(name="My Portfolio", tickers=["AAPL", "MISSING"])

    unknown = watchlist.validate_against_tickers({"AAPL", "MSFT"})

    assert unknown == ["MISSING"]


# ---------------------------------------------------------------------------
# SignalRecord — closed write-boundary model (handoff 08, B4)
# ---------------------------------------------------------------------------


def _ml_signal(**metadata) -> Signal:
    return Signal(
        ticker="AAPL",
        date=date(2026, 8, 30),
        signal_type="ml",
        action="BUY",
        confidence=75.0,
        reasoning="ML predicts next-day upside",
        metadata=metadata,
    )


def test_signal_record_round_trips_whitelisted_metadata():
    """Whitelisted keys survive a Signal -> SignalRecord -> Signal round-trip."""
    from equity_lake.signals.models import SignalRecord

    signal = _ml_signal(prediction=1, probability=0.75, horizon_days=5, model_mode="v1_direction", model_version="m1")
    record = SignalRecord.from_signal(signal)
    assert record.prediction == 1
    assert record.probability == 0.75
    assert record.horizon_days == 5

    restored = record.to_signal()
    assert restored.metadata["prediction"] == 1
    assert restored.metadata["probability"] == 0.75
    assert restored.metadata["model_mode"] == "v1_direction"
    assert restored.ticker == "AAPL"
    assert restored.confidence == 75.0


def test_signal_record_rejects_base_column_collision():
    """Metadata key 'confidence' collides with the base column -> explicit error."""
    from equity_lake.signals.models import SignalRecord

    signal = _ml_signal(confidence=75.0)  # the exact ml.py collision from the handoff
    with pytest.raises(ValueError, match="collides"):
        SignalRecord.from_signal(signal)


def test_signal_record_drops_unknown_metadata_keys():
    """Keys outside the whitelist are dropped (schema stays stable), not stored."""
    from equity_lake.signals.models import SignalRecord

    signal = _ml_signal(brand_new_key="value", probability=0.6)
    record = SignalRecord.from_signal(signal)
    dumped = record.model_dump()
    assert "brand_new_key" not in dumped
    assert record.probability == 0.6


def test_signal_record_serializes_barrier_settings_dict_as_json():
    """Nested dicts become JSON strings — no Delta struct columns."""
    import json

    from equity_lake.signals.models import SignalRecord

    settings = {"vertical_barrier_days": 5, "pt_mult": 1.5, "sl_mult": 1.0}
    signal = Signal(
        ticker="AAPL",
        date=date(2026, 8, 30),
        signal_type="ml",
        action="BUY",
        confidence=80.0,
        reasoning="meta-label accepted",
        metadata={"candidate_action": "BUY", "barrier_settings": settings},
    )
    record = SignalRecord.from_signal(signal)
    assert isinstance(record.barrier_settings_json, str)
    assert json.loads(record.barrier_settings_json) == settings
    assert record.to_signal().metadata["barrier_settings"] == settings


def test_signal_record_is_closed():
    """extra='forbid': unknown fields cannot sneak into the persisted schema."""
    from pydantic import ValidationError

    from equity_lake.signals.models import SignalRecord

    with pytest.raises(ValidationError, match="extra"):
        SignalRecord.model_validate(
            {
                "ticker": "AAPL",
                "date": date(2026, 8, 30),
                "signal_type": "ml",
                "action": "BUY",
                "confidence": 75.0,
                "reasoning": "r",
                "surprise_column": 1,
            }
        )


def test_signal_record_validates_confidence_range():
    from pydantic import ValidationError

    from equity_lake.signals.models import SignalRecord

    with pytest.raises(ValidationError):
        SignalRecord.model_validate(
            {
                "ticker": "AAPL",
                "date": date(2026, 8, 30),
                "signal_type": "ml",
                "action": "BUY",
                "confidence": 150.0,
                "reasoning": "r",
            }
        )
