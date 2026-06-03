# Signal Scanner & Portfolio Watchlist Module

**Design Document**
**Date:** 2025-03-03
**Status:** ✅ Implemented
**Version:** 1.0

## 1. Overview & Goals

### Objective
Add a modular signal scanning system that generates buy/sell/hold signals for a config-driven watchlist, prioritizing backtest-based strategies, news sentiment, and ML predictions with confidence scores.

### Key Capabilities
1. **Watchlist Management**: YAML-based portfolio/watchlist configuration
2. **Signal Generation**: 3 signal types (backtest strategies, sentiment, ML predictions)
3. **Confidence Scoring**: 0-100 score + Buy/Sell/Hold recommendation
4. **Multi-Format Output**: JSON (machine-readable), Markdown (readable), Terminal tables (rich console)
5. **Scheduled Reports**: Cron-friendly CLI for daily signal generation
6. **Signal History**: Track past signals for performance analysis

### User Workflow
```bash
# 1. Define watchlist in config/watchlist.yaml
# 2. Define signal rules in config/signals.yaml
# 3. Run: equity-signal scan --format md > signals_$(date +%Y%m%d).md
# 4. Cron: 0 9 * * * equity-signal scan --format json > /signals/latest.json
```

---

## 2. Architecture

### Module Structure
```
src/equity_lake/signals/
├── __init__.py              # Public API exports
├── scanner.py               # Main orchestrator: scans watchlist, runs generators
├── generators/
│   ├── __init__.py
│   ├── base.py              # Base SignalGenerator class
│   ├── backtest.py          # Backtest strategy signals (E)
│   ├── sentiment.py         # News sentiment signals (D)
│   └── ml.py                # XGBoost prediction signals (B)
├── formatters/
│   ├── __init__.py
│   ├── base.py              # Base SignalFormatter class
│   ├── json.py              # JSON output
│   ├── markdown.py          # Markdown report
│   └── terminal.py          # Rich terminal tables
├── models.py                # Pydantic models: Signal, Watchlist, SignalConfig
└── history.py               # Signal history tracking (Parquet storage)
```

### Configuration Files
```
config/
├── watchlist.yaml           # User's tickers & metadata
└── signals.yaml             # Signal rules & thresholds
```

### New CLI Entry
```
src/equity_lake/cli/signal.py
```

### Design Principles
- **Generator Pattern**: Each signal type (backtest/sentiment/ML) is a pluggable generator
- **Formatter Pattern**: Output formats are swappable
- **Config-Driven**: All rules/thresholds in YAML, not hardcoded
- **Reuse Existing**: Leverage `backtesting/` module for strategy signals, `sentiment/` for news
- **Parquet History**: Store signal history in `data/signals/` with Hive partitioning

---

## 3. Key Components

### 3.1 Data Models (models.py)
```python
@dataclass
class Signal:
    ticker: str
    date: date
    signal_type: Literal["backtest", "sentiment", "ml"]
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float  # 0-100
    reasoning: str     # Human-readable explanation
    metadata: Dict[str, Any]  # Strategy-specific details

@dataclass
class Watchlist:
    name: str
    tickers: List[str]
    groups: Optional[Dict[str, List[str]]] = None  # e.g., {"tech": ["AAPL", "MSFT"]}

@dataclass
class SignalConfig:
    backtest: Dict[str, Any]      # Strategy names, lookback periods
    sentiment: Dict[str, Any]     # Thresholds, sources
    ml: Dict[str, Any]            # Model paths, confidence thresholds
```

### 3.2 SignalScanner (scanner.py)
```python
class SignalScanner:
    """Main orchestrator that scans watchlist and generates signals."""

    def __init__(self, config: SignalConfig, watchlist: Watchlist):
        self.generators = [
            BacktestSignalGenerator(config.backtest),
            SentimentSignalGenerator(config.sentiment),
            MLPredictionSignalGenerator(config.ml),
        ]
        self.formatters = {
            "json": JSONFormatter(),
            "md": MarkdownFormatter(),
            "table": TerminalFormatter(),
        }

    def scan(self, date: Optional[date] = None) -> List[Signal]:
        """Scan all tickers and return aggregated signals."""
        pass

    def format_signals(self, signals: List[Signal], format: str) -> str:
        """Format signals for output."""
        pass

    def save_history(self, signals: List[Signal]):
        """Append signals to Parquet history."""
        pass
```

