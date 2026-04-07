# Equity Data Sources Analysis & Recommendations 2026

**Project:** equity_lake EOD Data Pipeline
**Date:** 2026-02-28
**Author:** AI Research Analysis
**Status:** Research Complete

> Status note (2026-03-13): this remains an active research report, not an
> implementation ledger. The repo now includes `cn_efinance.py` and
> `cn_hybrid.py`, but the current ingestion orchestrator still runs the China
> path with `akshare` active by default. Any recommendation text below that
> reads like a completed `efinance` migration, or a finished monitoring
> dashboard, should be interpreted as proposed direction rather than current
> shipped behavior.

---

## Executive Summary

This report provides a comprehensive analysis of equity data acquisition methods for the `equity_lake` project, comparing current implementations with available commercial and open-source alternatives. Key findings include:

- **Current stack (yfinance + akshare)** remains viable for EOD data but has reliability concerns
- **Multi-source redundancy** is emerging as industry best practice for 2026
- **efinance** offers superior performance for China A-shares compared to akshare
- **Free tiers** from Polygon.io, Finnhub, and FMP provide production-grade alternatives
- **IEX Cloud has shut down** (important for migration planning)

### Quick Recommendation

| Use Case | Recommended Source | Monthly Cost | Priority |
|----------|-------------------|--------------|----------|
| **Current: EOD Development** | Keep yfinance + akshare | $0 | Maintain |
| **China Real-Time** | Add efinance | $0 | High |
| **US Production** | Polygon.io Starter | $29 | Medium |
| **Financial Analysis** | FMP Starter | $19 | Low |
| **Institutional Grade** | Finnhub Professional | $200 | Low |

---

## 1. Current Implementation Analysis

### 1.1 Existing Data Sources

#### **US Equities: yfinance**
**File:** `src/equity_lake/ingestion/sources/us.py`

**Strengths:**
- ✅ Completely free, no rate limits on requests
- ✅ Wide market coverage (US, HK, SG, etc.)
- ✅ Mature Python library with active community
- ✅ Supports batch downloads for efficiency
- ✅ Includes adjusted close prices for total return calculations

**Weaknesses:**
- ❌ 10-15 minute data delays
- ❌ Unofficial API (Yahoo can change/break without notice)
- ❌ No service level agreement (SLA)
- ❌ Rate limiting can occur with frequent requests
- ❌ Occasional data quality issues

**Current Usage Pattern:**
```python
# From us.py:175-182
data = yf.download(
    self.tickers,
    start=start_date,
    end=end_date,
    group_by="ticker",
    progress=False,
    auto_adjust=False,
)
```

---

#### **China A-Shares: akshare**
**File:** `src/equity_lake/ingestion/sources/cn.py`

**Strengths:**
- ✅ Free, no registration required
- ✅ Comprehensive China market coverage
- ✅ Supports stocks, funds, bonds, futures
- ✅ Active development and community
- ✅ Good documentation (in Chinese)

**Weaknesses:**
- ❌ Anti-scraping measures can block requests
- ❌ Requires parallel threading for reasonable performance (currently 10 workers)
- ❌ Limited to 100 stocks in current implementation (`stock_limit=100`)
- ❌ Potential stability issues
- ❌ English documentation limited

**Current Usage Pattern:**
```python
# From cn.py:44-51
stock_data = ak.stock_zh_a_hist(
    symbol=stock_code,
    period="daily",
    start_date=date_str,
    end_date=date_str,
    adjust="",
)
```

---

#### **Hong Kong/Singapore: yfinance**
**File:** `src/equity_lake/ingestion/sources/hk_sg.py`

**Strengths/Weaknesses:** Same as US yfinance

**Current Coverage:**
- **HK:** 16 tickers (0700.HK, 9988.HK, etc.)
- **SG:** 10 tickers (D05.SI, O39.SI, etc.)

---

### 1.2 Identified Pain Points

1. **Reliability:** No official SLA or guaranteed uptime
2. **Rate Limiting:** Frequent requests can be throttled
3. **China Scale:** Current implementation limited to 100 stocks
4. **Single Source Failure:** No fallback mechanism if API goes down
5. **Data Delays:** 10-15 minute delays across all markets

---

## 2. Market Overview: Alternative Data Sources

### 2.1 Free & Open Source Options

