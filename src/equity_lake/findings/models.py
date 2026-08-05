"""Pydantic model for a single comparison / finding card."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FindingAxis = Literal["labeling", "model", "ablation", "strategy", "cost", "benchmark", "risk"]
FindingVerdict = Literal["positive", "negative", "inconclusive"]


class FindingCard(BaseModel):
    """One evidence-backed comparison or conclusion.

    Attributes:
        id: Stable unique identifier, e.g. ``"meta_label_vs_direction"``.
        axis: The comparison axis (see the roadmap "Lead narrative" table).
        claim: One-line hypothesis being tested.
        verdict: Outcome of the test. Negatives are valid and encouraged.
        conclusion: Honest one-line takeaway (state what was found, including
            when an approach did *not* help).
        metrics: Numeric evidence, e.g. ``{"sharpe": 1.2, "oos_accuracy": 0.54}``.
        evidence_refs: Paths to backing artifacts (parquet/png/json) under
            ``data/findings/<id>/``.
        run_date: Date the comparison was run.
        scope: Reproducibility metadata — tickers, window, costs, seed, etc.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Stable unique id")
    axis: FindingAxis
    claim: str = Field(..., min_length=1, description="One-line hypothesis tested")
    verdict: FindingVerdict
    conclusion: str = Field(..., min_length=1, description="Honest one-line takeaway")
    metrics: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list, description="Paths to evidence artifacts")
    run_date: date
    scope: dict[str, Any] = Field(default_factory=dict, description="Reproducibility metadata")
