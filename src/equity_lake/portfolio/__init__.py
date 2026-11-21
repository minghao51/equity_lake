"""Private portfolio ledger: events, store, CSV import, projections and reconciliation.

The package owns one private book in ``us_equity``/USD: the versioned event
contract (:mod:`equity_lake.portfolio.models`), the private SQLite store
(:mod:`equity_lake.portfolio.store`), the frozen canonical CSV importer with its
dry-run preview (:mod:`equity_lake.portfolio.csv_import`), the deterministic
effective-date replay (:mod:`equity_lake.portfolio.projections`), stored raw
book marks and as-of valuation (:mod:`equity_lake.portfolio.marks`) and
canonical statement reconciliation (:mod:`equity_lake.portfolio.reconcile`).

Deliberately absent: split accounting, FX, backup, migration, any operational
mark producer or mark-import command, any write inside a read command, time- or
money-weighted return claims, and the CLI surface itself. ML and signal policy
stay out of this package (ADR-0024); it also never imports ``cli/``,
``dashboard/``, ``sources/``, ``ml/`` or ``signals/``.
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
from equity_lake.portfolio.marks import (
    DEFAULT_MAX_STALE_SESSIONS,
    MARK_SCHEMA_VERSION,
    MAX_SUPPORTED_STALE_SESSIONS,
    STRICT_MAX_STALE_SESSIONS,
    MarkProvenance,
    MarksDocument,
    MarksError,
    MarkStatus,
    MarksValidationError,
    PortfolioValuation,
    PositionValuation,
    PriceBasis,
    PriceMark,
    ValuationBlock,
    ValuationBlockReason,
    completed_sessions_after,
    dump_marks_text,
    load_marks_file,
    parse_marks_text,
    value_positions,
)
from equity_lake.portfolio.models import (
    BasisStatus,
    LedgerEvent,
    LedgerEventType,
    LedgerIssue,
    LedgerValidationError,
    PortfolioLedgerError,
    instrument_label,
    validate_batch,
)
from equity_lake.portfolio.projections import (
    DIVIDEND_BREAKDOWN_UNAVAILABLE,
    LedgerProjection,
    PositionProjection,
    ProjectionError,
    average_cost_display,
    money_display,
    replay_ledger,
)
from equity_lake.portfolio.reconcile import (
    CASH_TOLERANCE,
    QUANTITY_TOLERANCE,
    STATEMENT_COLUMNS,
    ReconcileLine,
    ReconcileLineType,
    ReconcileState,
    ReconciliationReport,
    Statement,
    StatementError,
    StatementLineType,
    StatementPosition,
    parse_statement_file,
    parse_statement_text,
    reconcile,
)
from equity_lake.portfolio.store import (
    AppendOutcome,
    LedgerConflictError,
    LedgerFileVerification,
    LedgerMigrationReport,
    LedgerStore,
    LedgerStoreError,
    migrate_ledger,
    portfolio_db_path,
    verify_ledger_file,
)

__all__ = [
    "AppendOutcome",
    "BasisStatus",
    "CANONICAL_CSV_COLUMNS",
    "CANONICAL_CSV_PARSER_VERSION",
    "CASH_TOLERANCE",
    "DEFAULT_MAX_STALE_SESSIONS",
    "DIVIDEND_BREAKDOWN_UNAVAILABLE",
    "ImportPreview",
    "LedgerConflictError",
    "LedgerEvent",
    "LedgerEventType",
    "LedgerFileVerification",
    "LedgerIssue",
    "LedgerMigrationReport",
    "LedgerProjection",
    "LedgerStore",
    "LedgerStoreError",
    "LedgerValidationError",
    "MARK_SCHEMA_VERSION",
    "MAX_SUPPORTED_STALE_SESSIONS",
    "MarkProvenance",
    "MarkStatus",
    "MarksDocument",
    "MarksError",
    "MarksValidationError",
    "PortfolioLedgerError",
    "PortfolioValuation",
    "PositionProjection",
    "PositionValuation",
    "PriceBasis",
    "PriceMark",
    "ProjectionError",
    "QUANTITY_TOLERANCE",
    "ReconcileLine",
    "ReconcileLineType",
    "ReconcileState",
    "ReconciliationReport",
    "STATEMENT_COLUMNS",
    "STRICT_MAX_STALE_SESSIONS",
    "Statement",
    "StatementError",
    "StatementLineType",
    "StatementPosition",
    "ValuationBlock",
    "ValuationBlockReason",
    "average_cost_display",
    "commit_events",
    "completed_sessions_after",
    "dump_marks_text",
    "instrument_label",
    "load_marks_file",
    "migrate_ledger",
    "money_display",
    "parse_csv_file",
    "parse_csv_text",
    "parse_marks_text",
    "parse_statement_file",
    "parse_statement_text",
    "portfolio_db_path",
    "preview_events",
    "reconcile",
    "replay_ledger",
    "validate_batch",
    "value_positions",
    "verify_ledger_file",
]
