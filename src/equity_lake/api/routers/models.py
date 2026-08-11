"""Trained-model artifact read endpoints (Phase 2B)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from equity_lake.api import deps

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
def list_models() -> list[dict[str, Any]]:
    """List trained-model summaries (backend, mode, fold metrics, artifact name)."""
    return deps.list_models()
