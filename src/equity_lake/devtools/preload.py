"""Bounded Gold feature preload (features -> labels) for the local surfaces.

Why this is a module and not a shell loop: a label becomes computable on the
next **observed** session (ADR-0014), so sessions must be written oldest ->
newest for each run to finalize its predecessor. The scope also has to be
bounded — a 500-session run is already roughly an hour of writes at ~8s per
session for five tickers — so the count is validated here instead of in Make.
Sessions come from the stored Bronze prices (the ADR-0014 "observed session"),
not from a calendar, so the finalization chain follows exactly what the lake
holds.

It drives the same :func:`equity_lake.features.run_feature_job` stage the
pipeline uses. Nothing is hand-written: labels are only finalized by the label
lifecycle, and predictions are left to the real ML path.

    uv run python -m equity_lake.devtools.preload --days 60
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import structlog

from equity_lake.core.instruments import InstrumentKey
from equity_lake.devtools.seed_demo import _configured_identities  # same package: config-derived identity, fail-closed
from equity_lake.features import NoFeatureHistoryError, run_feature_job
from equity_lake.storage.delta import DeltaError, read_delta

logger = structlog.get_logger(__name__)

PRELOAD_MARKET = "us_equity"
#: Bronze table the preload derives its session list from: a session is one that
#: actually has a stored bar (the ADR-0014 "observed session"), not a calendar day.
BRONZE_PRICE_TABLE = f"01_bronze/market_data/{PRELOAD_MARKET}"
#: Upper bound on one preload: ~8s per session for five tickers at the time of
#: writing, so this is already a long write against the canonical Gold table.
MAX_PRELOAD_SESSIONS = 500
DEFAULT_PRELOAD_SESSIONS = 60
DEFAULT_PRELOAD_TICKERS: tuple[str, ...] = ("AAPL", "AMZN", "GOOGL", "MSFT", "NVDA")


class PreloadScopeError(ValueError):
    """The requested preload scope is empty or exceeds the bounded maximum."""


def observed_sessions(days: int, *, end: date | None = None, lake_dir: Path | None = None) -> list[date]:
    """Return the last *days* sessions with a stored Bronze bar, oldest first.

    Sessions come from the stored prices rather than an exchange calendar: the
    label lifecycle defines the next computable date as the next **observed**
    session (ADR-0014), and a seeded lake can hold bars a calendar would not
    schedule. Deriving the list this way keeps the finalization chain unbroken.
    """
    if days < 1:
        raise PreloadScopeError(f"--days must be at least 1, got {days}")
    if days > MAX_PRELOAD_SESSIONS:
        raise PreloadScopeError(
            f"--days={days} exceeds the {MAX_PRELOAD_SESSIONS}-session bound; run several bounded preloads instead",
        )
    last = end if end is not None else date.today() - timedelta(days=1)
    try:
        frame = read_delta(BRONZE_PRICE_TABLE, lake_dir=lake_dir, columns=["date"])
    except DeltaError as exc:
        raise PreloadScopeError(f"cannot read {BRONZE_PRICE_TABLE}: {exc}") from exc
    dates = sorted({day for day in frame["date"].to_list() if day is not None and day <= last})
    if len(dates) < days:
        raise PreloadScopeError(
            f"only {len(dates)} observed sessions on or before {last.isoformat()}, {days} requested; ingest the window or seed a demo lake first",
        )
    return dates[-days:]


@dataclass(frozen=True)
class PreloadReport:
    """Outcome of one bounded preload run (summary only, no artifact written)."""

    sessions: tuple[date, ...]
    tickers: tuple[str, ...]
    rows_written: int = 0
    labels_finalized: int = 0
    skipped_sessions: tuple[date, ...] = ()
    errors: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "sessions": [day.isoformat() for day in self.sessions],
            "tickers": list(self.tickers),
            "rows_written": self.rows_written,
            "labels_finalized": self.labels_finalized,
            "skipped_sessions": [day.isoformat() for day in self.skipped_sessions],
            "errors": list(self.errors),
        }


def preload_features(
    *,
    days: int = DEFAULT_PRELOAD_SESSIONS,
    end: date | None = None,
    tickers: Sequence[str] = DEFAULT_PRELOAD_TICKERS,
    instruments: Sequence[InstrumentKey] | None = None,
    lake_dir: Path | None = None,
) -> PreloadReport:
    """Write features for *days* consecutive sessions, oldest first.

    Each session finalizes the preceding session's label, so the last session's
    label stays pending by design (there is no next observed bar yet). Re-running
    is idempotent: the Gold upsert keys on the canonical identity and date.
    """
    sessions = observed_sessions(days, end=end, lake_dir=lake_dir)
    symbols = [symbol.strip().upper() for symbol in tickers if symbol.strip()]
    if not symbols:
        raise PreloadScopeError("at least one ticker is required")
    if instruments is None:
        resolved = _configured_identities(sorted(symbols))
        instruments = [resolved[symbol] for symbol in symbols]

    rows_written = 0
    labels_finalized = 0
    skipped: list[date] = []
    errors: list[str] = []
    for session in sessions:
        diagnostics: dict[str, Any] = {}
        try:
            frame = run_feature_job(
                tickers=symbols,
                output_start_date=session,
                output_end_date=session,
                compute_target=True,
                instruments=instruments,
                diagnostics=diagnostics,
            )
        except NoFeatureHistoryError:
            # A session in the price table with no computable bar for these
            # tickers: nothing to write, and nothing is fabricated for it.
            skipped.append(session)
            logger.warning("preload_session_skipped", date=session.isoformat(), reason="no feature history")
            continue
        rows_written += frame.height
        labels = diagnostics.get("label_finalization") or {}
        labels_finalized += int(labels.get("finalized") or 0)
        if labels.get("error"):
            errors.append(f"{session.isoformat()}: {labels['error']}")
        logger.info("preload_session_complete", date=session.isoformat(), rows=frame.height, labels=labels)

    report = PreloadReport(
        sessions=tuple(sessions),
        tickers=tuple(symbols),
        rows_written=rows_written,
        labels_finalized=labels_finalized,
        skipped_sessions=tuple(skipped),
        errors=tuple(errors),
    )
    logger.info("preload_complete", **report.to_payload())
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by ``make preload``."""
    parser = argparse.ArgumentParser(
        prog="python -m equity_lake.devtools.preload",
        description="Bounded Gold feature preload (features + label finalization).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_PRELOAD_SESSIONS,
        help=f"Observed {PRELOAD_MARKET} sessions to preload (max {MAX_PRELOAD_SESSIONS}; ~8s each for five tickers)",
    )
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="Last session YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--tickers", default=",".join(DEFAULT_PRELOAD_TICKERS), help="Comma-separated configured US tickers")
    args = parser.parse_args(argv)

    from equity_lake.core.logging import setup_structured_logging

    setup_structured_logging()
    try:
        report = preload_features(days=args.days, end=args.end, tickers=args.tickers.split(","))
    except (PreloadScopeError, ValueError) as exc:
        logger.error("preload_scope_error", error=str(exc))
        return 2
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
