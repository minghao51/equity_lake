"""Integration tests for DuckDB query helpers."""

from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.storage.duckdb import EquityDataDB
from equity_lake.storage.examples import QueryExamples

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

        with patch("equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet):
            db._create_market_view("us_equity", temp_partitioned_parquet, "us")

        assert "us_equity" in db.available_views
        result = db.query("SELECT COUNT(*) as cnt FROM us_equity")
        assert result.height == 1
        assert result["cnt"][0] > 0

    def test_query_execution(self, tmp_path, temp_partitioned_parquet):
        """Test executing SQL query."""
        db = EquityDataDB(db_path=":memory:")

        # Create view
        with patch("equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet):
            db._create_market_view("us_equity", temp_partitioned_parquet, "us")

        # Execute simple query
        sql = "SELECT COUNT(*) as count FROM us_equity"
        result = db.query(sql)

        assert isinstance(result, pl.DataFrame)
        assert "count" in result.columns

    def test_query_polars_execution(self, tmp_path, temp_partitioned_parquet):
        """Test executing SQL query returns Polars DataFrame."""
        db = EquityDataDB(db_path=":memory:")

        with patch("equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet):
            db._create_market_view("us_equity", temp_partitioned_parquet, "us")

        result = db.query("SELECT COUNT(*) as count FROM us_equity")

        assert isinstance(result, pl.DataFrame)
        assert "count" in result.columns

    def test_query_error_handling(self):
        """Test query error handling."""
        db = EquityDataDB(db_path=":memory:")

        # Invalid SQL should return empty DataFrame
        result = db.query("INVALID SQL QUERY")
        assert result.is_empty()


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

        assert isinstance(result, pl.DataFrame)
        assert not result.is_empty()
        assert "market" in result.columns
        assert "latest_date" in result.columns

    def test_query_2_top_volume_stocks(self, db_with_data):
        """Test Query 2: Top volume stocks."""
        queries = QueryExamples(db_with_data)
        result = queries.query_2_top_volume_stocks(days=7)

        assert isinstance(result, pl.DataFrame)
        if not result.is_empty():
            assert "ticker" in result.columns
            assert "total_volume" in result.columns

    def test_query_3_top_gainers_losers(self, db_with_data):
        """Test Query 3: Top gainers and losers."""
        queries = QueryExamples(db_with_data)
        result = queries.query_3_top_gainers_losers(days=7)

        assert isinstance(result, pl.DataFrame)
        if not result.is_empty():
            assert "ticker" in result.columns
            assert "pct_change" in result.columns

    def test_query_4_cross_market_comparison(self, db_with_data):
        """Test Query 4: Cross-market comparison."""
        queries = QueryExamples(db_with_data)
        result = queries.query_4_cross_market_comparison(ticker="AAPL")

        assert isinstance(result, pl.DataFrame)

    def test_query_5_moving_averages(self, db_with_data):
        """Test Query 5: Moving averages."""
        queries = QueryExamples(db_with_data)
        result = queries.query_5_moving_averages(ticker="AAPL", ma_days=20)

        assert isinstance(result, pl.DataFrame)

    def test_query_6_volatility_analysis(self, db_with_data):
        """Test Query 6: Volatility analysis."""
        queries = QueryExamples(db_with_data)
        result = queries.query_6_volatility_analysis(days=30)

        assert isinstance(result, pl.DataFrame)

    def test_query_7_market_summary_stats(self, db_with_data):
        """Test Query 7: Market summary statistics."""
        queries = QueryExamples(db_with_data)
        result = queries.query_7_market_summary_stats()

        assert isinstance(result, pl.DataFrame)

    def test_query_8_price_range_analysis(self, db_with_data):
        """Test Query 8: Price range analysis."""
        queries = QueryExamples(db_with_data)
        result = queries.query_8_price_range_analysis(days=30)

        assert isinstance(result, pl.DataFrame)


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestQueryEdgeCases:
    """Tests for edge cases in queries."""

    def test_empty_database(self):
        """Test queries on empty database."""
        db = EquityDataDB(db_path=":memory:")
        queries = QueryExamples(db)

        result = queries.query_1_latest_data_summary()
        assert isinstance(result, pl.DataFrame)

    def test_invalid_ticker_query(self, db_with_data):
        """Test query with invalid ticker."""
        queries = QueryExamples(db_with_data)
        result = queries.query_4_cross_market_comparison(ticker="INVALID_TICKER_123")

        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    def test_date_range_query(self, db_with_data):
        """Test query with various date ranges."""
        queries = QueryExamples(db_with_data)

        for days in [1, 7, 30, 90, 365]:
            result = queries.query_2_top_volume_stocks(days=days)
            assert isinstance(result, pl.DataFrame)


# =============================================================================
# Test Performance
# =============================================================================


class TestQueryPerformance:
    """Performance tests for queries."""

    def test_query_performance_benchmark(self, db_with_data):
        """Test query performance benchmarking."""
        from equity_lake.storage.examples import benchmark_queries

        results = benchmark_queries(db_with_data)

        assert isinstance(results, dict)
        assert len(results) > 0

        for _name, elapsed in results.items():
            assert isinstance(elapsed, int | float)

    def test_concurrent_queries(self, db_with_data):
        """Test that concurrent queries don't interfere."""
        queries = QueryExamples(db_with_data)

        result1 = queries.query_1_latest_data_summary()
        result2 = queries.query_2_top_volume_stocks(7)
        result3 = queries.query_7_market_summary_stats()

        assert isinstance(result1, pl.DataFrame)
        assert isinstance(result2, pl.DataFrame)
        assert isinstance(result3, pl.DataFrame)


# =============================================================================
# Test Data Validation
# =============================================================================


class TestDataValidation:
    """Tests for data validation in queries."""

    def test_data_type_consistency(self, db_with_data):
        """Test that data types are consistent."""
        queries = QueryExamples(db_with_data)
        result = queries.query_1_latest_data_summary()

        if not result.is_empty() and "latest_date" in result.columns:
            assert result["latest_date"].dtype is not None

    def test_null_value_handling(self, db_with_data):
        """Test handling of null values."""
        queries = QueryExamples(db_with_data)
        result = queries.query_2_top_volume_stocks(days=7)

        assert isinstance(result, pl.DataFrame)


# =============================================================================
# Test Integration
# =============================================================================


class TestQueryIntegration:
    """Integration tests for query functionality."""

    def test_full_query_workflow(self, temp_partitioned_parquet):
        """Test complete workflow from DB creation to query execution."""
        db = EquityDataDB(db_path=":memory:")

        with patch("equity_lake.storage.duckdb.US_EQUITY_DIR", temp_partitioned_parquet):
            db._create_market_view("us_equity", temp_partitioned_parquet, "us")
            db._create_unified_view()

        queries = QueryExamples(db)

        results = {
            "summary": queries.query_1_latest_data_summary(),
            "volume": queries.query_2_top_volume_stocks(7),
            "stats": queries.query_7_market_summary_stats(),
        }

        for name, result in results.items():
            assert isinstance(result, pl.DataFrame), f"Query {name} failed"

    def test_query_with_filters(self, db_with_data):
        """Test queries with various filters."""
        sql = """
        SELECT * FROM us_equity
        WHERE volume > 500000
        ORDER BY volume DESC
        LIMIT 10
        """

        result = db_with_data.query(sql)
        assert isinstance(result, pl.DataFrame)
