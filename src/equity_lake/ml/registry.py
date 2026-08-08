"""Weights & Biases registry adapter (best-effort).

Mirrors locally-persisted training metadata and comparison :class:`FindingCard`
objects to a public Weights & Biases project. Local artifacts —
``*.training_metadata.json`` / ``*.training_audit.parquet`` written by
:func:`equity_lake.ml.forecasting.PriceForecaster._save_training_metadata`, and
the FindingCard JSONs written by :mod:`equity_lake.findings.writer` — remain the
**source of truth**. This adapter only *logs* to W&B and MUST NEVER be a hard
runtime dependency of training or comparison.

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
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog

from equity_lake.findings.models import FindingCard

logger = structlog.get_logger(__name__)

__all__ = ["log_comparison", "log_training_run"]


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


def log_training_run(
    metadata_path: Path,
    *,
    shap_artifact: Path | None = None,
    project: str | None = None,
    entity: str | None = None,
) -> str | None:
    """Log a single training run (config + metrics + optional SHAP artifact).

    Reads the metadata JSON written by
    :meth:`equity_lake.ml.forecasting.PriceForecaster._save_training_metadata`
    and mirrors it to a W&B run: ``ticker``/``model_mode``/``params`` as config,
    the flattened ``metrics`` + ``validation_metrics`` as metrics, and — when
    ``shap_artifact`` points at an existing file — the SHAP importance dump as a
    W&B artifact. Best-effort: returns ``None`` (logging at debug) when W&B is
    unconfigured (``WANDB_API_KEY`` unset), the metadata file is missing, or any
    wandb call fails. Never raises due to W&B.

    Args:
        metadata_path: Path to ``<model>.training_metadata.json``.
        shap_artifact: Optional path to a SHAP artifact file to attach.
        project: W&B project; defaults to the ``WANDB_PROJECT`` env var.
        entity: W&B entity (team/user); defaults to ``WANDB_ENTITY``.

    Returns:
        The W&B run URL, or ``None`` when W&B is unconfigured.
    """
    if not os.getenv("WANDB_API_KEY"):
        logger.debug("wandb_disabled_no_api_key")
        return None
    if not metadata_path.exists():
        logger.debug("wandb_training_metadata_missing", path=str(metadata_path))
        return None
    project_name = project or os.getenv("WANDB_PROJECT")
    entity_name = entity or os.getenv("WANDB_ENTITY")
    try:
        import wandb

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        ticker = str(metadata.get("ticker", "unknown"))
        model_mode = str(metadata.get("model_mode", "unknown"))
        run = wandb.init(
            project=project_name,
            entity=entity_name,
            name=f"{ticker}-{model_mode}",
            job_type="train",
            config={
                "ticker": ticker,
                "model_mode": model_mode,
                "params": metadata.get("params", {}),
            },
        )
        metrics = {
            **_flatten_numeric(metadata.get("metrics", {})),
            **_flatten_numeric(metadata.get("validation_metrics", {})),
        }
        if metrics:
            wandb.log(metrics)
        if shap_artifact is not None and Path(shap_artifact).exists():
            artifact = wandb.Artifact(name=f"shap-{ticker}-{model_mode}", type="shap-importance")
            artifact.add_file(str(shap_artifact))
            run.log_artifact(artifact)
        url = str(run.url)
        with contextlib.suppress(Exception):
            run.finish()
        return url
    except Exception as exc:  # W&B is best-effort; never propagate.
        logger.debug("wandb_training_run_failed", error=str(exc))
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
        from wandb.apis.reports import MarkdownBlock, Report  # type: ignore[attr-defined]  # private/volatile API; guarded by the try/except below
    except Exception as exc:  # private API; best-effort.
        logger.debug("wandb_report_import_failed", error=str(exc))
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


def _flatten_numeric(values: dict[str, Any]) -> dict[str, float]:
    """Coerce a metadata metrics dict into ``{str: float}`` for ``wandb.log``.

    Non-numeric or nested values are dropped (W&B metrics must be scalar floats).
    """
    flat: dict[str, float] = {}
    for key, value in values.items():
        coerced = _to_float(value)
        if coerced is not None:
            flat[str(key)] = coerced
    return flat


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
