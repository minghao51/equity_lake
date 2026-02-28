# CONCERNS.md - Technical Debt & Concerns

## Overview

This document tracks technical debt, bugs, security concerns, and performance issues identified in the codebase.

## Last Updated

2026-02-28

## Summary Statistics

| Category | Count | Severity |
|----------|-------|----------|
| Large Files | 5 | Medium |
| Code Quality Issues | 45+ | Low-Medium |
| Performance Concerns | 3 | Medium |
| Security Issues | 0 | None |
| Missing Features | 2 | Low |

## Code Complexity

### Large Files (>500 lines)

| File | Lines | Concern |
|------|-------|---------|
| `src/equity_lake/ingestion/orchestrator.py` | 816 | **High complexity** - Consider splitting into smaller modules |
| `src/equity_lake/validators.py` | 633 | Large validation module - Could split by domain |
| `src/equity_lake/devtools/test_data.py` | 624 | Test data generator - Acceptable size |
| `src/equity_lake/storage/duckdb.py` | 593 | Database operations - Consider splitting queries |
| `src/equity_lake/run_pipeline.py` | 565 | Pipeline orchestration - Could be refactored |

**Recommendations**:
1. **orchestrator.py**: Split into `coordinator.py` and `executor.py`
2. **validators.py**: Split into `schema_validators.py` and `data_validators.py`
3. **duckdb.py**: Extract query definitions to separate module

## Code Quality Issues

### Print Statements (45+ occurrences)

**Severity**: Low

**Issue**: Extensive use of `print()` statements instead of structured logging

**Locations**:
- Multiple files throughout the codebase
- Mixed with structlog usage

**Impact**:
- Inconsistent output format
- Difficult to control log levels
- No structured metadata
- Cannot redirect to files easily

**Example**:
```python
# Current (bad)
print(f"Fetching data for {ticker}...")

# Should be (good)
logger.info("Fetching data", ticker=ticker)
```

**Recommendation**: Replace all `print()` statements with `structlog` calls

### Type Hint Coverage (~50%)

**Severity**: Medium

**Issue**: Inconsistent type hint coverage across codebase

**Impact**:
- Reduced IDE support
- Harder to catch bugs early
- MyPy errors in strict mode

**Recommendation**:
1. Enable `disallow_untyped_defs = true` in mypy config
2. Add type hints to all function signatures
3. Run `make check` in CI

### Missing Error Handling

**Severity**: Medium

**Issue**: Limited error handling patterns - only 3 files use bare `except:`

**Impact**:
- Potential unhandled exceptions
- Poor error messages for users
- Difficult debugging

**Locations**:
- Most ingestion sources have basic error handling
- Pipeline orchestration lacks comprehensive error recovery

**Recommendation**:
1. Implement circuit breaker pattern for API calls
2. Add comprehensive error handling in orchestrator
3. Create custom exception hierarchy

## Performance Concerns

### No Async I/O

**Severity**: Medium

**Issue**: All operations are synchronous, no async/await

**Impact**:
- Slower API calls (could parallelize with asyncio)
- Inefficient use of I/O wait time
- Lower throughput for bulk operations

**Current**: Thread-based parallelism in `parallel.py`

**Recommendation**:
```python
# Future enhancement
async def fetch_market_data_async(date: date) -> pd.DataFrame:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()
```

### S3 Sync via Subprocess

**Severity**: Medium

**Issue**: S3 sync uses subprocess calls to AWS CLI or s5cmd

**Location**: `src/equity_lake/storage/s3_sync.py`

**Impact**:
- Slower than native Python (boto3)
- Dependency on external binaries
- Harder error handling

**Current**:
```python
subprocess.run(["aws", "s3", "cp", ...])
```

**Recommendation**: Use boto3 for native Python S3 operations

### Database Query Optimization

**Severity**: Low

**Issue**: No query optimization or materialized views

**Location**: `src/equity_lake/storage/duckdb.py`

**Impact**:
- Repeated complex queries
- No caching of frequent queries

**Recommendation**:
```python
# Create materialized views
con.execute("""
    CREATE MATERIALIZED VIEW daily_summary AS
    SELECT date, market, COUNT(*) as ticker_count, AVG(close) as avg_close
    FROM equity_all
    GROUP BY date, market
""")
```

## Security Concerns

### Hardcoded Credentials

**Severity**: None ✓

**Status**: No hardcoded secrets found

**Best Practices Followed**:
- All credentials via environment variables
- `.env` file git-ignored
- `.env.example` template provided