### 3.3 Signal Generators (generators/)
```python
class SignalGenerator(ABC):
    """Base class for all signal generators."""

    @abstractmethod
    def generate(self, ticker: str, date: date) -> Optional[Signal]:
        """Generate signal for a single ticker."""
        pass

class BacktestSignalGenerator(SignalGenerator):
    """Generate signals based on backtest strategies."""
    # Reuses existing backtesting/strategy/* classes
    # Returns BUY when strategy enters position, SELL when exits
    # Includes historical win rate in metadata

class SentimentSignalGenerator(SignalGenerator):
    """Generate signals from news sentiment analysis."""
    # Reuses existing sentiment/analyzer.py
    # BUY when sentiment > threshold, SELL when < threshold
    # Includes article count and sentiment score in metadata

class MLPredictionSignalGenerator(SignalGenerator):
    """Generate signals from XGBoost price forecasts."""
    # Reuses existing ml/forecasting.py
    # BUY when predicted return > threshold + confidence > min
    # Includes prediction horizon and feature importance in metadata
```

### 3.4 Signal Formatters (formatters/)
```python
class SignalFormatter(ABC):
    @abstractmethod
    def format(self, signals: List[Signal]) -> str:
        pass

class MarkdownFormatter(SignalFormatter):
    """Generate readable Markdown report with tables by signal type."""
    # Sections: Summary by Action, Backtest Signals, Sentiment Signals, ML Signals

class JSONFormatter(SignalFormatter):
    """Machine-readable JSON output."""
    # Structured for parsing: list of Signal objects

class TerminalFormatter(SignalFormatter):
    """Rich console tables with colors (green=BUY, red=SELL, yellow=HOLD)."""
    # Uses tabulate or rich library
```

---

## 4. Data Flow

### 4.1 Signal Generation Flow
```
1. User runs: equity-signal scan --format md --date 2024-12-01
   ↓
2. CLI loads:
   - config/watchlist.yaml → Watchlist object
   - config/signals.yaml → SignalConfig object
   ↓
3. SignalScanner initialized with 3 generators:
   - BacktestSignalGenerator (from backtesting/strategy/)
   - SentimentSignalGenerator (from sentiment/)
   - MLPredictionSignalGenerator (from ml/)
   ↓
4. For each ticker in watchlist:
   a) Query historical data from DuckDB (last 90 days for features)
   b) Run each generator in parallel:
      - Backtest: Check strategy entry/exit conditions
      - Sentiment: Fetch news, analyze sentiment score
      - ML: Load model, predict next-day return
   c) Collect 0-3 signals per ticker
   ↓
5. Aggregate all signals (watchlist_size * 3 generators max)
   ↓
6. Format output:
   - Markdown: Generate report with tables by action/signal type
   - JSON: Serialize to structured array
   - Terminal: Print colored tables
   ↓
7. Save to history:
   - Append to data/signals/date=YYYY-MM-DD/signals.parquet
   ↓
8. Print/return formatted output
```

### 4.2 Dependencies on Existing Modules
```
signals/              (NEW)
  ├─→ backtesting/   (reuse: BacktestEngine, Strategy classes)
  ├─→ sentiment/     (reuse: SentimentAnalyzer)
  ├─→ ml/            (reuse: PriceForecaster, XGBoost models)
  ├─→ storage/duckdb.py  (reuse: query historical OHLCV data)
  └─→ config/        (reuse: load YAML configs)
```

### 4.3 Cron Integration Example
```bash
# crontab -e
# Daily signal scan at 9:00 AM, output all formats
0 9 * * * cd /path/to/equity-lake && equity-signal scan --format json > data/signals/latest.json
0 9 * * * cd /path/to/equity-lake && equity-signal scan --format md > data/signals/$(date +\%Y\%m\%d).md
```

---

## 5. Configuration

### 5.1 config/watchlist.yaml
```yaml
name: "My Portfolio"
description: "Core holdings and watchlist"

# Simple list or grouped
tickers:
  - AAPL
  - GOOGL
  - MSFT
  - TSLA
  - NVDA
  - 600000.SS  # China A-shares
  - 0700.HK    # Hong Kong

# Optional grouping for reporting
groups:
  tech:
    - AAPL
    - GOOGL
    - MSFT
    - NVDA
  china:
    - 600000.SS
    - 000001.SZ
  hk:
    - 0700.HK
    - 9988.HK

# Optional metadata
metadata:
  benchmark: "SPY"
  created: "2024-12-01"
```

