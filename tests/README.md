# Test Suite Guide

This directory contains the executable test suite for the active package. The
layout is organized by scope first and feature second.

## Layout

```text
tests/
├── conftest.py                 # Shared fixtures and test helpers
├── integration/               # Cross-module integration coverage
├── unit/                      # Focused unit coverage for core subsystems
├── test_*                     # Feature-level tests kept at top level
└── __init__.py
```

## Current Coverage Areas

- `tests/unit/test_fetchers.py`: market-source fetchers and fallback behavior
- `tests/unit/test_ingestion_orchestrator.py`: daily ingestion orchestration
- `tests/unit/test_macro_sources.py`: macro indicator fetchers and parquet writes
- `tests/unit/test_ml_jobs.py`: ML helper orchestration
- `tests/unit/test_news_fetcher.py`: news ingestion
- `tests/unit/test_social_sentiment.py`: sentiment ingestion
- `tests/integration/test_duckdb_queries.py`: DuckDB query paths
- `tests/integration/test_news_ingestion.py`: end-to-end news ingestion
- `tests/integration/test_pipeline_orchestrator.py`: pipeline stage orchestration
- top-level `tests/test_*`: signal scanning, generators, history, and formatters

## Common Commands

```bash
uv run pytest -v
uv run pytest tests/unit -v
uv run pytest tests/integration -v
uv run pytest tests/test_signal_scanner.py -v
uv run pytest --cov=src/equity_lake --cov-report=term
```

Use `make test`, `make test-unit`, and `make test-integration` when you want
the standard project wrappers.

## Conventions

- Add shared fixtures to `tests/conftest.py`.
- Keep fast, isolated tests in `tests/unit/`.
- Put multi-module workflows and filesystem-heavy checks in `tests/integration/`.
- Place feature-level regression tests at the top level only when they span
  multiple packages and do not fit one subsystem cleanly.
        'adj_close': [152.0, 382.0, 142.0, 182.0, 252.0]
    }
    return pd.DataFrame(data)
```

---

## Continuous Integration

### GitHub Actions Workflow

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: astral-sh/setup-uv@v1
      - run: uv sync
      - run: uv run pytest tests/unit/test_fetchers.py -v
```

---

## Expected Output

### Successful Test Run

```
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_initialization_with_default_batch_size PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_initialization_with_custom_batch_size PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_chunked_splits_tickers_correctly PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_chunked_handles_small_lists PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_chunked_handles_empty_list PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_fetch_with_batching PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_fetch_handles_partial_failures PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_fetch_standardizes_columns PASSED
tests/unit/test_fetchers.py::TestUSEquityFetcherBatching::test_fetch_with_single_ticker PASSED
tests/unit/test_fetchers.py::TestCNEfinanceFetcher::test_initialization_requires_efinance PASSED
tests/unit/test_fetchers.py::TestCNEfinanceFetcher::test_initialization_with_params PASSED
tests/unit/test_fetchers.py::TestCNEfinanceFetcher::test_fetch_single_stock PASSED
tests/unit/test_fetchers.py::TestCNEfinanceFetcher::test_fetch_single_stock_handles_failure PASSED
tests/unit/test_fetchers.py::TestCNEfinanceFetcher::test_fetch_standardizes_columns PASSED
tests/unit/test_fetchers.py::TestCNEfinanceFetcher::test_fetch_with_empty_stock_list PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_initialization_with_both_sources PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_initialization_efinance_only PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_initialization_akshare_only PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_initialization_fails_when_no_sources PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_fetch_uses_efinance_first PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_fetch_falls_back_to_akshare PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_fetch_returns_best_result PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_fetch_akshare_only PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_standardize_output PASSED
tests/unit/test_fetchers.py::TestCNHybridFetcher::test_standardize_output_empty_dataframe PASSED
tests/unit/test_fetchers.py::TestFetcherIntegration::test_us_fetcher_with_large_dataset PASSED
tests/unit/test_fetchers.py::TestFetcherIntegration::test_hybrid_fetcher_reliability PASSED

============================== 26 passed in 2.34s ===============================
```

---

## Troubleshooting

### Issue: Import Error for efinance

```
ImportError: efinance is not installed
```

**Solution**:
```bash
uv sync
```

### Issue: Tests Not Found

```
ERROR: file or module not found: tests/unit/test_fetchers.py
```

**Solution**:
```bash
# Run from project root
cd /path/to/equity_lake
uv run pytest tests/unit/test_fetchers.py -v
```

### Issue: Mock Not Working

```
AttributeError: <MagicMock> does not have attribute 'stock'
```

**Solution**: Ensure you're mocking at the right location:
```python
# ✅ Correct
@patch('equity_lake.ingestion.sources.cn_efinance.efinance')

# ❌ Wrong
@patch('efinance')
```

---

## Contributing

When adding new tests:

1. **Use descriptive names**: `test_<method>_<scenario>`
2. **Follow AAA pattern**: Arrange, Act, Assert
3. **Mock external dependencies**: yfinance, efinance, akshare
4. **Test edge cases**: empty lists, failures, null values
5. **Add fixtures** to `conftest.py` if reusable
6. **Document complex tests** with comments

---

## Future Enhancements

- [ ] Add property-based testing with Hypothesis
- [ ] Add performance regression tests
- [ ] Add visual test reports with pytest-html
- [ ] Add mutation testing with mutmut
- [ ] Add tests for CLI integration

---

**Last Updated**: 2026-02-28
**Total Tests**: 26
**Coverage**: New fetchers: 100%
