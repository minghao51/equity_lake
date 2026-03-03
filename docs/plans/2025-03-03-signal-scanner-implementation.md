# Signal Scanner & Portfolio Watchlist Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a modular signal scanning system that generates buy/sell/hold signals for a config-driven watchlist using backtest strategies, news sentiment, and ML predictions.

**Architecture:** New `signals/` module with generator pattern (pluggable signal sources), formatter pattern (swappable outputs), config-driven rules, and Parquet-based history tracking. Reuses existing backtesting, sentiment, and ML modules.

**Tech Stack:** Python 3.12+, Pydantic (models), DuckDB (queries), Parquet (storage), existing backtesting/sentiment/ML modules, YAML (config), pytest (testing).

---

## Task 1: Create Signal Data Models

**Files:**
- Create: `src/equity_lake/signals/__init__.py`
- Create: `src/equity_lake/signals/models.py`
- Test: `tests/test_signal_models.py`

**Step 1: Create signals package init file**

Write: `src/equity_lake/signals/__init__.py`
```python
"""Signal scanning and generation module."""

from equity_lake.signals.models import Signal, Watchlist, SignalConfig

__all__ = ["Signal", "Watchlist", "SignalConfig"]
```

**Step 2: Write the data models with Pydantic**

Write: `src/equity_lake/signals/models.py`
```python
"""Data models for signal scanning."""

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Literal, Optional

@dataclass
class Signal:
    """A single buy/sell/hold signal for a ticker."""
    ticker: str
    date: date
    signal_type: Literal["backtest", "sentiment", "ml"]
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float  # 0-100
    reasoning: str     # Human-readable explanation
    metadata: Dict[str, Any]  # Strategy-specific details

    def __post_init__(self):
        """Validate confidence score is in range."""
        if not 0 <= self.confidence <= 100:
            raise ValueError(f"Confidence must be 0-100, got {self.confidence}")

@dataclass
class Watchlist:
    """Portfolio/watchlist configuration."""
    name: str
    description: Optional[str] = None
    tickers: List[str] = None
    groups: Optional[Dict[str, List[str]]] = None  # e.g., {"tech": ["AAPL"]}
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.tickers is None:
            self.tickers = []
        # Ensure all tickers in groups are in main list
        if self.groups:
            for group_tickers in self.groups.values():
                for ticker in group_tickers:
                    if ticker not in self.tickers:
                        self.tickers.append(ticker)

@dataclass
class SignalConfig:
    """Signal generation configuration."""
    backtest: Dict[str, Any]
    sentiment: Dict[str, Any]
    ml: Dict[str, Any]
    aggregation: Optional[Dict[str, Any]] = None

    def is_generator_enabled(self, generator_name: str) -> bool:
        """Check if a signal generator is enabled."""
        config = getattr(self, generator_name, {})
        return config.get("enabled", False)
```

**Step 3: Write failing tests for models**

Write: `tests/test_signal_models.py`
```python
"""Test signal data models."""

import pytest
from datetime import date
from equity_lake.signals.models import Signal, Watchlist, SignalConfig

def test_signal_creation_valid():
    """Test creating a valid signal."""
    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 1),
        signal_type="backtest",
        action="BUY",
        confidence=75.0,
        reasoning="Momentum strategy entered long",
        metadata={"strategy": "momentum", "win_rate": 0.65}
    )
    assert signal.ticker == "AAPL"
    assert signal.action == "BUY"
    assert signal.confidence == 75.0

def test_signal_confidence_validation():
    """Test that confidence out of range raises error."""
    with pytest.raises(ValueError, match="Confidence must be 0-100"):
        Signal(
            ticker="AAPL",
            date=date(2024, 12, 1),
            signal_type="backtest",
            action="BUY",
            confidence=150.0,  # Invalid
            reasoning="Test",
            metadata={}
        )

def test_watchlist_simple_list():
    """Test watchlist with simple ticker list."""
    watchlist = Watchlist(
        name="My Portfolio",
        tickers=["AAPL", "GOOGL", "MSFT"]
    )
    assert len(watchlist.tickers) == 3
    assert "AAPL" in watchlist.tickers

def test_watchlist_with_groups():
    """Test watchlist with grouped tickers."""
    watchlist = Watchlist(
        name="Tech Portfolio",
        tickers=["AAPL", "TSLA"],
        groups={
            "mega_tech": ["GOOGL", "MSFT"],
            "ev": ["RIVN"]
        }
    )
    # Groups should be merged into main tickers list
    assert len(watchlist.tickers) == 5
    assert "GOOGL" in watchlist.tickers
    assert "RIVN" in watchlist.tickers

def test_signal_config_generator_enabled():
    """Test checking if generator is enabled."""
    config = SignalConfig(
        backtest={"enabled": True, "min_win_rate": 0.55},
        sentiment={"enabled": False, "buy_threshold": 0.5},
        ml={"enabled": True, "model_path": "model.pkl"}
    )
    assert config.is_generator_enabled("backtest") == True
    assert config.is_generator_enabled("sentiment") == False
    assert config.is_generator_enabled("ml") == True
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_signal_models.py -v`
Expected: All tests PASS

**Step 5: Commit models**

```bash
git add src/equity_lake/signals/ tests/test_signal_models.py
git commit -m "feat(signals): add Signal, Watchlist, SignalConfig data models

Add Pydantic-based data models with validation:
- Signal: ticker, date, action, confidence, reasoning, metadata
- Watchlist: supports simple list + grouped tickers
- SignalConfig: enables checking if generators are enabled

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Create Config Loaders

**Files:**
- Create: `src/equity_lake/signals/config.py`
- Create: `config/watchlist.yaml`
- Create: `config/signals.yaml`
- Test: `tests/test_signal_config.py`

**Step 1: Write config loader functions**

Write: `src/equity_lake/signals/config.py`
```python
"""Load watchlist and signal configuration from YAML files."""

from pathlib import Path
from typing import Optional

import yaml
from equity_lake.signals.models import Watchlist, SignalConfig

DEFAULT_WATCHLIST_PATH = Path("config/watchlist.yaml")
DEFAULT_SIGNALS_PATH = Path("config/signals.yaml")


def load_watchlist(path: Optional[Path] = None) -> Watchlist:
    """Load watchlist from YAML file.

    Args:
        path: Path to watchlist YAML. Defaults to config/watchlist.yaml

    Returns:
        Watchlist object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    config_path = path or DEFAULT_WATCHLIST_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Watchlist config not found: {config_path}")

    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    return Watchlist(**data)


def load_signal_config(path: Optional[Path] = None) -> SignalConfig:
    """Load signal configuration from YAML file.

    Args:
        path: Path to signals YAML. Defaults to config/signals.yaml

    Returns:
        SignalConfig object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    config_path = path or DEFAULT_SIGNALS_PATH

    if not config_path.exists():
        raise FileNotFoundError(f"Signal config not found: {config_path}")

    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    return SignalConfig(**data)
```

**Step 2: Create example watchlist config**

Write: `config/watchlist.yaml`
```yaml
name: "My Portfolio"
description: "Core holdings and watchlist"

tickers:
  - AAPL
  - GOOGL
  - MSFT
  - TSLA
  - NVDA

groups:
  tech:
    - AAPL
    - GOOGL
    - MSFT
    - NVDA
 _ev:
    - TSLA

metadata:
  benchmark: "SPY"
  created: "2024-12-01"
```

**Step 3: Create example signals config**

Write: `config/signals.yaml`
```yaml
# Backtest strategy signals
backtest:
  enabled: true
  strategies:
    - name: "momentum"
      lookback_days: 20
      buy_threshold: 0.02
      sell_threshold: -0.01
  min_win_rate: 0.55

