"""Options flow loader using yfinance."""

from __future__ import annotations

from datetime import date

import pandas as pd
import yfinance as yf

from equity_lake.loaders.base import BaseDataLoader, LoaderMetadata, LoadResult


class OptionsFlowLoader(BaseDataLoader):
    """Detect unusual options activity from Yahoo Finance options chains."""

    metadata = LoaderMetadata(
        name="options_flow",
        description="Options flow and unusual activity detector via yfinance.",
        supported_markets=["US"],
        data_types=["options"],
    )

    def _validate_config(self) -> None:
        self.volume_threshold = float(self.config.get("volume_threshold", 2.0))
        self.min_open_interest = int(self.config.get("min_open_interest", 100))

    def load(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> LoadResult:
        records: list[dict[str, object]] = []
        errors: list[str] = []

        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                for expiry in ticker.options[:3]:
                    chain = ticker.option_chain(expiry)
                    records.extend(self._extract_unusual_activity(symbol, expiry, chain.calls, "call"))
                    records.extend(self._extract_unusual_activity(symbol, expiry, chain.puts, "put"))
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")

        frame = pd.DataFrame.from_records(records)
        return LoadResult(
            success=not errors,
            data=frame,
            records_count=len(frame),
            errors=errors,
        )

    def _extract_unusual_activity(
        self,
        symbol: str,
        expiry: str,
        frame: pd.DataFrame,
        option_type: str,
    ) -> list[dict[str, object]]:
        if frame.empty:
            return []

        filtered = frame[(frame["openInterest"] >= self.min_open_interest) & (frame["volume"] >= frame["openInterest"] * self.volume_threshold)]
        return [
            {
                "ticker": symbol,
                "date": expiry,
                "option_type": option_type,
                "strike": row["strike"],
                "last_price": row["lastPrice"],
                "volume": row["volume"],
                "open_interest": row["openInterest"],
                "implied_volatility": row["impliedVolatility"],
                "volume_open_interest_ratio": (row["volume"] / row["openInterest"] if row["openInterest"] else 0),
            }
            for _, row in filtered.iterrows()
        ]

    def get_available_symbols(self) -> list[str]:
        return ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"]

    def validate_connection(self) -> bool:
        try:
            ticker = yf.Ticker("AAPL")
            return bool(ticker.options)
        except Exception:
            return False


__all__ = ["OptionsFlowLoader"]
