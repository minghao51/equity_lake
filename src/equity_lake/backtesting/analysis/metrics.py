import polars as pl
import structlog

logger = structlog.get_logger(__name__)


class PerformanceMetrics:
    def __init__(self, risk_free_rate: float = 0.04):
        self.risk_free_rate = risk_free_rate

    def compute(
        self,
        equity_curve: pl.Series,
        trades: pl.DataFrame | None = None,
        benchmark: pl.Series | None = None,
    ) -> dict[str, float]:
        if equity_curve.is_empty():
            logger.warning("Empty equity curve, returning empty metrics")
            return {}

        returns = equity_curve.pct_change().slice(1)
        metrics: dict[str, float] = {}
        metrics.update(self._calculate_return_metrics(equity_curve, returns))
        metrics.update(self._calculate_risk_metrics(equity_curve, returns))
        metrics.update(self._calculate_risk_adjusted_metrics(returns))
        if trades is not None and not trades.is_empty():
            metrics.update(self._calculate_trading_metrics(trades))
        if benchmark is not None and not benchmark.is_empty():
            metrics.update(self._calculate_benchmark_metrics(returns, benchmark))
        metrics.update(self._calculate_additional_metrics(equity_curve, returns))
        return metrics

    def _calculate_return_metrics(
        self,
        equity_curve: pl.Series,
        returns: pl.Series,
    ) -> dict[str, float]:
        if equity_curve.len() < 2:
            return {}

        initial_value = float(equity_curve.item(0))
        final_value = float(equity_curve.item(-1))
        total_return = (final_value / initial_value) - 1

        n = equity_curve.len()
        years = n / 252.0
        cagr = (final_value / initial_value) ** (1 / years) - 1 if years > 0 else 0

        daily_mean = float(returns.mean()) if returns.len() > 0 else 0.0

        return {
            "total_return": total_return,
            "cagr": float(cagr),
            "daily_return_mean": daily_mean,
            "final_value": final_value,
        }

    def _calculate_risk_metrics(
        self,
        equity_curve: pl.Series,
        returns: pl.Series,
    ) -> dict[str, float]:
        if returns.is_empty():
            return {}

        import numpy as np

        volatility = float(returns.std() * np.sqrt(252)) if returns.len() > 1 else 0.0

        neg_returns = returns.filter(returns < 0)
        downside_deviation = float(neg_returns.std() * np.sqrt(252)) if neg_returns.len() > 1 else 0.0

        cummax = equity_curve.cum_max()
        drawdown = (equity_curve - cummax) / cummax
        max_drawdown = float(drawdown.min()) if drawdown.len() > 0 else 0.0

        dd_neg = drawdown.filter(drawdown < 0)
        avg_drawdown = float(dd_neg.mean()) if dd_neg.len() > 0 else 0.0

        var_95 = float(returns.quantile(0.05)) if returns.len() > 0 else 0.0
        cvar_95_returns = returns.filter(returns <= var_95)
        cvar_95 = float(cvar_95_returns.mean()) if cvar_95_returns.len() > 0 else 0.0

        return {
            "volatility": volatility,
            "downside_deviation": downside_deviation,
            "max_drawdown": max_drawdown,
            "avg_drawdown": avg_drawdown,
            "var_95": var_95,
            "cvar_95": cvar_95,
        }

    def _calculate_risk_adjusted_metrics(
        self,
        returns: pl.Series,
    ) -> dict[str, float]:
        if returns.is_empty():
            return {}

        import numpy as np

        annual_return = float(returns.mean() * 252) if returns.len() > 0 else 0.0
        annual_vol = float(returns.std() * np.sqrt(252)) if returns.len() > 1 else 0.0

        sharpe_ratio = (annual_return - self.risk_free_rate) / annual_vol if annual_vol > 0 else 0.0

        neg_returns = returns.filter(returns < 0)
        downside_dev = float(neg_returns.std() * np.sqrt(252)) if neg_returns.len() > 1 else 0.0
        sortino_ratio = (annual_return - self.risk_free_rate) / downside_dev if downside_dev > 0 else 0.0

        cumsum = returns.cum_sum()
        cummax = cumsum.cum_max()
        max_dd = float((cumsum - cummax).min())
        calmar_ratio = annual_return / abs(max_dd) if max_dd != 0 else 0.0

        return {
            "sharpe_ratio": float(sharpe_ratio),
            "sortino_ratio": float(sortino_ratio),
            "calmar_ratio": float(calmar_ratio),
        }

    def _calculate_trading_metrics(self, trades: pl.DataFrame) -> dict[str, float]:
        if trades.is_empty():
            return {}

        metrics: dict[str, float] = {}
        metrics["num_trades"] = float(trades.height)

        if "pnl" in trades.columns:
            pnls = trades["pnl"]
            wins = pnls.filter(pnls > 0)
            losses = pnls.filter(pnls < 0)

            metrics["win_rate"] = wins.len() / pnls.len() if pnls.len() > 0 else 0.0
            metrics["avg_win"] = float(wins.mean()) if wins.len() > 0 else 0.0
            metrics["avg_loss"] = float(losses.mean()) if losses.len() > 0 else 0.0

            total_profit = float(wins.sum()) if wins.len() > 0 else 0.0
            total_loss = float(abs(losses.sum())) if losses.len() > 0 else 1.0
            metrics["profit_factor"] = total_profit / total_loss if total_loss > 0 else 0.0
            metrics["expectancy"] = float(pnls.mean())

        return metrics

    def _calculate_benchmark_metrics(
        self,
        returns: pl.Series,
        benchmark: pl.Series,
    ) -> dict[str, float]:
        import numpy as np

        benchmark_returns = benchmark.pct_change().slice(1)
        min_len = min(returns.len(), benchmark_returns.len())
        if min_len < 2:
            return {}

        ret = returns.slice(0, min_len).to_numpy()
        bench = benchmark_returns.slice(0, min_len).to_numpy()

        covariance = np.cov(ret, bench)[0, 1]
        bench_var = float(np.var(bench))
        beta = covariance / bench_var if bench_var > 0 else 0.0

        strategy_return = float(np.mean(ret) * 252)
        benchmark_return = float(np.mean(bench) * 252)
        alpha = strategy_return - (self.risk_free_rate + beta * (benchmark_return - self.risk_free_rate))

        excess = ret - bench
        tracking_error = float(np.std(excess) * np.sqrt(252))
        information_ratio = float(np.mean(excess) * 252 / tracking_error) if tracking_error > 0 else 0.0

        correlation = float(np.corrcoef(ret, bench)[0, 1])

        return {
            "alpha": float(alpha),
            "beta": float(beta),
            "information_ratio": information_ratio,
            "tracking_error": tracking_error,
            "correlation": correlation,
        }

    def _calculate_additional_metrics(
        self,
        equity_curve: pl.Series,
        returns: pl.Series,
    ) -> dict[str, float]:
        if equity_curve.is_empty() or returns.is_empty():
            return {}

        metrics = {
            "best_day": float(returns.max()),
            "worst_day": float(returns.min()),
            "avg_daily_range": float(returns.std() * 2) if returns.len() > 1 else 0.0,
        }

        initial_value = float(equity_curve.item(0))
        final_value = float(equity_curve.item(-1))
        total_return = (final_value / initial_value) - 1

        cummax = equity_curve.cum_max()
        drawdown = (equity_curve - cummax) / cummax
        max_dd = abs(float(drawdown.min()))
        metrics["recovery_factor"] = total_return / max_dd if max_dd > 0 else 0.0
        metrics["num_trading_days"] = float(equity_curve.len())

        return metrics


def compute_quick_metrics(
    equity_curve: pl.Series,
    risk_free_rate: float = 0.04,
) -> dict[str, float]:
    calculator = PerformanceMetrics(risk_free_rate=risk_free_rate)
    return calculator.compute(equity_curve=equity_curve)


__all__ = [
    "PerformanceMetrics",
    "compute_quick_metrics",
]
