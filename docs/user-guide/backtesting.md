# Backtesting Framework Usage Guide

This guide shows how to use the equity_lake backtesting framework for testing trading strategies.
`VectorBacktestEngine` is the supported engine surface.

## Quick Start

### 1. Basic Backtest Example

```python
from equity_lake.backtesting import BacktestDataLoader, VectorBacktestEngine
from equity_lake.backtesting.strategy import SMACrossoverStrategy
from datetime import date

# Initialize strategy
strategy = SMACrossoverStrategy(params={
    "fast_period": 50,
    "slow_period": 200,
    "use_ema": False
})

# Create backtest engine
engine = VectorBacktestEngine(
    strategy=strategy,
    tickers=["AAPL", "MSFT", "GOOGL"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    initial_cash=100_000,
    markets=["us"]
)

# Run backtest
result = engine.run()

# View results
print(result.summary())

# Access metrics
print(f"Total Return: {result.total_return:.2%}")
print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
print(f"Max Drawdown: {result.max_drawdown:.2%}")

# Access trades
for trade in result.trades[:10]:  # First 10 trades
    print(f"{trade['date']}: {trade['action']} {trade['shares']} shares of {trade['ticker']} @ ${trade['price']:.2f}")
```

### 2. Using Different Strategies

#### SMA Crossover (Trend Following)

```python
from equity_lake.backtesting.strategy import SMACrossoverStrategy

strategy = SMACrossoverStrategy(params={
    "fast_period": 50,      # Fast moving average
    "slow_period": 200,     # Slow moving average
    "use_ema": False,       # Use simple MA (True for exponential)
    "use_adx_filter": False # Only trade when ADX > 25
})

engine = VectorBacktestEngine(
    strategy=strategy,
    tickers=["AAPL", "MSFT"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    initial_cash=100_000
)
result = engine.run()
```

#### Cross-Sectional Momentum

```python
from equity_lake.backtesting.strategy import CrossSectionalMomentumStrategy

strategy = CrossSectionalMomentumStrategy(params={
    "lookback_days": 252,    # 1 year lookback for returns
    "skip_days": 21,         # Skip 1 month between lookback and trading
    "top_pct": 0.3,          # Long top 30% of stocks
    "bottom_pct": 0.3,       # Short bottom 30% (if long_only=False)
    "rebalance_days": 21,    # Rebalance monthly
    "long_only": True,       # Only long positions
    "min_stocks": 10         # Minimum 10 stocks required
})

# Works best with 20+ stocks
engine = VectorBacktestEngine(
    strategy=strategy,
    tickers=["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", ...],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    initial_cash=100_000
)
result = engine.run()
```

#### Bollinger Bands Mean Reversion

```python
from equity_lake.backtesting.strategy import BBMeanReversionStrategy

strategy = BBMeanReversionStrategy(params={
    "period": 20,              # 20-day Bollinger Bands
    "num_std": 2.0,            # 2 standard deviations
    "use_trend_filter": True,  # Only trade when price > 200 MA
    "stop_loss_pct": 0.05      # 5% stop loss
})

engine = VectorBacktestEngine(
    strategy=strategy,
    tickers=["AAPL", "MSFT"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    initial_cash=100_000
)
result = engine.run()
```

## Available Strategies

### Trend Following Strategies

1. **SMACrossoverStrategy** - Moving average crossover
2. **DonchianBreakoutStrategy** - Donchian channel breakout
3. **MACDStrategy** - MACD line crossover
4. **AdaptiveTrendStrategy** - SMA + ADX filter + ATR stops

### Momentum Strategies

1. **CrossSectionalMomentumStrategy** - Rank stocks by past returns
2. **TimeSeriesMomentumStrategy** - Long/short based on individual asset momentum

### Mean Reversion Strategies

1. **BBMeanReversionStrategy** - Bollinger Bands mean reversion
2. **RSIMeanReversionStrategy** - RSI oversold/overbought
3. **CombinedMeanReversionStrategy** - BB + RSI combined

## Data Loading

### Load Data for Custom Analysis

