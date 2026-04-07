"""Tests for the loader registry."""

from equity_lake.loaders import registry


def test_builtin_yfinance_loader_is_registered() -> None:
    metadata = {loader.name for loader in registry.list()}
    assert "yfinance" in metadata
    assert "reddit_sentiment" in metadata
    assert "sec_filings" in metadata
    assert "options_flow" in metadata
