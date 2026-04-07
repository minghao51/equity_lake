"""Built-in yfinance data loader."""

from __future__ import annotations

from datetime import date

import pandas as pd
import yfinance as yf

from equity_lake.loaders.base import (
    BaseDataLoader,
    LoaderMetadata,
    LoadResult,
)


class YFinanceLoader(BaseDataLoader):
    """Load OHLCV data from Yahoo Finance."""

    metadata = LoaderMetadata(
        name="yfinance",
        description="Yahoo Finance loader for US, HK, and SG symbols.",
        supported_markets=["US", "HK", "SG"],
    )

    def _validate_config(self) -> None:
        self.timeout = int(self.config.get("timeout", 30))

    def load(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> LoadResult:
        try:
            raw = yf.download(
                tickers=" ".join(symbols),
                start=start_date,
                end=end_date,
                interval=interval,
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=self.timeout,
            )
        except Exception as exc:
            return LoadResult(success=False, errors=[str(exc)])

        if raw.empty:
            return LoadResult(success=False, errors=["No data returned from yfinance"])

        data = self._normalize(raw, symbols)
        return LoadResult(
            success=not data.empty,
            data=data,
            records_count=len(data),
            errors=[] if not data.empty else ["No normalized records produced"],
            metadata={"symbols": symbols, "interval": interval},
        )

    def get_available_symbols(self) -> list[str]:
        return []

    def validate_connection(self) -> bool:
        try:
            result = yf.download(
                tickers="AAPL",
                period="5d",
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=self.timeout,
            )
        except Exception:
            return False
        return not result.empty

    def _normalize(self, data: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
        records: list[dict[str, object]] = []
        if isinstance(data.columns, pd.MultiIndex):
            for symbol in symbols:
                try:
                    symbol_frame = data[symbol]
                except KeyError:
                    continue
                for idx, row in symbol_frame.iterrows():
                    records.append(self._build_record(symbol, idx, row))
        else:
            symbol = symbols[0]
            for idx, row in data.iterrows():
                records.append(self._build_record(symbol, idx, row))

        frame = pd.DataFrame.from_records(records)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"])
        return frame.dropna(subset=["close"])

    def _build_record(self, symbol: str, idx: object, row: pd.Series) -> dict[str, object]:
        row_dict = row.to_dict()
        return {
            "ticker": symbol,
            "date": idx.date() if hasattr(idx, "date") else idx,
            "open": row_dict.get("Open", row_dict.get("open")),
            "high": row_dict.get("High", row_dict.get("high")),
            "low": row_dict.get("Low", row_dict.get("low")),
            "close": row_dict.get("Close", row_dict.get("close")),
            "volume": row_dict.get("Volume", row_dict.get("volume")),
        }


__all__ = ["YFinanceLoader"]
