"""FindingCard — the unifying comparison/finding artifact for the portfolio showcase.

Every portfolio comparison (strategy, model, ablation, cost, benchmark, risk)
emits one or more :class:`FindingCard` records. Cards are evidence-backed and
machine-readable; the React "Findings" surface renders them generically from the
serialized JSON. Negative results are first-class — a defensible negative is a
stronger portfolio line than a cherry-picked positive.

These are **auxiliary, non-catalog** artifacts (see AGENTS.md "Paths"). They live
under ``data/findings/`` (``FINDINGS_DIR``), not the medallion lake, and are not
validated by pointblank — the Pydantic model is the write-boundary contract.
"""

from __future__ import annotations

from equity_lake.findings.models import FindingAxis, FindingCard, FindingVerdict
from equity_lake.findings.writer import evidence_dir, load_finding_cards, write_finding_card

__all__ = [
    "FindingAxis",
    "FindingCard",
    "FindingVerdict",
    "evidence_dir",
    "load_finding_cards",
    "write_finding_card",
]
