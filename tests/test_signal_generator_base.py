"""Test base SignalGenerator class."""

import pytest
from datetime import date

from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal


class DummySignalGenerator(SignalGenerator):
    """Concrete implementation for testing."""

    def generate(self, ticker: str, date: date) -> Signal | None:
        return Signal(
            ticker=ticker,
            date=date,
            signal_type="test",
            action="HOLD",
            confidence=50.0,
            reasoning="Test signal",
            metadata={},
        )


def test_generator_enabled():
    """Test generator respects enabled flag."""
    config = {"enabled": True}
    gen = DummySignalGenerator(config)
    assert gen.is_enabled() is True


def test_generator_disabled():
    """Test generator when disabled."""
    config = {"enabled": False}
    gen = DummySignalGenerator(config)
    assert gen.is_enabled() is False


def test_abstract_class_cannot_instantiate():
    """Test that base class cannot be instantiated directly."""
    from equity_lake.signals.generators.base import SignalGenerator

    config = {"enabled": True}
    with pytest.raises(TypeError):
        SignalGenerator(config)