#### **Yahoo Finance (yfinance)** ⭐ Current
- **Cost:** Free
- **Rate Limits:** None officially, but practical limits exist
- **History:** Decades of historical data
- **Real-time:** No (10-15min delayed)
- **Markets:** US, Europe, Asia
- **Best For:** Development, testing, personal projects

---

#### **Alpha Vantage**
- **Cost:** Free tier available
- **Rate Limits:** 25 requests/day, 5 requests/minute
- **History:** 20+ years
- **Real-time:** No (15min delayed in free tier)
- **Features:** 100+ technical indicators
- **Best For:** Technical analysis projects

**Code Example:**
```python
from alpha_vantage.timeseries import TimeSeries
ts = TimeSeries(key='YOUR_API_KEY', output_format='pandas')
data, _ = ts.get_daily(symbol='AAPL', outputsize='full')
```

---

#### **Tushare** (China)
- **Cost:** Freemium (requires registration/token)
- **Rate Limits:** 200 calls/day (free tier), higher with points
- **History:** Comprehensive A-share history
- **Real-time:** Yes (paid tiers)
- **Best For:** Professional China market analysis

**Comparison to akshare:**
| Feature | Tushare | akshare | efinance |
|---------|---------|---------|----------|
| Registration | Required | No | No |
| Data Quality | ★★★★★ | ★★★☆☆ | ★★★★☆ |
| Real-time | Paid only | Limited | Yes (free) |
| Ease of Use | ★★★☆☆ | ★★★★☆ | ★★★★★ |

---

#### **efinance** (China) ⭐ Recommended Addition
- **Cost:** Free
- **Rate Limits:** Minimal restrictions
- **History:** Extensive A-share history
- **Real-time:** Yes, minute-level granularity
- **Best For:** Real-time China trading systems

**Why efinance over akshare:**
- Better real-time capabilities
- More stable connection (fewer anti-scraping blocks)
- Faster data retrieval
- Cross-market integration (stocks + crypto)
- Better Python API design

---

#### **Baostock** (China)
- **Cost:** Free
- **Rate Limits:** Less restrictive than Tushare
- **History:** Good historical coverage
- **Real-time:** No
- **Best For:** Historical A-share analysis without registration

---

#### **stock-mcp** (Multi-Source Aggregator)
- **Cost:** Free (open-source)
- **Architecture:** Unified API with automatic failover
- **Features:** Built-in technical indicators (RSI, MACD, Bollinger)
- **Best For:** Applications requiring redundancy

**Architecture:**
```
stock-mcp API
    ├── yfinance (primary)
    ├── Alpha Vantage (fallback)
    └── Twelve Data (backup)
```

---

### 2.2 Commercial Options (with Free Tiers)

#### **Polygon.io** ⭐ Top Pick for US Production

| Plan | Price | Historical Data | Rate Limit | Real-time |
|------|-------|-----------------|------------|-----------|
| **Stocks Basic** | $0 | 2 Years | 5 calls/min | No |
| **Stocks Starter** | $29/mo | 5 Years | Unlimited | 15min delay |
| **Stocks Developer** | $79/mo | 10 Years | Unlimited | 15min delay |
| **Stocks Advanced** | $199/mo | 20+ Years | Unlimited | Yes |
| **Stocks Business** | $2,000/mo | 20+ Years | Unlimited | Yes + redistribution |

**Strengths:**
- ✅ 100% market coverage (all US equities)
- ✅ Institutional-grade data quality
- ✅ WebSocket support for real-time streaming
- ✅ Flat file delivery (daily CSV dumps via S3) for paid plans
- ✅ 99.99% uptime SLA
- ✅ REST + WebSocket APIs

**Weaknesses:**
- ❌ Free tier very limited (5 calls/min, 2 years history)
- ❌ Real-time data requires $199/month plan

**Best For:** Production US equity data when reliability is critical

---

#### **Financial Modeling Prep (FMP)** ⭐ Best Value for Fundamentals

| Plan | Price | Rate Limit | History | Data Delay |
|------|-------|------------|---------|------------|
| **Basic** | $0 | 250/day | 5 Years | EOD |
| **Starter** | $19/mo | 300/min | 5 Years | Real-time |
| **Premium** | $49/mo | 750/min | 30+ Years | Real-time |
| **Ultimate** | $99/mo | 3,000/min | 30+ Years | Real-time |

