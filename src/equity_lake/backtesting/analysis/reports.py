"""
Report generation for backtesting results.

This module provides HTML and JSON report generation for backtest results.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import structlog

from equity_lake.backtesting.analysis.attribution import AttributionAnalyzer
from equity_lake.backtesting.analysis.metrics import PerformanceMetrics

logger = structlog.get_logger(__name__)


class ReportGenerator:
    """
    Generate backtest reports in various formats.

    Supports HTML and JSON output formats.

    Example:
        >>> generator = ReportGenerator()
        >>> generator.generate_html(
        ...     result=backtest_result,
        ...     output_path="report.html"
        ... )
    """

    def generate_html(
        self,
        result: Any,
        output_path: Path,
        include_charts: bool = True,
    ) -> None:
        """
        Generate HTML report.

        Args:
            result: BacktestResult object
            output_path: Output file path
            include_charts: Include charts (requires plotly)
        """
        html_content = self._build_html_report(result, include_charts)

        with open(output_path, 'w') as f:
            f.write(html_content)

        logger.info("HTML report generated", path=str(output_path))

    def generate_json(
        self,
        result: Any,
        output_path: Path,
    ) -> None:
        """
        Generate JSON report.

        Args:
            result: BacktestResult object
            output_path: Output file path
        """
        # Convert result to dictionary
        data = result.to_dict() if hasattr(result, 'to_dict') else vars(result)

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

        logger.info("JSON report generated", path=str(output_path))

    def _build_html_report(
        self,
        result: Any,
        include_charts: bool,
    ) -> str:
        """Build HTML report content."""
        html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>Backtest Report: {strategy_name}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; border-bottom: 1px solid #ccc; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .metric {{ display: inline-block; margin: 10px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
        .metric-label {{ font-weight: bold; color: #666; }}
        .metric-value {{ font-size: 24px; color: #333; }}
        .positive {{ color: green; }}
        .negative {{ color: red; }}
    </style>
</head>
<body>
    <h1>Backtest Report: {strategy_name}</h1>

    <h2>Summary</h2>
    <p><strong>Period:</strong> {start_date} to {end_date}</p>
    <p><strong>Initial Capital:</strong> ${initial_cash:,.2f}</p>
    <p><strong>Final Capital:</strong> ${final_cash:,.2f}</p>

    <h2>Performance Metrics</h2>
    <div class="metrics">
        {metrics_html}
    </div>

    <h2>Trades</h2>
    <p><strong>Total Trades:</strong> {num_trades}</p>

    {charts_html}

    <h2>Configuration</h2>
    <pre>{config_json}</pre>
</body>
</html>
        """

        # Extract result data
        strategy_name = getattr(result, 'strategy_name', 'Unknown Strategy')
        start_date = str(getattr(result, 'start_date', ''))
        end_date = str(getattr(result, 'end_date', ''))
        initial_cash = getattr(result, 'initial_cash', 0)
        final_cash = getattr(result, 'final_cash', 0)
        metrics = getattr(result, 'metrics', {})
        trades = getattr(result, 'trades', [])

        # Build metrics HTML
        metrics_html = self._build_metrics_html(metrics)

        # Build charts HTML (placeholder for now)
        charts_html = ""
        if include_charts:
            charts_html = "<!-- Charts would be rendered here -->"

        # Config JSON
        config_json = json.dumps(metrics, indent=2, default=str)

        # Fill template
        html = html_template.format(
            strategy_name=strategy_name,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            final_cash=final_cash,
            metrics_html=metrics_html,
            num_trades=len(trades),
            charts_html=charts_html,
            config_json=config_json,
        )

        return html

    def _build_metrics_html(self, metrics: Dict[str, float]) -> str:
        """Build HTML for metrics display."""
        key_metrics = [
            ('Total Return', 'total_return', '{:.2%}'),
            ('CAGR', 'cagr', '{:.2%}'),
            ('Sharpe Ratio', 'sharpe_ratio', '{:.2f}'),
            ('Max Drawdown', 'max_drawdown', '{:.2%}'),
            ('Volatility', 'volatility', '{:.2%}'),
            ('Win Rate', 'win_rate', '{:.1%}'),
        ]

        html_parts = []
        for label, key, format_str in key_metrics:
            value = metrics.get(key, 0)
            formatted = format_str.format(value)

            # Add color coding
            css_class = "positive" if value > 0 else "negative"

            html_parts.append(f"""
            <div class="metric">
                <div class="metric-label">{label}</div>
                <div class="metric-value {css_class}">{formatted}</div>
            </div>
            """)

        return "\n".join(html_parts)


__all__ = ["ReportGenerator"]
