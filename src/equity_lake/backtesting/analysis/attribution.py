from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


def _float_scalar(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class AttributionAnalyzer:
    def analyze(
        self,
        equity_curve: pl.Series,
        trades: pl.DataFrame | None = None,
        benchmark: pl.Series | None = None,
    ) -> dict[str, pl.DataFrame]:
        results = {}
        results["monthly"] = self._monthly_attribution(equity_curve)
        results["yearly"] = self._yearly_attribution(equity_curve)

        if trades is not None and not trades.is_empty():
            results["trades"] = self._trade_attribution(trades)

        if benchmark is not None:
            results["benchmark_comparison"] = self._benchmark_comparison(equity_curve, benchmark)

        return results

    def _monthly_attribution(self, equity_curve: pl.Series) -> pl.DataFrame:
        if equity_curve.is_empty():
            return pl.DataFrame()

        monthly_returns = equity_curve.pct_change().slice(1)
        n = monthly_returns.len()
        return pl.DataFrame(
            {
                "idx": list(range(n)),
                "value": equity_curve.slice(1).to_list(),
                "return": monthly_returns.to_list(),
            }
        )

    def _yearly_attribution(self, equity_curve: pl.Series) -> pl.DataFrame:
        if equity_curve.is_empty():
            return pl.DataFrame()

        yearly_returns = equity_curve.pct_change().slice(1)
        n = yearly_returns.len()
        return pl.DataFrame(
            {
                "idx": list(range(n)),
                "value": equity_curve.slice(1).to_list(),
                "return": yearly_returns.to_list(),
            }
        )

    def _trade_attribution(self, trades: pl.DataFrame) -> pl.DataFrame:
        if trades.is_empty() or "pnl" not in trades.columns:
            return pl.DataFrame()

        pnls = trades["pnl"]
        winners = pnls.filter(pnls > 0)
        losers = pnls.filter(pnls <= 0)

        return pl.DataFrame(
            {
                "category": ["Winners", "Losers"],
                "count": [winners.len(), losers.len()],
                "total_pnl": [_float_scalar(winners.sum()) if winners.len() > 0 else 0.0, _float_scalar(losers.sum()) if losers.len() > 0 else 0.0],
                "avg_pnl": [_float_scalar(winners.mean()) if winners.len() > 0 else 0.0, _float_scalar(losers.mean()) if losers.len() > 0 else 0.0],
            }
        )

    def _benchmark_comparison(
        self,
        equity_curve: pl.Series,
        benchmark: pl.Series,
    ) -> pl.DataFrame:
        min_len = min(equity_curve.len(), benchmark.len())
        if min_len < 2:
            return pl.DataFrame()

        ec = equity_curve.slice(0, min_len)
        bm = benchmark.slice(0, min_len)

        strategy_returns = ec.pct_change().slice(1)
        benchmark_returns = bm.pct_change().slice(1)

        return pl.DataFrame(
            {
                "strategy_value": ec.slice(1).to_list(),
                "benchmark_value": bm.slice(1).to_list(),
                "strategy_return": strategy_returns.to_list(),
                "benchmark_return": benchmark_returns.to_list(),
                "excess_return": (strategy_returns - benchmark_returns).to_list(),
            }
        )


__all__ = ["AttributionAnalyzer"]
