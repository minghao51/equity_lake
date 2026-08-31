"""Weights & Biases registry adapter (best-effort).

Mirrors comparison :class:`FindingCard` objects to a public Weights & Biases
project. Local artifacts — the FindingCard JSONs written by
:mod:`equity_lake.findings.writer` — remain the **source of truth**. This
adapter only *logs* to W&B and MUST NEVER be a hard runtime dependency of
training or comparison.

Configuration is raw/unprefixed (parent handoff §3 B3): ``WANDB_API_KEY``,
``WANDB_ENTITY`` and ``WANDB_PROJECT`` are read via :func:`os.getenv` at the
client seam and are deliberately **not** declared in
:class:`equity_lake.config.Settings` (which is ``extra="forbid"``). When
``WANDB_API_KEY`` is unset/falsy, every public function is a silent no-op that
returns ``None``; any wandb-side failure is caught, logged at debug, and also
returns ``None`` so W&B can never break a training or comparison run.

``wandb`` lives in the optional ``ml`` dependency group and is imported lazily
inside each function so a base ``uv sync`` (no ``ml`` group) keeps working.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence

import structlog

from equity_lake.findings.models import FindingCard

logger = structlog.get_logger(__name__)

__all__ = ["log_comparison"]


def log_comparison(
    finding_cards: Sequence[FindingCard],
    *,
    project: str | None = None,
    entity: str | None = None,
    name: str | None = None,
) -> str | None:
    """Log a comparison (one W&B Table of cards + a single Report) to W&B.

    Logs one W&B Table row per card (``id``/``axis``/``claim``/``verdict``/
    ``conclusion`` + ``metrics``) and creates one W&B Report summarizing the
    comparison. Best-effort: returns ``None`` (logging at debug) when W&B is
    unconfigured (``WANDB_API_KEY`` unset), when no cards are supplied, or when
    any wandb call fails. Never raises due to W&B.

    Args:
        finding_cards: The comparison's FindingCards (one table row per card).
        project: W&B project; defaults to the ``WANDB_PROJECT`` env var.
        entity: W&B entity (team/user); defaults to ``WANDB_ENTITY``.
        name: Run + report title; defaults to the card ids joined by ``-``.

    Returns:
        The W&B Report URL, the run URL as a defensive fallback, or ``None``
        when W&B is unconfigured.
    """
    if not os.getenv("WANDB_API_KEY"):
        logger.debug("wandb_disabled_no_api_key")
        return None
    cards = list(finding_cards)
    if not cards:
        logger.debug("wandb_comparison_no_cards")
        return None
    project_name = project or os.getenv("WANDB_PROJECT")
    entity_name = entity or os.getenv("WANDB_ENTITY")
    run_name = name or "-".join(card.id for card in cards)
    try:
        import wandb

        run = wandb.init(
            project=project_name,
            entity=entity_name,
            name=run_name,
            job_type="comparison",
        )
        table = wandb.Table(
            columns=["id", "axis", "claim", "verdict", "conclusion", "metrics"],
            data=[
                [
                    card.id,
                    card.axis,
                    card.claim,
                    card.verdict,
                    card.conclusion,
                    dict(card.metrics),
                ]
                for card in cards
            ],
        )
        wandb.log({"comparison_cards": table})
        run_url = str(run.url)
        report_url = _create_comparison_report(
            cards=cards,
            title=run_name,
            project=project_name,
            entity=entity_name,
        )
        with contextlib.suppress(Exception):
            run.finish()
        return report_url or run_url
    except Exception as exc:  # W&B is best-effort; never propagate.
        logger.debug("wandb_comparison_failed", error=str(exc))
        return None


def _create_comparison_report(
    *,
    cards: Sequence[FindingCard],
    title: str,
    project: str | None,
    entity: str | None,
) -> str | None:
    """Defensively create a W&B Report summarizing the comparison.

    The ``wandb.apis.reports`` surface is private/volatile across versions, so
    every step is wrapped: a missing or broken Report API simply returns
    ``None`` and the caller falls back to the run URL.
    """
    try:
        from wandb.apis.reports import MarkdownBlock, Report  # private/volatile API; guarded by the try/except below
    except Exception as exc:  # private API; best-effort.
        logger.debug("wandb_report_import_failed", error=str(exc))
        return None
    if project is None or entity is None:
        # Report() requires concrete project/entity strings; without them a
        # report cannot be addressed, so fall back to the run URL.
        return None
    try:
        lines = [f"# {title}", ""]
        for card in cards:
            metrics_render = ", ".join(f"{key}={value:.4g}" for key, value in card.metrics.items()) or "none"
            lines.extend(
                [
                    f"## {card.id} ({card.axis}) — {card.verdict}",
                    "",
                    f"**Claim:** {card.claim}",
                    "",
                    f"**Conclusion:** {card.conclusion}",
                    "",
                    f"**Metrics:** {metrics_render}",
                    "",
                ]
            )
        report = Report(project=project, entity=entity, title=title)
        report.blocks = [MarkdownBlock(text="\n".join(lines))]
        report.save()
        url = getattr(report, "url", None)
        return str(url) if url else None
    except Exception as exc:  # private API; best-effort.
        logger.debug("wandb_report_create_failed", error=str(exc))
        return None
