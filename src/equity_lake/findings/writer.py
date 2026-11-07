"""Serialize FindingCards to ``data/findings/`` (auxiliary, non-catalog).

The module also owns the shared "never persist a non-finite number" JSON refusal
(:func:`refuse_non_finite_json`) used by the dated/attempt evidence writers, so a
card artifact and the run/attempt manifests share exactly one implementation of
that rule.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def _json_location(path: tuple[Any, ...]) -> str:
    """Render a JSON path as ``entry 3 field 'price'`` (an empty path is ``payload``)."""
    parts: list[str] = []
    fields: list[str] = []
    for node in path:
        if isinstance(node, int) and not isinstance(node, bool):
            if fields:
                parts.append(f"field {'.'.join(fields)!r}")
                fields = []
            parts.append(f"entry {node}")
        else:
            fields.append(str(node))
    if fields:
        parts.append(f"field {'.'.join(fields)!r}")
    return " ".join(parts) if parts else "payload"


def refuse_non_finite_json(artifact: str, payload: Any, _path: tuple[Any, ...] = ()) -> None:
    """Refuse a JSON payload carrying a non-finite number, naming artifact and location.

    ``json.dumps(..., allow_nan=False)`` refuses the payload too, but its message
    names neither the artifact nor the offending entry/field, so a reviewer cannot
    tell *which* value is a defect. A non-finite price, fee or quantity is a data
    defect, not an unavailable measurement, so the run is **refused** rather than
    sanitised to ``null`` (ADR-0025; F03 precedent: an unavailable number is omitted,
    never persisted — and a measured-looking number is never invented).

    ``None`` fields pass: an open position legitimately has no realised pnl or exit
    price, and a JSON ``null`` is the honest encoding for it.

    Raises:
        ValueError: naming the artifact and the offending index/key, e.g.
            ``trades.json entry 3 field 'price' is non-finite``.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            refuse_non_finite_json(artifact, value, (*_path, str(key)))
        return
    if isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            refuse_non_finite_json(artifact, value, (*_path, index))
        return
    if payload is None or isinstance(payload, (bool, str, bytes)):
        return
    try:
        numeric = float(payload)
    except (TypeError, ValueError):
        return
    if not math.isfinite(numeric):
        raise ValueError(
            f"{artifact} {_json_location(_path)} is non-finite: a non-finite number is a data defect, not an "
            "unavailable measurement, so the run is refused rather than publishing NaN/Infinity as evidence "
            "(ADR-0025; F03 precedent)"
        )


def _non_finite_token_rejecter(artifact: str) -> Callable[[str], Any]:
    """``json.loads`` ``parse_constant`` hook refusing ``NaN``/``Infinity``/``-Infinity``."""

    def reject(token: str) -> Any:
        raise ValueError(
            f"{artifact} carries a non-finite JSON token {token!r}: NaN/Infinity is never persisted as measured "
            "evidence; an unavailable measurement is recorded as null (ADR-0025; F03 precedent)"
        )

    return reject


def write_finding_card(card: FindingCard, *, base: Path | None = None, refuse_non_finite: bool = False) -> Path:
    """Validate and write a :class:`FindingCard` as ``<base>/<id>.json``.

    The Pydantic model is the write-boundary contract: invalid cards raise before
    any file is touched. Returns the written path.

    ``refuse_non_finite=True`` is the opt-in strict path used by the immutable
    run/attempt evidence writers (E01b.1): the serialized payload is walked first —
    naming the artifact and field — and the text is proven to parse without a
    ``NaN``/``Infinity`` token before it reaches disk, so a card artifact inside
    evidence can never carry a non-finite number. The default path is unchanged, so
    the ``ml`` writers keep their exact behaviour (Pydantic's own encoding).
    """
    artifact = f"{card.id}.json"
    if not refuse_non_finite:
        path = card_path(card.id, base=base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(card.model_dump_json(indent=2), encoding="utf-8")
        logger.info("finding_card_written", card_id=card.id, axis=card.axis, path=str(path))
        return path

    payload = card.model_dump(mode="json")
    refuse_non_finite_json(artifact, payload)
    try:
        # Belt and braces: prove the text about to be written parses without a
        # non-finite token, so the writer never depends on a validator downstream.
        text = json.dumps(payload, indent=2, allow_nan=False)
        json.loads(text, parse_constant=_non_finite_token_rejecter(artifact))
    except ValueError as exc:
        raise ValueError(f"{artifact} cannot be written as strict JSON: {exc}") from exc
    path = card_path(card.id, base=base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    logger.info("finding_card_written", card_id=card.id, axis=card.axis, path=str(path), strict=True)
    return path


def load_finding_cards(base: Path | None = None) -> list[FindingCard]:
    """Load every ``<base>/*.json`` as a :class:`FindingCard` (best-effort).

    This is the **legacy flat** reader (``<base>/<id>.json``). Unreadable/invalid
    files are skipped with a warning so a corrupt artifact never breaks the API.
    Returns cards sorted by id for deterministic output. The findings/arena read
    projection (attempt-bound cards first) lives in
    :mod:`equity_lake.backtesting.evidence_view`.
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


__all__ = [
    "card_path",
    "evidence_dir",
    "load_finding_cards",
    "refuse_non_finite_json",
    "write_finding_card",
]
