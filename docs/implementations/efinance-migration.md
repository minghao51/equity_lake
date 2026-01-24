# 🔄 Data Source Migration: akshare → efinance for China A-Shares

**Created**: 2026-01-15
**Status**: Design Approved
**Implementation Target**: 2 days (Phased rollout)

---

## 📋 Executive Summary

Migrate China A-share data source from **akshare** to **efinance** (East Money API) to achieve:
- ✅ **20+ years of historical data** (vs. ~3-5 years with akshare)
- ✅ **100% free** - no registration, tokens, or payment required
- ✅ **Better reliability** - East Money is a major Chinese financial data provider
- ✅ **Backward compatible** - no changes to existing Parquet files or DuckDB queries

**Scope:**
- **China A-shares**: Switch from akshare → efinance
- **Hong Kong**: Keep yfinance (no changes)
- **US**: Keep yfinance (no changes)

---

## 🎯 Requirements Analysis

### Business Requirements
1. **Historical Data Depth**: 10-20+ years for backtesting
2. **Market Coverage**: China A-shares (SSE + SZSE), Hong Kong (HKEX)
3. **Cost**: Free, no license fees or token costs
4. **Reliability**: Production-ready with minimal downtime

### Technical Requirements
1. **Schema Compatibility**: Must match existing OHLCV schema
2. **Query Compatibility**: DuckDB queries must continue to work
3. **Performance**: Daily ingest < 5 minutes, backfill < 1 day
4. **Error Handling**: Graceful degradation on API failures

---

## 🏗️ Architecture Design

### Current State
```
China A-shares: akshare API → DataFrame → Parquet → DuckDB
Hong Kong:     yfinance API → DataFrame → Parquet → DuckDB
US:            yfinance API → DataFrame → Parquet → DuckDB
```

### Target State
```
China A-shares: efinance API → DataFrame → Normalize → Parquet → DuckDB
Hong Kong:     yfinance API → DataFrame → Parquet → DuckDB (unchanged)
US:            yfinance API → DataFrame → Parquet → DuckDB (unchanged)
```

### Key Design Principles

1. **Source Abstraction**: Pipeline remains agnostic to data source
2. **Schema Normalization**: All sources conform to standard OHLCV schema
3. **Backward Compatibility**: Existing data and queries unchanged
4. **Graceful Degradation**: Failures don't break entire pipeline

### Data Flow

**Daily Ingestion Flow:**
```
1. Fetch stock list (efinance.stock.get_base_info())
   └─> Returns 4000+ A-share tickers

2. Fetch OHLCV data (efinance.stock.get_quote_history())
   └─> Input: stock_codes, date_range
   └─> Output: DataFrame with Chinese columns

3. Schema normalization (normalize_efinance_data())
   ├─> Column renaming: 股票代码 → ticker
   ├─> Date conversion: 20240101 → 2024-01-01
   └─> Type conversion: str → float/int/date

4. Write to Parquet (write_to_partitioned_parquet())
   └─> Path: data/lake/cn_ashare/date=YYYY-MM-DD/

5. Validate & Log
   └─> Row count, file size, schema check
```

**Historical Backfill Flow:**
```
1. Batch strategy
   └─> Process 1 year at a time (avoid timeouts)

2. Progress tracking
   └─> State file tracks completed years
   └─> Resume capability if interrupted

3. Rate limiting
   └─> 100ms delay between batches
   └─> Respect API limits

4. Data quality checks
   ├─> Row count sanity check
   ├─> Null value analysis
   └─> Schema consistency
```

---

## 🔧 Component Design

### New Components

#### 1. EfinanceChinaFetcher Class
**Location**: `scripts/ingest_daily.py` (add to existing file)

**Responsibilities**:
- Fetch stock list from efinance
- Fetch OHLCV data for date ranges
- Handle API errors with retry logic
- Return normalized DataFrame

**Key Methods**:
```python
class EfinanceChinaFetcher(MarketDataFetcher):
    def __init__(self, retry_attempts=3, retry_delay=1.0):
        # Initialize with retry configuration
        pass

    def fetch(self, trading_date: date) -> pd.DataFrame:
        # Fetch data for specific date
        # Uses: efinance.stock.get_quote_history()
        pass

    def _get_stock_list(self) -> List[str]:
        # Get all A-share tickers
        # Uses: efinance.stock.get_base_info()
        pass
```

**API Endpoints**:
- Stock list: `efinance.stock.get_base_info()`
- Historical data: `efinance.stock.get_quote_history(stock_codes, beg, end)`

#### 2. Schema Normalizer Function
**Location**: `scripts/ingest_daily.py` (new utility function)

**Responsibilities**:
- Convert Chinese column names to English
- Transform date formats (YYYYMMDD → YYYY-MM-DD)
- Ensure proper data types (float, int, date)
- Sort and de-duplicate data

**Column Mapping**:
```python
COLUMN_MAPPING = {
    '股票代码': 'ticker',
    '日期': 'date',
    '开盘': 'open',
    '最高': 'high',
    '最低': 'low',
    '收盘': 'close',
    '成交量': 'volume',
    '成交额': 'amount',  # Optional
}
```

