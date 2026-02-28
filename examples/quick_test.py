#!/usr/bin/env python
"""
Quick validation script for backtesting framework.

This performs basic checks to verify the backtesting module is working.
"""
import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

def test_imports():
    """Test that all backtesting modules can be imported."""
    print("Testing imports...")

    try:
        from equity_lake.backtesting import BacktestEngine, BacktestDataLoader, BacktestResult
        print("  ✅ Core backtesting modules imported")
    except ImportError as e:
        print(f"  ❌ Failed to import core modules: {e}")
        return False

    try:
        from equity_lake.backtesting.strategy import (
            SMACrossoverStrategy,
            CrossSectionalMomentumStrategy,
            BBMeanReversionStrategy,
            BaseStrategy,
        )
        print("  ✅ Strategy modules imported")
    except ImportError as e:
        print(f"  ❌ Failed to import strategy modules: {e}")
        return False

    try:
        import duckdb
        import pandas as pd
        import numpy as np
        print("  ✅ Dependencies available (duckdb, pandas, numpy)")
    except ImportError as e:
        print(f"  ❌ Missing dependency: {e}")
        return False

    return True


def test_data_loader():
    """Test BacktestDataLoader initialization."""
    print("\nTesting BacktestDataLoader...")

    try:
        from equity_lake.backtesting import BacktestDataLoader

        loader = BacktestDataLoader()
        print("  ✅ DataLoader initialized")

        # Check what data is available
        try:
            us_tickers = loader.get_available_tickers("us")
            print(f"  ✅ US market: {len(us_tickers)} tickers available")
            if us_tickers:
                print(f"     Sample tickers: {us_tickers[:5]}")
        except Exception as e:
            print(f"  ⚠️  Could not get US tickers: {e}")

        try:
            cn_tickers = loader.get_available_tickers("cn")
            print(f"  ✅ CN market: {len(cn_tickers)} tickers available")
        except Exception as e:
            print(f"  ⚠️  Could not get CN tickers: {e}")

        loader.close()
        return True

    except Exception as e:
        print(f"  ❌ DataLoader test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_strategy_init():
    """Test strategy initialization."""
    print("\nTesting strategy initialization...")

    try:
        from equity_lake.backtesting.strategy import (
            SMACrossoverStrategy,
            CrossSectionalMomentumStrategy,
            BBMeanReversionStrategy,
        )

        # Test SMA Crossover
        sma = SMACrossoverStrategy(params={"fast_period": 10, "slow_period": 30})
        print(f"  ✅ SMACrossoverStrategy initialized: {sma.name}")

        # Test Momentum
        momentum = CrossSectionalMomentumStrategy(params={"lookback_days": 60})
        print(f"  ✅ CrossSectionalMomentumStrategy initialized: {momentum.name}")

        # Test BB Mean Reversion
        bb = BBMeanReversionStrategy(params={"period": 20, "num_std": 2.0})
        print(f"  ✅ BBMeanReversionStrategy initialized: {bb.name}")

        return True

    except Exception as e:
        print(f"  ❌ Strategy initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all validation checks."""
    print("=" * 70)
    print("BACKTESTING FRAMEWORK QUICK VALIDATION")
    print("=" * 70)
    print()

    all_passed = True

    # Test 1: Imports
    if not test_imports():
        all_passed = False
        print("\n❌ FAILED: Import test failed")
        print("\nTo fix this issue:")
        print("  1. Ensure dependencies are installed: uv sync")
        print("  2. Check that src/equity_lake/backtesting exists")
        return 1

    # Test 2: DataLoader
    if not test_data_loader():
        all_passed = False

    # Test 3: Strategy initialization
    if not test_strategy_init():
        all_passed = False

    # Summary
    print("\n" + "=" * 70)
    if all_passed:
        print("✅ ALL VALIDATION CHECKS PASSED")
        print("\nThe backtesting framework is ready for use!")
        print("\nNext steps:")
        print("  1. Run full test suite: python examples/backtest_demo.py")
        print("  2. Create custom strategies")
        print("  3. Run backtests on your data")
    else:
        print("⚠️  SOME VALIDATION CHECKS FAILED")
        print("\nPlease review the errors above and fix any issues.")
        print("\nCommon fixes:")
        print("  • Ensure data exists in data/lake/ directories")
        print("  • Run: uv sync (to install dependencies)")
        print("  • Run: make daily (to fetch latest data)")
    print("=" * 70)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
