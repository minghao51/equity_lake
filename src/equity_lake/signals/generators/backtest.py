"""Market-aware backtest-strategy signal generator."""

from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import polars as pl
import structlog

from equity_lake.core.instruments import InstrumentKey, normalize_identity_frame
from equity_lake.core.paths import LAKE_DIR
from equity_lake.signals.generators.base import GenerationOutcome, SignalGenerator
from equity_lake.signals.models import Signal
from equity_lake.storage.delta import delta_table_path, read_delta
from equity_lake.storage.lake_reader import with_price_adjustment

logger = structlog.get_logger(__name__)


class BacktestSignalGenerator(SignalGenerator):
    """Evaluate configured SMA strategies on strictly identified adjusted prices.

    A legacy ticker-only table or missing/stale corporate-action coverage is
    unavailable. This generator never falls back to raw US prices.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.strategies = config.get("strategies", [])
        self.lake_dir = Path(config.get("lake_dir", LAKE_DIR))

    def _load_prices(self, instrument: InstrumentKey, target_date: date) -> pl.DataFrame:
        table = f"01_bronze/market_data/{instrument.market}"
        path = delta_table_path(table, self.lake_dir)
        if not path.exists():
            raise FileNotFoundError(f"price table is missing for {instrument.market}")
        if (path / "_delta_log").exists():
            frame = read_delta(table, lake_dir=self.lake_dir)
        else:
            files = sorted(path.rglob("*.parquet"))
            if not files:
                raise FileNotFoundError(f"price table has no Parquet data for {instrument.market}")
            frame = pl.read_parquet([str(item) for item in files], hive_partitioning=True)
        try:
            frame = normalize_identity_frame(frame)
        except ValueError as exc:
            raise ValueError(f"price rows have unknown canonical identity: {exc}") from exc
        prices = frame.filter(
            (pl.col("market") == instrument.market)
            & (pl.col("mic") == instrument.mic)
            & (pl.col("symbol") == instrument.symbol)
            & (pl.col("date").cast(pl.Date, strict=False) <= target_date)
        )
        if prices.is_empty():
            return prices
        from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs

        actions, coverage = load_corporate_action_inputs(instrument.market, lake_dir=self.lake_dir)
        return with_price_adjustment(prices, actions, coverage=coverage, method="split_only", as_of=target_date).sort("date")

    def generate(self, instrument: InstrumentKey, target_date: date) -> Signal | None:
        """Return the strongest configured strategy candidate, if any."""
        return self.generate_outcome(instrument, target_date).signal

    def generate_outcome(self, instrument: InstrumentKey, target_date: date) -> GenerationOutcome:
        """Evaluate strategies without losing missing/invalid source coverage."""
        if not self.strategies:
            return GenerationOutcome(status="unavailable", detail="no backtest strategies configured")
        try:
            prices = self._load_prices(instrument, target_date)
        except Exception as exc:
            logger.warning(
                "backtest_signal_prices_unavailable",
                instrument=instrument.display_key,
                error_type=type(exc).__name__,
            )
            return GenerationOutcome(status="unavailable", detail=f"strict adjusted price coverage unavailable ({type(exc).__name__})")
        if prices.is_empty() or prices.filter(pl.col("date").cast(pl.Date) == target_date).is_empty():
            return GenerationOutcome(status="unavailable", detail="no canonical adjusted price bar for target date")
        if "close" not in prices.columns:
            return GenerationOutcome(status="unavailable", detail="price schema has no close column")
        prices = (
            prices.with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64, strict=False))
            .filter(pl.col("close").is_not_null() & pl.col("close").is_finite() & (pl.col("close") > 0))
            .sort("date")
        )
        lookback_days = max(int(strategy.get("lookback_days", 20)) for strategy in self.strategies)
        start_date = target_date - timedelta(days=lookback_days + 50)
        prices = prices.filter(pl.col("date") >= start_date)
        if prices.height < lookback_days:
            return GenerationOutcome(status="unavailable", detail="insufficient valid adjusted price history")

        signals = [signal for strategy in self.strategies if (signal := self._check_strategy(instrument, prices, strategy, target_date)) is not None]
        if not signals:
            return GenerationOutcome(status="no_condition")
        signals.sort(key=lambda signal: signal.confidence, reverse=True)
        return GenerationOutcome(status="signal", signal=signals[0])

    def _check_strategy(
        self,
        instrument: InstrumentKey,
        frame: pl.DataFrame,
        strategy: dict,
        target_date: date,
    ) -> Signal | None:
        """Check a single configured strategy on a canonical price frame."""
        name = strategy.get("name", "unknown")
        lookback = int(strategy.get("lookback_days", 20))
        buy_thresh = float(strategy.get("buy_threshold", 0.02))
        sell_thresh = float(strategy.get("sell_threshold", -0.01))
        latest = (
            frame.with_columns(pl.col("close").rolling_mean(window_size=lookback, min_samples=lookback).alias("sma"))
            .filter(pl.col("date") == target_date)
            .tail(1)
        )
        if latest.is_empty() or latest["sma"].null_count():
            return None
        price = float(latest["close"][0])
        sma = float(latest["sma"][0])
        if not sma or not (price > 0):
            return None
        pct_diff = (price - sma) / sma
        action: Literal["BUY", "SELL"] | None = "BUY" if pct_diff >= buy_thresh else "SELL" if pct_diff <= sell_thresh else None
        if action is None:
            return None
        return Signal(
            ticker=instrument.symbol,
            date=target_date,
            signal_type="backtest",
            action=action,
            confidence=70.0 if action == "BUY" else 60.0,
            reasoning=f"{name} strategy: split-adjusted price {pct_diff:+.1%} from {lookback}-session SMA",
            metadata={"strategy": name, "lookback_days": lookback, "pct_from_sma": pct_diff, "price": price, "sma": sma},
            market=instrument.market,
            mic=instrument.mic,
        )
