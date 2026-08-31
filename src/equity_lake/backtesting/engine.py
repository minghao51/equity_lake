from __future__ import annotations

from datetime import date
from typing import Any, cast

import polars as pl
import structlog

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.metrics import DEFAULT_RISK_FREE_RATE, equity_curve_metrics
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy.base import BaseStrategy

try:
    import polars_backtest  # noqa: F401

    POLARS_BACKTEST_AVAILABLE = True
except ImportError:
    POLARS_BACKTEST_AVAILABLE = False

logger = structlog.get_logger(__name__)

DEFAULT_FEE_RATIO = 0.001425
DEFAULT_TAX_RATIO = 0.003


def _float_scalar(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class VectorBacktestEngine:
    """
    Vectorized backtesting engine using polars-backtest.

    Uses long-format Polars DataFrames with target weight signals.
    Strategy.generate_weights() returns (date, ticker, weight) frames.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        tickers: list[str],
        start_date: date,
        end_date: date,
        initial_cash: float = 100_000.0,
        markets: list[str] | None = None,
        config: dict[str, Any] | None = None,
        preloaded_data: pl.DataFrame | None = None,
    ):
        self.strategy = strategy
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.markets = markets or ["us", "cn", "hk_sg"]
        self.config = config or {}
        self.preloaded_data = preloaded_data

        self.fee_ratio = self.config.get("fee_ratio", DEFAULT_FEE_RATIO)
        self.tax_ratio = self.config.get("tax_ratio", DEFAULT_TAX_RATIO)
        self.resample = self.config.get("resample", "D")
        self.stop_loss = self.config.get("stop_loss", 1.0)
        self.take_profit = self.config.get("take_profit", float("inf"))

        # Created lazily on first _load_data() call without preloaded_data — the
        # loader opens a DuckDB connection, and arena runs must not pay for it.
        self.data_loader: BacktestDataLoader | None = None
        self._warnings: list[str] = []

        self._report: Any = None
        self.metrics: dict[str, Any] = {}

        logger.info(
            "VectorBacktestEngine initialized",
            strategy=strategy.name,
            tickers=len(tickers),
            start_date=str(start_date),
            end_date=str(end_date),
            initial_cash=initial_cash,
        )

    def run(self) -> BacktestResult:
        if not POLARS_BACKTEST_AVAILABLE:
            raise ImportError("polars-backtest is required. Install with: uv sync --group backtesting")

        logger.info("Starting vectorized backtest", strategy=self.strategy.name)

        data = self._load_data()
        if data.is_empty():
            raise ValueError("No data available for backtest")

        self.strategy.initialize(data)
        weights_df = self.strategy.generate_weights(data)

        data_with_weights = data.join(weights_df, on=["date", "ticker"], how="left").with_columns(pl.col("weight").fill_null(0.0))

        bt_kwargs: dict[str, Any] = {
            "trade_at_price": "close",
            "position": pl.col("weight").cast(pl.Float64),
            "symbol": "ticker",
            "fee_ratio": self.fee_ratio,
            "tax_ratio": self.tax_ratio,
        }
        if self.resample:
            bt_kwargs["resample"] = self.resample
        if self.stop_loss < 1.0:
            bt_kwargs["stop_loss"] = self.stop_loss
        if self.take_profit < float("inf"):
            bt_kwargs["take_profit"] = self.take_profit

        report = cast(Any, data_with_weights).bt.backtest_with_report(**bt_kwargs)
        self._report = report

        try:
            stats = report.get_stats(riskfree_rate=DEFAULT_RISK_FREE_RATE)
        except Exception as exc:
            logger.warning("backtest_stats_unavailable", error=str(exc))
            self._warnings.append(f"stats unavailable: {exc}")
            stats = pl.DataFrame()

        trades = self._extract_trades(report)
        equity_curve = self._extract_equity_curve(report)
        self._compute_metrics(stats, report, equity_curve)

        self.strategy.finalize()

        result = BacktestResult(
            strategy_name=self.strategy.name,
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_cash=self.initial_cash,
            final_cash=_float_scalar(equity_curve.last(), self.initial_cash) if equity_curve.len() > 0 else self.initial_cash,
            equity_curve=equity_curve,
            trades=trades,
            metrics=self.metrics,
            warnings=self._warnings,
        )

        logger.info(
            "Vectorized backtest completed",
            strategy=self.strategy.name,
            total_return=result.total_return,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            num_trades=len(trades),
        )

        return result

    def _load_data(self) -> pl.DataFrame:
        if self.preloaded_data is not None:
            return self.preloaded_data.clone()
        if self.data_loader is None:
            self.data_loader = BacktestDataLoader()
        return self.data_loader.load(
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            markets=self.markets,
        )

    def _compute_metrics(self, stats: pl.DataFrame, report: Any, equity_curve: pl.Series) -> None:
        if stats.is_empty():
            self.metrics = {}
        else:
            row = stats.row(0, named=True)
            self.metrics = {
                "total_return": _float_scalar(row.get("total_return", 0.0)),
                "cagr": _float_scalar(row.get("cagr", 0.0)),
                "max_drawdown": _float_scalar(row.get("max_drawdown", 0.0)),
                "volatility": _float_scalar(row.get("daily_vol", 0.0)),
                "sharpe_ratio": _float_scalar(row.get("daily_sharpe", 0.0)),
                "sortino_ratio": _float_scalar(row.get("daily_sortino", 0.0)),
            }

        # Headline metrics go through the ONE shared equity-curve helper
        # (backtesting.metrics) so engine-side and report-side numbers share a
        # single explicit rf/annualization convention.
        if equity_curve.len() >= 2:
            shared = equity_curve_metrics(equity_curve, self.initial_cash)
            self.metrics.update(shared)

        trades_df = report.trades
        if trades_df is not None and not trades_df.is_empty():
            pnls = trades_df["pnl"].to_list() if "pnl" in trades_df.columns else []
            if not pnls and "return" in trades_df.columns:
                pnls = trades_df["return"].to_list()
            # Positions still open at the end of the window carry a null return.
            pnls = [p for p in pnls if p is not None]
            if pnls:
                wins = sum(1 for p in pnls if p > 0)
                self.metrics["win_rate"] = wins / len(pnls) if pnls else 0.0
                self.metrics["num_trades"] = float(len(pnls))

    def _extract_trades(self, report: Any) -> list[dict[str, Any]]:
        trades_df = report.trades
        if trades_df is None or trades_df.is_empty():
            return []

        trades: list[dict[str, Any]] = []
        for row in trades_df.iter_rows(named=True):
            stock_id = row.get("stock_id", row.get("symbol", ""))
            entry_price = row.get("entry_price") or 0
            exit_price = row.get("exit_price") or 0
            position = row.get("position") or 0
            ret = row.get("return") or 0
            try:
                pnl = _float_scalar(ret) * _float_scalar(entry_price) * _float_scalar(position) if entry_price and position else 0.0
            except (TypeError, ValueError):
                pnl = 0.0

            trades.append(
                {
                    "ticker": str(stock_id),
                    "entry_date": row.get("entry_date"),
                    "exit_date": row.get("exit_date"),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "shares": position,
                    "pnl": pnl,
                    "action": "SELL",
                }
            )

        return trades

    def _extract_equity_curve(self, report: Any) -> pl.Series:
        creturn = report.creturn
        if creturn is None:
            return pl.Series("equity", [], dtype=pl.Float64)

        if isinstance(creturn, pl.DataFrame):
            value_col = None
            for candidate in ["creturn", "equity", "portfolio_value", "value"]:
                if candidate in creturn.columns:
                    value_col = candidate
                    break
            if value_col is None and creturn.width >= 2:
                value_col = creturn.columns[-1]

            if value_col:
                return (creturn[value_col] * self.initial_cash).rename("equity")

        if isinstance(creturn, pl.Series):
            return (creturn * self.initial_cash).rename("equity")

        return pl.Series("equity", [], dtype=pl.Float64)


__all__ = ["VectorBacktestEngine"]
