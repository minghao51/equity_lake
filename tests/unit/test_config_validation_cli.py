"""CLI validation scope, missing-file, and nonfatal group-warning behavior."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from equity_lake.cli._app import config_app  # type: ignore[import-untyped]
from equity_lake.cli.commands import admin as admin_commands  # type: ignore[import-untyped]  # noqa: F401


def _write_ticker_config(path: Path) -> Path:
    path.write_text(
        """version: '1.0'
markets:
  us:
    currency: USD
    tickers:
      - symbol: AAPL
        name: Apple Inc.
        exchange: NASDAQ
        sector: Technology
        tags: [technology]
groups:
  list_watch:
    description: list references
    markets: [us]
    tickers: [MISSING_LIST]
  market_watch:
    description: market-scoped references
    markets: [us]
    tickers:
      us: [MISSING_US]
""",
        encoding="utf-8",
    )
    return path


def test_validation_help_labels_default_ticker_only_scope() -> None:
    result = CliRunner().invoke(config_app, ["validate", "--help"])

    assert result.exit_code == 0
    help_text = " ".join(result.output.replace("│", " ").split())
    assert "default scope is tickers only" in help_text
    assert "not selected by default" in help_text


def test_default_validation_is_ticker_only_and_surfaces_group_warnings(tmp_path: Path) -> None:
    runner = CliRunner()
    ticker_path = _write_ticker_config(tmp_path / "tickers.yaml")

    result = runner.invoke(config_app, ["validate", "--tickers", str(ticker_path)])

    assert result.exit_code == 0
    assert "All validations passed with warnings." in result.output
    assert "Group 'list_watch' references unknown ticker: MISSING_LIST" in result.output
    assert "Group 'market_watch' references unknown ticker for market 'us': MISSING_US" in result.output
    assert "Validating config/watchlist.yaml" not in result.output
    assert "Validating config/signals.yaml" not in result.output


def test_explicitly_selected_missing_watchlist_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    ticker_path = _write_ticker_config(tmp_path / "tickers.yaml")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        config_app,
        ["validate", "--tickers", str(ticker_path), "--watchlist", "config/watchlist.yaml"],
    )

    assert result.exit_code == 1
    assert "Validating config/watchlist.yaml" in result.output
    assert "File not found: config/watchlist.yaml" in result.output
    assert "Skipping" not in result.output
