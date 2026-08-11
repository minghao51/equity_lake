"""Findings read endpoints (Phase 2B).

Exposes the serialized FindingCards under ``data/findings/`` (the Phase-1/2A
research artifacts) as a read-only list + detail resource.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from equity_lake.api import deps
from equity_lake.findings.models import FindingCard

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get("", response_model=list[FindingCard])
def list_finding_cards() -> list[FindingCard]:
    """List every FindingCard (newest-first is not guaranteed; cards are id-sorted)."""
    return deps.list_findings()


@router.get("/{card_id}", response_model=FindingCard)
def get_finding_card(card_id: str) -> FindingCard:
    """Return one FindingCard by id, or 404 if it is not on disk."""
    for card in deps.list_findings():
        if card.id == card_id:
            return card
    raise HTTPException(status_code=404, detail=f"FindingCard '{card_id}' not found")
