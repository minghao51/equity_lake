# Concerns

**Last Updated**: 2026-03-05
**Project**: Equity EOD Data Pipeline

## Overview

This document tracks technical debt, bugs, security issues, performance concerns, and other areas needing attention in the codebase.

---

## Tech Debt

### Large Files Needing Refactoring

#### 1. Orchestrator Module
**File**: `src/equity_lake/ingestion/orchestrator.py` (880 lines)

**Issues**:
- File too large, handles too many responsibilities
- Mixes orchestration logic with validation and error handling
- Difficult to test and maintain

**Recommendations**:
- Extract validation logic to separate module
- Create dedicated error handling module
- Split into multiple orchestrators (one per market type)

**Priority**: Medium
**Effort**: 4-6 hours

---

#### 2. Feature Engineering Module
**File**: `src/equity_lake/features/engineering.py` (712 lines)

**Issues**:
- Monolithic module with many feature calculations
- Lacks modularity for adding new features
- Testing requires loading entire module

**Recommendations**:
- Split into separate modules by feature type
- Create base feature class for extensibility
- Implement feature registry pattern

**Priority**: Low (optional module)
**Effort**: 3-4 hours

---

### Code Duplication

#### 1. Pandas Imports
**Count**: 32 files import pandas

**Issue**: Potential memory concerns with large datasets

**Recommendations**:
- Consider lazy loading where appropriate
- Use PyArrow for certain operations (lighter than pandas)
- Consolidate DataFrame operations in utility functions

**Priority**: Low
**Effort**: 2-3 hours

---

#### 2. Similar Fetcher Patterns
**Files**:
- `src/equity_lake/ingestion/sources/us_equity.py`
- `src/equity_lake/ingestion/sources/hk_sg_equity.py`

**Issue**: Both use yfinance with similar logic

**Recommendations**:
- Extract common yfinance logic to base class
- Keep only market-specific differences in subclasses

**Priority**: Low
**Effort**: 1-2 hours

---

### Legacy Code Migration

#### 1. Scripts Directory
**Files**:
- `scripts/ingest_daily.py` (637 lines)
- `scripts/query_example.py` (594 lines)
- `scripts/sync_from_s3.py` (398 lines)

**Issue**: Legacy scripts being replaced by `src/` structure

**Recommendations**:
- Complete migration to `src/equity_lake/cli/`
- Add deprecation warnings to old scripts
- Remove from codebase after migration confirmed

**Priority**: Medium
**Effort**: 2-3 hours

---

## Bugs

### Known Issues

#### 1. No Known Bugs
**Status**: No critical bugs identified in current codebase

**Monitoring**:
- Check logs regularly: `logs/ingest_daily.log`
- Review test failures
- Monitor error rates in production

---

### Potential Issues

#### 1. Date Handling Edge Cases
**Concern**: Trading date validation

**Files**: Various fetchers

**Potential Issues**:
- Weekend dates (no trading data)
- Holiday dates (market-specific)
- Timezone issues for international markets

**Recommendations**:
- Add trading calendar integration
- Validate dates before fetching
- Handle missing dates gracefully

**Priority**: Medium
**Effort**: 2-3 hours

---

#### 2. API Rate Limiting
**Concern**: yfinance and akshare rate limits

**Files**:
- `src/equity_lake/ingestion/sources/us_equity.py`
- `src/equity_lake/ingestion/sources/cn_ashare.py`

**Potential Issues**:
- Rate limits may change without notice
- Burst requests may trigger throttling
- IP bans for excessive requests

**Recommendations**:
- Monitor API response codes
- Implement adaptive rate limiting
- Add circuit breaker pattern

**Priority**: Medium
**Effort**: 2-3 hours

---

## Security

### Current Status: Good

**Assessment**: No critical security issues found

**Positive Findings**:
- No hardcoded credentials in source code
- Environment variables properly managed in `.env.example`
- AWS credentials handled via standard AWS SDK
- No SQL injection risks (DuckDB parameterized queries)

---

### Recommendations

#### 1. API Key Management (Future)
**Context**: If adding premium data sources

**Recommendations**:
- Use environment variables for API keys
- Never commit keys to git
- Add `.env` to `.gitignore` (already done)
- Document key rotation process

**Priority**: Low (future consideration)

---

#### 2. Dependency Vulnerabilities
**Context**: Regular dependency updates needed

**Recommendations**:
- Run `uv pip list --outdated` regularly
- Subscribe to security advisories for dependencies
- Use `safety` tool to check for vulnerabilities
- Keep yfinance, akshare, duckdb updated

