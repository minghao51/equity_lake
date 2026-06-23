# Equity EOD Data Pipeline - Dockerfile
# Multi-stage build for production-ready images

# =============================================================================
# Stage 1: Base Image with uv
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/root/.cache/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    awscli \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --version 0.7.8
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# =============================================================================
# Stage 2: Dependencies Installation
# =============================================================================
FROM base AS dependencies

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

# =============================================================================
# Stage 3: Production Image
# =============================================================================
FROM base AS production

COPY --from=dependencies /app/.venv /app/.venv
COPY . .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs

RUN mkdir -p data/lake logs && \
    useradd --create-home --uid 1000 equity && \
    chown -R equity:equity /app

USER equity

HEALTHCHECK --interval=5m --timeout=10s --retries=3 \
    CMD python -c "import equity_lake; print('ok')" || exit 1

CMD ["equity", "pipeline"]

# =============================================================================
# Stage 4: Development Image (with dev tools)
# =============================================================================
FROM production AS development

USER root

RUN uv sync --frozen --all-groups

RUN curl -L https://github.com/peak/s5cmd/releases/download/v2.2.2/s5cmd_$(uname -s)_$(uname -m).tar.gz | \
    tar xz && \
    mv s5cmd /usr/local/bin/

USER equity

VOLUME ["/app/data", "/app/logs"]

CMD ["tail", "-f", "/dev/null"]