**Strengths:**
- ✅ Best for financial statement data (income statement, balance sheet, cash flow)
- ✅ 30+ years historical data (Premium+)
- ✅ Includes earnings call transcripts, 13F filings
- ✅ Very generous free tier (250/day, perpetual)
- ✅ Covers 70,000+ stocks across 90+ exchanges

**Weaknesses:**
- ❌ Real-time requires paid plan
- ❌ Primarily focused on US markets

**Best For:** Financial modeling, fundamental analysis, screening

---

#### **Finnhub** ⭐ Best for Real-Time Institutional Features

| Plan | Price | Rate Limit | History | Key Features |
|------|-------|------------|---------|--------------|
| **Free** | $0 | 60/min | 1 Year | US real-time (IEX), 50 WebSocket symbols |
| **Basic** | $49.99/mo | 150/min | Limited | Global profiles, crypto/forex |
| **Standard** | $129.99/mo | 300/min | Extended | Advanced fundamentals |
| **Professional** | $199.99/mo | 900/min | 20+ Years | Tick-level data |
| **All-in-One** | $3,000/mo | 900+/min | 20+ Years | Full global coverage |

**Strengths:**
- ✅ Real-time US data in free tier (via IEX)
- ✅ Low-latency WebSocket streaming
- ✅ Institutional features: insider trading, news sentiment
- ✅ Modular pricing (pay for what you need)

**Weaknesses:**
- ❌ International markets require paid add-ons (~$50/market)
- ❌ Free tier limited to 1 year history
- ❌ 30 calls/sec global hard cap

**Best For:** Real-time trading applications, sentiment analysis

---

#### **Twelve Data**
- **Free:** 8 requests/day
- **Paid:** $8/month for 800 requests/day
- **Coverage:** 80+ exchanges globally
- **Assets:** Stocks, forex, crypto, ETFs, indices
- **Best For:** Multi-asset portfolios, global coverage

---

#### **Marketstack**
- **Free:** 100 requests/month
- **Paid:** ~$25-50/month for higher volumes
- **Coverage:** 70+ exchanges, 170,000+ tickers
- **History:** 30+ years
- **Clients:** Uber, Amazon (enterprise-grade)
- **Best For:** Enterprise applications requiring global coverage

---

### 2.3 Institutional-Grade Solutions

#### **Intrinio**
- **US EOD Data:** ~$3,100/year
- **US Real-time:** ~$6,000/year
- **US Fundamentals:** ~$9,600/year
- **Real-time Options:** ~$30,000/year
- **Best For:** Hedge funds, trading platforms

**Comparison:**
| Feature | Intrinio | Polygon.io | FMP |
|---------|----------|------------|-----|
| EOD Data | $3,100/yr | $0 | $0 |
| Real-time | $6,000/yr | $199/mo | $19/mo |
| Fundamentals | $9,600/yr | Included | $19/mo |

**Verdict:** Intrinio is significantly more expensive than alternatives for similar data quality.

---

#### **Bloomberg Terminal**
- **Cost:** $24,000+ per year per terminal
- **Coverage:** Global, 40+ years history
- **Data Quality:** Industry standard
- **Best For:** Institutional desks, professional trading

---

#### **Wind (China) / 东方财富Choice**
- **Cost:** Enterprise pricing (contact sales)
- **Coverage:** Comprehensive China markets
- **Best For:** China-focused institutional investors

---

### 2.4 Deprecated/Shut Down Services

#### **❌ IEX Cloud - SHUT DOWN (2025)**
- **Status:** No longer operational
- **Migration Guide:** Available from Alpha Vantage
- **Impact:** Any existing IEX integrations need migration
- **Alternatives:** Polygon.io, Finnhub, FMP

---

## 3. Detailed Comparison Matrices

### 3.1 By Market Coverage

| Provider | US | China | HK | SG | Europe | Crypto | Forex |
|----------|----|----|----|----|----|--------|-------|
| **yfinance** | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **akshare** | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **efinance** | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Tushare** | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Alpha Vantage** | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| **Polygon.io** | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| **FMP** | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| **Finnhub** | ✅ | ❌ | ❌ | ❌ | ✅ (paid) | ✅ | ✅ |
| **Twelve Data** | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| **Marketstack** | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |

