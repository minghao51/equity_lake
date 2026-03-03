"""Markdown signal formatter."""

from collections import defaultdict

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.models import Signal


class MarkdownFormatter(SignalFormatter):
    """Format signals as readable Markdown report."""

    def format(self, signals: list[Signal]) -> str:
        """Format signals as Markdown report.

        Args:
            signals: List of Signal objects

        Returns:
            Markdown string
        """
        if not signals:
            return "# Signal Report\n\nNo signals generated.\n"

        lines = []
        lines.append("# Signal Report\n")
        lines.append(f"**Generated:** {signals[0].date}  \n")
        lines.append(f"**Total Signals:** {len(signals)}\n\n")

        # Group by action
        by_action = defaultdict(list)
        for signal in signals:
            by_action[signal.action].append(signal)

        # Summary table
        lines.append("## Summary by Action\n\n")
        lines.append("| Action | Count |")
        lines.append("|--------|-------|")
        for action in ["BUY", "SELL", "HOLD"]:
            count = len(by_action.get(action, []))
            lines.append(f"| {action} | {count} |")
        lines.append("\n")

        # Detailed sections by signal type
        for signal_type in ["backtest", "sentiment", "ml"]:
            type_signals = [s for s in signals if s.signal_type == signal_type]
            if not type_signals:
                continue

            lines.append(f"## {signal_type.title()} Signals\n\n")

            # Table header
            lines.append("| Ticker | Action | Confidence | Reasoning |")
            lines.append("|--------|--------|------------|-----------|")

            for signal in type_signals:
                lines.append(
                    f"| {signal.ticker} | {signal.action} | "
                    f"{signal.confidence:.0f} | {signal.reasoning} |"
                )
            lines.append("\n")

        return "".join(lines)
