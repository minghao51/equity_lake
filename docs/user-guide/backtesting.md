# Backtesting Framework Usage Guide

This guide shows how to use the equity_lake backtesting framework for testing trading strategies.
`VectorBacktestEngine` is the supported engine surface.

## CLI Usage

The `equity` CLI exposes three backtesting commands. `--start-date` and `--end-date`
are required on all three; run `equity <command> --help` for the full flag list.

### `equity backtest`

Flat top-level command sharing the strategy registry with `equity report backtest`:

```bash
dotenvx run -- uv run equity backtest --strategy momentum --tickers AAPL,MSFT \
  --start-date 2024-01-01 --end-date 2024-12-31 --output data/backtest.json
```

Key flags: `--strategy/-s` (default `momentum`), `--tickers/-t`, `--initial-cash`
(default 100,000), `--output/-o` for a JSON result.

### `equity report backtest`

Runs a single backtest under one cost regime and writes its report artifacts
(equity curve, drawdown, metrics, trades) under
`data/findings/<strategy>__<regime>/`:

```bash
dotenvx run -- uv run equity report backtest --strategy trend_following \
  --start-date 2024-01-01 --end-date 2024-12-31 --cost-regime high
```

`--cost-regime` selects `zero | realistic | high` (default `realistic`).

### `equity arena run`

Runs the full strategy arena — strategies `momentum | mean_reversion | trend_following`
× cost regimes `zero | realistic | high` — and emits FindingCards plus per-run
artifacts under `data/findings/`:

```bash
dotenvx run -- uv run equity arena run --start-date 2024-01-01 --end-date 2024-12-31
```

`--strategies` and `--cost-regimes` accept comma-separated subsets (default: all).

### Cost regimes

Costs are modeled as per-trade fee and tax ratios (`COST_REGIMES` in
`src/equity_lake/backtesting/arena.py`; defaults in `engine.py`): the realistic
regime uses
`DEFAULT_FEE_RATIO = 0.001425` (0.1425%) and `DEFAULT_TAX_RATIO = 0.003` (0.3%).

- `zero` — no costs
- `realistic` — default fee + default tax (the values above)
- `high` — 0.5% fee + default tax

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

### Trend Following

1. **SMACrossoverStrategy** - Moving-average crossover (`fast_period` 50, `slow_period` 200, `use_ema`)

### Momentum

2. **CrossSectionalMomentumStrategy** - Rank stocks by past returns (`lookback_days` 252, `skip_days` 21, `top_pct`/`bottom_pct` 0.3, `rebalance_days` 21, `long_only`, `min_stocks` 10)

### Mean Reversion

3. **BBMeanReversionStrategy** - Bollinger Bands mean reversion (`period` 20, `num_std` 2.0, `position_size` 0.95, `use_trend_filter`, `stop_loss_pct` 0.05)

## Data Loading

### Load Data for Custom Analysis

```python
from equity_lake.backtesting import BacktestDataLoader
from datetime import date

loader = BacktestDataLoader()

# load() always returns long-format polars (the format the engine consumes)
data = loader.load(
    tickers=["AAPL", "MSFT", "GOOGL"],
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    markets=["us"],          # default: all markets
    columns=None,            # default: ticker/date/open/high/low/close/volume
    fill_method="ffill",     # forward-fill missing trading days
)

# Data structure (one row per ticker/date):
#   ticker | date       | open  | close | volume
#   AAPL   | 2020-01-01 | 75.0  | 76.0  | 1000000
#   MSFT   | 2020-01-01 | 150.0 | 151.0 | 900000

loader.close()  # or use `with BacktestDataLoader() as loader:` (context manager)
```

Pivot to wide format for custom analysis with polars:

```python
close_wide = data.pivot("ticker", index="date", values="close")
```

## Creating Custom Strategies

### Strategy Template

```python
import polars as pl

from equity_lake.backtesting.strategy.base import BaseStrategy


class MyCustomStrategy(BaseStrategy):
    """My custom trading strategy."""

    def __init__(self, params: dict | None = None):
        # Set default parameters
        default_params = {
            "window": 10,
        }
        merged_params = {**default_params, **(params or {})}
        super().__init__(merged_params)

    def initialize(self, data: pl.DataFrame) -> None:
        """
        Pre-compute indicators from long-format data.

        data columns: date, ticker, open, high, low, close, volume
        """
        window = self.get_param("window")
        self.indicators["sma"] = data.with_columns(
            pl.col("close").rolling_mean(window_size=window).over("ticker").alias("sma")
        ).select("date", "ticker", "sma")

    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        """
        Return target portfolio weights.

        Returns:
            DataFrame with columns [date, ticker, weight] where weight
            is 0.0 (no position) to 1.0 (full allocation)
        """
        sma = self.indicators["sma"]
        return (
            data.join(sma, on=["date", "ticker"], how="left")
            .with_columns(
                pl.when(pl.col("close") > pl.col("sma"))
                .then(1.0)
                .otherwise(0.0)
                .alias("weight")
            )
            .select("date", "ticker", "weight")
        )
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
# Equity curve is a polars Series (pl.Series)
equity_curve = result.equity_curve

# Plot equity curve (requires matplotlib)
import matplotlib.pyplot as plt

values = equity_curve.to_list()
plt.figure(figsize=(12, 6))
plt.plot(values)
plt.title("Portfolio Value Over Time")
plt.xlabel("Trading days")
plt.ylabel("Portfolio Value ($)")
plt.grid(True)
plt.show()

# Calculate drawdowns (polars Series)
cummax = equity_curve.cum_max()
drawdown = (equity_curve - cummax) / cummax
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

# Check before running: probe coverage with a thin column slice
probe = loader.load(
    tickers=["AAPL", "MSFT"],
    start_date=date(2019, 1, 1),
    end_date=date(2024, 12, 31),
    markets=["us"],
    columns=["ticker", "date"],
)
min_date, max_date = probe["date"].min(), probe["date"].max()
available_tickers = probe["ticker"].unique().to_list()

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

## Notebooks and Further Examples

The `notebooks/` directory contains runnable walkthroughs of this framework:

- `notebooks/08-backtesting.ipynb` — guided tour of the engine, strategies, and results
- `notebooks/11-strategy-lab.ipynb` — interactive strategy lab over the lake

For a quick sanity check, re-run the [Quick Start](#1-basic-backtest-example) snippet
above, or work through the Data Loading section earlier in this guide against a
seeded lake (`make demo`).

### Test Suite

Backtesting regressions are covered by the unit suite:

```bash
uv run pytest tests/unit -k backtest
```

## Getting Help

1. **Check notebooks**: `notebooks/` directory (start with `08-backtesting.ipynb` and `11-strategy-lab.ipynb`)
2. **Review source code**: `src/equity_lake/backtesting/`
3. **Check the CLI surface**: `equity backtest --help`, `equity report backtest --help`, `equity arena run --help`
4. **Open the archive**: `docs/archive/` for historical design and test notes

## Next Steps

1. Start with simple strategies (SMA Crossover)
2. Progress to more complex strategies
3. Create your own custom strategies
4. Optimize parameters using grid search
5. Validate with walk-forward analysis
6. Consider transaction costs for realism