**Priority**: Medium
**Frequency**: Monthly

---

#### 3. Data Privacy
**Context**: EOD market data is public information

**Assessment**: Low risk

**Notes**:
- No PII in this project
- Market data is public
- Still, keep `data/` directory git-ignored (already done)

---

## Performance

### Current Performance

**Assessment**: Good for current scale

**Benchmarks**:
- S3 Sync: ~5-10 GB in ~5-10 minutes (with 32 workers)
- Daily Ingestion: ~5-50 MB per market in ~1-2 minutes
- Queries: Sub-second for filtered queries

---

### Optimization Opportunities

#### 1. Pandas Memory Usage
**Files**: 32 files import pandas

**Concern**: Memory overhead with large datasets

**Recommendations**:
- Use `dtype` parameter when reading Parquet (specify types)
- Process data in chunks for large operations
- Consider PyArrow for certain operations (lighter)

**Priority**: Low
**Effort**: 2-3 hours

---

#### 2. API Rate Limiting Impact
**Files**: 11 source files implement rate limiting

**Concern**: Delays between requests slow down ingestion

**Current Implementation**:
- yfinance: 0.5-1 second delay between requests
- akshare: 0.1 second delay between requests

**Recommendations**:
- Implement batch requests where possible
- Use concurrent fetchers (with rate limiting)
- Cache responses to reduce redundant calls

**Priority**: Low
**Effort**: 3-4 hours

---

#### 3. Query Optimization
**File**: `src/equity_lake/storage/duckdb.py`

**Current State**: Good (uses partition pruning)

**Recommendations**:
- Add query result caching
- Create materialized views for frequent queries
- Implement query performance monitoring

**Priority**: Low
**Effort**: 2-3 hours

---

### Scalability Concerns

#### 1. Single-Threaded Ingestion
**Current**: Sequential market fetching

**Impact**: Not bottleneck for current scale

**Recommendations** (for future scaling):
- Implement concurrent market fetching
- Use asyncio for I/O-bound operations
- Add worker queue for large-scale operations

**Priority**: Low (future consideration)
**Effort**: 4-6 hours

---

## Maintainability

### Code Quality

#### 1. Type Hint Coverage
**Current**: Good (mypy strict mode enabled)

**Status**: All functions should have type hints

**Files Missing Type Hints**: None identified

---

#### 2. Documentation
**Current**: Good

**Existing**:
- `CLAUDE.md`: Comprehensive AI assistant guide
- Codebase docs: This directory (`.planning/codebase/`)
- Function docstrings: Google-style

**Gaps**:
- No API documentation (Sphinx/mkdocs)
- Limited architecture diagrams
- No developer onboarding guide

**Recommendations**:
- Add Sphinx/mkdocs for API docs
- Create architecture diagrams (Mermaid)
- Write developer onboarding guide

**Priority**: Low
**Effort**: 4-6 hours

---

#### 3. Error Messages
**Current**: Good (structured logging)

**Status**: Clear error messages with context

**Example**:
```python
logger.error(
    "Failed to fetch market data",
    market="us",
    date="2024-12-01",
    error="Connection timeout"
)
```

---

### Testing Gaps

#### 1. Coverage
**Current**: Good (90%+ target)

**Files with Low Coverage**: None critical identified

**Recommendations**:
- Run coverage report regularly: `make test`
- Aim for 95%+ coverage on critical paths
- Add integration tests for edge cases

---

#### 2. End-to-End Testing
**Current**: Limited

**Recommendations**:
- Add E2E tests for complete workflows
- Test with real data (sample dataset)
- Validate data quality in E2E tests

**Priority**: Medium
**Effort**: 3-4 hours

---

## Documentation Gaps

### Missing Documentation

#### 1. Developer Guide
**Missing**: Comprehensive developer onboarding guide

**Should Include**:
- Development environment setup
- Code contribution workflow
- Testing guidelines
- Release process

**Priority**: Medium
**Effort**: 2-3 hours

---

#### 2. API Documentation
**Missing**: Auto-generated API docs

**Recommendations**:
- Add Sphinx configuration
- Generate API docs from docstrings
- Host on GitHub Pages or ReadTheDocs

**Priority**: Low
**Effort**: 3-4 hours

---

#### 3. Architecture Diagrams
**Missing**: Visual architecture documentation

**Recommendations**:
- Create Mermaid diagrams for data flow
- Document component relationships
- Add deployment architecture diagram

**Priority**: Low
**Effort**: 2-3 hours

---

## Dependencies

### Outdated Dependencies

**Check Regularly**:
```bash
# Check for outdated packages
uv pip list --outdated

# Update dependencies
uv sync --upgrade
```