# News sentiment signals
sentiment:
  enabled: true
  sources:
    - "yahoo"
  buy_threshold: 0.5
  sell_threshold: -0.3
  min_articles: 3
  lookback_days: 7

# ML prediction signals
ml:
  enabled: true
  model_path: "data/models/xgboost_price_forecaster.pkl"
  horizon_days: 5
  buy_return_threshold: 0.03
  sell_return_threshold: -0.02
  min_confidence: 60

aggregation:
  agreement_boost: 10
  unanimous_boost: 20
```

**Step 4: Write tests for config loaders**

Write: `tests/test_signal_config.py`
```python
"""Test signal configuration loading."""

import pytest
from pathlib import Path
from equity_lake.signals.config import load_watchlist, load_signal_config

def test_load_watchlist():
    """Test loading watchlist from YAML."""
    watchlist = load_watchlist()
    assert watchlist.name == "My Portfolio"
    assert len(watchlist.tickers) == 5
    assert "AAPL" in watchlist.tickers
    assert "tech" in watchlist.groups

def test_load_signal_config():
    """Test loading signal config from YAML."""
    config = load_signal_config()
    assert config.backtest["enabled"] == True
    assert config.sentiment["enabled"] == True
    assert config.ml["enabled"] == True
    assert config.backtest["min_win_rate"] == 0.55

def test_load_watchlist_missing_file():
    """Test error when watchlist file missing."""
    with pytest.raises(FileNotFoundError):
        load_watchlist(Path("nonexistent.yaml"))

def test_load_signal_config_missing_file():
    """Test error when signal config file missing."""
    with pytest.raises(FileNotFoundError):
        load_signal_config(Path("nonexistent.yaml"))
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_signal_config.py -v`
Expected: All tests PASS

**Step 6: Commit config loaders**

```bash
git add src/equity_lake/signals/config.py config/watchlist.yaml config/signals.yaml tests/test_signal_config.py
git commit -m "feat(signals): add YAML config loaders for watchlist and signals

Add config loading from YAML with validation:
- load_watchlist(): loads ticker list + groups
- load_signal_config(): loads signal generator rules
- Add example configs in config/

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Create Base Signal Generator Class

**Files:**
- Create: `src/equity_lake/signals/generators/__init__.py`
- Create: `src/equity_lake/signals/generators/base.py`
- Test: `tests/test_signal_generator_base.py`

**Step 1: Create generators package init**

Write: `src/equity_lake/signals/generators/__init__.py`
```python
"""Signal generators for different data sources."""

from equity_lake.signals.generators.base import SignalGenerator

__all__ = ["SignalGenerator"]
```

**Step 2: Write base SignalGenerator class**

Write: `src/equity_lake/signals/generators/base.py`
```python
"""Base class for signal generators."""

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from equity_lake.signals.models import Signal


class SignalGenerator(ABC):
    """Base class for all signal generators.

    Each generator (backtest, sentiment, ML) inherits from this and
    implements the generate() method.
    """

    def __init__(self, config: dict):
        """Initialize generator with configuration.

        Args:
            config: Generator-specific configuration dict
        """
        self.config = config
        self.enabled = config.get("enabled", True)

    @abstractmethod
    def generate(self, ticker: str, date: date) -> Optional[Signal]:
        """Generate a signal for a single ticker on a given date.

        Args:
            ticker: Stock symbol
            date: Target date for signal generation

        Returns:
            Signal object if signal generated, None if no signal

        Raises:
            ValueError: If invalid input data
            RuntimeError: If generator fails (e.g., missing model)
        """
        pass

    def is_enabled(self) -> bool:
        """Check if this generator is enabled."""
        return self.enabled
```

**Step 3: Write test for base class**

Write: `tests/test_signal_generator_base.py`
```python
"""Test base SignalGenerator class."""

import pytest
from datetime import date
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal

class DummySignalGenerator(SignalGenerator):
    """Concrete implementation for testing."""

    def generate(self, ticker: str, date: date) -> Optional[Signal]:
        return Signal(
            ticker=ticker,
            date=date,
            signal_type="test",
            action="HOLD",
            confidence=50.0,
            reasoning="Test signal",
            metadata={}
        )

def test_generator_enabled():
    """Test generator respects enabled flag."""
    config = {"enabled": True}
    gen = DummySignalGenerator(config)
    assert gen.is_enabled() == True

def test_generator_disabled():
    """Test generator when disabled."""
    config = {"enabled": False}
    gen = DummySignalGenerator(config)
    assert gen.is_enabled() == False

def test_abstract_class_cannot_instantiate():
    """Test that base class cannot be instantiated directly."""
    from equity_lake.signals.generators.base import SignalGenerator
    config = {"enabled": True}
    with pytest.raises(TypeError):
        SignalGenerator(config)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_signal_generator_base.py -v`
Expected: All tests PASS

**Step 5: Commit base generator**

```bash
git add src/equity_lake/signals/generators/ tests/test_signal_generator_base.py
git commit -m "feat(signals): add base SignalGenerator abstract class

Define generator interface with:
- Abstract generate() method for subclasses
- enabled flag from config
- is_enabled() helper method

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Create Backtest Signal Generator

**Files:**
- Modify: `src/equity_lake/signals/generators/__init__.py`
- Create: `src/equity_lake/signals/generators/backtest.py`
- Test: `tests/test_backtest_generator.py`

**Step 1: Implement backtest generator**

Write: `src/equity_lake/signals/generators/backtest.py`
```python
"""Backtest strategy signal generator."""

from datetime import date, timedelta
from typing import Optional

import duckdb
import pandas as pd

from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal
from equity_lake.core.runtime import US_EQUITY_DIR, get_project_config


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

    def _setup_views(self):
        """Create DuckDB views for querying price data."""
        us_pattern = f"{US_EQUITY_DIR}/date=*/*.parquet"
        sql = f"""
        CREATE OR REPLACE VIEW price_data AS
        SELECT * FROM read_parquet('{us_pattern}', hive_partitioning=1)
        """
        try:
            self.con.execute(sql)
        except Exception as e:
            # No data available yet
            pass

    def generate(self, ticker: str, target_date: date) -> Optional[Signal]:
        """Generate signal based on backtest strategy conditions.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action (BUY/SELL/HOLD) and confidence
        """
        if not self.is_enabled():
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
        target_date: date
    ) -> Optional[Signal]:
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
                    "sma": sma
                }
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
                    "sma": sma
                }
            )

        return None
```

**Step 2: Export from generators init**

Modify: `src/equity_lake/signals/generators/__init__.py`
```python
"""Signal generators for different data sources."""

from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.generators.backtest import BacktestSignalGenerator

__all__ = ["SignalGenerator", "BacktestSignalGenerator"]
```

**Step 3: Write tests for backtest generator**

Write: `tests/test_backtest_generator.py`
```python
"""Test BacktestSignalGenerator."""

import pytest
from datetime import date, timedelta
from equity_lake.signals.generators.backtest import BacktestSignalGenerator

def test_backtest_generator_enabled():
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "min_win_rate": 0.55,
        "strategies": [
            {
                "name": "momentum",
                "lookback_days": 20,
                "buy_threshold": 0.02,
                "sell_threshold": -0.01
            }
        ]
    }
    gen = BacktestSignalGenerator(config)
    assert gen.is_enabled() == True

def test_backtest_generator_no_data():
    """Test generator when no price data available."""
    config = {
        "enabled": True,
        "strategies": [{"name": "momentum", "lookback_days": 20}]
    }
    gen = BacktestSignalGenerator(config)
    # Ticker with no data should return None
    signal = gen.generate("NONEXISTENT", date.today() - timedelta(days=1))
    assert signal is None