**Key Finding:** No single provider covers all markets. Multi-source strategy is required for global coverage.

---

### 3.2 By Pricing Tier (Monthly Cost)

| Provider | Free | <$20 | $20-50 | $50-200 | $200+ |
|----------|------|------|--------|---------|-------|
| **yfinance** | ✅ | - | - | - | - |
| **akshare** | ✅ | - | - | - | - |
| **efinance** | ✅ | - | - | - | - |
| **Alpha Vantage** | ✅ | - | - | - | - |
| **Polygon.io** | ⚠️ (5/min) | - | ✅ ($29) | ✅ ($79-199) | ✅ ($2,000) |
| **FMP** | ✅ (250/day) | ✅ ($19) | ✅ ($49) | ✅ ($99) | - |
| **Finnhub** | ✅ (60/min) | - | ✅ ($50) | ✅ ($130-200) | ✅ ($3,000) |
| **Twelve Data** | ⚠️ (8/day) | ✅ ($8) | - | - | - |
| **Marketstack** | ⚠️ (100/mo) | - | ✅ (~$25) | ✅ (~$50) | ✅ (enterprise) |
| **Intrinio** | ❌ | ❌ | ❌ | ❌ | ✅ ($3,100+/yr) |

**Legend:**
- ✅ Available at this tier
- ⚠️ Limited free tier
- ❌ Not available

---

### 3.3 By Feature Set

| Feature | yfinance | akshare | efinance | Polygon | FMP | Finnhub |
|---------|----------|---------|----------|---------|-----|---------|
| **EOD Data** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Real-time** | ❌ | ⚠️ | ✅ | 💰 | 💰 | ✅ (free US) |
| **Adjusted Close** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Dividends** | ✅ | ⚠️ | ⚠️ | ✅ | ✅ | ✅ |
| **Splits** | ✅ | ⚠️ | ⚠️ | ✅ | ✅ | ✅ |
| **Fundamentals** | ❌ | ⚠️ | ❌ | ✅ | ✅ | ✅ |
| **Financial Statements** | ❌ | ⚠️ | ❌ | ✅ | ✅ | ✅ |
| **Technical Indicators** | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| **News Sentiment** | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| **Insider Trading** | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| **WebSocket** | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ |
| **Bulk Downloads** | ❌ | ❌ | ❌ | ✅ (paid) | ❌ | ❌ |
| **API Documentation** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Python SDK** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Legend:**
- ✅ Full support
- ⚠️ Limited support
- ❌ Not supported
- 💰 Requires paid plan

---

### 3.4 By Data Quality & Reliability

| Provider | Data Quality | Uptime SLA | Official API | Error Rate | Update Frequency |
|----------|--------------|------------|--------------|------------|------------------|
| **yfinance** | ★★★☆☆ | None | ❌ | Medium | 15min delay |
| **akshare** | ★★★☆☆ | None | ❌ | Medium-High | Variable |
| **efinance** | ★★★★☆ | None | ⚠️ | Low | Real-time |
| **Alpha Vantage** | ★★★★☆ | Unknown | ✅ | Low | 15min delay |
| **Polygon.io** | ★★★★★ | 99.99% | ✅ | Very Low | Real-time |
| **FMP** | ★★★★☆ | Unknown | ✅ | Low | Real-time (paid) |
| **Finnhub** | ★★★★★ | 99.9% | ✅ | Very Low | Real-time |
| **Intrinio** | ★★★★★ | 99.9% | ✅ | Very Low | Real-time |
| **Bloomberg** | ★★★★★ | 99.99% | ✅ | Minimal | Real-time |

---

## 4. Pros & Cons Analysis

### 4.1 Current Stack (yfinance + akshare)

**Pros:**
- ✅ **Zero cost** - completely free
- ✅ **No registration** required
- ✅ **Unlimited requests** (practical limits only)
- ✅ **Decades of historical data** available
- ✅ **Active community** support
- ✅ **Simple integration** - minimal setup required

**Cons:**
- ❌ **No SLA or guarantees** - can break without notice
- ❌ **Rate limiting** can occur with frequent requests
- ❌ **Data delays** (10-15 minutes)
- ❌ **Single point of failure** - no fallback
- ❌ **China scaling issues** - limited to 100 stocks
- ❌ **Potential legal grey area** - scraping vs. official API