**Key Dependencies to Monitor**:
- `yfinance`: Frequent updates, may break compatibility
- `akshare`: Active development, API changes
- `duckdb`: Rapid feature additions
- `pandas`: Major version changes may break code

**Recommendations**:
- Pin major versions in `requirements.txt`
- Test thoroughly before updating
- Subscribe to project release notes

---

### Dependency Bloat

**Current**: 20+ production dependencies

**Assessment**: Reasonable for data pipeline

**Recommendations**:
- Audit dependencies annually
- Remove unused dependencies
- Consider lighter alternatives (e.g., PyArrow vs pandas for some operations)

---

## Deployment Concerns

### Production Readiness

#### 1. Error Monitoring
**Current**: File-based logging only

**Recommendations**:
- Add error tracking (Sentry, Rollbar)
- Implement alerting for critical failures
- Create health check endpoint

**Priority**: Medium (if deploying to production)
**Effort**: 2-3 hours

---

#### 2. Monitoring & Metrics
**Current**: Manual log review

**Recommendations**:
- Add metrics collection (Prometheus)
- Track API success/failure rates
- Monitor data latency (time to fetch)
- Alert on data quality issues

**Priority**: Medium (if deploying to production)
**Effort**: 3-4 hours

---

#### 3. Deployment Automation
**Current**: Manual deployment

**Recommendations**:
- Add CI/CD pipeline (GitHub Actions)
- Automated testing on PR
- Automated deployment on merge to main
- Rollback procedure for failed deployments

**Priority**: Low (if no production deployment yet)
**Effort**: 4-6 hours

---

## Data Quality

### Validation

#### 1. Schema Validation
**Current**: Good (`validate_schema` function)

**Coverage**: All markets validated before writing

**Status**: No concerns

---

#### 2. Data Quality Checks
**Current**: Basic validation (no null prices)

**Recommendations**:
- Add more quality checks:
  - Price sanity checks (e.g., no negative prices)
  - Volume validation (no negative volume)
  - Price range checks (high >= low, close within range)
  - Duplicate detection

**Priority**: Medium
**Effort**: 2-3 hours

---

#### 3. Data Freshness Monitoring
**Current**: Manual checks

**Recommendations**:
- Add automated freshness checks
- Alert if data not updated within expected window
- Track historical latency

**Priority**: Medium (if running automated)
**Effort**: 2-3 hours

---

## Internationalization

### Timezone Handling

**Current**: Limited timezone support

**Concern**: Multi-market timezones

**Markets**:
- US: Eastern Time
- China: CST (UTC+8)
- Hong Kong: HKT (UTC+8)
- Singapore: SGT (UTC+8)

**Recommendations**:
- Document timezone assumptions
- Add timezone awareness where needed
- Use UTC for storage, convert for display

**Priority**: Low
**Effort**: 2-3 hours

---

## Compliance

### Data Usage

**Context**: EOD market data from public sources

**Considerations**:
- yfinance: Free for personal use
- akshare: Free for personal use
- Check terms of service for commercial use

**Recommendations**:
- Review API terms of service
- Add attribution if required
- Ensure compliance with data provider policies

**Priority**: Low (legal review)

---

## Summary

### High Priority Items
1. **Date Validation**: Add trading calendar integration (2-3 hours)
2. **Data Quality Checks**: Enhanced validation (2-3 hours)
3. **Error Monitoring**: Add error tracking for production (2-3 hours)

### Medium Priority Items
1. **Refactor Orchestrator**: Split into smaller modules (4-6 hours)
2. **API Rate Limiting**: Implement adaptive throttling (2-3 hours)
3. **Developer Guide**: Create onboarding documentation (2-3 hours)
4. **Dependency Updates**: Monthly security updates (ongoing)

### Low Priority Items
1. **Feature Engineering**: Split into modules (3-4 hours)
2. **Performance Optimization**: Memory and speed improvements (2-3 hours)
3. **API Documentation**: Generate Sphinx docs (3-4 hours)
4. **Architecture Diagrams**: Create visual docs (2-3 hours)

### Overall Assessment
- **Security**: Good (no critical issues)
- **Performance**: Good (suitable for current scale)
- **Maintainability**: Good (clear structure, good testing)
- **Documentation**: Good (comprehensive CLAUDE.md, gaps in API docs)
- **Deployment**: Not production-ready (needs monitoring/alerting)

**Next Steps**:
1. Address high-priority items
2. Plan production deployment (if needed)
3. Schedule regular dependency updates
4. Continuously monitor logs and errors