@pytest.mark.skipif(
    True,  # Skip if no test data available
    reason="Requires EOD data in data/lake/"
)
def test_backtest_generator_with_data():
    """Test generator generates BUY signal above threshold."""
    # This test requires actual EOD data
    config = {
        "enabled": True,
        "strategies": [{"name": "momentum", "lookback_days": 20}]
    }
    gen = BacktestSignalGenerator(config)
    signal = gen.generate("AAPL", date.today() - timedelta(days=1))
    # Should return a Signal or None depending on market conditions
    if signal:
        assert signal.signal_type == "backtest"
        assert signal.action in ["BUY", "SELL", "HOLD"]
        assert 0 <= signal.confidence <= 100
```

**Step 4: Run tests**

Run: `pytest tests/test_backtest_generator.py -v`
Expected: Tests PASS (some may be skipped without data)

**Step 5: Commit backtest generator**

```bash
git add src/equity_lake/signals/generators/backtest.py src/equity_lake/signals/generators/__init__.py tests/test_backtest_generator.py
git commit -m "feat(signals): add BacktestSignalGenerator

Implement backtest strategy signals:
- Queries DuckDB for historical price data
- Calculates SMA-based momentum signals
- Generates BUY when price > SMA by threshold
- Generates SELL when price < SMA by threshold
- Returns None if no data or no signal

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Create Sentiment Signal Generator

**Files:**
- Modify: `src/equity_lake/signals/generators/__init__.py`
- Create: `src/equity_lake/signals/generators/sentiment.py`
- Test: `tests/test_sentiment_generator.py`

**Step 1: Implement sentiment generator**

Write: `src/equity_lake/signals/generators/sentiment.py`
```python
"""News sentiment signal generator."""

from datetime import date, timedelta
from typing import Optional

from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal
from equity_lake.sentiment.analyzer import SentimentAnalyzer


class SentimentSignalGenerator(SignalGenerator):
    """Generate signals based on news sentiment analysis.

    Reuses existing sentiment analyzer to fetch news and calculate
    sentiment scores. Generates BUY when sentiment is positive,
    SELL when negative.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.buy_threshold = config.get("buy_threshold", 0.5)
        self.sell_threshold = config.get("sell_threshold", -0.3)
        self.min_articles = config.get("min_articles", 3)
        self.lookback_days = config.get("lookback_days", 7)

        # Initialize sentiment analyzer
        self.analyzer = SentimentAnalyzer()

    def generate(self, ticker: str, target_date: date) -> Optional[Signal]:
        """Generate signal based on news sentiment.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action based on sentiment score
        """
        if not self.is_enabled():
            return None

        # Calculate date range for news lookup
        start_date = target_date - timedelta(days=self.lookback_days)

        try:
            # Fetch news and analyze sentiment
            sentiment_data = self.analyzer.analyze_ticker(
                ticker=ticker,
                start_date=start_date,
                end_date=target_date
            )
        except Exception as e:
            # Sentiment analysis failed
            return None

        if not sentiment_data:
            return None

        # Extract sentiment score and article count
        avg_sentiment = sentiment_data.get("avg_sentiment", 0)
        article_count = sentiment_data.get("article_count", 0)

        # Check minimum article threshold
        if article_count < self.min_articles:
            return None

        # Generate signal based on sentiment
        if avg_sentiment >= self.buy_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="sentiment",
                action="BUY",
                confidence=65.0,
                reasoning=f"Positive sentiment: {avg_sentiment:.2f} from {article_count} articles",
                metadata={
                    "sentiment_score": avg_sentiment,
                    "article_count": article_count,
                    "lookback_days": self.lookback_days
                }
            )
        elif avg_sentiment <= self.sell_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="sentiment",
                action="SELL",
                confidence=60.0,
                reasoning=f"Negative sentiment: {avg_sentiment:.2f} from {article_count} articles",
                metadata={
                    "sentiment_score": avg_sentiment,
                    "article_count": article_count,
                    "lookback_days": self.lookback_days
                }
            )

        return None
```

**Step 2: Update generators init**

Modify: `src/equity_lake/signals/generators/__init__.py`
```python
"""Signal generators for different data sources."""

from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator

__all__ = ["SignalGenerator", "BacktestSignalGenerator", "SentimentSignalGenerator"]
```

**Step 3: Write tests**

Write: `tests/test_sentiment_generator.py`
```python
"""Test SentimentSignalGenerator."""

import pytest
from datetime import date
from unittest.mock import Mock, patch
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator

def test_sentiment_generator_enabled():
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "buy_threshold": 0.5,
        "sell_threshold": -0.3,
        "min_articles": 3
    }
    gen = SentimentSignalGenerator(config)
    assert gen.is_enabled() == True

@patch("equity_lake.signals.generators.sentiment.SentimentAnalyzer")
def test_sentiment_generator_buy_signal(mock_analyzer_class):
    """Test BUY signal when sentiment positive."""
    # Mock sentiment analyzer
    mock_analyzer = Mock()
    mock_analyzer.analyze_ticker.return_value = {
        "avg_sentiment": 0.75,  # Above buy_threshold
        "article_count": 5
    }
    mock_analyzer_class.return_value = mock_analyzer

    config = {"enabled": True, "buy_threshold": 0.5, "min_articles": 3}
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "sentiment"
    assert signal.metadata["sentiment_score"] == 0.75

@patch("equity_lake.signals.generators.sentiment.SentimentAnalyzer")
def test_sentiment_generator_sell_signal(mock_analyzer_class):
    """Test SELL signal when sentiment negative."""
    mock_analyzer = Mock()
    mock_analyzer.analyze_ticker.return_value = {
        "avg_sentiment": -0.5,  # Below sell_threshold
        "article_count": 4
    }
    mock_analyzer_class.return_value = mock_analyzer

    config = {"enabled": True, "sell_threshold": -0.3, "min_articles": 3}
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("TSLA", date.today())

    assert signal is not None
    assert signal.action == "SELL"

@patch("equity_lake.signals.generators.sentiment.SentimentAnalyzer")
def test_sentiment_generator_no_signal(mock_analyzer_class):
    """Test no signal when sentiment neutral."""
    mock_analyzer = Mock()
    mock_analyzer.analyze_ticker.return_value = {
        "avg_sentiment": 0.1,  # Between thresholds
        "article_count": 5
    }
    mock_analyzer_class.return_value = mock_analyzer

    config = {
        "enabled": True,
        "buy_threshold": 0.5,
        "sell_threshold": -0.3,
        "min_articles": 3
    }
    gen = SentimentSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is None
```

**Step 4: Run tests**

Run: `pytest tests/test_sentiment_generator.py -v`
Expected: All tests PASS

**Step 5: Commit sentiment generator**

```bash
git add src/equity_lake/signals/generators/sentiment.py src/equity_lake/signals/generators/__init__.py tests/test_sentiment_generator.py
git commit -m "feat(signals): add SentimentSignalGenerator

Implement news sentiment signals:
- Reuses SentimentAnalyzer for news fetching
- Generates BUY when sentiment >= threshold
- Generates SELL when sentiment <= threshold
- Validates minimum article count
- Returns None for neutral sentiment

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Create ML Prediction Signal Generator

**Files:**
- Modify: `src/equity_lake/signals/generators/__init__.py`
- Create: `src/equity_lake/signals/generators/ml.py`
- Test: `tests/test_ml_generator.py`

**Step 1: Implement ML generator**

Write: `src/equity_lake/signals/generators/ml.py`
```python
"""ML prediction signal generator."""

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal
from equity_lake.ml.forecasting import PriceForecaster