**Verdict:** Keep for development and testing, but add production-grade alternatives.

---

### 4.2 Recommended Addition: efinance

**Pros:**
- ✅ **Free** - no cost
- ✅ **Real-time data** - minute-level granularity
- ✅ **Better stability** than akshare (fewer anti-scraping blocks)
- ✅ **Faster retrieval** - optimized API
- ✅ **Cross-market** - stocks + crypto integration
- ✅ **No registration** required

**Cons:**
- ❌ **Documentation primarily in Chinese**
- ❌ **Smaller community** than akshare/tushare
- ❌ **Focused on China markets only**

**Verdict:** **High priority addition** for China A-shares. Replace or supplement akshare.

---

### 4.3 Polygon.io

**Pros:**
- ✅ **Institutional-grade quality** - 99.99% uptime
- ✅ **100% US market coverage**
- ✅ **WebSocket support** for real-time streaming
- ✅ **Bulk data delivery** (CSV via S3 for paid plans)
- ✅ **Official API** with guaranteed availability
- ✅ **Excellent documentation** and Python SDK

**Cons:**
- ❌ **Free tier very limited** (5 calls/min, 2 years history)
- ❌ **Real-time requires $199/month** plan
- ❌ **US-focused** - limited international coverage
- ❌ **Can get expensive** for production use

**Verdict:** Recommended for US production when reliability is critical. Start with free tier for testing.

---

### 4.4 Financial Modeling Prep (FMP)

**Pros:**
- ✅ **Best value for fundamentals** - $19/mo for financial statements
- ✅ **Generous free tier** - 250 calls/day, perpetual
- ✅ **30+ years history** on premium plans
- ✅ **Comprehensive financial data** - transcripts, 13F filings
- ✅ **Good Python SDK** and documentation

**Cons:**
- ❌ **Primarily US-focused**
- ❌ **Real-time requires paid plan**
- ❌ **Lower name recognition** vs. Polygon/Finnhub
- ❌ **Less suitable for pure price data** - strength is fundamentals

**Verdict:** Recommended addition for fundamental analysis and financial modeling use cases.

---

### 4.5 Finnhub

**Pros:**
- ✅ **Real-time US data in free tier** (via IEX)
- ✅ **Institutional features** - insider trading, sentiment
- ✅ **Low-latency WebSocket** streaming
- ✅ **Modular pricing** - pay for features you need
- ✅ **Global coverage** (with paid add-ons)

**Cons:**
- ❌ **International markets expensive** ($50/market/month)
- ❌ **Free tier limited to 1 year history**
- ❌ **Global 30 calls/sec hard cap**
- ❌ **Complex pricing** - can get expensive quickly

**Verdict:** Good choice if you need real-time US data for free, or institutional features like insider trading.

---

### 4.6 Multi-Source Architecture (stock-mcp pattern)

**Pros:**
- ✅ **Automatic failover** - if one source fails, try next
- ✅ **Redundancy** - higher reliability
- ✅ **Cost optimization** - use free tiers primarily, paid as backup
- ✅ **Data validation** - cross-check sources for accuracy

**Cons:**
- ❌ **Complexity** - more moving parts
- ❌ **Data normalization** - different schemas to unify
- ❌ **Maintenance overhead** - tracking API changes across providers
- ❌ **Rate limit management** - per-provider limits

**Verdict:** Industry best practice for 2026. Recommended for production systems.

---

## 5. Cost-Benefit Analysis

### 5.1 Monthly Cost Comparison

| Scenario | Configuration | Monthly Cost | Annual Cost |
|----------|--------------|--------------|-------------|
| **Current** | yfinance + akshare | $0 | $0 |
| **Free Tier Stack** | yfinance + akshare + efinance + FMP free | $0 | $0 |
| **Enhanced Free** | Above + Alpha Vantage + Finnhub free | $0 | $0 |
| **Hybrid (Basic)** | Free stack + Polygon Starter | $29 | $348 |
| **Hybrid (Pro)** | Free stack + FMP Starter + Finnhub Basic | $68 | $816 |
| **Production** | Polygon Advanced + FMP Premium + efinance | $248 | $2,976 |
| **Full Enterprise** | Polygon Business + Finnhub Professional + FMP Ultimate | $2,299 | $27,588 |