```python
from equity_lake.backtesting import BacktestDataLoader
from datetime import date

loader = BacktestDataLoader()

# Load wide-format data (for backtesting)
data = loader.load(
    tickers=["AAPL", "MSFT", "GOOGL"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    markets=["us"],
    wide_format=True
)

# Data structure: MultiIndex columns (ticker, field)
#   Index: date
#   Columns: (AAPL, close), (AAPL, volume), (MSFT, close), ...

# Extract close prices
close_prices = data.xs('close', level='field', axis=1)

# Load long-format data (for analysis)
data_long = loader.load(
    tickers=["AAPL", "MSFT"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    wide_format=False
)

# Data structure:
#   ticker | date       | open  | close | volume
#   AAPL   | 2020-01-01 | 75.0  | 76.0  | 1000000
#   MSFT   | 2020-01-01 | 150.0 | 151.0 | 900000

loader.close()
```

### Check Available Data

```python
from equity_lake.backtesting import BacktestDataLoader

loader = BacktestDataLoader()

# Get available tickers in US market
us_tickers = loader.get_available_tickers("us")
print(f"US tickers: {len(us_tickers)}")

# Get date range for a specific ticker
min_date, max_date = loader.get_date_range("us", "AAPL")
print(f"AAPL data: {min_date} to {max_date}")

# Get overall market date range
min_date, max_date = loader.get_date_range("us")
print(f"US market data: {min_date} to {max_date}")

loader.close()
```

## Creating Custom Strategies

### Strategy Template

```python
from equity_lake.backtesting.strategy.base import BaseStrategy
import pandas as pd

class MyCustomStrategy(BaseStrategy):
    """My custom trading strategy."""

    def __init__(self, params=None):
        # Set default parameters
        default_params = {
            "param1": 10,
            "param2": 0.5,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pd.DataFrame) -> None:
        """
        Initialize strategy with historical data.

        Use this to pre-compute indicators.
        """
        # Extract close prices
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data.xs('close', level='field', axis=1)
        else:
            close_df = data

        # Compute your indicators
        self.indicators['close'] = close_df
        self.indicators['my_indicator'] = close_df.rolling(
            window=self.get_param('param1')
        ).mean()

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate entry and exit signals.

        Returns:
            DataFrame with 'entry' and 'exit' columns (boolean)
        """
        close_df = self.indicators['close']
        indicator = self.indicators['my_indicator']

        # Generate entry signals
        entry_signals = (close_df > indicator).any(axis=1)

        # Generate exit signals
        exit_signals = (close_df < indicator).any(axis=1)

        return pd.DataFrame({
            'entry': entry_signals,
            'exit': exit_signals,
        })
```

### Using Custom Strategy

```python
from equity_lake.backtesting import VectorBacktestEngine
from my_custom_strategies import MyCustomStrategy

strategy = MyCustomStrategy(params={"param1": 20, "param2": 0.3})

engine = VectorBacktestEngine(
    strategy=strategy,
    tickers=["AAPL", "MSFT"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    initial_cash=100_000
)

result = engine.run()
print(result.summary())
```

## Analyzing Results

### Access Performance Metrics

```python
result = engine.run()

# All available metrics
metrics = result.metrics
print(f"Total Return: {metrics['total_return']:.2%}")
print(f"CAGR: {metrics['cagr']:.2%}")
print(f"Volatility: {metrics['volatility']:.2%}")
print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
print(f"Max Drawdown: {metrics['max_drawdown']:.2%}")
print(f"Win Rate: {metrics['win_rate']:.1%}")
print(f"Number of Trades: {metrics['num_trades']}")
```

### Access Equity Curve

```python
# Equity curve is a pandas Series
equity_curve = result.equity_curve

# Plot equity curve (requires matplotlib)
import matplotlib.pyplot as plt

equity_curve.plot(figsize=(12, 6), title="Portfolio Value Over Time")
plt.xlabel("Date")
plt.ylabel("Portfolio Value ($)")
plt.grid(True)
plt.show()

# Calculate drawdowns
cummax = equity_curve.cummax()
drawdown = (equity_curve - cummax) / cummax

# Plot drawdowns
drawdown.plot(figsize=(12, 6), title="Drawdown Over Time")
plt.xlabel("Date")
plt.ylabel("Drawdown")
plt.grid(True)
plt.show()
```

### Analyze Trades