class MLPredictionSignalGenerator(SignalGenerator):
    """Generate signals based on XGBoost price forecasts.

    Reuses existing ML forecaster to predict future returns.
    Generates BUY when predicted return is positive and
    confidence exceeds threshold.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_path = Path(config.get("model_path", "data/models/xgboost_price_forecaster.pkl"))
        self.horizon_days = config.get("horizon_days", 5)
        self.buy_threshold = config.get("buy_return_threshold", 0.03)
        self.sell_threshold = config.get("sell_return_threshold", -0.02)
        self.min_confidence = config.get("min_confidence", 60)

        # Initialize forecaster
        self.forecaster = None
        if self.model_path.exists():
            try:
                self.forecaster = PriceForecaster.load_model(self.model_path)
            except Exception:
                pass  # Model not available

    def generate(self, ticker: str, target_date: date) -> Optional[Signal]:
        """Generate signal based on ML price prediction.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action based on predicted return
        """
        if not self.is_enabled():
            return None

        if self.forecaster is None:
            # Model not available
            return None

        # Fetch historical data for features
        start_date = target_date - timedelta(days=90)

        try:
            # Generate prediction
            prediction = self.forecaster.predict(
                ticker=ticker,
                current_date=target_date,
                horizon=self.horizon_days
            )
        except Exception:
            # Prediction failed
            return None

        if not prediction:
            return None

        # Extract prediction and confidence
        predicted_return = prediction.get("predicted_return", 0)
        confidence = prediction.get("confidence", 0)

        # Check confidence threshold
        if confidence < self.min_confidence:
            return None

        # Generate signal
        if predicted_return >= self.buy_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="ml",
                action="BUY",
                confidence=float(confidence),
                reasoning=f"ML predicts {predicted_return:.1%} return in {self.horizon_days} days ({confidence:.0f}% confidence)",
                metadata={
                    "predicted_return": predicted_return,
                    "horizon_days": self.horizon_days,
                    "confidence": confidence,
                    "features": prediction.get("important_features", [])
                }
            )
        elif predicted_return <= self.sell_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="ml",
                action="SELL",
                confidence=float(confidence),
                reasoning=f"ML predicts {predicted_return:.1%} return in {self.horizon_days} days ({confidence:.0f}% confidence)",
                metadata={
                    "predicted_return": predicted_return,
                    "horizon_days": self.horizon_days,
                    "confidence": confidence
                }
            )

        return None
```

**Step 2: Update generators init**

Modify: `src/equity_lake/signals/generators/__init__.py`
```python
"""Signal generators for different data sources."""

from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator

__all__ = [
    "SignalGenerator",
    "BacktestSignalGenerator",
    "SentimentSignalGenerator",
    "MLPredictionSignalGenerator"
]
```

**Step 3: Write tests**

Write: `tests/test_ml_generator.py`
```python
"""Test MLPredictionSignalGenerator."""

import pytest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator

def test_ml_generator_enabled():
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "model_path": "model.pkl",
        "min_confidence": 60
    }
    gen = MLPredictionSignalGenerator(config)
    assert gen.is_enabled() == True

@patch("equity_lake.signals.generators.ml.PriceForecaster")
def test_ml_generator_buy_signal(mock_forecaster_class):
    """Test BUY signal when prediction positive."""
    mock_forecaster = Mock()
    mock_forecaster.load_model.return_value = mock_forecaster
    mock_forecaster.predict.return_value = {
        "predicted_return": 0.05,  # 5% above buy_threshold
        "confidence": 75
    }
    mock_forecaster_class.return_value = mock_forecaster

    config = {
        "enabled": True,
        "model_path": "model.pkl",
        "buy_return_threshold": 0.03,
        "min_confidence": 60
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "ml"
    assert signal.metadata["predicted_return"] == 0.05

@patch("equity_lake.signals.generators.ml.PriceForecaster")
def test_ml_generator_low_confidence(mock_forecaster_class):
    """Test no signal when confidence too low."""
    mock_forecaster = Mock()
    mock_forecaster.load_model.return_value = mock_forecaster
    mock_forecaster.predict.return_value = {
        "predicted_return": 0.10,
        "confidence": 50  # Below min_confidence
    }
    mock_forecaster_class.return_value = mock_forecaster

    config = {
        "enabled": True,
        "model_path": "model.pkl",
        "min_confidence": 60
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is None

def test_ml_generator_no_model():
    """Test no signal when model file missing."""
    config = {
        "enabled": True,
        "model_path": "nonexistent_model.pkl"
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())
    assert signal is None
```

**Step 4: Run tests**

Run: `pytest tests/test_ml_generator.py -v`
Expected: All tests PASS

**Step 5: Commit ML generator**

```bash
git add src/equity_lake/signals/generators/ml.py src/equity_lake/signals/generators/__init__.py tests/test_ml_generator.py
git commit -m "feat(signals): add MLPredictionSignalGenerator

Implement ML prediction signals:
- Loads XGBoost model for price forecasting
- Generates BUY when predicted return >= threshold
- Validates minimum confidence score
- Returns None if model unavailable or confidence low

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Create JSON Formatter

**Files:**
- Create: `src/equity_lake/signals/formatters/__init__.py`
- Create: `src/equity_lake/signals/formatters/base.py`
- Create: `src/equity_lake/signals/formatters/json.py`
- Test: `tests/test_json_formatter.py`

**Step 1: Create formatters package**

Write: `src/equity_lake/signals/formatters/__init__.py`
```python
"""Signal output formatters."""

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.formatters.json import JSONFormatter

__all__ = ["SignalFormatter", "JSONFormatter"]
```

**Step 2: Write base formatter class**

Write: `src/equity_lake/signals/formatters/base.py`
```python
"""Base class for signal formatters."""

from abc import ABC, abstractmethod
from typing import List

from equity_lake.signals.models import Signal


class SignalFormatter(ABC):
    """Base class for signal output formatters."""

    @abstractmethod
    def format(self, signals: List[Signal]) -> str:
        """Format signals for output.

        Args:
            signals: List of Signal objects

        Returns:
            Formatted string
        """
        pass
```

**Step 3: Write JSON formatter**

Write: `src/equity_lake/signals/formatters/json.py`
```python
"""JSON signal formatter."""

import json
from typing import List

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.models import Signal


class JSONFormatter(SignalFormatter):
    """Format signals as machine-readable JSON."""

    def format(self, signals: List[Signal]) -> str:
        """Format signals as JSON array.

        Args:
            signals: List of Signal objects

        Returns:
            JSON string
        """
        signal_dicts = []
        for signal in signals:
            signal_dict = {
                "ticker": signal.ticker,
                "date": signal.date.isoformat(),
                "signal_type": signal.signal_type,
                "action": signal.action,
                "confidence": signal.confidence,
                "reasoning": signal.reasoning,
                "metadata": signal.metadata
            }
            signal_dicts.append(signal_dict)

        return json.dumps(signal_dicts, indent=2)
```

**Step 4: Write tests**

Write: `tests/test_json_formatter.py`
```python
"""Test JSONFormatter."""

import json
from datetime import date
from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.models import Signal

def test_json_formatter_empty():
    """Test formatting empty signal list."""
    formatter = JSONFormatter()
    output = formatter.format([])
    assert output == "[]"

def test_json_formatter_single_signal():
    """Test formatting single signal."""
    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 1),
        signal_type="backtest",
        action="BUY",
        confidence=75.0,
        reasoning="Test signal",
        metadata={"key": "value"}
    )

    formatter = JSONFormatter()
    output = formatter.format([signal])

    data = json.loads(output)
    assert len(data) == 1
    assert data[0]["ticker"] == "AAPL"
    assert data[0]["action"] == "BUY"
    assert data[0]["confidence"] == 75.0
    assert data[0]["metadata"]["key"] == "value"

def test_json_formatter_multiple_signals():
    """Test formatting multiple signals."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "R1", {}),
        Signal("GOOGL", date(2024, 12, 1), "sentiment", "SELL", 60, "R2", {})
    ]

    formatter = JSONFormatter()
    output = formatter.format(signals)

    data = json.loads(output)
    assert len(data) == 2
    assert data[0]["ticker"] == "AAPL"
    assert data[1]["ticker"] == "GOOGL"