---

### 5.2 Feature vs. Cost Matrix

| Feature Set | Free | $29/mo | $68/mo | $248/mo | Enterprise |
|-------------|------|--------|--------|---------|------------|
| **US EOD Data** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **US Real-time** | ❌ | ⚠️ (15min) | ✅ | ✅ | ✅ |
| **China EOD** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **China Real-time** | ⚠️ | ⚠️ | ⚠️ | ✅ | ✅ |
| **Fundamentals** | ⚠️ | ⚠️ | ✅ | ✅ | ✅ |
| **10+ Year History** | ✅ | ⚠️ (5yr) | ✅ | ✅ | ✅ |
| **SLA Guarantee** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **Technical Indicators** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **WebSocket** | ❌ | ❌ | ✅ | ✅ | ✅ |
| **Bulk Data Export** | ❌ | ✅ | ❌ | ✅ | ✅ |

---

## 6. Recommendations

### 6.1 Short-Term (Immediate - Next 1-2 Months)

#### Priority 1: Add efinance for China Markets 🚨
**Action:** Integrate efinance alongside or replacing akshare for China A-shares

**Benefits:**
- Better real-time data
- More stable connections
- Faster performance
- No additional cost

**Implementation:**
```python
# New file: src/equity_lake/ingestion/sources/cn_efinance.py
class CNEfinanceFetcher(MarketDataFetcher):
    """Fetch China A-share data using efinance for better real-time performance."""
    # Implementation similar to akshare but using efinance API
```

**Migration Path:**
1. Add efinance as optional source
2. Run parallel testing with akshare
3. Compare data quality and performance
4. Gradually migrate to efinance as primary source

---

#### Priority 2: Implement Multi-Source Fallback
**Action:** Add fallback mechanism for critical failures

**Architecture:**
```
Primary: yfinance/akshare/efinance
    ↓ (if fails)
Fallback: FMP free tier (250/day)
    ↓ (if fails)
Backup: Finnhub free tier (60/min)
```

**Benefits:**
- Higher reliability
- Graceful degradation
- Zero additional cost

---

#### Priority 3: Cache Optimization
**Action:** Implement aggressive caching to reduce API dependency

**Strategy:**
- Cache all historical data locally (already done with Parquet)
- Only fetch incremental updates
- Implement retry with exponential backoff
- Use parallel fetching for batch operations

---

### 6.2 Medium-Term (3-6 Months)

#### Phase 1: Commercial Tier Evaluation
**Action:** Trial Polygon.io Starter and FMP Starter

**Goals:**
- Test data quality vs. free sources
- Evaluate reliability improvements
- Assess ROI of paid tiers
- Benchmark performance

**Decision Criteria:**
- If reliability > 99.5% → Consider paid tier
- If speed improvement > 50% → Consider paid tier
- If data quality issues with free sources → Migrate

---

#### Phase 2: Hybrid Architecture
**Action:** Design multi-source architecture

**Proposed Stack:**
```
Development/Testing:
  - yfinance (US/HK/SG)
  - efinance (China)
  - FMP free tier (fallback)

Production:
  - Polygon Starter (US EOD)
  - efinance (China)
  - FMP Starter (fundamentals)
  - yfinance (emergency fallback)
```

**Benefits:**
- Cost optimization ($29-68/month vs. $200+/month)
- Production-grade reliability
- Redundancy and failover
- Best of both worlds

---

### 6.3 Long-Term (6-12 Months)

#### Phase 1: Real-Time Requirements Assessment
**Question:** Does the project need real-time data?

**If YES:**
- Evaluate Polygon Advanced ($199/mo) or Finnhub Professional ($200/mo)
- Consider WebSocket integration for streaming
- Assess latency requirements

**If NO:**
- Continue with EOD-focused stack
- Optimize for historical analysis
- Focus on data breadth rather than speed

---

#### Phase 2: Global Expansion
**Action:** Add European and Asian market coverage

**Options:**
1. **Twelve Data** ($8-50/month) - 80+ exchanges
2. **Marketstack** - enterprise-grade global coverage
3. **Multi-source approach** - regional providers per market

---

#### Phase 3: Institutional Features
**Action:** Add advanced data types if needed

