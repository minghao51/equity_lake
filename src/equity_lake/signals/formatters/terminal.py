"""Terminal table signal formatter."""

from collections import defaultdict

try:
    from tabulate import tabulate

    TABULATE_AVAILABLE = True
except ImportError:
    TABULATE_AVAILABLE = False

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.models import Signal


class TerminalFormatter(SignalFormatter):
    """Format signals as colored terminal tables."""

    def format(self, signals: list[Signal]) -> str:
        """Format signals as terminal tables.

        Args:
            signals: List of Signal objects

        Returns:
            Formatted string for terminal
        """
        if not signals:
            return "No signals generated.\n"

        lines = []
        lines.append("=" * 80)
        lines.append(f"SIGNAL REPORT - {signals[0].date}")
        lines.append(f"Total Signals: {len(signals)}")
        lines.append("=" * 80)
        lines.append("")

        # Group by action for summary
        by_action = defaultdict(list)
        for signal in signals:
            by_action[signal.action].append(signal)

        # Summary
        lines.append("SUMMARY:")
        for action in ["BUY", "SELL", "HOLD"]:
            count = len(by_action.get(action, []))
            lines.append(f"  {action}: {count}")
        lines.append("")

        # Group by signal type
        for signal_type in ["backtest", "sentiment", "ml"]:
            type_signals = [s for s in signals if s.signal_type == signal_type]
            if not type_signals:
                continue

            lines.append(f"\n{signal_type.upper()} SIGNALS:")
            lines.append("-" * 80)

            if TABULATE_AVAILABLE:
                table_data = []
                for signal in type_signals:
                    table_data.append(
                        [
                            signal.ticker,
                            signal.action,
                            f"{signal.confidence:.0f}",
                            (
                                signal.reasoning[:50] + "..."
                                if len(signal.reasoning) > 50
                                else signal.reasoning
                            ),
                        ]
                    )
                lines.append(
                    tabulate(
                        table_data, headers=["Ticker", "Action", "Conf", "Reasoning"]
                    )
                )
            else:
                # Fallback: simple table
                for signal in type_signals:
                    lines.append(
                        f"  {signal.ticker:10s} | {signal.action:6s} | {signal.confidence:5.0f} | {signal.reasoning}"
                    )

            lines.append("")

        return "\n".join(lines)