**Signature**:
```python
def normalize_efinance_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize efinance data to standard OHLCV schema.

    Args:
        df: Raw efinance DataFrame

    Returns:
        Normalized DataFrame with STANDARD_COLUMNS
    """
```

#### 3. Historical Backfill Script
**Location**: `scripts/backfill_china_historical.py` (new file)

**Responsibilities**:
- Fetch 20+ years of historical data
- Process in yearly batches
- Track progress and support resume
- Validate data quality

**Key Features**:
- Batch processing (1 year per batch)
- Progress tracking (state file)
- Rate limiting (100ms delay)
- Resume capability (if interrupted)

**CLI Interface**:
```bash
python -m scripts.backfill_china_historical \
    --start-date 2004-01-01 \
    --end-date 2024-11-30 \
    --batch-size 365 \
    --delay 0.1
```

### Modified Components

#### Update fetch_market_data() Function
**Location**: `scripts/ingest_daily.py`

**Changes**:
```python
# Add new elif branch
if market == "us":
    fetcher = USEquityFetcher(...)
elif market == "cn":
    fetcher = EfinanceChinaFetcher(...)  # NEW
elif market == "hk_sg":
    fetcher = HKSGEquityFetcher(...)

# Keep old as fallback (optional)
# elif market == "cn":
#     fetcher = CNAshareFetcher(...)  # DEPRECATED
```

**Impact**: 5-10 lines of code change

---

## 🛡️ Error Handling Strategy

### Four-Layer Error Handling

**Layer 1: API-Level Retries**
- Exponential backoff: 1s → 2s → 4s
- Max attempts: 3 (configurable)
- Catches: Network errors, timeouts, HTTP 5xx

**Layer 2: Data-Level Validation**
- Empty DataFrame → Log warning, skip write
- Schema validation → Fail if missing columns
- Null check → Warn if >50% null in critical columns
- Date consistency → Ensure data matches target date

**Layer 3: Graceful Degradation**
- **Scenario**: efinance API completely down
- **Action**: Log critical error, continue to HK/US
- **Result**: Partial success (China skipped, others ingest)
- **Recovery**: Retry on next scheduled run

**Layer 4: Monitoring & Alerts**
- Log failures to `logs/ingest_daily.log`
- Track success rate per market
- Alert on 3+ consecutive failures

### Failure Scenarios

**Example 1: Single Stock Failure**
```
Error: Failed to fetch 600000.SH on 2024-12-01
Action: Log warning, continue with other 3999 stocks
Result: 99.98% success rate
```

**Example 2: API Timeout**
```
Error: efinance API timeout after 30s
Action: Retry with exponential backoff (3 attempts)
Result: Success on 2nd attempt
```

**Example 3: Schema Mismatch**
```
Error: Missing column 'volume' in response
Action: Fail fast, log critical error
Result: Skip China market, continue HK/US
```

---

## 🧪 Testing Strategy

### Unit Tests

**File**: `tests/test_efinance_fetcher.py` (new)

**Test Cases**:
1. Schema normalization
2. Fetcher returns DataFrame
3. Retry logic on API failure
4. Column mapping correctness
5. Date format conversion

### Integration Tests

**Test Cases**:
1. End-to-end daily ingestion
2. Historical backfill (1-month sample)
3. DuckDB query compatibility
4. Parquet file validation

### Manual Testing Checklist

Before production deployment:

- [ ] Test single stock fetch
- [ ] Test date range (1 week)
- [ ] Test edge cases (weekends, holidays, IPOs)
- [ ] Test Parquet write/read
- [ ] Test DuckDB query compatibility
- [ ] Test rate limiting
- [ ] Test historical backfill (1 year)

### Data Quality Validation

Post-ingestion validation function:
```python
def validate_china_ashare_data(df: pd.DataFrame) -> Dict[str, Any]:
    """Validate China A-share data quality."""
    return {
        'row_count': len(df),
        'unique_tickers': df['ticker'].nunique(),
        'null_percentage': df.isnull().sum() / len(df) * 100,
        'date_range': (df['date'].min(), df['date'].max()),
        'price_sanity': (
            (df['high'] >= df['low']) &
            (df['close'] >= df['low']) &
            (df['close'] <= df['high'])
        ).all()
    }
```

---

## 📅 Implementation Plan

### Phase 1: Incremental Rollout (1-2 days)

**Goal**: Test efinance with small date range before full migration.

**Step 1: Install efinance**
```bash
uv pip install efinance
```

**Step 2: Implement Core Components**
- Create `EfinanceChinaFetcher` class
- Implement `normalize_efinance_data()` function
- Add unit tests

**Step 3: Test with Small Date Range**
```bash
# Fetch last 30 days (dry-run)
python -m scripts.backfill_china_historical \
    --start-date 2024-11-01 \
    --end-date 2024-11-30 \
    --dry-run
```

**Step 4: Compare with Existing Data**
- Query both akshare and efinance for same period
- Verify OHLCV values match
- Check for missing tickers
- Validate schema consistency

