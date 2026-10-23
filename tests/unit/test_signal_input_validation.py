"""Strict offline validation for signal YAML inputs and US watchlist identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from equity_lake.signals.config import load_signal_config, load_watchlist  # type: ignore[import-untyped]


def _ticker_config(path: Path, *, duplicate_market: bool = False, inactive_us: bool = False) -> Path:
    cn_ticker = "AAPL" if duplicate_market else "600000"
    path.write_text(
        """markets:
  us:
    currency: USD
    tickers:
      - {symbol: AAPL, name: Apple, exchange: NASDAQ, sector: Technology, active: """
        + ("false" if inactive_us else "true")
        + """}
      - {symbol: MSFT, name: Microsoft, exchange: NASDAQ, sector: Technology, active: true}
  cn:
    currency: CNY
    tickers:
      - {symbol: """
        + f'"{cn_ticker}"'
        + """, name: Synthetic CN, exchange: SSE, sector: Finance, active: true}
""",
        encoding="utf-8",
    )
    return path


def _watchlist(path: Path, content: str = "name: Test\ntickers: [AAPL]\n") -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _signal_config(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_watchlist_accepts_active_unambiguous_us_symbols(tmp_path: Path) -> None:
    ticker_config = _ticker_config(tmp_path / "tickers.yaml")
    watchlist = load_watchlist(_watchlist(tmp_path / "watchlist.yaml"), ticker_config)
    assert watchlist.tickers == ["AAPL"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- not-a-mapping\n", "root must be a mapping"),
        ("name: Test\ntickers: AAPL\n", "list"),
        ("name: Test\ntickers: [AAPL]\nmarket: us\n", "Unknown watchlist field"),
        ("name: Test\ntickers: [AAPL]\ngroups: {tech: AAPL}\n", "list"),
        ("name: Test\ntickers: [AAPL, AAPL]\n", "unique"),
    ],
)
def test_watchlist_rejects_malformed_roots_and_nested_values(tmp_path: Path, content: str, message: str) -> None:
    ticker_config = _ticker_config(tmp_path / "tickers.yaml")
    with pytest.raises((TypeError, ValueError), match=message):
        load_watchlist(_watchlist(tmp_path / "watchlist.yaml", content), ticker_config)


def test_watchlist_rejects_unsupported_inactive_and_ambiguous_identity(tmp_path: Path) -> None:
    watchlist_path = tmp_path / "watchlist.yaml"
    unsupported_config = _ticker_config(tmp_path / "unsupported.yaml")
    with pytest.raises(ValueError, match="unsupported market"):
        load_watchlist(_watchlist(watchlist_path, 'name: Test\ntickers: ["600000"]\n'), unsupported_config)

    inactive_config = _ticker_config(tmp_path / "inactive.yaml", inactive_us=True)
    with pytest.raises(ValueError, match="not an active US ticker"):
        load_watchlist(_watchlist(watchlist_path), inactive_config)

    ambiguous_config = _ticker_config(tmp_path / "ambiguous.yaml", duplicate_market=True)
    with pytest.raises(ValueError, match="ambiguous across markets"):
        load_watchlist(_watchlist(watchlist_path), ambiguous_config)


@pytest.mark.parametrize(
    "content",
    [
        "- not-a-mapping\n",
        "backtest: {enabled: true}\nsentiment: {}\nml: {}\nunknown: true\n",
        "backtest: {enabled: true, unknown: 1}\nsentiment: {}\nml: {}\n",
        "backtest: []\nsentiment: {}\nml: {}\n",
        "backtest: {}\nsentiment: {min_articles: 0}\nml: {}\n",
        "backtest: {}\nsentiment: {}\nml: {buy_probability_threshold: 1.2}\n",
        "backtest: {}\nsentiment: {}\nml: {horizon_days: 0}\n",
    ],
)
def test_signal_config_rejects_invalid_roots_types_unknowns_and_ranges(tmp_path: Path, content: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        load_signal_config(_signal_config(tmp_path / "signals.yaml", content))


def test_signal_config_rejects_explicit_null_and_unknown_nested_ml_fields(tmp_path: Path) -> None:
    path = tmp_path / "signals.yaml"
    with pytest.raises(ValueError, match="null"):
        load_signal_config(_signal_config(path, "backtest: {}\nsentiment: {}\nml: {enabled: null}\n"))
    with pytest.raises(ValueError, match="Unknown MLConfig field|Extra inputs are not permitted"):
        load_signal_config(_signal_config(path, "backtest: {}\nsentiment: {}\nml: {mdoe: v1_direction}\n"))


@pytest.mark.parametrize(
    "content",
    [
        "backtest: {}\nsentiment: {buy_threshold: -0.8}\nml: {}\n",
        "backtest: {}\nsentiment: {sell_threshold: 0.8}\nml: {}\n",
        "backtest: {}\nsentiment: {}\nml: {buy_probability_threshold: 0.3}\n",
        "backtest: {}\nsentiment: {}\nml: {sell_probability_threshold: 0.8}\n",
        "backtest: {}\nsentiment: {}\nml: {min_confidence: 20, buy_probability_threshold: 0.5}\n",
    ],
)
def test_signal_config_rejects_threshold_ordering_against_effective_defaults(tmp_path: Path, content: str) -> None:
    with pytest.raises(ValueError, match="threshold"):
        load_signal_config(_signal_config(tmp_path / "signals.yaml", content))


@pytest.mark.parametrize(
    "content",
    [
        "backtest: {}\nsentiment: {buy_threshold: 0.2}\nml: {}\n",
        "backtest: {}\nsentiment: {sell_threshold: -0.8}\nml: {}\n",
        "backtest: {}\nsentiment: {}\nml: {buy_probability_threshold: 0.8}\n",
        "backtest: {}\nsentiment: {}\nml: {sell_probability_threshold: 0.2}\n",
        "backtest: {}\nsentiment: {}\nml: {min_confidence: 80, buy_probability_threshold: 0.5}\n",
    ],
)
def test_signal_config_accepts_single_threshold_when_ordered_with_effective_default(tmp_path: Path, content: str) -> None:
    config = load_signal_config(_signal_config(tmp_path / "signals.yaml", content))

    assert config.backtest == {}