### 5.2 config/signals.yaml
```yaml
# Backtest strategy signals
backtest:
  enabled: true
  strategies:
    - name: "momentum"
      lookback_days: 20
      buy_threshold: 0.02  # 2% above SMA
      sell_threshold: -0.01
    - name: "mean_reversion"
      lookback_days: 10
      rsi_buy: 30
      rsi_sell: 70

  # Minimum historical win rate to trust signal
  min_win_rate: 0.55

# News sentiment signals
sentiment:
  enabled: true
  sources:
    - "yahoo"
    - "benzinga"

  # Sentiment thresholds (-1 to 1)
  buy_threshold: 0.5
  sell_threshold: -0.3

  # Min articles to generate signal
  min_articles: 3

  # Lookback period (days)
  lookback_days: 7

# ML prediction signals
ml:
  enabled: true
  model_path: "data/models/xgboost_price_forecaster.pkl"

  # Prediction settings
  horizon_days: 5  # Predict 5-day return

  # Generate BUY when predicted return > this AND confidence > min_confidence
  buy_return_threshold: 0.03  # 3%
  sell_return_threshold: -0.02  # -2%

  # Min confidence score (0-100)
  min_confidence: 60

# Aggregation: How to combine multiple signals?
aggregation:
  # If 2+ generators agree, boost confidence by this %
  agreement_boost: 10

  # If all 3 agree, boost by this %
  unanimous_boost: 20
```

---

## 6. CLI Interface

### 6.1 Command Structure
```bash
equity-signal scan              # Main command: scan watchlist & generate signals
equity-signal history           # View historical signals
equity-signal backtest          # Test signal accuracy
equity-signal watchlist         # Watchlist management (optional v2)
```

### 6.1.1 equity-signal scan (Primary)
```bash
# Basic usage
equity-signal scan

# Specify output format
equity-signal scan --format json      # JSON output
equity-signal scan --format md        # Markdown report
equity-signal scan --format table     # Terminal tables (default)

# Specify date (default: yesterday)
equity-signal scan --date 2024-12-01

# Custom config paths
equity-signal scan --watchlist config/my_stocks.yaml
equity-signal scan --config config/my_signals.yaml

# Save to file
equity-signal scan --format md --output signals_20241201.md

# Verbose mode
equity-signal scan --verbose

# Dry run (don't save history)
equity-signal scan --dry-run

# Filter by signal type
equity-signal scan --signal-types backtest,sentiment  # Skip ML

# Filter by action only
equity-signal scan --actions BUY,SELL  # Skip HOLD signals
```

### 6.1.2 equity-signal history
```bash
# View recent signals
equity-signal history --days 7

# View specific ticker
equity-signal history --ticker AAPL --days 30

# View signal performance
equity-signal history --performance  # Show accuracy stats
```

### 6.1.3 equity-signal backtest
```bash
# Backtest signal accuracy over period
equity-signal backtest --start 2024-01-01 --end 2024-11-30

# Compare signal types
equity-signal backtest --compare-types

# Generate performance report
equity-signal backtest --output signal_performance.md
```

### 6.2 Help Output Example
```
$ equity-signal scan --help

Usage: equity-signal scan [OPTIONS]

  Scan watchlist and generate buy/sell/hold signals.

Options:
  --format [json|md|table]  Output format (default: table)
  --date YYYY-MM-DD         Target date (default: yesterday)
  --watchlist PATH          Watchlist config path
  --config PATH             Signal config path
  --output PATH             Save output to file
  --signal-types TEXT       Comma-separated: backtest,sentiment,ml
  --actions TEXT            Comma-separated: BUY,SELL,HOLD
  --dry-run                Don't save to history
  --verbose, -v             Enable verbose logging
  --help                    Show this message
```

---

## 7. Error Handling

### 7.1 Graceful Degradation
```python
# If one generator fails, others continue
try:
    backtest_signals = BacktestSignalGenerator().generate(ticker, date)
except Exception as e:
    logger.warning(f"Backtest generator failed for {ticker}: {e}")
    backtest_signals = []  # Continue with other generators

# If one ticker fails, continue with rest of watchlist
for ticker in watchlist.tickers:
    try:
        signals = scan_ticker(ticker)
        all_signals.extend(signals)
    except Exception as e:
        logger.error(f"Failed to scan {ticker}: {e}")
        continue
```

