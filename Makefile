# Equity EOD Data Pipeline - Makefile
# Provides convenient commands for development and operation

.PHONY: help setup sync daily query test clean docker-up docker-down docker-logs lint format check generate-test-data

# Default target
help:
	@echo "Equity EOD Data Pipeline - Available Commands:"
	@echo ""
	@echo "  setup      - Create virtual environment and install dependencies"
	@echo "  test       - Run tests with coverage"
	@echo "  lint       - Run code linting (ruff)"
	@echo "  format     - Format code (ruff)"
	@echo "  check      - Run type checking (mypy)"
	@echo "  clean      - Clean cache and temporary files"
	@echo ""
	@echo "Data Pipeline Commands:"
	@echo "  sync       - One-time S3 sync (requires S3_BUCKET env var)"
	@echo "  daily      - Run daily EOD data ingestion"
	@echo "  query      - Run DuckDB query examples"
	@echo "  generate-test-data - Generate realistic test data"
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
	@if [ ! -d ".venv" ]; then \
		echo "Creating uv virtual environment..."; \
		uv venv; \
	fi
	@echo "Installing core dependencies..."
	uv sync
	@echo "✅ Setup complete!"
	@echo "Activate with: source .venv/bin/activate"
	@echo "Run tests with: make test"

dev-setup: setup
	@echo "🛠️  Installing development dependencies..."
	uv sync --group dev
	uv sync --group s3
	uv sync --group visualization
	@echo "✅ Development setup complete!"

# Validation
validate:
	@echo "🔍 Validating project setup..."
	@echo "Python version: $$(python --version)"
	@echo "uv version: $$(uv --version)"
	@echo "Virtual environment: $$(python -c 'import sys; print(sys.prefix)')"
	@echo "Installed packages:"
	uv pip list | grep -E "(yfinance|akshare|duckdb|pandas)"
	@echo "✅ Validation complete!"

# Data Pipeline Commands
sync:
	@echo "🔄 Starting S3 sync..."
	@uv run python -m scripts.sync_from_s3

daily:
	@echo "📊 Running daily EOD data ingestion..."
	uv run python scripts/ingest_daily.py

query:
	@echo "🦆 Running DuckDB query examples..."
	uv run python scripts/query_example.py

generate-test-data:
	@echo "🎲 Generating realistic test data..."
	uv run python scripts/generate_test_data.py

# Testing
test:
	@echo "🧪 Running tests..."
	uv run pytest -v --cov=scripts --cov-report=html --cov-report=term

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
	uv run ruff check scripts/ tests/

format:
	@echo "🎨 Formatting code..."
	uv run ruff format scripts/ tests/
	uv run ruff check --fix scripts/ tests/

check:
	@echo "🔬 Running type checking..."
	uv run mypy scripts/

check-all: lint format check
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