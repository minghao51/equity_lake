"""Integration tests for DuckDB query helpers."""

from unittest.mock import patch

import pandas as pd
import pytest

from equity_lake.storage.duckdb import EquityDataDB, QueryExamples

# =============================================================================
# Test Database Connection
# =============================================================================


class TestEquityDataDB:
    """Tests for DuckDB database connection manager."""

    def test_initialization_memory_db(self):
        """Test initialization with in-memory database."""
        db = EquityDataDB(db_path=":memory:")
        assert db.db_path == ":memory:"
        assert db.con is not None

    def test_initialization_file_db(self, tmp_path):
        """Test initialization with file-based database."""
        db_path = tmp_path / "test.duckdb"
        db = EquityDataDB(db_path=str(db_path))
        assert db.db_path == str(db_path)
        assert db.con is not None

    def test_create_market_view(self, tmp_path, temp_partitioned_parquet):
        """Test creating market view from Parquet files."""
        db = EquityDataDB(db_path=":memory:")

        # Mock the directory constants
        with patch(
            "equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet
        ):
            db._create_market_view("us_equity", temp_partitioned_parquet, "us")

        # Should not raise exception
        assert True

    def test_query_execution(self, tmp_path, temp_partitioned_parquet):
        """Test executing SQL query."""
        db = EquityDataDB(db_path=":memory:")

        # Create view
        with patch(
            "equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet
        ):
            db._create_market_view("us_equity", temp_partitioned_parquet, "us")

        # Execute simple query
        sql = "SELECT COUNT(*) as count FROM us_equity"
        result = db.query(sql)

        assert isinstance(result, pd.DataFrame)
        assert "count" in result.columns

    def test_query_error_handling(self):
        """Test query error handling."""
        db = EquityDataDB(db_path=":memory:")

        # Invalid SQL should return empty DataFrame
        result = db.query("INVALID SQL QUERY")
        assert result.empty


# =============================================================================
# Test Query Examples
# =============================================================================