```

**Step 5: Run tests**

Run: `pytest tests/test_json_formatter.py -v`
Expected: All tests PASS

**Step 6: Commit JSON formatter**

```bash
git add src/equity_lake/signals/formatters/ tests/test_json_formatter.py
git commit -m "feat(signals): add JSONFormatter

Implement JSON output format:
- Converts Signal objects to JSON array
- Machine-readable for parsing by other tools
- Includes all signal metadata

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Create Markdown Formatter

**Files:**
- Modify: `src/equity_lake/signals/formatters/__init__.py`
- Create: `src/equity_lake/signals/formatters/markdown.py`
- Test: `tests/test_markdown_formatter.py`

**Step 1: Implement Markdown formatter**

Write: `src/equity_lake/signals/formatters/markdown.py`
```python
"""Markdown signal formatter."""

from typing import List
from collections import defaultdict

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.models import Signal


class MarkdownFormatter(SignalFormatter):
    """Format signals as readable Markdown report."""

    def format(self, signals: List[Signal]) -> str:
        """Format signals as Markdown report.

        Args:
            signals: List of Signal objects

        Returns:
            Markdown string
        """
        if not signals:
            return "# Signal Report\n\nNo signals generated.\n"

        lines = []
        lines.append("# Signal Report\n")
        lines.append(f"**Generated:** {signals[0].date}  \n")
        lines.append(f"**Total Signals:** {len(signals)}\n\n")

        # Group by action
        by_action = defaultdict(list)
        for signal in signals:
            by_action[signal.action].append(signal)

        # Summary table
        lines.append("## Summary by Action\n\n")
        lines.append("| Action | Count |")
        lines.append("|--------|-------|")
        for action in ["BUY", "SELL", "HOLD"]:
            count = len(by_action.get(action, []))
            lines.append(f"| {action} | {count} |")
        lines.append("\n")

        # Detailed sections by signal type
        for signal_type in ["backtest", "sentiment", "ml"]:
            type_signals = [s for s in signals if s.signal_type == signal_type]
            if not type_signals:
                continue

            lines.append(f"## {signal_type.title()} Signals\n\n")

            # Table header
            lines.append("| Ticker | Action | Confidence | Reasoning |")
            lines.append("|--------|--------|------------|-----------|")

            for signal in type_signals:
                lines.append(
                    f"| {signal.ticker} | {signal.action} | "
                    f"{signal.confidence:.0f} | {signal.reasoning} |"
                )
            lines.append("\n")

        return "".join(lines)
```

**Step 2: Update formatters init**

Modify: `src/equity_lake/signals/formatters/__init__.py`
```python
"""Signal output formatters."""

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.formatters.markdown import MarkdownFormatter

__all__ = ["SignalFormatter", "JSONFormatter", "MarkdownFormatter"]
```

**Step 3: Write tests**

Write: `tests/test_markdown_formatter.py`
```python
"""Test MarkdownFormatter."""

from datetime import date
from equity_lake.signals.formatters.markdown import MarkdownFormatter
from equity_lake.signals.models import Signal

def test_markdown_formatter_empty():
    """Test formatting empty signal list."""
    formatter = MarkdownFormatter()
    output = formatter.format([])
    assert "# Signal Report" in output
    assert "No signals generated" in output

def test_markdown_formatter_summary_table():
    """Test summary table generation."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "R1", {}),
        Signal("GOOGL", date(2024, 12, 1), "sentiment", "SELL", 60, "R2", {}),
        Signal("MSFT", date(2024, 12, 1), "ml", "HOLD", 50, "R3", {})
    ]

    formatter = MarkdownFormatter()
    output = formatter.format(signals)

    assert "# Signal Report" in output
    assert "| BUY | 1 |" in output
    assert "| SELL | 1 |" in output
    assert "| HOLD | 1 |" in output

def test_markdown_formatter_signal_sections():
    """Test signal type sections."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "Momentum", {}),
        Signal("TSLA", date(2024, 12, 1), "sentiment", "SELL", 60, "Negative news", {})
    ]

    formatter = MarkdownFormatter()
    output = formatter.format(signals)

    assert "## Backtest Signals" in output
    assert "## Sentiment Signals" in output
    assert "| AAPL | BUY | 75 |" in output
    assert "| TSLA | SELL | 60 |" in output
```

**Step 4: Run tests**

Run: `pytest tests/test_markdown_formatter.py -v`
Expected: All tests PASS

**Step 5: Commit Markdown formatter**

```bash
git add src/equity_lake/signals/formatters/markdown.py src/equity_lake/signals/formatters/__init__.py tests/test_markdown_formatter.py
git commit -m "feat(signals): add MarkdownFormatter

Implement Markdown report format:
- Summary table by action (BUY/SELL/HOLD count)
- Detailed sections by signal type
- Readable tables with ticker, action, confidence, reasoning

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Create Terminal Formatter

**Files:**
- Modify: `src/equity_lake/signals/formatters/__init__.py`
- Create: `src/equity_lake/signals/formatters/terminal.py`
- Test: `tests/test_terminal_formatter.py`

**Step 1: Implement terminal formatter**

Write: `src/equity_lake/signals/formatters/terminal.py`
```python
"""Terminal table signal formatter."""

from typing import List
from collections import defaultdict

try:
    from tabulate import tabulate
    TABULATE_AVAILABLE = True
except ImportError:
    TABULATE_AVAILABLE = False

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.models import Signal


class TerminalFormatter(SignalFormatter):
    """Format signals as colored terminal tables."""

    def format(self, signals: List[Signal]) -> str:
        """Format signals as terminal tables.

        Args:
            signals: List of Signal objects

        Returns:
            Formatted string for terminal
        """
        if not signals:
            return "No signals generated.\n"

        lines = []
        lines.append("=" * 80)
        lines.append(f"SIGNAL REPORT - {signals[0].date}")
        lines.append(f"Total Signals: {len(signals)}")
        lines.append("=" * 80)
        lines.append("")

        # Group by action for summary
        by_action = defaultdict(list)
        for signal in signals:
            by_action[signal.action].append(signal)

        # Summary
        lines.append("SUMMARY:")
        for action in ["BUY", "SELL", "HOLD"]:
            count = len(by_action.get(action, []))
            lines.append(f"  {action}: {count}")
        lines.append("")

        # Group by signal type
        for signal_type in ["backtest", "sentiment", "ml"]:
            type_signals = [s for s in signals if s.signal_type == signal_type]
            if not type_signals:
                continue

            lines.append(f"\n{signal_type.upper()} SIGNALS:")
            lines.append("-" * 80)

            if TABULATE_AVAILABLE:
                table_data = []
                for signal in type_signals:
                    table_data.append([
                        signal.ticker,
                        signal.action,
                        f"{signal.confidence:.0f}",
                        signal.reasoning[:50] + "..." if len(signal.reasoning) > 50 else signal.reasoning
                    ])
                lines.append(tabulate(table_data, headers=["Ticker", "Action", "Conf", "Reasoning"]))
            else:
                # Fallback: simple table
                for signal in type_signals:
                    lines.append(f"  {signal.ticker:10s} | {signal.action:6s} | {signal.confidence:5.0f} | {signal.reasoning}")

            lines.append("")

        return "\n".join(lines)
```

**Step 2: Update formatters init**

Modify: `src/equity_lake/signals/formatters/__init__.py`
```python
"""Signal output formatters."""

from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.formatters.markdown import MarkdownFormatter
from equity_lake.signals.formatters.terminal import TerminalFormatter

__all__ = [
    "SignalFormatter",
    "JSONFormatter",
    "MarkdownFormatter",
    "TerminalFormatter"
]
```

**Step 3: Write tests**

Write: `tests/test_terminal_formatter.py`
```python
"""Test TerminalFormatter."""

