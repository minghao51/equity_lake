#!/bin/bash
# Script to run the backtesting test demo

set -e  # Exit on error

echo "=================================="
echo "Running Backtesting Framework Test"
echo "=================================="
echo ""

# Activate virtual environment if it exists
if [ -f ".venv/bin/activate" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
fi

# Run the test demo
python examples/backtest_demo.py

echo ""
echo "=================================="
echo "Test Complete!"
echo "=================================="
