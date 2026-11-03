"""Private portfolio ledger: append-only events, canonical CSV import (ADR-0024).

The package owns one private book in ``us_equity``/USD: the versioned event
contract (:mod:`equity_lake.portfolio.models`), the private SQLite store
(:mod:`equity_lake.portfolio.store`) and the frozen canonical CSV importer with
its dry-run preview (:mod:`equity_lake.portfolio.csv_import`).

Deliberately absent: positions, cash, basis, P&L, NAV projections and
reconciliation (P02), marks and staleness, split accounting, FX, backup,
migration, and the CLI surface. ML and signal policy stay out of this package
(ADR-0024); it also never imports ``cli/``, ``dashboard/``, ``sources/``,
``ml/`` or ``signals/``.
"""

from __future__ import annotations

from equity_lake.portfolio.csv_import import (
    CANONICAL_CSV_COLUMNS,
    CANONICAL_CSV_PARSER_VERSION,
    ImportPreview,
    commit_events,
    parse_csv_file,
    parse_csv_text,
    preview_events,
)
from equity_lake.portfolio.models import (
    BasisStatus,
    LedgerEvent,
    LedgerEventType,
    LedgerIssue,
    LedgerValidationError,
    PortfolioLedgerError,
    validate_batch,
)
from equity_lake.portfolio.store import (
    AppendOutcome,
    LedgerConflictError,
    LedgerStore,
    LedgerStoreError,
    portfolio_db_path,
)

__all__ = [
    "AppendOutcome",
    "BasisStatus",
    "CANONICAL_CSV_COLUMNS",
    "CANONICAL_CSV_PARSER_VERSION",
    "ImportPreview",
    "LedgerConflictError",
    "LedgerEvent",
    "LedgerEventType",
    "LedgerIssue",
    "LedgerStore",
    "LedgerStoreError",
    "LedgerValidationError",
    "PortfolioLedgerError",
    "commit_events",
    "parse_csv_file",
    "parse_csv_text",
    "portfolio_db_path",
    "preview_events",
    "validate_batch",
]