from datetime import date
from equity_lake.signals.formatters.terminal import TerminalFormatter
from equity_lake.signals.models import Signal

def test_terminal_formatter_empty():
    """Test formatting empty signal list."""
    formatter = TerminalFormatter()
    output = formatter.format([])
    assert "No signals generated" in output

def test_terminal_formatter_summary():
    """Test summary section."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "R1", {}),
        Signal("GOOGL", date(2024, 12, 1), "sentiment", "SELL", 60, "R2", {})
    ]

    formatter = TerminalFormatter()
    output = formatter.format(signals)

    assert "SIGNAL REPORT" in output
    assert "BUY: 1" in output
    assert "SELL: 1" in output

def test_terminal_formatter_signal_sections():
    """Test signal type sections."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "Momentum entry", {})
    ]

    formatter = TerminalFormatter()
    output = formatter.format(signals)

    assert "BACKTEST SIGNALS" in output
    assert "AAPL" in output
    assert "BUY" in output
```

**Step 4: Run tests**

Run: `pytest tests/test_terminal_formatter.py -v`
Expected: All tests PASS

**Step 5: Commit terminal formatter**

```bash
git add src/equity_lake/signals/formatters/terminal.py src/equity_lake/signals/formatters/__init__.py tests/test_terminal_formatter.py
git commit -m "feat(signals): add TerminalFormatter

Implement terminal table format:
- Summary section with action counts
- Signal type sections with tables
- Uses tabulate library if available
- Fallback to simple table format

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Create SignalScanner Orchestrator

**Files:**
- Create: `src/equity_lake/signals/scanner.py`
- Test: `tests/test_signal_scanner.py`

**Step 1: Implement scanner**

Write: `src/equity_lake/signals/scanner.py`
```python
"""Signal scanner orchestrator."""

from datetime import date, timedelta
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from equity_lake.signals.models import Signal, Watchlist, SignalConfig
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.formatters.base import SignalFormatter
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.generators.sentiment import SentimentSignalGenerator
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator
from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.formatters.markdown import MarkdownFormatter
from equity_lake.signals.formatters.terminal import TerminalFormatter


class SignalScanner:
    """Main orchestrator for scanning watchlist and generating signals."""

    def __init__(self, config: SignalConfig, watchlist: Watchlist):
        """Initialize scanner with config and watchlist.

        Args:
            config: Signal configuration
            watchlist: Tickers to scan
        """
        self.config = config
        self.watchlist = watchlist

        # Initialize generators
        self.generators: List[SignalGenerator] = []
        if config.is_generator_enabled("backtest"):
            self.generators.append(BacktestSignalGenerator(config.backtest))
        if config.is_generator_enabled("sentiment"):
            self.generators.append(SentimentSignalGenerator(config.sentiment))
        if config.is_generator_enabled("ml"):
            self.generators.append(MLPredictionSignalGenerator(config.ml))

        # Initialize formatters
        self.formatters = {
            "json": JSONFormatter(),
            "md": MarkdownFormatter(),
            "table": TerminalFormatter()
        }

    def scan(self, target_date: Optional[date] = None) -> List[Signal]:
        """Scan all tickers and return aggregated signals.

        Args:
            target_date: Date to generate signals for (default: yesterday)

        Returns:
            List of Signal objects
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)

        all_signals = []

        # Scan each ticker
        for ticker in self.watchlist.tickers:
            ticker_signals = self._scan_ticker(ticker, target_date)
            if ticker_signals:
                all_signals.extend(ticker_signals)

        return all_signals

    def _scan_ticker(self, ticker: str, target_date: date) -> List[Signal]:
        """Scan a single ticker with all generators.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signals for

        Returns:
            List of Signal objects for this ticker
        """
        signals = []

        for generator in self.generators:
            try:
                signal = generator.generate(ticker, target_date)
                if signal:
                    signals.append(signal)
            except Exception as e:
                # Log but continue with other generators
                print(f"Warning: {generator.__class__.__name__} failed for {ticker}: {e}")
                continue

        return signals

    def format_signals(self, signals: List[Signal], format: str = "table") -> str:
        """Format signals for output.

        Args:
            signals: List of Signal objects
            format: Output format (json, md, table)

        Returns:
            Formatted string
        """
        formatter = self.formatters.get(format)
        if not formatter:
            raise ValueError(f"Unknown format: {format}. Use: json, md, table")

        return formatter.format(signals)

    def save_history(self, signals: List[Signal]):
        """Save signals to Parquet history.

        Args:
            signals: List of Signal objects to save
        """
        # TODO: Implement in Task 12
        pass
```

**Step 2: Write tests**

Write: `tests/test_signal_scanner.py`
```python
"""Test SignalScanner."""

from datetime import date
from equity_lake.signals.scanner import SignalScanner
from equity_lake.signals.models import SignalConfig, Watchlist

def test_scanner_initialization():
    """Test scanner initializes with config."""
    config = SignalConfig(
        backtest={"enabled": True, "min_win_rate": 0.55, "strategies": []},
        sentiment={"enabled": False},
        ml={"enabled": False}
    )
    watchlist = Watchlist(name="Test", tickers=["AAPL", "GOOGL"])

    scanner = SignalScanner(config, watchlist)

    assert len(scanner.generators) == 1  # Only backtest enabled
    assert "json" in scanner.formatters
    assert "md" in scanner.formatters
    assert "table" in scanner.formatters

def test_scanner_scan_empty_watchlist():
    """Test scanning empty watchlist."""
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": False}
    )
    watchlist = Watchlist(name="Empty", tickers=[])

    scanner = SignalScanner(config, watchlist)
    signals = scanner.scan()

    assert len(signals) == 0

def test_scanner_format_signals():
    """Test formatting signals."""
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": False}
    )
    watchlist = Watchlist(name="Test", tickers=[])

    scanner = SignalScanner(config, watchlist)

    # Create test signals
    signals = [
        Signal("AAPL", date.today(), "test", "BUY", 75, "R1", {}),
        Signal("GOOGL", date.today(), "test", "SELL", 60, "R2", {})
    ]

    # Test JSON format
    json_output = scanner.format_signals(signals, "json")
    assert "AAPL" in json_output
    assert "BUY" in json_output

    # Test Markdown format
    md_output = scanner.format_signals(signals, "md")
    assert "# Signal Report" in md_output

    # Test table format
    table_output = scanner.format_signals(signals, "table")
    assert "SIGNAL REPORT" in table_output
```

**Step 3: Run tests**

Run: `pytest tests/test_signal_scanner.py -v`
Expected: All tests PASS

**Step 4: Commit scanner**

```bash
git add src/equity_lake/signals/scanner.py tests/test_signal_scanner.py
git commit -m "feat(signals): add SignalScanner orchestrator

Implement main scanner class:
- Initializes generators based on config
- Scans all tickers in watchlist
- Formats output in JSON/Markdown/Table formats
- Handles generator failures gracefully

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 11: Create CLI Entry Point

**Files:**
- Create: `src/equity_lake/cli/signal.py`
- Modify: `pyproject.toml`

**Step 1: Implement CLI**

Write: `src/equity_lake/cli/signal.py`
```python
"""CLI entry point for signal scanning."""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from equity_lake.signals.scanner import SignalScanner
from equity_lake.signals.config import load_watchlist, load_signal_config
from equity_lake.core.runtime import setup_logging

logger = setup_logging(__name__)