### Input Validation

**Severity**: Low

**Issue**: Limited input validation for user-provided data

**Locations**:
- CLI argument parsing
- Configuration file loading

**Recommendation**: Add Pydantic validation for all user inputs

### API Key Exposure

**Severity**: None ✓

**Status**: No API keys in code

**Best Practices Followed**:
- FRED API key via environment variable
- AWS credentials via environment

## Missing Features

### Circuit Breaker Pattern

**Severity**: Low

**Issue**: No circuit breaker for API failures

**Impact**:
- Wastes API calls on failing services
- No automatic recovery detection

**Recommendation**:
```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_count = 0
        self.last_failure_time = None
        self.failure_threshold = failure_threshold
        self.timeout = timeout

    def call(self, func):
        if self.failure_count >= self.failure_threshold:
            if time.time() - self.last_failure_time < self.timeout:
                raise CircuitBreakerOpenError()
        # Call function and track failures
```

### Data Validation Library

**Severity**: Low

**Issue**: Custom validation instead of using dedicated library

**Impact**:
- Duplicate code
- Potential for validation bugs

**Recommendation**: Consider using `pandera` or `great_expectations` for data validation

## Tech Debt Items

### Refactoring Needs

1. **orchestrator.py** (816 lines)
   - Split into smaller modules
   - Extract strategy patterns
   - Priority: Medium

2. **Replace print statements**
   - 45+ occurrences
   - Use structured logging
   - Priority: Low

3. **Add type hints**
   - Coverage: ~50%
   - Enable strict mypy
   - Priority: Medium

4. **Error handling**
   - Add comprehensive error handling
   - Custom exception hierarchy
   - Priority: Medium

### Code Duplication

**Issue**: Similar patterns across market fetchers

**Locations**:
- `ingestion/sources/us.py`
- `ingestion/sources/cn.py`
- `ingestion/sources/hk_sg.py`

**Recommendation**: Extract common logic to base class

### Inconsistent Naming

**Issue**: Some inconsistency in variable naming

**Examples**:
- `data_df` vs `df` vs `result_df`
- `ticker_list` vs `tickers` vs `ticker_symbols`

**Recommendation**: Standardize naming conventions

## Bugs & Known Issues

### None Currently Tracked

No active bugs tracked. Good job!

## Deprecation Warnings

### Library Versions

**No deprecation warnings** detected in current dependencies.

### Python Version

**Current**: Python 3.12+
**Status**: Latest stable version - no concerns

## Dependency Issues

### Outdated Dependencies

**Status**: All dependencies up-to-date

**Check**: `uv pip list --outdated`

### Security Vulnerabilities

**Status**: No known vulnerabilities in dependencies

**Check**: `uv pip check` or `safety check`

## Performance Metrics

### File Sizes

| File Type | Size | Notes |
|-----------|------|-------|
| Parquet files | Varies | Efficient compression |
| DuckDB database | ~100MB | Acceptable |
| Log files | <10MB | Rotate if larger |

### Query Performance

**Status**: No performance issues reported

**Benchmarks**: Run `make query` for performance tests

## Recommendations Priority

### High Priority

1. ✅ No critical issues identified

### Medium Priority

1. Split large files (>500 lines)
2. Add comprehensive type hints
3. Improve error handling

### Low Priority

1. Replace print statements with logging
2. Implement circuit breaker pattern
3. Consider async I/O for API calls

## Future Improvements

### Architecture

1. **Event-Driven Pipeline**: Consider message queue for pipeline stages
2. **Microservices**: Split into services if scale increases
3. **Caching Layer**: Add Redis for frequently accessed data

### Features

1. **Real-time Data**: Add support for intraday data
2. **More Markets**: Expand to European, Asian markets
3. **Alternative Data**: Integrate news, sentiment data

### Developer Experience

1. **Interactive CLI**: Add `--interactive` mode
2. **Progress Bars**: More detailed progress tracking
3. **Dry Run Mode**: Better preview of operations

## Maintenance

### Review Schedule

- **Monthly**: Review and update this document
- **Quarterly**: Review large files and complexity
- **Annually**: Major refactoring assessment

### Deletion Policy

Items removed from this document when:
- Issue is fixed and verified
- Feature is implemented
- Debt is refactored

## Related Documents

- `docs/implementations/` - Implementation notes
- `docs/planning/development-log.md` - Development log
- `CLAUDE.md` - AI development guide

---

**Note**: This is a living document. Update it regularly as issues are discovered and resolved.
