"""Shared CLI display helpers — the single FindingCard print contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from collections.abc import Iterable

    from equity_lake.findings.models import FindingCard


def format_finding_card(card: FindingCard) -> str:
    """One canonical FindingCard CLI line."""
    return f"  [{card.axis}] {card.id}: {card.verdict} — {card.conclusion}"


def print_finding_cards(cards: Iterable[FindingCard]) -> None:
    """Print FindingCards in the canonical CLI format."""
    for card in cards:
        typer.echo(format_finding_card(card))
