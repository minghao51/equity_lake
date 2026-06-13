import polars as pl
import structlog

from equity_lake.backtesting.engine import VectorBacktestEngine
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class WalkForwardValidator:
    def __init__(
        self,
        train_size: int = 252,
        test_size: int = 63,
        step_size: int = 21,
    ):
        self.train_size = train_size
        self.test_size = test_size
        self.step_size = step_size

    def validate(
        self,
        strategy: BaseStrategy,
        tickers: list[str],
        data: pl.DataFrame,
        initial_cash: float = 100_000.0,
    ) -> "WalkForwardResult":
        logger.info(
            "Starting walk-forward validation",
            train_size=self.train_size,
            test_size=self.test_size,
            step_size=self.step_size,
        )

        folds = self._generate_folds(data)

        logger.info(f"Generated {len(folds)} validation folds")

        results = []
        for i, (train_data, test_data) in enumerate(folds):
            logger.info(f"Running fold {i + 1}/{len(folds)}")

            combined_data = pl.concat([train_data, test_data]).sort(["ticker", "date"])

            test_dates = test_data["date"]
            engine = VectorBacktestEngine(
                strategy=strategy,
                tickers=tickers,
                start_date=test_dates.min(),
                end_date=test_dates.max(),
                initial_cash=initial_cash,
                preloaded_data=combined_data,
            )

            try:
                result = engine.run()
                results.append(result)
            except Exception as e:
                logger.error(f"Fold {i + 1} failed", error=str(e))

        wf_result = WalkForwardResult(
            folds=results,
            train_size=self.train_size,
            test_size=self.test_size,
            step_size=self.step_size,
        )

        logger.info(
            "Walk-forward validation completed",
            mean_sharpe=wf_result.mean_sharpe,
            std_sharpe=wf_result.std_sharpe,
        )

        return wf_result

    def _generate_folds(
        self,
        data: pl.DataFrame,
    ) -> list[tuple[pl.DataFrame, pl.DataFrame]]:
        folds = []
        unique_dates = data["date"].unique().sort().to_list()

        start_idx = 0
        while True:
            train_end = start_idx + self.train_size
            test_end = train_end + self.test_size

            if test_end > len(unique_dates):
                break

            train_dates = set(unique_dates[start_idx:train_end])
            test_dates = set(unique_dates[train_end:test_end])

            train_data = data.filter(pl.col("date").is_in(train_dates))
            test_data = data.filter(pl.col("date").is_in(test_dates))

            folds.append((train_data, test_data))

            start_idx += self.step_size

        return folds


class WalkForwardResult:
    def __init__(
        self,
        folds: list[BacktestResult],
        train_size: int,
        test_size: int,
        step_size: int,
    ):
        self.folds = folds
        self.train_size = train_size
        self.test_size = test_size
        self.step_size = step_size

    @property
    def mean_sharpe(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.metrics.get("sharpe_ratio", 0) for f in self.folds) / len(self.folds)

    @property
    def std_sharpe(self) -> float:
        if len(self.folds) < 2:
            return 0.0
        sharpe_values = [f.metrics.get("sharpe_ratio", 0) for f in self.folds]
        import statistics

        return statistics.stdev(sharpe_values)

    @property
    def stability_score(self) -> float:
        if not self.folds:
            return 0.0

        positive_sharpe = sum(1 for f in self.folds if f.metrics.get("sharpe_ratio", 0) > 0)

        return positive_sharpe / len(self.folds)


__all__ = [
    "WalkForwardValidator",
    "WalkForwardResult",
]
