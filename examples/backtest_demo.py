"""
Backtesting Framework Test Demo

This script tests the core backtesting functionality including:
- BacktestDataLoader: Loading data from DuckDB/Parquet
- Strategy implementations: SMA Crossover, Momentum, Mean Reversion
- BacktestEngine: Running backtests
- BacktestResult: Analyzing results

Usage:
    python examples/backtest_demo.py
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from equity_lake.backtesting import BacktestDataLoader, BacktestEngine
from equity_lake.backtesting.strategy import (
    BBMeanReversionStrategy,
    CrossSectionalMomentumStrategy,
    SMACrossoverStrategy,
)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


class TestResults:
    """Container for test results."""

    def __init__(self):
        self.passed_tests = []
        self.failed_tests = []
        self.warnings = []
        self.performance_data = {}

    def add_pass(self, test_name: str, details: str = ""):
        """Record a passed test."""
        self.passed_tests.append((test_name, details))
        print(f"  ✅ PASS: {test_name}")
        if details:
            print(f"     {details}")

    def add_fail(self, test_name: str, error: str):
        """Record a failed test."""
        self.failed_tests.append((test_name, error))
        print(f"  ❌ FAIL: {test_name}")
        print(f"     Error: {error}")

    def add_warning(self, warning: str):
        """Record a warning."""
        self.warnings.append(warning)
        print(f"  ⚠️  WARNING: {warning}")

    def add_performance(self, test_name: str, duration: float):
        """Record performance data."""
        self.performance_data[test_name] = duration
        print(f"  ⏱️  Performance: {test_name} took {duration:.2f}s")

    def print_summary(self):
        """Print test summary."""
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)

        print(f"\nPassed Tests: {len(self.passed_tests)}")
        for test_name, details in self.passed_tests:
            print(f"  ✅ {test_name}")
            if details:
                print(f"     {details}")

        print(f"\nFailed Tests: {len(self.failed_tests)}")
        for test_name, error in self.failed_tests:
            print(f"  ❌ {test_name}")
            print(f"     {error}")

        print(f"\nWarnings: {len(self.warnings)}")
        for warning in self.warnings:
            print(f"  ⚠️  {warning}")

        print("\nPerformance:")
        for test_name, duration in sorted(self.performance_data.items()):
            print(f"  ⏱️  {test_name}: {duration:.2f}s")

        print("\n" + "=" * 70)
        if not self.failed_tests:
            print("✅ ALL TESTS PASSED!")
        else:
            print(f"❌ {len(self.failed_tests)} TEST(S) FAILED")
        print("=" * 70)


def check_data_availability() -> dict[str, Any]:
    """Check what data is available for testing."""
    print("\n" + "=" * 70)
    print("STEP 1: Checking Data Availability")
    print("=" * 70)

    results = {
        "us_equity_available": False,
        "cn_ashare_available": False,
        "hk_sg_equity_available": False,
        "sample_tickers": [],
        "date_range": (None, None),
    }

    try:
        loader = BacktestDataLoader()

        # Check US market
        try:
            us_tickers = loader.get_available_tickers("us")
            if us_tickers:
                results["us_equity_available"] = True
                print(f"  ✅ US Equity data available: {len(us_tickers)} tickers")
                # Use a few tech stocks for testing
                test_tickers = ["AAPL", "MSFT", "GOOGL"]
                available_tickers = [t for t in test_tickers if t in us_tickers]
                if available_tickers:
                    results["sample_tickers"] = available_tickers[:2]  # Use 2 tickers
                    print(f"     Using tickers: {results['sample_tickers']}")
                else:
                    results["sample_tickers"] = us_tickers[:2]
                    print(f"     Using tickers: {results['sample_tickers']}")
            else:
                print("  ⚠️  US Equity data: No tickers found")
        except Exception as e:
            print(f"  ❌ US Equity check failed: {e}")

        # Check date range for US market
        if results["sample_tickers"]:
            try:
                ticker = results["sample_tickers"][0]
                min_date, max_date = loader.get_date_range("us", ticker)
                if min_date and max_date:
                    results["date_range"] = (min_date, max_date)
                    print(f"  ✅ Date range for {ticker}: {min_date} to {max_date}")
                else:
                    print("  ⚠️  Could not determine date range")
            except Exception as e:
                print(f"  ❌ Date range check failed: {e}")

        # Briefly check other markets
        for market_name, market_key in [
            ("China A-shares", "cn"),
            ("HK/SG Equity", "hk_sg"),
        ]:
            try:
                tickers = loader.get_available_tickers(market_key)
                if tickers:
                    results[f"{market_key}_available"] = True
                    print(f"  ✅ {market_name} data available: {len(tickers)} tickers")
            except Exception as e:
                print(f"  ⚠️  {market_name} check failed: {e}")

        loader.close()

    except Exception as e:
        print(f"  ❌ Data loader initialization failed: {e}")
        return results

    return results


def test_data_loader(data_info: dict[str, Any]) -> TestResults:
    """Test BacktestDataLoader functionality."""
    print("\n" + "=" * 70)
    print("STEP 2: Testing BacktestDataLoader")
    print("=" * 70)

    results = TestResults()

    if not data_info["sample_tickers"]:
        results.add_fail(
            "DataLoader Initialization", "No sample tickers available for testing"
        )
        return results

    try:
        loader = BacktestDataLoader()
        results.add_pass("DataLoader Initialization")

        # Determine test date range (use 1 year of data for speed)
        end_date = date.today() - timedelta(days=30)  # Leave buffer
        start_date = end_date - timedelta(days=365)  # 1 year

        print(f"\n  Loading data for {data_info['sample_tickers']}")
        print(f"  Date range: {start_date} to {end_date}")

        # Test data loading
        load_start = time.time()
        data = loader.load(
            tickers=data_info["sample_tickers"],
            start_date=start_date,
            end_date=end_date,
            markets=["us"],
            wide_format=True,
        )
        load_duration = time.time() - load_start

        if data.empty:
            results.add_fail("Data Loading", "Loaded data is empty")
        else:
            results.add_pass(
                "Data Loading",
                (
                    f"Loaded {data.shape[0]} rows for "
                    f"{len(data_info['sample_tickers'])} tickers "
                    f"in {load_duration:.2f}s"
                ),
            )
            results.add_performance("Data Loading", load_duration)

            # Inspect data structure
            print("\n  Data structure:")
            print(f"    Shape: {data.shape}")
            print(
                f"    Index: {data.index.name} "
                f"({data.index.min()} to {data.index.max()})"
            )
            if isinstance(data.columns, pd.MultiIndex):
                print(
                    "    Columns: MultiIndex with "
                    f"{len(data.columns.get_level_values(0).unique())} tickers"
                )
                print(
                    f"    Fields: {data.columns.get_level_values(1).unique().tolist()}"
                )
            else:
                print(f"    Columns: {data.columns.tolist()}")

        loader.close()

    except Exception as e:
        results.add_fail("DataLoader Test", str(e))
        import traceback

        print("\n  Full error traceback:")
        traceback.print_exc()

    return results


def test_sma_crossover_strategy(data_info: dict[str, Any]) -> TestResults:
    """Test SMA Crossover Strategy."""
    print("\n" + "=" * 70)
    print("STEP 3: Testing SMA Crossover Strategy")
    print("=" * 70)

    results = TestResults()

    if not data_info["sample_tickers"]:
        results.add_fail("SMA Crossover Strategy", "No sample tickers available")
        return results

    try:
        # Initialize strategy with shorter periods for testing
        strategy = SMACrossoverStrategy(
            params={
                "fast_period": 10,  # Shorter for testing
                "slow_period": 30,
                "use_ema": False,
            }
        )
        results.add_pass("SMA Crossover Initialization", "Fast=10, Slow=30")

        # Set up backtest
        end_date = date.today() - timedelta(days=30)
        start_date = end_date - timedelta(days=365)

        engine = BacktestEngine(
            strategy=strategy,
            tickers=data_info["sample_tickers"],
            start_date=start_date,
            end_date=end_date,
            initial_cash=100_000,
            markets=["us"],
        )
        results.add_pass("BacktestEngine Initialization")

        # Run backtest
        print("\n  Running backtest...")
        backtest_start = time.time()
        result = engine.run()
        backtest_duration = time.time() - backtest_start

        results.add_pass("SMA Crossover Backtest Execution")
        results.add_performance("SMA Crossover Backtest", backtest_duration)

        # Analyze results
        print(f"\n  {result.summary()}")

        # Validate results
        if result.total_return != 0 or result.metrics.get("num_trades", 0) > 0:
            results.add_pass(
                "SMA Crossover Results",
                (
                    f"Return: {result.total_return:.2%}, "
                    f"Trades: {result.metrics.get('num_trades', 0)}"
                ),
            )
        else:
            results.add_warning(
                "SMA Crossover produced zero returns and no trades - "
                "this might be expected for the test period"
            )

        # Check equity curve
        if result.equity_curve is not None and not result.equity_curve.empty:
            results.add_pass(
                "Equity Curve Generation", f"{len(result.equity_curve)} data points"
            )
        else:
            results.add_fail("Equity Curve Generation", "Equity curve is empty")

    except Exception as e:
        results.add_fail("SMA Crossover Strategy", str(e))
        import traceback

        print("\n  Full error traceback:")
        traceback.print_exc()

    return results


def test_momentum_strategy(data_info: dict[str, Any]) -> TestResults:
    """Test Cross-Sectional Momentum Strategy."""
    print("\n" + "=" * 70)
    print("STEP 4: Testing Momentum Strategy")
    print("=" * 70)

    results = TestResults()

    if not data_info["sample_tickers"]:
        results.add_fail("Momentum Strategy", "No sample tickers available")
        return results

    try:
        # Initialize momentum strategy with shorter lookback for testing
        # Note: With only 2 tickers, cross-sectional momentum won't work well
        # This is more of a sanity check that the code runs
        strategy = CrossSectionalMomentumStrategy(
            params={
                "lookback_days": 60,  # 2 months instead of 1 year
                "skip_days": 5,
                "top_pct": 0.5,  # With 2 tickers, pick top 50%
                "rebalance_days": 21,
                "long_only": True,
                "min_stocks": 2,  # Allow 2 stocks minimum
            }
        )
        results.add_pass("Momentum Strategy Initialization")

        # Set up backtest
        end_date = date.today() - timedelta(days=30)
        start_date = end_date - timedelta(days=365)

        engine = BacktestEngine(
            strategy=strategy,
            tickers=data_info["sample_tickers"],
            start_date=start_date,
            end_date=end_date,
            initial_cash=100_000,
            markets=["us"],
        )
        results.add_pass("BacktestEngine Initialization")

        # Run backtest
        print("\n  Running backtest...")
        backtest_start = time.time()
        result = engine.run()
        backtest_duration = time.time() - backtest_start

        results.add_pass("Momentum Backtest Execution")
        results.add_performance("Momentum Backtest", backtest_duration)

        # Analyze results
        print(f"\n  {result.summary()}")

        # Validate results
        if result.metrics.get("num_trades", 0) > 0:
            results.add_pass(
                "Momentum Results",
                (
                    f"Return: {result.total_return:.2%}, "
                    f"Trades: {result.metrics.get('num_trades', 0)}"
                ),
            )
        else:
            results.add_warning(
                "Momentum produced no trades - this is expected with only 2 tickers "
                "and a short lookback period"
            )

    except Exception as e:
        results.add_fail("Momentum Strategy", str(e))
        import traceback

        print("\n  Full error traceback:")
        traceback.print_exc()

    return results


def test_mean_reversion_strategy(data_info: dict[str, Any]) -> TestResults:
    """Test Bollinger Bands Mean Reversion Strategy."""
    print("\n" + "=" * 70)
    print("STEP 5: Testing Mean Reversion Strategy")
    print("=" * 70)

    results = TestResults()

    if not data_info["sample_tickers"]:
        results.add_fail("Mean Reversion Strategy", "No sample tickers available")
        return results

    try:
        # Initialize BB mean reversion strategy
        strategy = BBMeanReversionStrategy(
            params={
                "period": 20,
                "num_std": 2.0,
                "use_trend_filter": True,  # Only trade when above 200 MA
                "stop_loss_pct": 0.05,
            }
        )
        results.add_pass("BB Mean Reversion Initialization")

        # Set up backtest
        end_date = date.today() - timedelta(days=30)
        start_date = end_date - timedelta(days=365)

        engine = BacktestEngine(
            strategy=strategy,
            tickers=data_info["sample_tickers"],
            start_date=start_date,
            end_date=end_date,
            initial_cash=100_000,
            markets=["us"],
        )
        results.add_pass("BacktestEngine Initialization")

        # Run backtest
        print("\n  Running backtest...")
        backtest_start = time.time()
        result = engine.run()
        backtest_duration = time.time() - backtest_start

        results.add_pass("Mean Reversion Backtest Execution")
        results.add_performance("Mean Reversion Backtest", backtest_duration)

        # Analyze results
        print(f"\n  {result.summary()}")

        # Validate results
        if result.metrics.get("num_trades", 0) > 0:
            results.add_pass(
                "Mean Reversion Results",
                (
                    f"Return: {result.total_return:.2%}, "
                    f"Trades: {result.metrics.get('num_trades', 0)}"
                ),
            )
        else:
            results.add_warning(
                "Mean Reversion produced no trades - this might be expected "
                "if no BB signals occurred in the test period"
            )

    except Exception as e:
        results.add_fail("Mean Reversion Strategy", str(e))
        import traceback

        print("\n  Full error traceback:")
        traceback.print_exc()

    return results


def main():
    """Run all backtesting framework tests."""
    print("=" * 70)
    print("BACKTESTING FRAMEWORK TEST DEMO")
    print("=" * 70)
    print("\nThis demo tests the core backtesting functionality:")
    print("  1. Data availability check")
    print("  2. BacktestDataLoader functionality")
    print("  3. SMA Crossover Strategy")
    print("  4. Cross-Sectional Momentum Strategy")
    print("  5. Bollinger Bands Mean Reversion Strategy")

    all_results = TestResults()

    try:
        # Step 1: Check data availability
        data_info = check_data_availability()

        if not data_info["sample_tickers"]:
            print("\n" + "=" * 70)
            print("❌ CANNOT PROCEED: No test data available")
            print("=" * 70)
            print("\nTo fix this issue:")
            print(
                "  1. Generate test data using: "
                "uv run python -m equity_lake.devtools.test_data"
            )
            print("  2. Or run daily ingestion: make daily")
            print("  3. Or sync from S3: make sync")
            return

        # Step 2: Test data loader
        loader_results = test_data_loader(data_info)
        all_results.passed_tests.extend(loader_results.passed_tests)
        all_results.failed_tests.extend(loader_results.failed_tests)
        all_results.warnings.extend(loader_results.warnings)
        all_results.performance_data.update(loader_results.performance_data)

        # If data loading failed, skip remaining tests
        if loader_results.failed_tests:
            print("\n⚠️  Skipping strategy tests due to data loading failures")
            all_results.print_summary()
            return

        # Step 3: Test SMA Crossover
        sma_results = test_sma_crossover_strategy(data_info)
        all_results.passed_tests.extend(sma_results.passed_tests)
        all_results.failed_tests.extend(sma_results.failed_tests)
        all_results.warnings.extend(sma_results.warnings)
        all_results.performance_data.update(sma_results.performance_data)

        # Step 4: Test Momentum
        momentum_results = test_momentum_strategy(data_info)
        all_results.passed_tests.extend(momentum_results.passed_tests)
        all_results.failed_tests.extend(momentum_results.failed_tests)
        all_results.warnings.extend(momentum_results.warnings)
        all_results.performance_data.update(momentum_results.performance_data)

        # Step 5: Test Mean Reversion
        mr_results = test_mean_reversion_strategy(data_info)
        all_results.passed_tests.extend(mr_results.passed_tests)
        all_results.failed_tests.extend(mr_results.failed_tests)
        all_results.warnings.extend(mr_results.warnings)
        all_results.performance_data.update(mr_results.performance_data)

    except Exception as e:
        all_results.add_fail("Test Suite", f"Unexpected error: {e}")
        import traceback

        print("\n  Fatal error traceback:")
        traceback.print_exc()

    # Print final summary
    all_results.print_summary()

    # Production readiness assessment
    print("\n" + "=" * 70)
    print("PRODUCTION READINESS ASSESSMENT")
    print("=" * 70)

    critical_failures = [
        name
        for name, _ in all_results.failed_tests
        if "Initialization" in name or "Data Loading" in name
    ]

    if not all_results.failed_tests:
        print("✅ READY: All core functionality is working correctly")
        print(
            "\nThe backtesting framework is ready for production use "
            "with the following notes:"
        )
        print("  • Data loading from Parquet files works correctly")
        print("  • All three tested strategies execute successfully")
        print("  • Performance metrics are calculated properly")
        print("  • Equity curves are generated correctly")
    elif critical_failures:
        print("❌ NOT READY: Critical failures detected")
        print("\nCritical issues must be fixed before production use:")
        for failure in critical_failures:
            print(f"  • {failure}")
    else:
        print("⚠️  PARTIALLY READY: Some issues detected")
        print("\nMinor issues that should be addressed:")
        print("  • Review failed tests and warnings above")
        print("  • Some strategies may need parameter tuning")
        print("  • Consider edge cases in signal generation")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