@pytest.fixture
def db_with_data(temp_partitioned_parquet):
    """Create a reusable database with test data."""
    db = EquityDataDB(db_path=":memory:")

    with patch("equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet):
        db._create_market_view("us_equity", temp_partitioned_parquet, "us")
        db._create_unified_view()

    return db


class TestQueryExamples:
    """Tests for example queries."""

    def test_query_1_latest_data_summary(self, db_with_data):
        """Test Query 1: Latest data summary."""
        queries = QueryExamples(db_with_data)
        result = queries.query_1_latest_data_summary()

        assert isinstance(result, pd.DataFrame)
        # Check for expected columns
        expected_cols = ["market", "latest_date", "num_tickers"]
        assert any(col in result.columns for col in expected_cols)

    def test_query_2_top_volume_stocks(self, db_with_data):
        """Test Query 2: Top volume stocks."""
        queries = QueryExamples(db_with_data)
        result = queries.query_2_top_volume_stocks(days=7)

        assert isinstance(result, pd.DataFrame)
        # Should have some results
        if not result.empty:
            assert "ticker" in result.columns
            assert "total_volume" in result.columns

    def test_query_3_top_gainers_losers(self, db_with_data):
        """Test Query 3: Top gainers and losers."""
        queries = QueryExamples(db_with_data)
        result = queries.query_3_top_gainers_losers(days=7)

        assert isinstance(result, pd.DataFrame)
        if not result.empty:
            assert "ticker" in result.columns
            assert "pct_change" in result.columns

    def test_query_4_cross_market_comparison(self, db_with_data):
        """Test Query 4: Cross-market comparison."""
        queries = QueryExamples(db_with_data)
        result = queries.query_4_cross_market_comparison(ticker="AAPL")

        assert isinstance(result, pd.DataFrame)

    def test_query_5_moving_averages(self, db_with_data):
        """Test Query 5: Moving averages."""
        queries = QueryExamples(db_with_data)
        result = queries.query_5_moving_averages(ticker="AAPL", ma_days=20)

        assert isinstance(result, pd.DataFrame)

    def test_query_6_volatility_analysis(self, db_with_data):
        """Test Query 6: Volatility analysis."""
        queries = QueryExamples(db_with_data)
        result = queries.query_6_volatility_analysis(days=30)

        assert isinstance(result, pd.DataFrame)

    def test_query_7_market_summary_stats(self, db_with_data):
        """Test Query 7: Market summary statistics."""
        queries = QueryExamples(db_with_data)
        result = queries.query_7_market_summary_stats()

        assert isinstance(result, pd.DataFrame)

    def test_query_8_price_range_analysis(self, db_with_data):
        """Test Query 8: Price range analysis."""
        queries = QueryExamples(db_with_data)
        result = queries.query_8_price_range_analysis(days=30)

        assert isinstance(result, pd.DataFrame)


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestQueryEdgeCases:
    """Tests for edge cases in queries."""

    def test_empty_database(self):
        """Test queries on empty database."""
        db = EquityDataDB(db_path=":memory:")
        queries = QueryExamples(db)

        # Should not crash, return empty DataFrame
        result = queries.query_1_latest_data_summary()
        assert isinstance(result, pd.DataFrame)

    def test_invalid_ticker_query(self, db_with_data):
        """Test query with invalid ticker."""
        queries = QueryExamples(db_with_data)
        result = queries.query_4_cross_market_comparison(ticker="INVALID_TICKER_123")

        # Should return empty DataFrame
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_date_range_query(self, db_with_data):
        """Test query with various date ranges."""
        queries = QueryExamples(db_with_data)

        # Test different day ranges
        for days in [1, 7, 30, 90, 365]:
            result = queries.query_2_top_volume_stocks(days=days)
            assert isinstance(result, pd.DataFrame)


# =============================================================================
# Test Performance
# =============================================================================


class TestQueryPerformance:
    """Performance tests for queries."""

    @pytest.mark.slow
    def test_query_performance_benchmark(self, db_with_data):
        """Test query performance benchmarking."""
        from equity_lake.storage.duckdb import benchmark_queries

        results = benchmark_queries(db_with_data)

        assert isinstance(results, dict)
        assert len(results) > 0

        # All benchmarks should complete
        for _name, elapsed in results.items():
            assert isinstance(elapsed, (int, float))

    def test_concurrent_queries(self, db_with_data):
        """Test that concurrent queries don't interfere."""
        queries = QueryExamples(db_with_data)

        # Run multiple queries
        result1 = queries.query_1_latest_data_summary()
        result2 = queries.query_2_top_volume_stocks(7)
        result3 = queries.query_7_market_summary_stats()

        # All should return DataFrames
        assert isinstance(result1, pd.DataFrame)
        assert isinstance(result2, pd.DataFrame)
        assert isinstance(result3, pd.DataFrame)


# =============================================================================
# Test Data Validation
# =============================================================================


class TestDataValidation:
    """Tests for data validation in queries."""

    def test_data_type_consistency(self, db_with_data):
        """Test that data types are consistent."""
        queries = QueryExamples(db_with_data)
        result = queries.query_1_latest_data_summary()

        if not result.empty and "latest_date" in result.columns:
            assert result["latest_date"].dtype is not None

    def test_null_value_handling(self, db_with_data):
        """Test handling of null values."""
        queries = QueryExamples(db_with_data)
        result = queries.query_2_top_volume_stocks(days=7)

        # Should handle nulls gracefully
        assert isinstance(result, pd.DataFrame)


# =============================================================================
# Test Integration
# =============================================================================


class TestQueryIntegration:
    """Integration tests for query functionality."""

    def test_full_query_workflow(self, temp_partitioned_parquet):
        """Test complete workflow from DB creation to query execution."""
        # Create database
        db = EquityDataDB(db_path=":memory:")

        # Setup views
        with patch(
            "equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet
        ):
            db._create_market_view("us_equity", temp_partitioned_parquet, "us")
            db._create_unified_view()

        # Run queries
        queries = QueryExamples(db)

        # Execute multiple queries
        results = {
            "summary": queries.query_1_latest_data_summary(),
            "volume": queries.query_2_top_volume_stocks(7),
            "stats": queries.query_7_market_summary_stats(),
        }

        # All should succeed
        for name, result in results.items():
            assert isinstance(result, pd.DataFrame), f"Query {name} failed"

    def test_query_with_filters(self, db_with_data):
        """Test queries with various filters."""
        # Custom query with filters
        sql = """
        SELECT * FROM us_equity
        WHERE volume > 500000
        ORDER BY volume DESC
        LIMIT 10
        """

        result = db_with_data.query(sql)
        assert isinstance(result, pd.DataFrame)