**Options:**
- **FMP Ultimate** ($99/mo) - transcripts, 13F filings
- **Finnhub Professional** ($200/mo) - insider trading, sentiment
- **Intrinio** ($3,100+/year) - if institutional-grade required

---

## 7. Implementation Roadmap

### 7.1 Phase 1: Quick Wins (Week 1-2)

**Tasks:**
1. ✅ Add efinance integration for China markets
2. ✅ Implement basic multi-source fallback
3. ✅ Add comprehensive logging for data quality monitoring
4. ✅ Create health check endpoints for data sources

**Deliverables:**
- New `cn_efinance.py` fetcher
- Updated orchestrator with fallback logic
- Monitoring dashboard

---

### 7.2 Phase 2: Commercial Evaluation (Month 2-3)

**Tasks:**
1. ✅ Sign up for Polygon.io, FMP, Finnhub free tiers
2. ✅ Build comparison framework (data quality, latency, uptime)
3. ✅ Run parallel testing for 4 weeks
4. ✅ Document findings and ROI analysis

**Deliverables:**
- Comparison report with metrics
- Cost-benefit analysis
- Recommendation document

---

### 7.3 Phase 3: Production Migration (Month 4-6)

**Tasks:**
1. ✅ Implement chosen commercial tier(s)
2. ✅ Build multi-source orchestrator with automatic failover
3. ✅ Add caching layer for API call optimization
4. ✅ Create monitoring and alerting
5. ✅ Gradual migration with blue-green deployment

**Deliverables:**
- Production-ready multi-source ingestion system
- Monitoring and alerting setup
- Documentation and runbooks

---

## 8. Risk Assessment

### 8.1 Current Stack Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **yfinance API breaks** | Medium | High | Multi-source fallback |
| **akshare blocked** | Medium | High | Switch to efinance |
| **Rate limiting** | High | Medium | Implement caching, retry logic |
| **Data quality issues** | Low | Medium | Cross-validation with multiple sources |

---

### 8.2 Migration Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Cost overruns** | Medium | Medium | Usage monitoring, alerts |
| **Vendor lock-in** | Low | High | Multi-source architecture |
| **Data schema changes** | Low | Medium | Versioned data models |
| **Integration complexity** | Medium | Low | Incremental migration |

---

## 9. Key Decision Points

### Decision 1: Stay Free or Move to Paid?

**Stay Free If:**
- Project is for learning/development
- EOD data is sufficient
- Budget is constrained
- Occasional outages are acceptable

**Move to Paid If:**
- Production reliability required
- Real-time data needed
- SLA guarantees necessary
- Business value justifies cost

**Recommendation:** **Start with enhanced free stack** (yfinance + efinance + FMP free tier), evaluate paid options after 3 months of monitoring.

---

### Decision 2: Single Source or Multi-Source?

**Single Source If:**
- Simple use case
- Low data volume
- Minimal reliability requirements

**Multi-Source If:**
- Production environment
- High reliability required
- Need redundancy
- Want cost optimization

**Recommendation:** **Multi-source architecture** - industry best practice for 2026.

---

### Decision 3: China Market Strategy

**Options:**
1. **Keep akshare** - Current implementation, known limitations
2. **Switch to efinance** - Better performance, real-time
3. **Switch to Tushare** - More standardized, requires token
4. **Use multiple** - Redundancy and validation

**Recommendation:** **Migrate to efinance as primary**, keep akshare as fallback for 100+ stock scenarios.

---

## 10. Summary & Next Steps

### 10.1 Key Takeaways

1. **Current stack is viable** for development but has reliability concerns
2. **efinance is superior** for China A-shares compared to akshare
3. **Multi-source redundancy** is critical for production systems
4. **Free tiers are sufficient** for many use cases
5. **IEX Cloud shutdown** means migrations may be needed
6. **Polygon.io and FMP** offer best value in paid tiers

---

### 10.2 Immediate Action Items

#### This Week:
1. ✅ Add efinance to project dependencies
2. ✅ Create `cn_efinance.py` fetcher implementation
3. ✅ Sign up for FMP, Polygon, Finnhub free tiers
4. ✅ Create data quality monitoring dashboard

#### This Month:
1. ✅ Implement multi-source fallback logic
2. ✅ Run parallel testing with all sources
3. ✅ Document performance metrics
4. ✅ Present findings and recommendations