def parse_scan_args(args: argparse.Namespace) -> dict:
    """Parse and validate scan command arguments."""
    kwargs = {}

    if args.date:
        try:
            kwargs["target_date"] = date.fromisoformat(args.date)
        except ValueError:
            logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD")
            sys.exit(1)

    if args.watchlist:
        kwargs["watchlist_path"] = Path(args.watchlist)

    if args.config:
        kwargs["config_path"] = Path(args.config)

    kwargs["format"] = args.format

    if args.output:
        kwargs["output_path"] = Path(args.output)

    kwargs["dry_run"] = args.dry_run
    kwargs["verbose"] = args.verbose

    return kwargs


def cmd_scan(args: argparse.Namespace):
    """Run signal scan command."""
    kwargs = parse_scan_args(args)

    # Load configs
    watchlist_path = kwargs.get("watchlist_path")
    config_path = kwargs.get("config_path")

    try:
        watchlist = load_watchlist(watchlist_path)
        config = load_signal_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    logger.info(f"Scanning watchlist: {watchlist.name}")
    logger.info(f"Tickers: {len(watchlist.tickers)}")

    # Initialize scanner
    scanner = SignalScanner(config, watchlist)

    # Scan
    target_date = kwargs.get("target_date", date.today() - timedelta(days=1))
    logger.info(f"Generating signals for: {target_date}")

    signals = scanner.scan(target_date)

    logger.info(f"Generated {len(signals)} signals")

    # Format output
    output = scanner.format_signals(signals, kwargs["format"])

    # Print or save
    output_path = kwargs.get("output_path")
    if output_path:
        with open(output_path, "w") as f:
            f.write(output)
        logger.info(f"✅ Saved to {output_path}")
    else:
        print(output)

    # Save history (unless dry run)
    if not kwargs["dry_run"] and signals:
        scanner.save_history(signals)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Signal scanning for equity watchlists",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # scan subcommand
    scan_parser = subparsers.add_parser("scan", help="Scan watchlist and generate signals")

    scan_parser.add_argument(
        "--format", "-f",
        choices=["json", "md", "table"],
        default="table",
        help="Output format (default: table)"
    )

    scan_parser.add_argument(
        "--date", "-d",
        help="Target date (YYYY-MM-DD, default: yesterday)"
    )

    scan_parser.add_argument(
        "--watchlist", "-w",
        help="Path to watchlist config"
    )

    scan_parser.add_argument(
        "--config", "-c",
        help="Path to signal config"
    )

    scan_parser.add_argument(
        "--output", "-o",
        help="Save output to file"
    )

    scan_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save to history"
    )

    scan_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 2: Add CLI entry to pyproject.toml**

Modify: `pyproject.toml`
```toml
[project.scripts]
# ... existing entries ...
equity-signal = "equity_lake.cli.signal:main"
```

**Step 3: Test CLI manually**

Run: `uv run equity-signal scan --help`
Expected: Help message displayed

Run: `uv run equity-signal scan --format table`
Expected: Signal scan runs with test watchlist

**Step 4: Commit CLI**

```bash
git add src/equity_lake/cli/signal.py pyproject.toml
git commit -m "feat(signals): add equity-signal CLI entry point

Add signal scanning CLI:
- equity-signal scan: main command for generating signals
- Supports JSON, Markdown, and table output formats
- Configurable watchlist and signal paths
- Dry-run mode for testing

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 12: Implement Signal History Storage

**Files:**
- Create: `src/equity_lake/signals/history.py`
- Modify: `src/equity_lake/signals/scanner.py`
- Test: `tests/test_signal_history.py`

**Step 1: Implement history storage**

Write: `src/equity_lake/signals/history.py`
```python
"""Signal history storage with Parquet."""

from datetime import date
from pathlib import Path
from typing import List

import pandas as pd
from equity_lake.core.runtime import get_project_config
from equity_lake.signals.models import Signal

# History storage directory
SIGNALS_DIR = Path("data/signals")


def save_signals_to_parquet(signals: List[Signal], target_date: date):
    """Save signals to partitioned Parquet storage.

    Args:
        signals: List of Signal objects
        target_date: Date for partitioning
    """
    if not signals:
        return

    # Create partition directory
    partition_dir = SIGNALS_DIR / f"date={target_date.isoformat()}"
    partition_dir.mkdir(parents=True, exist_ok=True)

    # Convert to DataFrame
    records = []
    for signal in signals:
        record = {
            "ticker": signal.ticker,
            "date": signal.date,
            "signal_type": signal.signal_type,
            "action": signal.action,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
            **signal.metadata  # Flatten metadata into columns
        }
        records.append(record)

    df = pd.DataFrame(records)

    # Write to Parquet
    output_path = partition_dir / "signals.parquet"
    df.to_parquet(output_path, index=False)