```python
# Convert trades to DataFrame
import pandas as pd

trades_df = pd.DataFrame(result.trades)

# View first trades
print(trades_df.head(10))

# Analyze by ticker
print(trades_df.groupby('ticker')['action'].value_counts())

# Calculate trade statistics
buy_trades = trades_df[trades_df['action'] == 'BUY']
sell_trades = trades_df[trades_df['action'] == 'SELL']

print(f"Total buys: {len(buy_trades)}")
print(f"Total sells: {len(sell_trades)}")
print(f"Total volume traded: ${trades_df['value'].sum():,.2f}")
```

## Multi-Market Backtesting

```python
# Test across multiple markets
strategy = SMACrossoverStrategy(params={
    "fast_period": 50,
    "slow_period": 200
})

# Backtest on US market
us_result = VectorBacktestEngine(
    strategy=strategy,
    tickers=["AAPL", "MSFT", "GOOGL"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    initial_cash=100_000,
    markets=["us"]
).run()

# Backtest on China market
cn_result = VectorBacktestEngine(
    strategy=strategy,
    tickers=["000001", "600000", "600519"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    initial_cash=100_000,
    markets=["cn"]
).run()

# Compare results
print(f"US Return: {us_result.total_return:.2%}, Sharpe: {us_result.sharpe_ratio:.2f}")
print(f"CN Return: {cn_result.total_return:.2%}, Sharpe: {cn_result.sharpe_ratio:.2f}")
```

## Parameter Optimization

### Grid Search Example

```python
from itertools import product

# Define parameter grid
fast_periods = [10, 20, 50]
slow_periods = [50, 100, 200]

results = []

for fast, slow in product(fast_periods, slow_periods):
    if fast >= slow:
        continue  # Skip invalid combinations

    strategy = SMACrossoverStrategy(params={
        "fast_period": fast,
        "slow_period": slow
    })

    engine = VectorBacktestEngine(
        strategy=strategy,
        tickers=["AAPL", "MSFT", "GOOGL"],
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
        initial_cash=100_000
    )

    result = engine.run()

    results.append({
        "fast_period": fast,
        "slow_period": slow,
        "total_return": result.total_return,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown": result.max_drawdown,
        "num_trades": result.metrics['num_trades']
    })

# Convert to DataFrame and analyze
import pandas as pd

results_df = pd.DataFrame(results)
print(results_df.sort_values('sharpe_ratio', ascending=False))
```

## Best Practices

### 1. Data Quality

- Always check data availability before running backtests
- Use sufficient history for strategy warm-up (e.g., 200 days for 200-day MA)
- Verify date ranges cover your test period

```python
loader = BacktestDataLoader()

# Check before running
tickers = loader.get_available_tickers("us")
min_date, max_date = loader.get_date_range("us")

start_date = max(min_date, date(2020, 1, 1))  # Ensure data exists
end_date = min(max_date, date(2024, 12, 31))

loader.close()
```

### 2. Strategy Selection

- **Trend following**: Use in strong bull markets
- **Mean reversion**: Use in range-bound markets
- **Momentum**: Use with diversified portfolio (20+ stocks)

### 3. Parameter Tuning

- Use walk-forward analysis for robustness
- Avoid overfitting (keep parameters simple)
- Test out-of-sample performance

### 4. Performance Evaluation

- Look at multiple metrics (not just returns)
- Consider risk-adjusted returns (Sharpe ratio)
- Analyze drawdowns carefully
- Check trade frequency (too many = high transaction costs)

### 5. Common Pitfalls

- **Look-ahead bias**: Using future data in signals
- **Survivorship bias**: Only testing current winners
- **Ignoring costs**: Transaction costs eat profits
- **Overfitting**: Too complex for historical period
- **Insufficient data**: Less than 3 years is risky

## Running the Tests

### Quick Validation

```bash
# Run quick validation check
uv run python examples/quick_test.py
```

### Full Test Suite

```bash
# Run comprehensive backtesting tests
uv run python examples/backtest_demo.py
```

## Getting Help

1. **Check examples**: `examples/` directory
2. **Review source code**: `src/equity_lake/backtesting/`
3. **Check logs**: `logs/backtest_cache/` for data loader issues
4. **Open the archive**: `docs/developer/history/backtesting/` for historical design and test notes

## Next Steps

1. Start with simple strategies (SMA Crossover)
2. Progress to more complex strategies
3. Create your own custom strategies
4. Optimize parameters using grid search
5. Validate with walk-forward analysis
6. Consider transaction costs for realism
