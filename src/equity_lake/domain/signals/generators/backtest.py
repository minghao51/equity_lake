"""Backtest strategy signal generator."""

from contextlib import suppress
from datetime import date, timedelta

import duckdb
import pandas as pd

from equity_lake.core.paths import US_EQUITY_DIR
from equity_lake.domain.signals.generators.base import SignalGenerator
from equity_lake.domain.signals.models import Signal


class BacktestSignalGenerator(SignalGenerator):
    """Generate signals based on backtest strategy entry/exit conditions.

    Reuses existing backtesting strategies to determine when a strategy
    would enter (BUY) or exit (SELL) a position.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.min_win_rate = config.get("min_win_rate", 0.55)
        self.strategies = config.get("strategies", [])

        # Connect to DuckDB for historical data
        self.con = duckdb.connect(":memory:")
        self._setup_views()

    def _setup_views(self) -> None:
        """Create DuckDB views for querying price data."""
        us_pattern = f"{US_EQUITY_DIR}/date=*/*.parquet"
        sql = f"""
        CREATE OR REPLACE VIEW price_data AS
        SELECT * FROM read_parquet('{us_pattern}', hive_partitioning=1)
        """
        with suppress(Exception):
            self.con.execute(sql)

    def generate(self, ticker: str, target_date: date) -> Signal | None:
        """Generate signal based on backtest strategy conditions.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action (BUY/SELL/HOLD) and confidence
        """
        if not self.is_enabled():
            return None

        if not self.strategies:
            return None

        # Fetch historical data for strategy calculation
        lookback_days = max([s.get("lookback_days", 20) for s in self.strategies])
        start_date = target_date - timedelta(days=lookback_days + 50)

        # Query price data
        query = f"""
        SELECT date, close, volume
        FROM price_data
        WHERE ticker = '{ticker}'
          AND date >= '{start_date}'
          AND date <= '{target_date}'
        ORDER BY date
        """

        try:
            df = self.con.execute(query).df()
        except Exception:
            # No data available
            return None

        if df.empty or len(df) < lookback_days:
            return None

        # Check each strategy for signals
        signals = []
        for strategy in self.strategies:
            signal = self._check_strategy(ticker, df, strategy, target_date)
            if signal:
                signals.append(signal)

        # Return strongest signal (highest confidence)
        if not signals:
            return None

        # Sort by confidence, return highest
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals[0]

    def _check_strategy(
        self,
        ticker: str,
        df: pd.DataFrame,
        strategy: dict,
        target_date: date,
    ) -> Signal | None:
        """Check if a single strategy triggers a signal.

        For MVP: Simple momentum strategy
        - BUY: price > SMA by buy_threshold
        - SELL: price < SMA by sell_threshold
        """
        name = strategy.get("name", "unknown")
        lookback = strategy.get("lookback_days", 20)
        buy_thresh = strategy.get("buy_threshold", 0.02)
        sell_thresh = strategy.get("sell_threshold", -0.01)

        # Calculate SMA
        df = df.copy()
        df["sma"] = df["close"].rolling(window=lookback).mean()

        # Get latest row (target date)
        latest = df[df["date"] == target_date]
        if latest.empty:
            return None

        price = latest["close"].iloc[0]
        sma = latest["sma"].iloc[0]

        # Calculate % difference from SMA
        pct_diff = (price - sma) / sma

        # Generate signal
        if pct_diff >= buy_thresh:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="backtest",
                action="BUY",
                confidence=70.0,
                reasoning=f"{name} strategy: price {pct_diff:.1%} above {lookback}-day SMA",
                metadata={
                    "strategy": name,
                    "lookback_days": lookback,
                    "pct_from_sma": pct_diff,
                    "price": price,
                    "sma": sma,
                },
            )
        elif pct_diff <= sell_thresh:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="backtest",
                action="SELL",
                confidence=60.0,
                reasoning=f"{name} strategy: price {pct_diff:.1%} below {lookback}-day SMA",
                metadata={
                    "strategy": name,
                    "lookback_days": lookback,
                    "pct_from_sma": pct_diff,
                    "price": price,
                    "sma": sma,
                },
            )

        return None