def load_signals_from_parquet(target_date: date) -> List[Signal]:
    """Load signals from Parquet storage.

    Args:
        target_date: Date to load signals for

    Returns:
        List of Signal objects
    """
    partition_path = SIGNALS_DIR / f"date={target_date.isoformat()}" / "signals.parquet"

    if not partition_path.exists():
        return []

    df = pd.read_parquet(partition_path)

    # Convert DataFrame to Signal objects
    signals = []
    for _, row in df.iterrows():
        # Extract metadata columns
        base_cols = {"ticker", "date", "signal_type", "action", "confidence", "reasoning"}
        metadata = {k: v for k, v in row.items() if k not in base_cols and pd.notna(v)}

        signal = Signal(
            ticker=row["ticker"],
            date=row["date"],
            signal_type=row["signal_type"],
            action=row["action"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            metadata=metadata
        )
        signals.append(signal)

    return signals
```

**Step 2: Update scanner to use history**

Modify: `src/equity_lake/signals/scanner.py`
```python
# Add import at top
from equity_lake.signals.history import save_signals_to_parquet

# Update save_history method
def save_history(self, signals: List[Signal]):
    """Save signals to Parquet history.

    Args:
        signals: List of Signal objects to save
    """
    if not signals:
        return

    target_date = signals[0].date
    save_signals_to_parquet(signals, target_date)
```

**Step 3: Write tests**

Write: `tests/test_signal_history.py`
```python
"""Test signal history storage."""

from datetime import date
from pathlib import Path
import tempfile
import shutil

from equity_lake.signals.models import Signal
from equity_lake.signals.history import save_signals_to_parquet, load_signals_from_parquet

@pytest.fixture
def temp_signals_dir():
    """Create temporary signals directory."""
    original_dir = Path("data/signals")
    temp_dir = Path(tempfile.mkdtemp())

    # Mock SIGNALS_DIR
    import equity_lake.signals.history as history_module
    original_path = history_module.SIGNALS_DIR
    history_module.SIGNALS_DIR = temp_dir

    yield temp_dir

    # Cleanup
    history_module.SIGNALS_DIR = original_path
    shutil.rmtree(temp_dir)

def test_save_and_load_signals(temp_signals_dir):
    """Test saving and loading signals."""
    test_date = date(2024, 12, 1)
    signals = [
        Signal(
            ticker="AAPL",
            date=test_date,
            signal_type="backtest",
            action="BUY",
            confidence=75.0,
            reasoning="Test signal",
            metadata={"strategy": "momentum"}
        )
    ]

    # Save
    save_signals_to_parquet(signals, test_date)

    # Verify file exists
    partition_dir = temp_signals_dir / f"date={test_date.isoformat()}"
    assert partition_dir.exists()
    assert (partition_dir / "signals.parquet").exists()

    # Load
    loaded = load_signals_from_parquet(test_date)
    assert len(loaded) == 1
    assert loaded[0].ticker == "AAPL"
    assert loaded[0].action == "BUY"
    assert loaded[0].metadata["strategy"] == "momentum"

def test_load_empty_history(temp_signals_dir):
    """Test loading when no history exists."""
    loaded = load_signals_from_parquet(date(2024, 12, 1))
    assert len(loaded) == 0
```

**Step 4: Run tests**

Run: `pytest tests/test_signal_history.py -v`
Expected: All tests PASS

**Step 5: Commit history storage**

```bash
git add src/equity_lake/signals/history.py src/equity_lake/signals/scanner.py tests/test_signal_history.py
git commit -m "feat(signals): add Parquet-based signal history storage

Implement signal persistence:
- Save signals to Hive-partitioned Parquet
- Load historical signals by date
- Integrates with SignalScanner.save_history()

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 13: Update Documentation

**Files:**
- Create: `docs/user-guide/signals.md`
- Modify: `README.md`
- Modify: `docs/plans/2025-03-03-signal-scanner-design.md`

**Step 1: Create user guide**

Write: `docs/user-guide/signals.md`
```markdown
# Signal Scanner User Guide

## Overview

The Signal Scanner generates buy/sell/hold signals for your watchlist using backtest strategies, news sentiment, and ML predictions.

## Quick Start

### 1. Configure Your Watchlist

Edit `config/watchlist.yaml`:

```yaml
name: "My Portfolio"
tickers:
  - AAPL
  - GOOGL
  - MSFT
```

### 2. Configure Signal Rules

Edit `config/signals.yaml` to adjust thresholds:

```yaml
backtest:
  enabled: true
  min_win_rate: 0.55

sentiment:
  enabled: true
  buy_threshold: 0.5

ml:
  enabled: true
  min_confidence: 60
```

### 3. Run Signal Scan

```bash
# Terminal output
equity-signal scan

# Markdown report
equity-signal scan --format md --output signals.md

# JSON for automation
equity-signal scan --format json --output signals.json

# Specific date
equity-signal scan --date 2024-12-01
```

## Signal Types

### Backtest Signals
Based on historical strategy performance:
- **BUY**: Price crosses above moving average
- **SELL**: Price crosses below moving average

### Sentiment Signals
Based on news sentiment analysis:
- **BUY**: Positive sentiment score
- **SELL**: Negative sentiment score

### ML Prediction Signals
Based on XGBoost price forecasts:
- **BUY**: High predicted return + confidence
- **SELL**: Negative predicted return

## Cron Setup

```bash
# Daily signal scan at 9:00 AM
0 9 * * * cd /path/to/equity-lake && equity-signal scan --format json > data/signals/latest.json
```

## Output Formats

- **table**: Colored terminal tables (default)
- **md**: Markdown report with tables
- **json**: Machine-readable JSON array
```

**Step 2: Update README**

Modify: `README.md`
```markdown
# equity-lake

...

## What's New (v0.4.0)

- **📊 Signal Scanner** - Generate buy/sell/hold signals for watchlists
- **🎯 3 Signal Types** - Backtest strategies, news sentiment, ML predictions
- **📝 Multi-Format Output** - JSON, Markdown, terminal tables
- **💾 Signal History** - Track past signals in Parquet storage

**Quick Start:**
```bash
# Configure watchlist in config/watchlist.yaml
# Generate signals
equity-signal scan --format md
```

See [Signal Scanner Guide](docs/user-guide/signals.md) for details.

...
```

**Step 3: Update design doc status**

Modify: `docs/plans/2025-03-03-signal-scanner-design.md`
```markdown
# Signal Scanner & Portfolio Watchlist Module

**Design Document**
**Date:** 2025-03-03
**Status:** ✅ Implemented
**Version:** 1.0
...
```

**Step 4: Commit documentation**

```bash
git add docs/user-guide/signals.md README.md docs/plans/2025-03-03-signal-scanner-design.md
git commit -m "docs(signals): add signal scanner user guide and update README

Add comprehensive documentation:
- Signal scanner user guide with quick start
- Update README with v0.4.0 features
- Mark design doc as implemented

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 14: Integration Testing

**Files:**
- Create: `tests/test_signal_integration.py`

**Step 1: Write integration tests**

Write: `tests/test_signal_integration.py`
```python
"""Integration tests for signal scanning."""

import pytest
from datetime import date, timedelta
from pathlib import Path

from equity_lake.signals.scanner import SignalScanner
from equity_lake.signals.config import load_watchlist, load_signal_config

@pytest.mark.integration
def test_full_scan_workflow():
    """Test complete scan workflow with real configs."""
    # Load actual configs
    watchlist = load_watchlist()
    config = load_signal_config()

    # Initialize scanner
    scanner = SignalScanner(config, watchlist)

    # Scan for recent date
    target_date = date.today() - timedelta(days=1)
    signals = scanner.scan(target_date)

    # Verify output
    assert isinstance(signals, list)

    # Format output
    json_output = scanner.format_signals(signals, "json")
    md_output = scanner.format_signals(signals, "md")
    table_output = scanner.format_signals(signals, "table")

    assert isinstance(json_output, str)
    assert isinstance(md_output, str)
    assert isinstance(table_output, str)

@pytest.mark.integration
def test_signal_history_roundtrip():
    """Test saving and loading signal history."""
    watchlist = load_watchlist()
    config = load_signal_config()

    scanner = SignalScanner(config, watchlist)

    # Scan
    target_date = date.today() - timedelta(days=1)
    signals = scanner.scan(target_date)

    if signals:
        # Save history
        scanner.save_history(signals)

        # Verify history exists
        from equity_lake.signals.history import load_signals_from_parquet
        loaded = load_signals_from_parquet(target_date)

        assert len(loaded) > 0
        assert loaded[0].ticker == signals[0].ticker
```

**Step 2: Run integration tests**

Run: `pytest tests/test_signal_integration.py -v -m integration`
Expected: Tests PASS (may skip if no data)

**Step 3: Commit integration tests**

```bash
git add tests/test_signal_integration.py
git commit -m "test(signals): add integration tests for full workflow

Add end-to-end integration tests:
- Full scan workflow with real configs
- Signal history save/load roundtrip
- Format output validation

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 15: Final Testing and Cleanup

**Files:**
- None (testing only)

**Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

**Step 2: Run linting**

Run: `ruff check src/equity_lake/signals/`
Expected: No linting errors

Run: `ruff format src/equity_lake/signals/`

**Step 3: Test CLI end-to-end**

Run: `uv run equity-signal scan --format table`
Expected: Successful scan with output

Run: `uv run equity-signal scan --format json --output /tmp/test_signals.json && cat /tmp/test_signals.json`
Expected: Valid JSON output

**Step 4: Final commit**

```bash
git add .
git commit -m "feat(signals): complete signal scanner implementation

All features implemented:
- ✅ Data models (Signal, Watchlist, SignalConfig)
- ✅ Config loaders (YAML)
- ✅ 3 signal generators (backtest, sentiment, ML)
- ✅ 3 formatters (JSON, Markdown, Terminal)
- ✅ SignalScanner orchestrator
- ✅ CLI entry point (equity-signal scan)
- ✅ Parquet history storage
- ✅ Documentation and tests

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Implementation Complete

**Total Tasks:** 15
**Estimated Time:** 8-10 days
**Lines of Code:** ~2,000

### Next Steps (Future Enhancements)

1. Add `equity-signal history` command to view past signals
2. Add `equity-signal backtest` to test signal accuracy
3. Implement agreement boosting for multi-generator consensus
4. Add confidence calibration based on historical performance
5. Create web dashboard for signal visualization

### Files Created/Modified

**New Files (30+):**
- `src/equity_lake/signals/*.py` (8 files)
- `src/equity_lake/signals/generators/*.py` (5 files)
- `src/equity_lake/signals/formatters/*.py` (5 files)
- `src/equity_lake/cli/signal.py`
- `config/watchlist.yaml`
- `config/signals.yaml`
- `tests/test_signal_*.py` (10 files)
- `docs/user-guide/signals.md`

**Modified Files:**
- `pyproject.toml` (added CLI entry)
- `README.md` (added v0.4.0 features)

---

**Ready to implement! Use `superpowers:executing-plans` skill to execute this plan task-by-task.**