#### Next Quarter:
1. ✅ Evaluate commercial tier ROI
2. ✅ Design hybrid architecture
3. ✅ Plan production migration
4. ✅ Build monitoring and alerting

---

### 10.3 Recommended Technology Stack

#### For Development/Testing:
```python
US Markets:    yfinance (free, unlimited)
China Markets: efinance (free, real-time) + akshare (fallback)
HK/SG Markets: yfinance (free)
Fundamentals:  FMP free tier (250/day)
```

#### For Production (EOD Focus):
```python
US Markets:    Polygon Starter ($29/mo) or FMP Starter ($19/mo)
China Markets: efinance (free) + Tushare Pro (paid, if needed)
HK/SG Markets: yfinance (free) or Polygon Starter
Fundamentals:  FMP Starter ($19/mo)
Fallback:      yfinance + akshare
```

#### For Production (Real-Time):
```python
US Markets:    Polygon Advanced ($199/mo) or Finnhub Professional ($200/mo)
China Markets: efinance (free, real-time)
Fundamentals:  FMP Premium ($49/mo)
Backup:        Free tier stack
```

---

## 11. Sources & References

### Web Search Sources:
1. [2025 Free Stock API Collection](https://learnku.com/articles/91346)
2. [10 Best Stock APIs for Developers](https://learnku.com/articles/89627)
3. [Stock-mcp GitHub Repository](https://github.com/huweihua123/stock-mcp)
4. [Four Practical Stock Data APIs](https://m.blog.csdn.net/CryptoLL/article/details/156392683)
5. [China Stock Data: efinance vs akshare vs tushare](https://m.bilibili.com/opus/693822275582951424)
6. [Daily Stock Analysis Multi-Source Strategy](https://m.blog.csdn.net/gitblog_00782/article/details/153092255)
7. [2026 US Stock API Selection Guide](https://cloud.tencent.com/developer/article/2625654)
8. [IEX Cloud Shutdown Analysis](https://www.alphavantage.co/iexcloud_shutdown_analysis_and_migration/)
9. [2025 Five Major Stock Data Providers](https://www.bright.cn/blog/web-data/best-stock-data-providers)
10. [Intrinio Official Site](https://intrinio.com/)

### Google AI Mode Sources:
1. [Polygon.io Pricing and Features 2026](https://www.f6s.com/software/polygon-io)
2. [Finnhub API Pricing 2026](https://finnhub.io/)
3. [Financial Modeling Prep Pricing](https://site.financialmodelingprep.com/pricing-plans)
4. [Best Real-Time Stock Data APIs](https://www.mexc.co/en-IN/news/476023)
5. [Polygon Python Integration Guide](https://python.plainenglish.io/how-to-automatically-download-and-store-daily-stock-prices-using-the-polygon-api-and-python-b241ad8aa6c5)

---

## Appendix A: API Quick Reference

### A.1 yfinance
```python
import yfinance as yf

# Download data
data = yf.download('AAPL', start='2024-01-01', end='2024-12-31')

# Multi-ticker
data = yf.download(['AAPL', 'GOOGL'], start='2024-01-01', group_by='ticker')
```

### A.2 efinance
```python
import efinance as ef

# Real-time stock quote
stock_quote = ef.stock.get_realtime_quotes('000001')

# Historical data
stock_data = ef.stock.get_hist_data('000001', beg='20240101', end='20241231')
```

### A.3 Polygon.io
```python
import requests

# API key from polygon.io
api_key = 'YOUR_API_KEY'

# Get daily OHLCV
url = f'https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-12-31?apiKey={api_key}'
response = requests.get(url)
data = response.json()
```

### A.4 FMP
```python
import requests

api_key = 'YOUR_API_KEY'

# Get historical price
url = f'https://financialmodelingprep.com/api/v3/historical-price-full/AAPL?apikey={api_key}'
response = requests.get(url)
data = response.json()
```

### A.5 Finnhub
```python
import finnhub

client = finnhub.Client(api_key='YOUR_API_KEY')

# Get candle data
data = client.stock_candles('AAPL', 'D', 1609459200, 1609872000)
```

---

**End of Report**

---

*Prepared by:* AI Research Analysis
*Project:* equity_lake
*Last Updated:* 2026-02-28
*Version:* 1.0
