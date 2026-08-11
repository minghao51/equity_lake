"""Serialize FindingCards to ``data/findings/`` (auxiliary, non-catalog)."""

from __future__ import annotations

from pathlib import Path

import structlog

from equity_lake.core.paths import FINDINGS_DIR
from equity_lake.findings.models import FindingCard

logger = structlog.get_logger(__name__)


def evidence_dir(card_id: str, *, base: Path | None = None) -> Path:
    """Return (creating if needed) the per-card evidence directory.

    Evidence artifacts (equity curves, metrics dumps, plots) for a card live
    under ``<base>/<card_id>/``.
    """
    directory = (base or FINDINGS_DIR) / card_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def card_path(card_id: str, *, base: Path | None = None) -> Path:
    """Return the canonical write path ``<base>/<id>.json`` without writing it."""
    return (base or FINDINGS_DIR) / f"{card_id}.json"


def write_finding_card(card: FindingCard, *, base: Path | None = None) -> Path:
    """Validate and write a :class:`FindingCard` as ``<base>/<id>.json``.

    The Pydantic model is the write-boundary contract: invalid cards raise before
    any file is touched. Returns the written path.
    """
    path = card_path(card.id, base=base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(card.model_dump_json(indent=2), encoding="utf-8")
    logger.info("finding_card_written", card_id=card.id, axis=card.axis, path=str(path))
    return path


def load_finding_cards(base: Path | None = None) -> list[FindingCard]:
    """Load every ``<base>/*.json`` as a :class:`FindingCard` (best-effort).

    Unreadable/invalid files are skipped with a warning so a corrupt artifact
    never breaks the API. Returns cards sorted by id for deterministic output.
    """
    root = base or FINDINGS_DIR
    if not root.exists():
        return []
    cards: list[FindingCard] = []
    for path in sorted(root.glob("*.json")):
        try:
            cards.append(FindingCard.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001 — never let one bad card break listing
            logger.warning("finding_card_load_failed", path=str(path), error=str(exc))
    return cards