**Step 5: Production Test**
```bash
# Fetch yesterday's data
python -m scripts.ingest_daily --markets cn --date 2024-12-01
```

**Success Criteria**:
- ✅ efinance returns valid data for 30-day period
- ✅ Schema matches existing akshare data
- ✅ Parquet files readable by DuckDB
- ✅ No API rate limit issues

### Phase 2: Full Migration (1 day)

**Goal**: Replace akshare with efinance in production.

**Step 1: Full Historical Backfill**
```bash
# Backfill 20 years (2004-2024)
python -m scripts.backfill_china_historical \
    --start-date 2004-01-01 \
    --end-date 2024-11-30
```

**Expected Time**: 5-10 hours (4000 stocks × 20 years)

**Step 2: Update Pipeline Configuration**
- Modify `fetch_market_data()` to use `EfinanceChinaFetcher`
- Keep `CNAshareFetcher` as fallback (commented)
- Update environment variables if needed

**Step 3: Update Documentation**
- Update `CLAUDE.md` (reference efinance)
- Update `README.md` (installation instructions)
- Add troubleshooting section

**Step 4: Deploy to Production**
- Test daily cron with new fetcher
- Monitor logs for 3-5 days
- Verify data quality daily

**Step 5: Cleanup (Optional)**
- Remove akshare from dependencies
- Delete `CNAshareFetcher` class
- Remove unused code

---

## ⚠️ Risk Mitigation

### Risk 1: efinance API Changes
**Probability**: Low
**Impact**: High

**Mitigation**:
- Pin efinance version in requirements.txt
- Monitor for updates before upgrading

**Recovery**:
- Keep akshare as fallback for 30 days
- Rollback if critical issues arise

### Risk 2: Historical Backfill Fails
**Probability**: Medium
**Impact**: Medium

**Mitigation**:
- Process in 1-year batches
- Implement resume capability
- Add progress tracking

**Recovery**:
- Re-run failed years individually
- Adjust batch size or delay

### Risk 3: Data Quality Issues
**Probability**: Low
**Impact**: High

**Mitigation**:
- Compare with akshare for overlapping period
- Validate data quality checks
- Monitor for anomalies

**Recovery**:
- Re-fetch problematic date ranges
- Report issues to efinance maintainers

### Risk 4: Rate Limiting
**Probability**: Medium
**Impact**: Medium

**Mitigation**:
- Implement 100ms delays
- Use exponential backoff
- Monitor for HTTP 429 errors

**Recovery**:
- Increase delay between requests
- Reduce batch size
- Contact efinance for rate limit increase

---

## 🔄 Rollback Plan

If critical issues arise in production:

```bash
# 1. Stop efinance ingestion
# 2. Switch back to akshare
# Edit scripts/ingest_daily.py
# Change: EfinanceChinaFetcher → CNAshareFetcher

# 3. Restart daily job
make daily

# 4. Verify logs
tail -f logs/ingest_daily.log
```

**Rollback Time**: < 5 minutes

---

## 📊 Success Criteria

### Functional Requirements
- ✅ efinance successfully fetches China A-share data
- ✅ Historical backfill completes (20+ years)
- ✅ Daily ingestion works reliably
- ✅ DuckDB queries return correct results

### Performance Requirements
- ✅ Daily ingest < 5 minutes for 4000 stocks
- ✅ Historical backfill < 1 day for 20 years
- ✅ Query performance unchanged

### Quality Requirements
- ✅ Data matches akshare for overlapping period
- ✅ Schema compliance 100%
- ✅ Test coverage > 80%
- ✅ Zero data loss during migration

---

## 📚 References

### efinance Documentation
- GitHub: https://github.com/efinance-team/efinance
- PyPI: https://pypi.org/project/efinance/
- Docs: https://efinance.readthedocs.io/

### East Money Data
- Website: https://data.eastmoney.com/
- API: https://push2.eastmoney.com/

### Comparison with Alternatives
| Feature | efinance | akshare | tushare | baostock |
|---------|----------|---------|---------|----------|
| Cost | Free | Free | Paid (free tier limited) | Free |
| History | 20+ yrs | 3-5 yrs | 20+ yrs | 20+ yrs |
| Reliability | High | Medium | High | Low |
| Maintenance | Active | Active | Active | Stale |
| Registration | None | None | Required | None |

---

## 📝 Notes

### Key Implementation Details
1. **Batch Processing**: Process 1 year at a time during backfill to avoid timeouts
2. **Rate Limiting**: Add 100ms delay between API calls to respect efinance limits
3. **State Management**: Track progress in JSON file for resume capability
4. **Date Format**: efinance uses YYYYMMDD, convert to YYYY-MM-DD for Parquet

### Future Enhancements
- [ ] Add real-time data option (efinance supports streaming)
- [ ] Implement incremental backfill (only missing dates)
- [ ] Add data quality monitoring dashboard
- [ ] Integrate with more East Money endpoints (fundamentals, etc.)

---

**Last Updated**: 2026-01-15
**Next Steps**: Begin Phase 1 implementation
