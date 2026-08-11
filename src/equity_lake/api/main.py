"""ASGI application factory for the Phase 2B read API.

FastAPI is imported lazily inside :func:`create_app` so the rest of the package
(and any code that only imports ``equity_lake.api``) never pays the framework
import cost unless the app is actually built/served.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - present only for type checkers
    from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build and configure the read-only ASGI application (routers mounted here)."""
    from fastapi import FastAPI

    from equity_lake.api.routers import backtests, findings, health, models, predictions, signals

    app = FastAPI(
        title="Equity Lake API",
        version="0.1.0",
        description="Read-only API over the equity data lake (findings, signals, predictions).",
    )
    app.include_router(health.router)
    app.include_router(findings.router)
    app.include_router(signals.router)
    app.include_router(models.router)
    app.include_router(predictions.router)
    app.include_router(backtests.router)
    return app


__all__ = ["create_app"]