### 7.2 Validation
```python
# Validate configs at startup
def validate_config(config: SignalConfig):
    if config.backtest["min_win_rate"] < 0 or > 1:
        raise ValueError("min_win_rate must be between 0 and 1")

    if config.sentiment["buy_threshold"] < -1 or > 1:
        raise ValueError("sentiment threshold must be -1 to 1")

    # Check if watchlist tickers exist in data
    missing_tickers = validate_tickers_in_data(watchlist.tickers)
    if missing_tickers:
        logger.warning(f"Tickers not found in data: {missing_tickers}")
```

### 7.3 Error Recovery
```python
# Handle missing data gracefully
def generate_with_fallback(ticker, date):
    try:
        signal = generator.generate(ticker, date)
    except DuckDBCatalogError:  # No data for ticker
        logger.warning(f"No data found for {ticker}, skipping")
        return None
    except ModelNotFoundError:
        logger.warning("ML model not found, skipping ML signals")
        return None
```

---

## 8. Testing Strategy

### 8.1 Unit Tests
```python
# tests/test_signal_generators.py
def test_backtest_generator_entry_signal():
    """Test BUY signal generation when strategy conditions met."""
    generator = BacktestSignalGenerator(config)
    signal = generator.generate("AAPL", date(2024, 12, 1))
    assert signal.action == "BUY"
    assert signal.confidence > 70

def test_sentiment_generator_threshold():
    """Test sentiment below sell threshold generates SELL."""
    generator = SentimentSignalGenerator(config)
    # Mock negative sentiment
    signal = generator.generate("TSLA", date(2024, 12, 1))
    assert signal.action == "SELL"

# tests/test_formatters.py
def test_markdown_formatter():
    """Test Markdown output format."""
    formatter = MarkdownFormatter()
    signals = [Signal(...)]
    output = formatter.format(signals)
    assert "# Signal Report" in output
    assert "| AAPL | BUY | 85 |" in output
```

### 8.2 Integration Tests
```python
# tests/test_signal_scanner.py (marked @integration)
def test_full_scan_workflow():
    """Test complete scan with real data."""
    scanner = SignalScanner(config, watchlist)
    signals = scanner.scan(date(2024, 12, 1))

    assert len(signals) > 0
    assert all(s.confidence >= 0 and s.confidence <= 100 for s in signals)

def test_save_and_load_history():
    """Test Parquet history persistence."""
    scanner = SignalScanner(config, watchlist)
    signals = scanner.scan()
    scanner.save_history(signals)

    # Verify saved
    loaded = load_history_signals(date(2024, 12, 1))
    assert len(loaded) == len(signals)
```

### 8.3 Backtest Signal Performance
```python
# tests/test_signal_accuracy.py
def test_backtest_signal_win_rate():
    """Test that backtest signals meet minimum win rate."""
    scanner = SignalScanner(config, watchlist)
    results = backtest_signals(scanner, start="2024-01-01", end="2024-11-30")

    win_rate = results["win_rate"]
    assert win_rate >= config.backtest["min_win_rate"]
```

---

## 9. Implementation Notes

### Priority Order
1. **Phase 1**: Core scanner + 1 generator (backtest) + 1 formatter (JSON)
2. **Phase 2**: Add sentiment + ML generators
3. **Phase 3**: Add Markdown + Terminal formatters
4. **Phase 4**: Signal history + backtest performance tracking
5. **Phase 5**: Watchlist management CLI (equity-signal watchlist)

### Estimated Effort
- **Phase 1**: 2-3 days (MVP usable)
- **Phase 2**: 2 days (all generators working)
- **Phase 3**: 1 day (rich outputs)
- **Phase 4**: 2 days (history & performance)
- **Phase 5**: 1-2 days (nice-to-have features)

**Total**: 8-10 days for full implementation

### Dependencies to Add
```toml
# Optional: for rich terminal tables
[project.optional-dependencies]
signals = [
    "rich>=13.0.0",     # Terminal formatting
    "tabulate>=0.9.0",  # Table formatting
]
```

---

## 10. Future Enhancements (Out of Scope)
- Web dashboard for signal visualization
- Real-time signal alerts (email/Slack/webhook)
- Multi-portfolio support (separate watchlists)
- Signal attribution (which generators contributed most)
- Auto-trading integration (execute trades based on signals)
- Multi-factor models (combine signals mathematically)
