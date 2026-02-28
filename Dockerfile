# Equity EOD Data Pipeline - Dockerfile
# Multi-stage build for production-ready images

# =============================================================================
# Stage 1: Base Image with uv
# =============================================================================
FROM python:3.11-slim AS base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Set uv cache directory
    UV_CACHE_DIR=/root/.cache/uv

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # curl for downloading uv
    curl \
    # AWS CLI for S3 operations
    awscli \
    # git for version control
    git \
    # clean up
    && rm -rf /var/lib/apt/lists/*

# Install uv (ultra-fast Python package installer)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Set working directory
WORKDIR /app

# =============================================================================
# Stage 2: Dependencies Installation
# =============================================================================
FROM base AS dependencies

# Copy dependency files
COPY pyproject.toml requirements.txt requirements-dev.txt ./

# Install dependencies with uv
RUN uv pip install --system -r requirements.txt

# Install dev dependencies if needed (comment out for production)
# RUN uv pip install --system -r requirements-dev.txt

# =============================================================================
# Stage 3: Production Image
# =============================================================================
FROM base AS production

# Copy dependencies from previous stage
COPY --from=dependencies /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p data/lake/us_equity data/lake/cn_ashare data/lake/hk_sg_equity logs

# Set environment variables
ENV PYTHONPATH=/app \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs

# Default command (can be overridden)
CMD ["uv", "run", "equity-daily"]

# =============================================================================
# Stage 4: Development Image (with dev tools)
# =============================================================================
FROM production AS development

# Install development dependencies
RUN uv pip install --system -r requirements-dev.txt

# Install s5cmd for faster S3 sync
RUN curl -L https://github.com/peak/s5cmd/releases/latest/download/s5cmd_$(uname -s)_$(uname -m).tar.gz | \
    tar xz && \
    mv s5cmd /usr/local/bin/

# Volume mounts for development
VOLUME ["/app/data", "/app/logs"]

# Keep container running for development
CMD ["tail", "-f", "/dev/null"]
