# ML/AI Analytics Platform Design

**Project:** Equity EOD Data Pipeline - Machine Learning Extension
**Date:** 2026-01-24
**Status:** Design Phase
**Author:** AI-Assisted Design (Claude Code)

---

## Executive Summary

This document outlines the design for a comprehensive ML/AI analytics platform that extends the existing equity EOD data pipeline. The platform will provide:

1. **Price forecasting** using XGBoost models with interpretable features
2. **Risk analysis** including VaR, correlation matrices, and Monte Carlo simulations
3. **Interactive visualization** via Streamlit dashboard for exploration and insights

**Target Users:** Personal traders, quantitative researchers, and learners
**Approach:** Start simple with interpretable models (XGBoost), iterate to advanced methods
**Timeline:** 10 weeks (phased implementation)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Feature Engineering Layer](#2-feature-engineering-layer)
3. [Price Forecaster Component](#3-price-forecaster-component)
4. [Risk Analyzer Component](#4-risk-analyzer-component)
5. [Streamlit Dashboard](#5-streamlit-dashboard)
6. [Data Flow & Integration](#6-data-flow--integration)
7. [Implementation Phases](#7-implementation-phases)
8. [Dependencies & Libraries](#8-dependencies--libraries)
9. [Testing & Validation](#9-testing--validation)

---

## 1. Architecture Overview

The platform extends the existing EOD pipeline with three new layers:

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Existing Pipeline                         │
│  Daily EOD Ingestion → Hive Partitioned Parquet → DuckDB     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              1. Feature Engineering Layer                     │
│  - Technical indicators (RSI, MACD, Bollinger Bands)         │
│  - Time-based features (seasonality, trends)                 │
│  - Return features (momentum, volatility)                    │
│  - Cross-asset features (beta, correlation)                  │
│  Output: data/lake/features/date=*/                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    2. Modeling Layer                          │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │ Price Forecaster │  │  Risk Analyzer   │                 │
│  │ - XGBoost model  │  │ - VaR/CVaR       │                 │
│  │ - Next-day ret.  │  │ - Correlation    │                 │
│  │ - Feature imp.   │  │ - Monte Carlo    │                 │
│  └──────────────────┘  └──────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              3. Visualization Layer                          │
│  Streamlit Dashboard (http://localhost:8501)                 │
│  - Forecast Explorer Page                                    │
│  - Risk Analysis Page                                        │
│  - Correlation Matrix Page                                   │
│  - Portfolio Simulator Page                                  │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow Summary

```
Daily EOD Parquet
    ↓
Feature Engineering (20-30 features per ticker)
    ↓
ML Models (XGBoost forecaster + Risk metrics)
    ↓
Predictions (Parquet with Hive partitioning)
    ↓
Streamlit Dashboard (real-time exploration)
```

### Key Design Decisions

**Why XGBoost?**
- ✅ Interpretable (feature importance, SHAP values)
- ✅ Fast training on daily data
- ✅ Handles missing values well
- ✅ State-of-the-art for tabular data
- ✅ Easy to explain predictions

**Why Streamlit over Dash?**
- ✅ Simpler syntax (pure Python)
- ✅ Faster development (no HTML/CSS/JS)
- ✅ Built-in data widgets
- ✅ Better for personal projects/learning
- ✅ Easier to share and deploy

**Why Hive Partitioning for Features/Predictions?**
- ✅ Consistent with existing data architecture
- ✅ Efficient time-range queries (partition pruning)
- ✅ Scalable to multi-year data
- ✅ DuckDB native support

---

## 2. Feature Engineering Layer

### Component: `FeatureEngineer` class

**Location:** `src/equity_lake/feature_engineering.py`

This module transforms raw OHLCV data into ML-ready features using the existing DuckDB views for efficient data access.

### Feature Categories

#### 2.1 Technical Indicators (Momentum & Volatility)

**Momentum Features:**
- **RSI (14-period)**: Relative Strength Index
  - Range: 0-100, overbought >70, oversold <30
  - Formula: `100 - (100 / (1 + RS))` where RS = avg gain / avg loss

- **MACD (12, 26, 9)**: Moving Average Convergence Divergence
  - MACD line = EMA(12) - EMA(26)
  - Signal line = EMA(9, MACD)
  - Histogram = MACD - Signal

- **Rate of Change (5, 10, 20-day)**: Price velocity
  - Formula: `(close - close_n_days_ago) / close_n_days_ago`

**Volatility Features:**
- **ATR (14-period)**: Average True Range
  - Measures price volatility
  - Accounts for gaps (high-low vs close-prev_close)

- **Rolling Std Dev (20, 60-day)**: Historical volatility
  - `std(close, window=20)` annualized: `× sqrt(252)`

- **Bollinger Bands (20-day, 2σ)**: Price envelopes
  - Upper = SMA(20) + 2×std(20)
  - Lower = SMA(20) - 2×std(20)
  - Bandwidth = (Upper - Lower) / SMA(20)

#### 2.2 Time-Based Features

**Seasonality:**
- Day of week (Mon-Fri)
- Day of month (1-31)
- Month (1-12)
- Quarter (1-4)
- Days to month-end (window dressing effect)
- Trading day of month (1-22)

**Rationale:** Markets exhibit seasonal patterns (e.g., "January effect", "sell in May")

#### 2.3 Return Features

**Lagged Returns:**
- 1-day return: `(close_t / close_t-1) - 1`
- 5-day return: `(close_t / close_t-5) - 1`
- 10-day return: `(close_t / close_t-10) - 1`
- 20-day return: `(close_t / close_t-20) - 1`

**Intraday Patterns:**
- Overnight return: `(open - prev_close) / prev_close`
- Intraday return: `(close - open) / open`
- High-Low range: `(high - low) / close`

**Rationale:** Captures momentum and mean-reversion patterns

#### 2.4 Volume Features

- **Volume Moving Average (20-day)**
- **Volume Rate of Change (5-day)**
- **On-Balance Volume (OBV)**: Cumulative volume indicator
  - `OBV_t = OBV_t-1 + volume if close_up else -volume`

**Rationale:** Volume confirms price trends

#### 2.5 Cross-Asset Features

- **Market Beta**: Sensitivity to market index
  - Formula: `cov(return_stock, return_market) / var(return_market)`
  - 60-day rolling window
  - US stocks: SPY as market proxy
  - CN stocks: 000001.SH as market proxy

- **Sector Relative Strength** (optional, requires sector mapping)
  - Stock return vs sector return
  - Identifies leaders vs laggards

### Implementation Structure

```python
# src/equity_lake/feature_engineering.py

class FeatureEngineer:
    def __init__(self, db_conn: duckdb.DuckDBPyConnection):
        self.conn = db_conn

    def compute_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute RSI, MACD, ATR, Bollinger Bands"""
        pass

    def compute_time_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add seasonality features"""
        pass

    def compute_return_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add lagged returns and momentum features"""
        pass

    def compute_volume_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add volume-based indicators"""
        pass

    def compute_cross_asset_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute beta and correlation to market"""
        pass

    def generate_features(self, tickers: List[str], start_date: date, end_date: date) -> pd.DataFrame:
        """Main method: orchestrates all feature computation"""
        pass
```

### Storage Strategy

**Location:** `data/lake/features/`
**Format:** Hive-partitioned Parquet
**Schema:**
```
ticker (string)
date (date)
feature_1: rsi_14 (float)
feature_2: macd (float)
feature_3: macd_signal (float)
feature_4: atr_14 (float)
...
feature_n: beta_60d (float)
target: next_day_return (float)
```

**Incremental Updates:**
- Only recompute last 60 days (rolling window for features)
- Use `date` partition for efficient updates
- Cached with DuckDB for fast model training

---

## 3. Price Forecaster Component

### Component: `PriceForecaster` class

**Location:** `src/equity_lake/forecasting.py`

This module uses XGBoost to predict next-day price direction (up/down) and magnitude (returns).

### Model Architecture

#### 3.1 Target Variable

**Binary Classification (Primary):**
- Target: `next_day_close > today_close` (1=up, 0=down)
- Use case: Directional trading signals
- Metric: Accuracy, Precision, Recall, F1

**Regression (Secondary):**
- Target: `(next_day_close - today_close) / today_close`
- Use case: Position sizing based on expected return
- Metric: RMSE, MAE

#### 3.2 Feature Set

**Input Features (20-30 selected from feature engineering):**
- Momentum: RSI, MACD, Rate of Change
- Volatility: ATR, Rolling Std Dev
- Returns: 1/5/10/20-day lagged returns
- Volume: Volume MA, Volume ROC
- Time: Day of week, month
- Cross-asset: Beta, market correlation

**Feature Selection:**
- Start with all features
- Use XGBoost's built-in feature importance
- Remove low-importance features (< 1% contribution)
- Check for multicollinearity (VIF analysis)

#### 3.3 Model Configuration

**Algorithm:** XGBoost (eXtreme Gradient Boosting)

**Hyperparameters (default):**
```python
params = {
    'max_depth': 5,           # Tree depth (3-7 typical)
    'learning_rate': 0.05,    # Step size shrinkage
    'n_estimators': 200,      # Number of trees
    'subsample': 0.9,         # Row sampling (0.8-1.0)
    'colsample_bytree': 0.9,  # Feature sampling
    'objective': 'binary:logistic',  # Classification
    'eval_metric': 'logloss',
    'random_state': 42
}
```

**Training Window:**
- Rolling 2-year lookback (~500 trading days)
- Time-based split: Train 80%, Validation 20%
- No shuffling (preserve temporal order)

**Validation:**
- Walk-forward validation: Train on T-500 to T-1, predict T
- Prevents look-ahead bias
- Simulates real-world trading

### Key Methods

#### `train_model(ticker, start_date, end_date)`

**Process:**
1. Load features from DuckDB
2. Split chronologically (train: 80%, val: 20%)
3. Hyperparameter tuning via `GridSearchCV`:
   - `max_depth`: [3, 5, 7]
   - `learning_rate`: [0.01, 0.05, 0.1]
   - `n_estimators`: [100, 200, 300]
4. Train final model on full dataset
5. Save model to `data/models/{ticker}_xgboost_{date}.pkl`

**Returns:**
- Trained model
- Validation metrics (accuracy, precision, recall, F1)

#### `predict(ticker, date)`

**Process:**
1. Load latest features for ticker
2. Handle missing features (imputation or skip)
3. Generate prediction
4. Calculate SHAP values (interpretability)

**Returns:**
```python
{
    'ticker': 'AAPL',
    'date': '2026-01-23',
    'prediction': 1,  # 1=up, 0=down
    'probability': 0.68,  # Confidence score
    'predicted_return': 0.015,  # Expected return
    'feature_importance': {...},
    'shap_values': [...]
}
```

#### `backtest(ticker, start_date, end_date)`

**Process:**
1. Walk-forward validation loop
2. For each day T:
   - Train on T-500 to T-1
   - Predict T
   - Record prediction vs actual
3. Calculate performance metrics
4. Generate equity curve

**Metrics:**
- Accuracy, Precision, Recall, F1
- Win rate vs buy-and-hold
- Sharpe Ratio (if trading strategy applied)
- Maximum Drawdown

### Model Interpretability

**Feature Importance (Global):**
- XGBoost's built-in feature importance
- Bar chart: Top 10 features
- Explains overall model behavior

**SHAP Values (Local):**
- Explains individual predictions
- Waterfall plot: Feature contributions
- Example: "AAPL up 68% because RSI=35 (oversold), MACD=positive"

**Partial Dependence Plots:**
- Shows relationship between feature and prediction
- Example: "As RSI decreases below 30, probability of up-move increases"

### Storage

**Model Artifacts:**
```
data/models/
├── AAPL_xgboost_2026-01-23.pkl
├── GOOGL_xgboost_2026-01-23.pkl
└── ...
```

**Predictions:**
```
data/lake/predictions/
└── date=2026-01-23/
    └── predictions.parquet
```

**Schema:**
```
ticker (string)
date (date)
prediction (int)  # 0/1
probability (float)  # 0-1
predicted_return (float)
actual_return (float)  # Filled next day
model_version (string)  # "xgboost_v1.0"
```

### Performance Tracking

**Daily Monitoring:**
- Log predictions to Parquet
- Calculate rolling accuracy (30-day window)
- Compare to baseline (buy-and-hold)

**Alerts:**
- If accuracy drops below 55% (random = 50%)
- If feature distribution shifts (drift detection)
- If prediction distribution skews (> 80% up or down)

---

## 4. Risk Analyzer Component

### Component: `RiskAnalyzer` class

**Location:** `src/equity_lake/risk_analyzer.py`

This module calculates portfolio risk metrics, correlations, and runs stress tests to understand downside risk.

### 4.1 Portfolio Risk Metrics

#### Value at Risk (VaR)

**Definition:** Maximum expected loss over a specified period at a given confidence level.

**Methods:**

1. **Historical VaR:**
   ```python
   returns = portfolio_returns.hist(252 days)  # 1 year
   var_95 = np.percentile(returns, 5)  # 5th percentile
   var_99 = np.percentile(returns, 1)  # 1st percentile
   ```
   - Non-parametric (no distribution assumption)
   - Uses actual historical returns
   - Simple and robust

2. **Parametric VaR (Variance-Covariance):**
   ```python
   mean = returns.mean()
   std = returns.std()
   var_95 = mean - 1.65 * std  # 95% confidence
   var_99 = mean - 2.33 * std  # 99% confidence
   ```
   - Assumes normal distribution
   - Faster computation
   - Less accurate for fat-tailed distributions

3. **Conditional VaR (CVaR / Expected Shortfall):**
   ```python
   var_95 = np.percentile(returns, 5)
   cvar_95 = returns[returns <= var_95].mean()
   ```
   - Expected loss **beyond** VaR
   - Better measure of tail risk
   - More conservative than VaR

**Output:**
```python
{
    'var_95_amount': 1000,  # $1,000 loss
    'var_95_pct': 0.02,  # 2% portfolio loss
    'cvar_95_amount': 1500,  # $1,500 expected loss in worst 5%
    'confidence': 0.95
}
```

#### Drawdown Analysis

**Definition:** Peak-to-trough decline in portfolio value.

**Metrics:**
```python
def calculate_drawdown(equity_curve):
    # Running maximum
    running_max = equity_curve.cummax()
    # Drawdown = (current - peak) / peak
    drawdown = (equity_curve - running_max) / running_max
    # Metrics
    max_drawdown = drawdown.min()
    avg_drawdown = drawdown.mean()
    max_dd_duration = ...  # Longest period below peak
```

**Output:**
- Max Drawdown: -15% (largest peak-to-trough)
- Average Drawdown: -3%
- Max DD Duration: 45 days

#### Portfolio Volatility

**Formula:**
```
σ_p = sqrt(w^T Σ w)
```
where:
- `w` = weight vector
- `Σ` = covariance matrix
- `σ_p` = portfolio volatility (daily)

**Annualization:**
```
σ_p_annual = σ_p_daily × sqrt(252)
```

### 4.2 Correlation Analysis

#### Correlation Matrix

**Calculation:**
```python
# Rolling 60-day correlation
corr_matrix = returns.rolling(window=60).corr()
```

**Visualization:**
- Heatmap (seaborn/plotly)
- Color scale: Red (-1) to White (0) to Green (+1)
- Interactive: Hover for correlation values

**Applications:**
- Diversification: Select low-correlation assets
- Risk management: Reduce concentration
- Pairs trading: Find highly correlated pairs

#### Correlation Break Detection

**Method:**
```python
# Z-score of correlation
corr_mean = correlation.rolling(60).mean()
corr_std = correlation.rolling(60).std()
z_score = (corr_today - corr_mean) / corr_std

# Alert if |z_score| > 2
```

**Rationale:**
- Correlations spike during market stress
- Early warning of regime change
- Example: All stocks become correlated in crashes

### 4.3 Monte Carlo Simulation

**Purpose:** Generate 10,000 potential price paths to understand distribution of future outcomes.

**Model:** Geometric Brownian Motion (GBM)
```
S_t = S_0 × exp((μ - 0.5σ²)t + σ√t×Z)
```
where:
- `S_0` = current price
- `μ` = drift (expected return)
- `σ` = volatility
- `Z` = random normal variable

**Correlated GBM:**
- Preserves correlation structure via Cholesky decomposition
```python
# Cholesky decomposition of correlation matrix
L = np.linalg.cholesky(corr_matrix)
# Generate correlated random variables
Z = L @ np.random.normal(0, 1, (n_assets, n_paths))
```

**Process:**
1. Estimate historical drift (μ) and volatility (σ)
2. Generate 10,000 random paths for each asset
3. Calculate portfolio value for each path
4. Analyze distribution of outcomes

**Output:**
- 5th percentile (worst case)
- Median (expected case)
- 95th percentile (best case)
- Probability of loss > X%

**Visualization:**
- Fan chart (spaghetti plot of paths)
- Histogram of final portfolio values
- Confidence bands around median

### 4.4 Stress Testing

**Predefined Scenarios:**

1. **2008 Financial Crisis:**
   - Market drops 30%
   - Volatility spikes to 2x normal
   - Correlations approach 1.0

2. **COVID Crash (March 2020):**
   - Market drops 20% in 5 days
   - Flight to safety (bonds up, stocks down)
   - Sector rotation (tech outperforms energy)

3. **Tech Bubble (2000):**
   - NASDAQ drops 40%
   - Value stocks outperform growth
   - Small caps hit hardest

**Custom Scenarios:**
```python
def stress_test_portfolio(portfolio, scenario):
    """
    scenario = {
        'market_shock': -0.20,  # -20% market
        'volatility_multiplier': 1.5,  # Volatility +50%
        'sector_shocks': {
            'tech': -0.30,
            'energy': 0.10
        }
    }
    """
    # Apply shock to each asset
    # Recalculate portfolio value
    # Return loss under scenario
```

### Storage

**Risk Metrics:**
```
data/lake/risk_metrics/
└── date=2026-01-23/
    └── risk_metrics.parquet
```

**Schema:**
```
portfolio_id (string)
date (date)
var_95 (float)
cvar_95 (float)
max_drawdown (float)
volatility_annual (float)
sharpe_ratio (float)
```

**Correlation Matrices:**
```
data/lake/correlations/
└── date=2026-01-23/
    └── correlation_matrix.parquet
```

---

## 5. Streamlit Dashboard

### Component: Multi-page Streamlit app

**Location:** `src/equity_lake/dashboard/app.py`
**URL:** `http://localhost:8501`

This is the user-facing interface for exploring forecasts, risk metrics, and correlations interactively.

### 5.1 Architecture

**Navigation:**
```python
import streamlit as st

st.set_page_config(
    page_title="Equity ML Analytics",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Sidebar
with st.sidebar:
    st.title("🎯 ML Equity Analytics")
    page = st.radio("Navigate", ["Forecast Explorer", "Risk Analysis", "Correlation Matrix", "Portfolio Simulator"])

    # Filters
    st.date_input("Date Range", [start_date, end_date])
    st.multiselect("Markets", ["US", "CN", "HK-SG"])
    st.text_input("Ticker Search", "AAPL")
```

### 5.2 Page 1: Forecast Explorer

**Purpose:** View price predictions and model performance for individual tickers.

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  Ticker: [AAPL]        Date Range: [2024-01-01 to 2025]  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Price Chart with Predictions                            │
│  ┌────────────────────────────────────────────────┐      │
│  │ 180 ┤     ● Prediction (Up, 68%)               │      │
│  │ 170 ┤    ╱ ╲                                  │      │
│  │ 160 ┤   ╱   ╲ ● Actual                        │      │
│  │ 150 ┤  ╱     ╲                                │      │
│  │ 140 ┤ ───────                                │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Prediction Probability Over Time                        │
│  ┌────────────────────────────────────────────────┐      │
│  │ 1.0 ┤ ─────────────────────                    │      │
│  │ 0.8 ┤     ████████░░░░                         │      │
│  │ 0.5 ┤ ─────────────────────                    │      │
│  │ 0.2 ┤ ░░░░░░░░░░░░░░░░░░░                     │      │
│  │ 0.0 ┤ ─────────────────────                    │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Feature Importance (Top 10)                             │
│  ┌────────────────────────────────────────────────┐      │
│  │ RSI_14      ████████████████████████ 0.25      │      │
│  │ MACD        ████████████████░░░░░░░░ 0.18      │      │
│  │ Beta_60d    ██████████████░░░░░░░░░░░ 0.12      │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Metrics                                                 │
│  ┌────────────────────────────────────────────────┐      │
│  │ Accuracy (30d):    62%     (Baseline: 50%)      │      │
│  │ Precision:         58%                           │      │
│  │ Recall:            65%                           │      │
│  │ F1 Score:          0.61                          │      │
│  └────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

**Key Widgets:**
- Ticker selector (single or multi-select)
- Date range picker
- Model version selector (if multiple versions exist)
- "Explain Prediction" button (shows SHAP waterfall)

### 5.3 Page 2: Risk Analysis

**Purpose:** Analyze portfolio risk, view VaR, and run stress tests.

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  Portfolio Builder                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ AAPL 40% │  │ GOOGL 30%│  │ MSFT 30% │  [Normalize]│
│  └──────────┘  └──────────┘  └──────────┘              │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  VaR / CVaR Distribution                                 │
│  ┌────────────────────────────────────────────────┐      │
│  │ 30% ┤ ╱╲                                        │      │
│  │ 20% ┤╱  ╲   ╱─╲ VaR 95% = $1,000 (2%)         │      │
│  │ 10% ┤     ╲╱    ╲                               │      │
│  │  0% ┼─────┴─────┴───────────                   │      │
│  │     ┼────────────────────────────→ Loss        │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Drawdown Underwater Curve                               │
│  ┌────────────────────────────────────────────────┐      │
│  │  0% ┤ ─────────────────                        │      │
│  │ -5% ┤          ╱╲                              │      │
│  │-10% ┤         ╱  ╲     Max DD: -15%            │      │
│  │-15% ┤        ╱    ╲___╱                       │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Risk Heatmap                                            │
│  ┌────────────────────────────────────────────────┐      │
│  │          VaR95   CVaR95  Vol    MaxDD          │      │
│  │ AAPL      $800    $1200   18%    -12%          │      │
│  │ GOOGL     $650    $950    16%    -10%          │      │
│  │ MSFT      $700    $1100   17%    -11%          │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Monte Carlo Simulation (10,000 paths)                   │
│  ┌────────────────────────────────────────────────┐      │
│  │ $120k ┤     ╱  ╲  95th percentile              │      │
│  │ $110k ┤    ╱    ╲╱                              │      │
│  │ $100k ┤───╱──────╲── Median                    │      │
│  │  $90k ┤ ╱        ╲                             │      │
│  │  $80k ┤╱          ╲─ 5th percentile            │      │
│  └────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

**Key Widgets:**
- Portfolio weight sliders (auto-normalize to 100%)
- Confidence level selector (95%, 99%, 99.9%)
- Time horizon (1 day, 1 week, 1 month)
- Stress test selector (2008, COVID, Tech Bubble, Custom)

### 5.4 Page 3: Correlation Matrix

**Purpose:** Explore correlations between tickers and detect regime changes.

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  Tickers: [AAPL, GOOGL, MSFT, TSLA, NVDA]                │
│  Window: [60 days ▼]    Date: [───────◀───────]          │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Interactive Heatmap                                     │
│  ┌────────────────────────────────────────────────┐      │
│  │        AAPL    GOOGL   MSFT    TSLA    NVDA    │      │
│  │ AAPL    1.0     0.65    0.72    0.45    0.68   │      │
│  │ GOOGL   0.65    1.0     0.70    0.42    0.65   │      │
│  │ MSFT    0.72    0.70    1.0     0.48    0.71   │      │
│  │ TSLA    0.45    0.42    0.48    1.0     0.50   │      │
│  │ NVDA    0.68    0.65    0.71    0.50    1.0    │      │
│  └────────────────────────────────────────────────┘      │
│  Color: Red (-1) → White (0) → Green (+1)                │
│                                                          │
│  Correlation Network Graph                               │
│  ┌────────────────────────────────────────────────┐      │
│  │        AAPL ──────── GOOGL                     │      │
│  │          │ \     /     │                       │      │
│  │          │  \   /      │                       │      │
│  │         MSFT──X────── NVDA                     │      │
│  │              │                                 │      │
│  │             TSLA                               │      │
│  │  (Edge thickness = correlation strength)       │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Breaking Correlation Events                             │
│  ┌────────────────────────────────────────────────┐      │
│  │ ⚠️  2026-01-15: AAPL-GOOGL correlation spiked  │      │
│  │    from 0.65 to 0.92 (Z-score: 2.8)           │      │
│  │ ⚠️  2026-01-10: Tech sector correlation 0.95   │      │
│  │    (market stress event)                       │      │
│  └────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

**Key Widgets:**
- Ticker multi-select
- Rolling window selector (20, 60, 120 days)
- Date slider (historical view)
- Network graph toggle
- "Export Matrix" button

### 5.5 Page 4: Portfolio Simulator

**Purpose:** Backtest ML strategies and compare to buy-and-hold.

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  Strategy Configuration                                   │
│  ┌───────────────────────┐  ┌───────────────────────┐    │
│  │ ML Strategy:          │  │ Buy & Hold:          │    │
│  │ - Top 5 predictions   │  │ - Equal weight        │    │
│  │ - Rebalance weekly    │  │ - Hold entire period  │    │
│  │ - Min prob: 60%       │  │                       │    │
│  └───────────────────────┘  └───────────────────────┘    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Equity Curve Comparison                                 │
│  ┌────────────────────────────────────────────────┐      │
│  │ $120k ┤ ╱─ ML Strategy                         │      │
│  │ $110k ┤╱   ╱── Buy & Hold                     │      │
│  │ $100k ┤───╱                                    │      │
│  │  $90k ┤ ╱                                      │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  Performance Comparison                                   │
│  ┌────────────────────────────────────────────────┐      │
│  │              ML Strategy    Buy & Hold         │      │
│  │ CAGR              15.2%         12.8%          │      │
│  │ Max DD           -18.5%        -22.3%          │      │
│  │ Sharpe            1.25          0.95           │      │
│  │ Win Rate          58%          N/A             │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  What-If Stress Test Scenarios                           │
│  ┌────────────────────────────────────────────────┐      │
│  │ [2008 Crisis] [COVID] [Tech Bubble] [Custom]  │      │
│  │                                                  │      │
│  │ Portfolio Value Under 2008 Scenario:            │      │
│  │ ML Strategy: $78,000 (-35%)                     │      │
│  │ Buy & Hold: $72,000 (-40%)                      │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  [Export Results to CSV] [Generate Report]              │
└──────────────────────────────────────────────────────────┘
```

**Key Widgets:**
- Strategy builder (rules-based)
- Backtest date range
- Transaction cost slider (0-0.5%)
- Stress test buttons
- Export options (CSV, PDF report)

### 5.6 Performance Optimizations

**Caching:**
```python
@st.cache_data(ttl=3600)  # Cache for 1 hour
def load_features(tickers, start_date, end_date):
    query = f"SELECT * FROM features WHERE..."
    return conn.execute(query).df()

@st.cache_resource  # Cache once per session
def load_model(ticker):
    return joblib.load(f"data/models/{ticker}_xgboost.pkl")
```

**Lazy Loading:**
- Only load data when page is selected
- Use `st.session_state` to persist data across pages
- Progress bars for long-running computations

**Async Operations:**
- Run Monte Carlo in background (`@st.experimental_memo`)
- Show spinner while computing
- Cancel button for long simulations

---

## 6. Data Flow & Integration

### Component: Daily ML Pipeline orchestrator

**Location:** `src/equity_lake/ml_daily.py`

This script runs after the existing `ingest_daily.py`, orchestrating the full ML workflow from raw data to predictions.

### 6.1 Daily Workflow

**Step 1: Data Ingestion** (existing pipeline)
```bash
# Your current ingestion
uv run equity-daily --date $(date -d "yesterday" +%Y-%m-%d)
```
- Fetches raw OHLCV data
- Writes to `data/lake/{market}/date=*/`

**Step 2: Feature Engineering** (new)
```bash
python -m equity_lake.features.engineering --date $(date -d "yesterday" +%Y-%m-%d)
```
- Reads raw OHLCV from DuckDB `equity_all` view
- Computes 20-30 features per ticker
- Writes to `data/lake/features/date=*/`
- **Incremental**: Only recomputes last 60 days (rolling window)

**Step 3: Model Training/Prediction** (new)
```bash
python -m equity_lake.price_forecaster --mode predict --date $(date -d "yesterday" +%Y-%m-%d)
python -m equity_lake.risk_analyzer --date $(date -d "yesterday" +%Y-%m-%d)
```
- **Forecaster**:
  - Loads latest features
  - Runs prediction for each ticker
  - Calculates SHAP values
  - Writes to `data/lake/predictions/date=*/`

- **Risk Analyzer**:
  - Computes VaR, correlations for all tickers
  - Runs Monte Carlo simulation (10,000 paths)
  - Writes to `data/lake/risk_metrics/date=*/`

**Step 4: Dashboard Update** (manual/automatic)
- Streamlit app auto-detects new data via `@st.cache_data(ttl=3600)`
- Users refresh browser to see latest predictions
- Optional: Auto-refresh every 5 minutes (`st.autorun`)

### 6.2 Error Handling & Logging

**Graceful Degradation:**
```python
try:
    run_feature_engineering(date)
except Exception as e:
    logger.error(f"Feature engineering failed: {e}")
    # Continue to risk analysis (can use historical features)
    notify_user("Feature engineering failed, using cached features")

try:
    run_price_forecaster(date)
except Exception as e:
    logger.error(f"Forecaster failed: {e}")
    # Don't block risk analysis
    notify_user("Price forecasts unavailable")

try:
    run_risk_analyzer(date)
except Exception as e:
    logger.error(f"Risk analyzer failed: {e}")
    notify_user("Risk metrics unavailable")
```

**Retry Logic:**
- 3 attempts with exponential backoff (existing pattern)
- Delay: 1s, 2s, 4s
- Skip ticker after 3 failures, continue with others

**Structured Logging:**
```python
import structlog

logger = structlog.get_logger()
logger.info(
    "ml_pipeline_started",
    date=str(date),
    markets=["US", "CN", "HK-SG"],
    n_tickers=100
)
```

**Alerts:**
- Email/Slack if prediction accuracy drops below 55%
- PagerDuty if pipeline fails completely
- Daily summary email with pipeline status

### 6.3 Data Validation

**Pre-flight Checks:**
```python
def validate_features(df):
    """Ensure features meet quality standards"""
    assert not df.isnull().any().any(), "Missing values detected"
    assert not np.isinf(df.select_dtypes(include=[np.number])).any().any(), "Infinite values"
    assert (df['close'] >= 0).all(), "Negative prices found"
    assert (df['volume'] >= 0).all(), "Negative volume found"
    assert check_date_continuity(df['date'], max_gap_days=5), "Date gaps detected"
```

**Schema Validation:**
```python
def validate_parquet_schema(file_path, expected_schema):
    """Ensure Parquet file matches expected schema"""
    pf = pq.ParquetFile(file_path)
    actual_schema = pf.schema_arrow
    assert actual_schema == expected_schema, "Schema mismatch"
```

### 6.4 Storage Summary

**Directory Structure:**
```
data/lake/
├── us_equity/           # Raw OHLCV (existing)
│   └── date=2026-01-23/
├── cn_ashare/           # Raw OHLCV (existing)
│   └── date=2026-01-23/
├── hk_sg_equity/        # Raw OHLCV (existing)
│   └── date=2026-01-23/
├── features/            # NEW: ML features
│   └── date=2026-01-23/
│       └── features.parquet
├── predictions/         # NEW: Model predictions
│   └── date=2026-01-23/
│       └── predictions.parquet
├── risk_metrics/        # NEW: Risk analysis
│   └── date=2026-01-23/
│       └── risk_metrics.parquet
└── correlations/        # NEW: Correlation matrices
    └── date=2026-01-23/
        └── correlation_matrix.parquet
```

**Transparency & Reproducibility:**
- All Parquet files include metadata:
  - `model_version`: "xgboost_v1.0"
  - `generated_at`: timestamp
  - `feature_hash`: hash of feature schema
  - `git_commit`: commit SHA
- Enables full reproducibility of predictions

### 6.5 Cron Schedule

**Daily Execution:**
```bash
# Run daily after market close (6 PM UTC)
0 18 * * 1-5 cd /path/to/project && \
    uv run equity-daily && \
    uv run python -m equity_lake.features.engineering && \
    uv run python -m equity_lake.price_forecaster && \
    uv run python -m equity_lake.risk_analyzer && \
    echo "ML pipeline completed $(date)" >> logs/ml_pipeline.log
```

**Weekly Tasks:**
```bash
# Every Sunday 2 AM: Retrain models
0 2 * * 0 cd /path/to/project && \
    uv run python -m equity_lake.price_forecaster --mode retrain && \
    echo "Model retraining completed $(date)" >> logs/model_retrain.log
```

**Monthly Tasks:**
```bash
# First day of month: Generate performance report
0 3 1 * * cd /path/to/project && \
    uv run python -m equity_lake.generate_monthly_report && \
    echo "Monthly report generated $(date)" >> logs/reports.log
```

---

## 7. Implementation Phases

Build incrementally to validate each layer before moving to the next.

### Phase 1: Foundation & Feature Engineering (Week 1-2)

**Goals:**
- Set up ML environment
- Implement basic feature engineering
- Validate feature output

**Tasks:**
1. Install ML dependencies:
   ```bash
   uv add xgboost scikit-learn shap pandas-ta streamlit plotly
   uv sync
   ```

2. Create `src/equity_lake/feature_engineering.py`:
   - Implement basic features first (5-10 momentum indicators)
   - Add docstrings and type hints
   - Write unit tests

3. Test on single ticker:
   ```python
   engineer = FeatureEngineer(conn)
   df = engineer.generate_features(['AAPL'], date(2024, 1, 1), date(2025, 1, 1))
   assert not df.empty
   assert 'rsi_14' in df.columns
   ```

4. Validate Parquet output:
   ```python
   write_to_partitioned_parquet(df, 'data/lake/features/', 'date')
   ```

5. Extend to all tickers and markets

**Success Criteria:**
- ✅ Features generate without errors
- ✅ Schema validated
- ✅ Features written to Parquet
- ✅ Unit tests pass (> 80% coverage)

### Phase 2: Price Forecaster MVP (Week 3-4)

**Goals:**
- Train first XGBoost model
- Generate predictions
- Create basic UI

**Tasks:**
1. Create `src/equity_lake/forecasting.py` and `src/equity_lake/price_forecaster.py`:
   - Implement `train_model()` method
   - Implement `predict()` method
   - Add SHAP interpretability
   - Write unit tests

2. Train on US market data:
   ```python
   forecaster = PriceForecaster()
   model = forecaster.train_model('AAPL', date(2023, 1, 1), date(2024, 1, 1))
   ```

3. Generate predictions for latest date:
   ```python
   pred = forecaster.predict('AAPL', date(2026, 1, 23))
   ```

4. Create basic Streamlit UI (`src/equity_lake/dashboard/app.py`):
   - Ticker selector
   - Price chart with prediction overlay
   - Accuracy metrics display

5. Test dashboard:
   ```bash
   streamlit run src/equity_lake/dashboard/app.py
   ```

**Success Criteria:**
- ✅ Model accuracy > 55% (baseline: 50%)
- ✅ Predictions written to Parquet
- ✅ UI renders correctly
- ✅ Dashboard loads in < 3 seconds

### Phase 3: Risk Analysis (Week 5-6)

**Goals:**
- Implement VaR/CVaR calculations
- Build correlation matrix calculator
- Create risk visualization pages

**Tasks:**
1. Create `src/equity_lake/risk_analyzer.py`:
   - Implement `calculate_var()` (historical method)
   - Implement `compute_correlation_matrix()`
   - Implement simple Monte Carlo (1000 paths)
   - Write unit tests

2. Test risk calculations:
   ```python
   analyzer = RiskAnalyzer()
   var = analyzer.calculate_var(['AAPL', 'GOOGL'], [0.5, 0.5])
   assert var['var_95_pct'] > 0
   ```

3. Add Streamlit pages:
   - Risk Analysis page (VaR heatmap, drawdown chart)
   - Correlation Matrix page (interactive heatmap)
   - Use Plotly for interactivity

4. Performance optimization:
   - Cache correlation matrices
   - Parallel Monte Carlo simulation
   - Lazy loading of historical data

**Success Criteria:**
- ✅ VaR/CVaR calculate correctly
- ✅ Correlation matrix symmetric
- ✅ Monte Carlo completes in < 5 seconds
- ✅ Visualizations render without errors

### Phase 4: Advanced Features (Week 7-8)

**Goals:**
- Enhance models with hyperparameter tuning
- Add ensemble methods
- Implement advanced risk techniques

**Tasks:**
1. Model enhancements:
   - Hyperparameter tuning with Optuna:
     ```python
     study = optuna.create_study(direction='maximize')
     study.optimize(objective, n_trials=100)
     ```
   - Ensemble: XGBoost + RandomForest
   - Add more features (sector, macro indicators)

2. Advanced risk:
   - Parametric VaR method
   - Stress testing scenarios (2008, COVID)
   - 10,000-path Monte Carlo simulation

3. Add Portfolio Simulator page to Streamlit:
   - Backtest ML strategy vs buy-and-hold
   - What-if scenarios
   - Export results

4. Add model interpretability:
   - SHAP summary plots
   - Partial dependence plots
   - Feature interaction plots

**Success Criteria:**
- ✅ Improved accuracy (> 60%)
- ✅ Full feature set working
- ✅ Stress tests execute correctly
- ✅ Backtest generates equity curve

### Phase 5: Productionization (Week 9-10)

**Goals:**
- Automate daily pipeline
- Add error handling and monitoring
- Write documentation

**Tasks:**
1. Add `src/equity_lake/ml_daily.py` orchestrator:
   - Runs feature engineering → prediction → risk analysis
   - Graceful error handling
   - Logging and alerts

2. Set up cron job:
   ```bash
   crontab -e
   # Add daily pipeline schedule
   ```

3. Performance optimization:
   - DuckDB query caching
   - Feature computation parallelization
   - Model versioning (MLflow or simple file-based)

4. Documentation:
   - Update CLAUDE.md with ML section
   - Create example Jupyter notebooks
   - Write user guide for Streamlit dashboard
   - Add inline code comments

5. Testing:
   - End-to-end integration tests
   - Performance benchmarks
   - Load testing (simulate 100 concurrent users)

6. Deployment (optional):
   - Docker container for dashboard
   - Streamlit Cloud deployment (free tier)
   - Set up monitoring (Prometheus/Grafana)

**Success Criteria:**
- ✅ Daily pipeline runs automatically
- ✅ Dashboard stable for 24+ hours
- ✅ Documentation complete
- ✅ Integration tests pass
- ✅ Ready for production use

---

## 8. Dependencies & Libraries

### Core ML Libraries

**Add to project using `uv`:**

```bash
# Machine Learning
uv add xgboost scikit-learn shap

# Technical Analysis
uv add pandas-ta  # Pure Python, easier than ta-lib

# Visualization
uv add streamlit plotly seaborn networkx

# Scientific Computing
uv add scipy statsmodels

# Sync environment
uv sync
```

### Updated `pyproject.toml`

```toml
[project]
dependencies = [
    # Existing dependencies...
    "yfinance>=0.2.50",
    "akshare>=1.15.0",
    "duckdb>=1.0.0",
    "pandas>=2.2.0",
    "pyarrow>=18.0.0",
    "python-dotenv>=1.0.0",

    # NEW: Machine Learning
    "xgboost>=2.0.0",           # Gradient boosting
    "scikit-learn>=1.4.0",      # ML algorithms, metrics
    "shap>=0.44.0",             # Model interpretability

    # NEW: Technical Analysis
    "pandas-ta>=0.3.14b",       # 130+ indicators (pure Python)

    # NEW: Visualization
    "streamlit>=1.31.0",        # Web dashboard
    "plotly>=5.18.0",           # Interactive charts
    "seaborn>=0.13.0",          # Statistical visualizations
    "networkx>=3.2.0",          # Correlation networks

    # NEW: Scientific Computing
    "scipy>=1.12.0",            # Statistical distributions
    "statsmodels>=0.14.0",      # Econometric models
]

[project.optional-dependencies]
dev = [
    # Existing dev dependencies...
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.8.0",
    "mypy>=1.11.0",

    # NEW: ML Dev Tools (optional, Phase 4)
    "optuna>=3.5.0",            # Hyperparameter optimization
    "wandb>=0.16.0",            # Experiment tracking
    "jupyter>=1.0.0",           # Notebooks
    "ipywidgets>=8.1.0",        # Interactive widgets
]
```

### Library Usage Summary

| Library | Purpose | Key Features |
|---------|---------|--------------|
| **xgboost** | Price forecasting | Gradient boosting, fast training, handles missing data |
| **scikit-learn** | ML utilities | Preprocessing, metrics, cross-validation, RandomForest |
| **shap** | Interpretability | SHAP values, feature importance per prediction |
| **pandas-ta** | Technical analysis | RSI, MACD, Bollinger Bands, ATR (130+ indicators) |
| **streamlit** | Dashboard | Web UI, caching, data widgets |
| **plotly** | Charts | Candlestick, line, heatmaps, interactive |
| **seaborn** | Statistical plots | Correlation heatmaps, distribution plots |
| **networkx** | Graphs | Correlation networks, node visualization |
| **scipy** | Scientific computing | Statistical distributions, optimization |
| **statsmodels** | Econometrics | Time series models (ARIMA/GARCH) - optional Phase 4 |
| **optuna** | Hyperparameter tuning | Bayesian optimization (Phase 4) |
| **wandb** | Experiment tracking | Model versioning, metrics logging (Phase 4) |

### Installation Best Practices

**Using `uv` (recommended):**
```bash
# Add dependencies
uv add xgboost scikit-learn shap pandas-ta streamlit plotly seaborn networkx scipy statsmodels

# Install all dependencies from pyproject.toml
uv sync

# Install with dev extras
uv sync --all-extras
```

**Alternative: Traditional pip**
```bash
# Create venv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

**Verifying Installation:**
```bash
# Test ML imports
python -c "import xgboost, sklearn, shap, pandas_ta; print('✅ ML libs OK')"

# Test visualization imports
python -c "import streamlit, plotly, seaborn, networkx; print('✅ Viz libs OK')"

# Test scientific imports
python -c "import scipy, statsmodels; print('✅ Sci libs OK')"

# Run Streamlit test
streamlit hello
```

---

## 9. Testing & Validation

### Test Suite Structure

**Location:** `tests/test_ml_*.py`

### Unit Tests (`tests/test_ml_models.py`)

**Feature Engineering Tests:**
```python
def test_technical_indicators_shape():
    """Verify output has correct columns"""
    df = engineer.compute_technical_indicators(sample_data)
    assert 'rsi_14' in df.columns
    assert 'macd' in df.columns
    assert len(df) == len(sample_data)

def test_rsi_range():
    """RSI values must be between 0-100"""
    df = engineer.compute_technical_indicators(sample_data)
    assert df['rsi_14'].between(0, 100).all()

def test_returns_calculation():
    """Verify return formulas"""
    df = engineer.compute_return_features(sample_data)
    expected_return = (100 - 90) / 90  # Simple example
    assert abs(df['return_1d'].iloc[0] - expected_return) < 1e-6

def test_missing_value_handling():
    """Ensure NaN handling works"""
    df_with_nans = sample_data.copy()
    df_with_nans.loc[0, 'close'] = np.nan
    df = engineer.compute_technical_indicators(df_with_nans)
    assert not df['rsi_14'].isnull().all()  # Some values computed
```

**Model Tests:**
```python
def test_model_train():
    """XGBoost trains without errors"""
    forecaster = PriceForecaster()
    model = forecaster.train_model('AAPL', date(2024, 1, 1), date(2024, 12, 31))
    assert model is not None
    assert hasattr(model, 'predict')

def test_prediction_shape():
    """Predictions return correct format"""
    pred = forecaster.predict('AAPL', date(2026, 1, 23))
    assert 'prediction' in pred
    assert 'probability' in pred
    assert 0 <= pred['probability'] <= 1

def test_probability_range():
    """Probabilities must be in [0, 1]"""
    probs = [forecaster.predict('AAPL', d)['probability']
             for d in date_range]
    assert all(0 <= p <= 1 for p in probs)

def test_feature_importance_exists():
    """Model returns feature importance"""
    forecaster = PriceForecaster()
    model = forecaster.train_model(...)
    importance = model.get_booster().get_score(importance_type='weight')
    assert len(importance) > 0
```

**Risk Analyzer Tests:**
```python
def test_var_positive():
    """VaR should be positive (loss amount)"""
    analyzer = RiskAnalyzer()
    var = analyzer.calculate_var(['AAPL'], [1.0])
    assert var['var_95_amount'] > 0
    assert var['var_95_pct'] > 0

def test_correlation_symmetric():
    """Correlation matrix must be symmetric"""
    corr = analyzer.compute_correlation_matrix(['AAPL', 'GOOGL', 'MSFT'])
    assert np.allclose(corr, corr.T, atol=1e-6)

def test_monte_carlo_paths():
    """Monte Carlo returns N simulated paths"""
    paths = analyzer.run_monte_carlo(['AAPL'], [1.0], n_simulations=1000, days=10)
    assert paths.shape == (10, 1000)  # 10 days × 1000 paths

def test_portfolio_weights_sum_to_one():
    """Validate weight normalization"""
    weights = np.array([0.5, 0.3, 0.2])
    assert abs(weights.sum() - 1.0) < 1e-6
```

### Integration Tests (`tests/test_ml_pipeline.py`)

```python
@pytest.mark.integration
def test_end_to_end_pipeline():
    """Run full pipeline on synthetic data"""
    # 1. Generate synthetic OHLCV data
    synthetic_data = generate_synthetic_ohlcv(100 days, 10 tickers)

    # 2. Run feature engineering
    engineer = FeatureEngineer(conn)
    features = engineer.generate_features(tickers, start, end)
    assert not features.empty
    assert 'rsi_14' in features.columns

    # 3. Train model
    forecaster = PriceForecaster()
    model = forecaster.train_model('TICKER1', start, end)
    assert model is not None

    # 4. Generate prediction
    pred = forecaster.predict('TICKER1', end)
    assert 'prediction' in pred

    # 5. Calculate risk metrics
    analyzer = RiskAnalyzer()
    var = analyzer.calculate_var(['TICKER1'], [1.0])
    assert var['var_95_pct'] > 0

    print("✅ End-to-end pipeline passed")

@pytest.mark.integration
def test_dashboard_renders():
    """Streamlit app starts without errors"""
    # This test requires manual verification or automated screenshot comparison
    subprocess.run(['streamlit', 'run', 'src/equity_lake/dashboard/app.py', '--server.headless', 'true'])
    # Check if server started successfully
    assert True  # Placeholder

@pytest.mark.integration
def test_parquet_schema_compliance():
    """Output Parquet files match expected schema"""
    # Generate features
    engineer.generate_features(...)
    # Validate schema
    validate_parquet_schema('data/lake/features/date=.../', EXPECTED_SCHEMA)
```

### Validation Metrics

#### Model Performance Benchmarks

**Minimum Acceptable Performance:**
- Accuracy > 55% (baseline: 50% random)
- Precision > 0.50 (minimize false positives)
- Recall > 0.50 (capture opportunities)
- F1 Score > 0.50
- Sharpe Ratio > 1.0 (risk-adjusted returns)

**Target Performance:**
- Accuracy > 60%
- Precision > 0.60
- Recall > 0.60
- F1 Score > 0.60
- Sharpe Ratio > 1.5

#### Risk Model Validation

**VaR Backtesting:**
```python
def test_var_backtest():
    """Actual 5% worst losses should match 95% VaR"""
    returns = portfolio_returns  # 252 days (1 year)
    var_95 = np.percentile(returns, 5)

    # Count actual breaches
    breaches = (returns < var_95).sum()
    breach_rate = breaches / len(returns)

    # Should be close to 5% (± 2% tolerance)
    assert 0.03 < breach_rate < 0.07
```

**Correlation Stability:**
- No sudden spikes > 0.9 without explanation
- Correlation drift < 0.2 per month (normally)
- Breaking events must be flagged

**Monte Carlo Coverage:**
- 95% of actual outcomes should be within 95% prediction interval
- Test on out-of-sample data (not used in calibration)

#### Data Quality Checks

```python
def validate_data_quality(df):
    """Ensure data meets quality standards"""
    # No missing values in features
    assert not df.isnull().any().any(), "Missing values detected"

    # No infinite values
    assert not np.isinf(df.select_dtypes(include=[np.number])).any().any(), "Infinite values"

    # Price data monotonic (no negative prices)
    assert (df['close'] >= 0).all(), "Negative prices found"

    # Volume data non-negative
    assert (df['volume'] >= 0).all(), "Negative volume found"

    # Date column continuous (no gaps > 5 days)
    dates = df['date'].sort_values()
    gaps = dates.diff().dt.days
    assert (gaps <= 5).all(), "Date gaps > 5 days detected"

    print("✅ Data quality validation passed")
```

### Running Tests

**Unit tests only (fast):**
```bash
uv run pytest tests/test_ml_models.py -v
```

**Integration tests (require data, slower):**
```bash
uv run pytest tests/test_ml_pipeline.py -v -m integration
```

**Full test suite with coverage:**
```bash
uv run pytest tests/ -v --cov=equity_lake.price_forecaster --cov=equity_lake.features.engineering --cov-report=html
```

**Skip slow tests:**
```bash
uv run pytest -m "not slow"
```

**Coverage thresholds:**
- Unit tests: > 90% coverage
- Integration tests: > 70% coverage
- Overall: > 80% coverage

### Performance Benchmarks

**Target Performance (100 tickers × 500 days):**
- Feature engineering: < 10 seconds
- Model training: < 30 seconds (single ticker)
- Prediction generation: < 1 second (100 tickers)
- Risk calculation: < 5 seconds (10-ticker portfolio, 10,000 MC paths)
- Dashboard page load: < 3 seconds
- Monte Carlo simulation: < 5 seconds (10,000 paths)

**Profiling:**
```bash
# Profile feature engineering
python -m cProfile -o profile.stats -m equity_lake.features.engineering
python -m pstats profile.stats
# sort by cumulative time
sort cumulative
```

---

## Appendix A: Research Summary

### A.1 ML Approaches for Equity Forecasting (2026)

**Key Findings from Research:**

**Models:**
- **XGBoost**: Best for tabular data, interpretable, fast training
- **LSTM**: Good for long-term dependencies, but slower
- **Transformer**: State-of-the-art for volatile markets, but high computational cost
- **Hybrid Models**: Combine XGBoost + LSTM/Transformer for best accuracy

**Feature Engineering:**
- **Technical Indicators**: RSI, MACD, Bollinger Bands (improves F1 score)
- **Fundamental Data**: P/E ratios, EPS (for long-term forecasting)
- **Alternative Data**: Sentiment scores, macro indicators (improves accuracy)

**Risk Metrics:**
- **VaR/CVaR**: Standard for portfolio risk measurement
- **Sharpe Ratio**: Measures risk-adjusted returns
- **Maximum Drawdown**: Peak-to-trough decline
- **Tail Risk**: Extreme events beyond VaR

**Python Libraries:**
- **Core**: pandas, numpy, scikit-learn, xgboost
- **Technical Analysis**: ta-lib, pandas-ta
- **Deep Learning**: TensorFlow, PyTorch (optional Phase 4)
- **Backtesting**: Backtrader, vectorbt
- **Visualization**: Plotly, Matplotlib

### A.2 Financial Risk Analysis Techniques (2026)

**VaR Calculation Methods:**
1. **Historical Simulation**: Uses actual historical returns
2. **Parametric (Variance-Covariance)**: Assumes normal distribution
3. **Monte Carlo Simulation**: Random sampling for complex portfolios

**Correlation Analysis:**
- Rolling correlation matrices (60-day window)
- Correlation spikes during market stress
- Network graphs for visualization

**Monte Carlo Simulation:**
- Geometric Brownian Motion (GBM) model
- Cholesky decomposition for correlated assets
- 10,000 paths for robust statistics

**Stress Testing:**
- Predefined scenarios: 2008, COVID, Tech Bubble
- Custom "what-if" scenarios
- Portfolio resilience assessment

### A.3 Financial Visualization Best Practices (2026)

**Tools:**
- **Streamlit**: Easiest for building interactive dashboards
- **Plotly**: Interactive charts (candlestick, line, heatmaps)
- **Seaborn**: Statistical visualizations
- **NetworkX**: Correlation network graphs

**Best Practices:**
- Match chart type to purpose (line for trends, heatmap for correlations)
- Prioritize clarity and simplicity
- Ensure accuracy (Y-axis starts at zero for bar charts)
- Embrace interactivity (filters, zoom, tooltips)
- Leverage modern tools (AI-powered insights, real-time streaming)

**Dashboard Design:**
- Clear navigation with sidebar
- Consistent color scheme (red for loss, green for gain)
- Responsive layout (works on desktop/mobile)
- Performance optimization (caching, lazy loading)

---

## Appendix B: Example Code Snippets

### B.1 Feature Engineering Example

```python
# src/equity_lake/feature_engineering.py

import pandas as pd
import pandas_ta as ta
from datetime import date

class FeatureEngineer:
    def __init__(self, db_conn):
        self.conn = db_conn

    def compute_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute RSI, MACD, Bollinger Bands using pandas-ta"""
        # RSI (14-period)
        df['rsi_14'] = ta.rsi(df['close'], length=14)

        # MACD
        macd = ta.macd(df['close'])
        df['macd'] = macd['MACD_12_26_9']
        df['macd_signal'] = macd['MACDs_12_26_9']

        # Bollinger Bands
        bb = ta.bbands(df['close'], length=20)
        df['bb_upper'] = bb['BBU_20_2.0']
        df['bb_middle'] = bb['BBM_20_2.0']
        df['bb_lower'] = bb['BBL_20_2.0']

        # ATR
        df['atr_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        return df

    def compute_return_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute lagged returns and momentum features"""
        # Lagged returns
        for lag in [1, 5, 10, 20]:
            df[f'return_{lag}d'] = df['close'].pct_change(lag)

        # Overnight return
        df['overnight_return'] = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)

        # Intraday return
        df['intraday_return'] = (df['close'] - df['open']) / df['open']

        return df

    def generate_features(self, tickers: list, start_date: date, end_date: date) -> pd.DataFrame:
        """Main method: orchestrate feature computation"""
        # Load OHLCV data from DuckDB
        query = f"""
            SELECT ticker, date, open, high, low, close, volume
            FROM equity_all
            WHERE ticker IN {tuple(tickers)}
            AND date BETWEEN '{start_date}' AND '{end_date}'
        """
        df = self.conn.execute(query).df()

        # Compute features
        df = self.compute_technical_indicators(df)
        df = self.compute_return_features(df)
        # ... more feature methods

        return df
```

### B.2 Price Forecaster Example

```python
# src/equity_lake/forecasting.py

import xgboost as xgb
import pandas as pd
from datetime import date
import joblib

class PriceForecaster:
    def __init__(self):
        self.model = None

    def train_model(self, ticker: str, start_date: date, end_date: date):
        """Train XGBoost model for price prediction"""
        # Load features
        query = f"""
            SELECT * FROM features
            WHERE ticker = '{ticker}'
            AND date BETWEEN '{start_date}' AND '{end_date}'
        """
        df = conn.execute(query).df()

        # Prepare data
        feature_cols = [c for c in df.columns if c.startswith(('rsi_', 'macd_', 'return_', 'atr_'))]
        X = df[feature_cols].fillna(0)
        y = (df['close'].shift(-1) > df['close']).astype(int)  # Binary target

        # Time-based split
        split_idx = int(len(df) * 0.8)
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]

        # Train model
        params = {
            'max_depth': 5,
            'learning_rate': 0.05,
            'n_estimators': 200,
            'objective': 'binary:logistic',
            'eval_metric': 'logloss'
        }
        self.model = xgb.XGBClassifier(**params)
        self.model.fit(X_train, y_train,
                      eval_set=[(X_val, y_val)],
                      early_stopping_rounds=20,
                      verbose=False)

        # Save model
        joblib.dump(self.model, f"data/models/{ticker}_xgboost_{end_date}.pkl")

        return self.model

    def predict(self, ticker: str, date: date) -> dict:
        """Generate prediction for a specific date"""
        # Load latest features
        query = f"""
            SELECT * FROM features
            WHERE ticker = '{ticker}'
            AND date = '{date}'
        """
        df = conn.execute(query).df()

        # Load model
        model = joblib.load(f"data/models/{ticker}_xgboost_*.pkl")

        # Predict
        feature_cols = model.get_booster().feature_names
        X = df[feature_cols].fillna(0)
        probability = model.predict_proba(X)[0][1]
        prediction = (probability > 0.5).astype(int)

        return {
            'ticker': ticker,
            'date': date,
            'prediction': int(prediction),
            'probability': float(probability)
        }
```

### B.3 Risk Analyzer Example

```python
# src/equity_lake/risk_analyzer.py

import pandas as pd
import numpy as np
from datetime import date

class RiskAnalyzer:
    def calculate_var(self, tickers: list, weights: list, confidence: float = 0.95):
        """Calculate Value at Risk using historical method"""
        # Load historical returns
        query = f"""
            SELECT ticker, date,
                   (close - lag(close) OVER (PARTITION BY ticker ORDER BY date)) / lag(close) OVER (PARTITION BY ticker ORDER BY date) as daily_return
            FROM equity_all
            WHERE ticker IN {tuple(tickers)}
            AND date >= CURRENT_DATE - INTERVAL '252 days'
        """
        returns_df = conn.execute(query).df()

        # Calculate portfolio returns
        portfolio_returns = []
        for date in returns_df['date'].unique():
            daily_returns = returns_df[returns_df['date'] == date]['daily_return']
            portfolio_return = np.dot(daily_returns, weights)
            portfolio_returns.append(portfolio_return)

        portfolio_returns = np.array(portfolio_returns)

        # Historical VaR
        var_amount = np.percentile(portfolio_returns, (1 - confidence) * 100)
        cvar_amount = portfolio_returns[portfolio_returns <= var_amount].mean()

        return {
            'var_95_amount': abs(var_amount),
            'var_95_pct': abs(var_amount),
            'cvar_95_amount': abs(cvar_amount),
            'confidence': confidence
        }

    def compute_correlation_matrix(self, tickers: list, window: int = 60):
        """Compute rolling correlation matrix"""
        query = f"""
            SELECT ticker, date, close
            FROM equity_all
            WHERE ticker IN {tuple(tickers)}
            AND date >= CURRENT_DATE - INTERVAL '{window} days'
        """
        df = conn.execute(query).df()

        # Pivot to get tickers as columns
        prices = df.pivot(index='date', columns='ticker', values='close')

        # Calculate returns
        returns = prices.pct_change()

        # Correlation matrix
        corr_matrix = returns.corr()

        return corr_matrix

    def run_monte_carlo(self, tickers: list, weights: list,
                       n_simulations: int = 10000, days: int = 10):
        """Run Monte Carlo simulation for portfolio"""
        # Load historical data
        query = f"""
            SELECT ticker, date, close
            FROM equity_all
            WHERE ticker IN {tuple(tickers)}
            AND date >= CURRENT_DATE - INTERVAL '252 days'
        """
        df = conn.execute(query).df()

        # Calculate drift and volatility
        returns = df.pivot(index='date', columns='ticker', values='close').pct_change()
        mu = returns.mean() * 252  # Annualized
        sigma = returns.std() * np.sqrt(252)  # Annualized

        # Correlation matrix
        corr = returns.corr()

        # Cholesky decomposition
        L = np.linalg.cholesky(corr)

        # Simulate
        paths = []
        for _ in range(n_simulations):
            # Generate correlated random variables
            Z = L @ np.random.normal(0, 1, (len(tickers), days))

            # Generate price paths (GBM)
            S0 = df.groupby('ticker')['close'].last().values
            dt = 1/252  # Daily
            path = np.zeros((len(tickers), days))
            path[:, 0] = S0

            for t in range(1, days):
                path[:, t] = path[:, t-1] * np.exp((mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * Z[:, t])

            paths.append(path)

        # Calculate portfolio values
        portfolio_values = []
        for path in paths:
            portfolio_value = np.dot(weights, path[:, -1])
            portfolio_values.append(portfolio_value)

        return {
            'median': np.median(portfolio_values),
            'percentile_5': np.percentile(portfolio_values, 5),
            'percentile_95': np.percentile(portfolio_values, 95)
        }
```

---

## Appendix C: Makefile Updates

**Add to existing `Makefile`:**

```makefile
# ML/AI Commands

.PHONY: ml-features ml-train ml-predict ml-risk ml-dashboard

ml-features:
	@echo "Generating features..."
	uv run python -m equity_lake.features.engineering --date $$(date -d "yesterday" +%Y-%m-%d)

ml-train:
	@echo "Training ML models..."
	uv run python -m equity_lake.price_forecaster --mode train --tickers AAPL,GOOGL,MSFT

ml-predict:
	@echo "Generating predictions..."
	uv run python -m equity_lake.price_forecaster --mode predict --date $$(date -d "yesterday" +%Y-%m-%d)

ml-risk:
	@echo "Calculating risk metrics..."
	uv run python -m equity_lake.risk_analyzer --date $$(date -d "yesterday" +%Y-%m-%d)

ml-dashboard:
	@echo "Starting Streamlit dashboard..."
	streamlit run src/equity_lake/dashboard/app.py --server.port 8501

ml-pipeline: ml-features ml-predict ml-risk
	@echo "ML pipeline completed"

ml-test:
	@echo "Running ML tests..."
	uv run pytest tests/test_ml_*.py -v

ml-install:
	@echo "Installing ML dependencies..."
	uv add xgboost scikit-learn shap pandas-ta streamlit plotly seaborn networkx scipy statsmodels
	uv sync
```

---

## Document Metadata

**Version:** 1.0
**Last Updated:** 2026-01-24
**Status:** Ready for Implementation
**Reviewed by:** [Pending]
**Approved by:** [Pending]

**Change Log:**
- 2026-01-24: Initial design created (Claude Code + User Collaboration)

**Next Steps:**
1. Review and approve design
2. Begin Phase 1 implementation (Feature Engineering)
3. Set up weekly progress reviews
4. Iterate based on testing results

---

**End of Design Document**
