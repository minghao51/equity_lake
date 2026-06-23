# Equity EOD Data Pipeline - Makefile
# Provides convenient commands for development and operation

.PHONY: help setup sync daily query pipeline monitor test clean docker-up docker-down docker-logs lint lint-md format check generate-test-data fetch-macro news news-dry sentiment sentiment-dry test-backtest quick-test

# Default target
help:
	@echo "Equity EOD Data Pipeline - Available Commands:"
	@echo ""
	@echo "  setup      - Create virtual environment and install core dependencies"
	@echo "  test       - Run tests with coverage"
	@echo "  lint       - Run code linting (ruff)"
	@echo "  lint-md    - Run Markdown linting"
	@echo "  format     - Format code (ruff)"
	@echo "  check      - Run type checking (mypy)"
	@echo "  clean      - Clean cache and temporary files"
	@echo ""
	@echo "Data Pipeline Commands:"
	@echo "  sync       - One-time S3 sync (requires S3_BUCKET env var)"
	@echo "  daily      - Run daily EOD data ingestion"
	@echo "  query      - Run DuckDB query examples"
	@echo "  generate-test-data - Generate realistic test data"
	@echo "  news       - Fetch news with sentiment analysis"
	@echo "  news-dry   - Test news fetching (dry run)"
	@echo "  sentiment  - Fetch social sentiment (Reddit/Twitter)"
	@echo "  sentiment-dry - Test social sentiment fetching (dry run)"
	@echo ""
	@echo "Backtesting Commands:"
	@echo "  quick-test - Quick validation of backtesting framework"
	@echo "  test-backtest - Full backtesting test suite"
	@echo ""
	@echo "Docker Commands:"
	@echo "  docker-up  - Start Docker containers"
	@echo "  docker-down - Stop Docker containers"
	@echo "  docker-logs - Show Docker logs"
	@echo ""
	@echo "Development:"
	@echo "  dev-setup  - Install development dependencies"
	@echo "  validate   - Validate project setup"

# Environment and Setup
setup:
	@echo "🚀 Setting up Equity EOD Data Pipeline..."
	@echo "Installing core dependencies..."
	uv sync
	@echo "✅ Setup complete!"
	@echo "For the full ML pipeline: uv sync --extra ml"
	@echo "Run tests with: make test"

dev-setup: setup
	@echo "🛠️  Installing all development and optional dependencies..."
	uv sync --all-groups
	@echo "✅ Development setup complete!"

# Validation
validate:
	@echo "🔍 Validating project setup..."
	@echo "Python version: $$(uv run python --version)"
	@echo "uv version: $$(uv --version)"
	@echo "Virtual environment: $$(uv run python -c 'import sys; print(sys.prefix)')"
	@echo "Installed packages:"
	uv pip list | grep -E "(yfinance|akshare|duckdb|pandas)"
	@echo "✅ Validation complete!"

# Data Pipeline Commands
sync:
	@echo "🔄 Starting S3 sync..."
	@uv run equity sync

daily:
	@echo "📊 Running daily EOD data ingestion..."
	dotenvx run -- uv run equity ingest

query:
	@echo "🦆 Running DuckDB query examples..."
	uv run equity query

pipeline:
	@echo "🚀 Running the full ingestion → features → ML pipeline..."
	dotenvx run -- uv run equity pipeline

monitor:
	@echo "🩺 Running pipeline health checks..."
	uv run equity monitor

fetch-macro:
	@echo "📈 Fetching macro indicators for gold ETF analysis..."
	uv run equity macro

generate-test-data:
	@echo "🎲 Generating realistic test data..."
	uv run equity-generate-test-data

# News & Sentiment
news:
	@echo "📰 Fetching news with sentiment analysis..."
	uv run equity news

news-dry:
	@echo "🔍 Testing news fetching (dry run)..."
	uv run equity news --dry-run --verbose

# Social Sentiment
sentiment:
	@echo "📱 Fetching social sentiment (Reddit/Twitter)..."
	uv run equity sentiment

sentiment-dry:
	@echo "🔍 Testing social sentiment fetching (dry run)..."
	uv run equity sentiment --dry-run --verbose

# Testing
test:
	@echo "🧪 Running tests..."
	dotenvx run -- uv run pytest -v --cov=src/equity_lake --cov-report=html --cov-report=term

test-unit:
	@echo "🔬 Running unit tests..."
	uv run pytest tests/ -v -m "unit"

test-integration:
	@echo "🔗 Running integration tests..."
	uv run pytest tests/ -v -m "integration"

test-slow:
	@echo "🐌 Running slow tests..."
	uv run pytest tests/ -v -m "slow"

# Code Quality
lint:
	@echo "🔍 Running code linting..."
	uv run ruff check src/ tests/

lint-md:
	@echo "📝 Running Markdown linting..."
	uv run pymarkdown -d md013 scan README.md docs

format:
	@echo "🎨 Formatting code..."
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

check:
	@echo "🔬 Running type checking..."
	uv run mypy src/equity_lake

check-all: lint lint-md format check
	@echo "✅ All code quality checks complete!"

# Cleaning
clean:
	@echo "🧹 Cleaning up..."
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type f -name ".coverage" -delete
	find . -type d -name "htmlcov" -exec rm -rf {} +
	find . -type d -name ".cache" -exec rm -rf {} +
	find . -type f -name "*.log" -delete
	rm -f equity_data.duckdb
	@echo "✅ Clean complete!"

# Docker Commands
docker-up:
	@echo "🐳 Starting Docker containers..."
	docker compose up -d

docker-down:
	@echo "🐳 Stopping Docker containers..."
	docker compose down

docker-logs:
	@echo "📋 Showing Docker logs..."
	docker compose logs -f

docker-build:
	@echo "🔨 Building Docker image..."
	docker compose build

# Development shortcuts
dev-test: format lint test
	@echo "✅ Development test cycle complete!"

ci: validate check-all test-unit
	@echo "✅ CI checks complete!"

# Backtesting tests
quick-test:
	@echo "🔍 Running quick backtesting validation..."
	uv run python examples/quick_test.py

test-backtest:
	@echo "🧪 Running full backtesting test suite..."
	uv run python examples/backtest_demo.py

# Quick start (for new developers)
quick-start:
	@echo "🚀 Quick start for Equity EOD Data Pipeline..."
	make setup
	make dev-setup
	make validate
	@echo ""
	@echo "🎉 Quick start complete!"
	@echo "Next steps:"
	@echo "  1. Copy .env.example to .env and configure"
	@echo "  2. Run 'make test' to verify everything works"
	@echo "  3. Run 'make daily' to fetch test data"
	@echo "  4. Run 'make query' to explore data"
	@echo "  5. Run 'make quick-test' to validate backtesting framework"
	@echo ""
	@echo "Or try the unified CLI:"
	@echo "  equity bootstrap sample    # Generate sample data in seconds"
	@echo "  equity --help              # See all commands"
