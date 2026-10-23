"""Offline guards for optional CN providers and Yahoo's nullable return."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from equity_lake.sources.cn_hybrid import CNHybridFetcher  # type: ignore[import-untyped]


def _fallback_fetcher() -> CNHybridFetcher:
    fetcher = CNHybridFetcher.__new__(CNHybridFetcher)
    fetcher._explicit_tickers = ["600000", "000001"]
    return fetcher


def test_yahoo_none_result_returns_empty_without_accessing_pandas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.sources.cn_hybrid.yf.download", lambda *args, **kwargs: None)
    assert _fallback_fetcher()._fetch_yfinance_fallback(date(2024, 12, 2)).is_empty()


def test_yahoo_series_groups_are_not_appended_as_ohlcv_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = pd.DataFrame({"600000.SS": [1.0], "000001.SZ": [2.0]})
    monkeypatch.setattr("equity_lake.sources.cn_hybrid.yf.download", lambda *args, **kwargs: malformed)
    assert _fallback_fetcher()._fetch_yfinance_fallback(date(2024, 12, 2)).is_empty()


def test_missing_optional_efinance_never_calls_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.sources.cn_hybrid.CNEfinanceFetcher", None)
    primary = MagicMock()
    monkeypatch.setattr("equity_lake.sources.cn_hybrid.CNAshareFetcher", lambda **kwargs: primary)
    config = MagicMock()
    config.get_tickers_for_market.return_value = ["600000"]
    fetcher = CNHybridFetcher(enable_efinance=True, enable_akshare=True, ticker_config=config)
    assert fetcher.enable_efinance is False
    assert fetcher.efinance_fetcher is None
    assert fetcher.akshare_fetcher is primary
