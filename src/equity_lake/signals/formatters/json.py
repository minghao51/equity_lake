"""JSON signal formatter."""

import json

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.models import Signal


class JSONFormatter(SignalFormatter):
    """Format signals as machine-readable JSON."""

    def format(self, signals: list[Signal]) -> str:
        """Format signals as JSON array.

        Args:
            signals: List of Signal objects

        Returns:
            JSON string
        """
        signal_dicts = []
        for signal in signals:
            signal_dict = {
                "ticker": signal.ticker,
                "date": signal.date.isoformat(),
                "signal_type": signal.signal_type,
                "action": signal.action,
                "confidence": signal.confidence,
                "reasoning": signal.reasoning,
                "metadata": signal.metadata,
            }
            signal_dicts.append(signal_dict)

        return json.dumps(signal_dicts, indent=2)
