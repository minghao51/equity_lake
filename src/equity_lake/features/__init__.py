"""Feature engineering domain APIs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import structlog

from equity_lake.core.instruments import InstrumentKey  # pyright: ignore[reportMissingImports] -- source import verified by uv

if TYPE_CHECKING:
    from equity_lake.core.availability import CoverageBasis
    from equity_lake.features.engineering import FeatureEngineer

logger = structlog.get_logger(__name__)


class NoFeatureHistoryError(RuntimeError):
    """Raised by ``run_feature_job`` when no features can be produced.

    Distinguishes a recoverable "missing history" condition (which the pipeline
    may resolve via ``--allow-history-backfill``) from other failures.
    """


def _load_feature_engineer() -> type[FeatureEngineer]:
    try:
        from equity_lake.features.engineering import FeatureEngineer
    except ImportError as exc:
        raise RuntimeError(
            "Feature engineering requires the optional 'ml' dependencies.",
        ) from exc
    return FeatureEngineer


def load_feature_engineer() -> FeatureEngineer:
    """Instantiate the feature engineer (public accessor for CLI callers).

    Defers the optional ``ml`` dependency import to call time and raises
    :class:`RuntimeError` with an actionable message when it is missing —
    callers outside this package (e.g. ``equity ml ablate``) must use this
    accessor instead of importing the private loader.
    """
    return _load_feature_engineer()()


def scope_action_inputs(
    *,
    instruments: Sequence[InstrumentKey],
    start_date: date,
    end_date: date,
    lake_dir: Path | None = None,
    coverage_basis: CoverageBasis = "source_clock",
) -> dict[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]]:
    """Load per-instrument corporate-action inputs from one explicit lake root.

    Returns the ``action_inputs`` mapping :meth:`FeatureEngineer.generate_features`
    consumes: one ``(actions, coverage)`` pair per canonical instrument key. The
    action store holds every ticker of a market, and a ticker-only event is
    resolved against coverage evidence; an event whose ticker has no proof is
    refused by the read as unverified. Scoping both frames per instrument keeps
    an explicitly requested subset readable without widening the resolver.

    Refusals (all :class:`AvailabilityContractError`):

    * no coverage proof is recorded for the instrument;
    * the requested basis has no evidence (strict ``source_clock`` against
      weak-only evidence, or the weak basis against strict-only evidence);
    * the requested window does not reach the instrument's action coverage
      bound — the watermark is reported, never silently clamped.

    ``lake_dir`` is the root the run reads (``None`` is the canonical lake); the
    price and action reads of one run share it, and no root is ever mixed.
    """
    from equity_lake.core.availability import COVERAGE_BASES, AvailabilityContractError, parse_coverage_proof
    from equity_lake.ingestion.corporate_actions import coverage_frame_for_write, load_corporate_action_inputs

    if coverage_basis not in COVERAGE_BASES:
        raise ValueError(f"Unknown coverage basis: {coverage_basis!r} (expected one of {COVERAGE_BASES})")
    if end_date < start_date:
        raise AvailabilityContractError(f"feature window ends {end_date} before it starts {start_date}")

    market_inputs: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}
    action_inputs: dict[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]] = {}
    for instrument in instruments:
        if instrument.market not in market_inputs:
            market_inputs[instrument.market] = load_corporate_action_inputs(instrument.market, lake_dir=lake_dir)
        actions, coverage = market_inputs[instrument.market]
        proofs = [parse_coverage_proof(row) for row in coverage.to_dicts()]
        instrument_proofs = [proof for proof in proofs if proof.identity == instrument.tuple]
        if not instrument_proofs:
            raise AvailabilityContractError(
                f"{instrument.display_key}: no corporate-action coverage proof is recorded, so the feature run cannot establish action coverage"
            )
        selected = [proof for proof in instrument_proofs if proof.coverage_basis == coverage_basis]
        if not selected:
            recorded = ", ".join(sorted({proof.coverage_basis for proof in instrument_proofs}))
            if coverage_basis == "source_clock":
                raise AvailabilityContractError(
                    f"{instrument.display_key}: point-in-time source_clock coverage is unavailable; "
                    f"only {recorded} evidence exists (ADR-0032), and a point-in-time decision is refused"
                )
            raise AvailabilityContractError(
                f"{instrument.display_key}: vendor_events_no_clock coverage is unavailable; only {recorded} evidence exists"
            )
        if not any(proof.coverage_start <= end_date for proof in selected):
            first_start = min(proof.coverage_start for proof in selected)
            raise AvailabilityContractError(
                f"{instrument.display_key}: action coverage starts {first_start}, after the requested window ends {end_date}"
            )
        reaching = [proof for proof in selected if proof.coverage_end >= end_date]
        if not reaching:
            bound = max(proof.coverage_end for proof in selected)
            raise AvailabilityContractError(
                f"{instrument.display_key}: requested window ends {end_date}, beyond the action coverage bound {bound} "
                "recorded for this instrument; refresh its corporate actions instead of clamping the window"
            )
        instrument_actions = actions.filter(pl.col("ticker") == instrument.symbol) if not actions.is_empty() else actions
        action_inputs[instrument.tuple] = (instrument_actions, coverage_frame_for_write(reaching))
    return action_inputs


def run_feature_job(
    *,
    tickers: list[str],
    output_start_date: date,
    output_end_date: date,
    compute_target: bool = True,
    include_sentiment: bool = False,
    include_social_sentiment: bool = False,
    include_enriched_sentiment: bool = False,
    include_analyst_ratings: bool = False,
    include_sec_features: bool = False,
    diagnostics: dict[str, Any] | None = None,
    instruments: Sequence[InstrumentKey] | None = None,
    coverage_basis: CoverageBasis = "source_clock",
    lake_dir: Path | None = None,
    action_inputs: Mapping[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]] | None = None,
    dry_run: bool = False,
) -> pl.DataFrame:
    """Generate features over a warm-up window, then persist the requested range.

    The returned DataFrame contract is unchanged. Pass ``diagnostics`` (an
    out-dict) to receive requested/produced/missing ticker coverage with stable
    exclusion reason codes (W02/F07): ``no_price_rows``, ``insufficient_history``,
    ``invalid_output`` (phase-1 skips from the engineer), ``missing_output_bar``
    (computed in the warm-up window but absent from the requested output window),
    and ``intentional_limit`` (reserved for an explicit owner-approved cap).
    """
    feature_engineer_cls = _load_feature_engineer()

    query_start_date = output_start_date - timedelta(days=120)
    query_end_date = output_end_date

    engineer = feature_engineer_cls(lake_dir=lake_dir)
    engineer_diagnostics: dict[str, Any] = {}
    try:
        features_df = engineer.generate_features(
            tickers=tickers,
            start_date=query_start_date,
            end_date=query_end_date,
            compute_target=compute_target,
            include_sentiment=include_sentiment,
            include_social_sentiment=include_social_sentiment,
            include_enriched_sentiment=include_enriched_sentiment,
            include_analyst_ratings=include_analyst_ratings,
            include_sec_features=include_sec_features,
            diagnostics=engineer_diagnostics,
            instruments=instruments,
            action_inputs=action_inputs,
            coverage_basis=coverage_basis,
        )
    finally:
        engineer.close()

    phase1_skipped: dict[str, str] = dict(engineer_diagnostics.get("phase1_skipped", {}))
    requested_instruments = list(instruments or ())
    requested_symbol_counts: dict[str, int] = {}
    for instrument in requested_instruments:
        requested_symbol_counts[instrument.symbol] = requested_symbol_counts.get(instrument.symbol, 0) + 1
    if diagnostics is not None:
        # Fill early so even the no-output error paths below report scope with
        # reason codes rather than an empty payload. Identity fields are
        # additive; legacy ticker diagnostics remain available to callers.
        missing_instruments = {
            instrument.display_key: phase1_skipped.get(instrument.display_key, phase1_skipped.get(instrument.symbol, "missing_output_bar"))
            for instrument in requested_instruments
        }
        missing_tickers = {
            instrument.symbol if requested_symbol_counts[instrument.symbol] == 1 else instrument.display_key: reason
            for instrument, reason in zip(requested_instruments, missing_instruments.values(), strict=True)
        }
        diagnostics.clear()
        diagnostics.update(
            {
                "requested": [instrument.symbol for instrument in requested_instruments] if instruments is not None else list(tickers),
                "requested_instruments": [instrument.model_dump() for instrument in requested_instruments],
                "produced": [],
                "missing": missing_tickers if instruments is not None else {t: phase1_skipped.get(t, "missing_output_bar") for t in tickers},
                "missing_instruments": missing_instruments,
                "phase1_skipped": phase1_skipped,
            }
        )

    if features_df.is_empty():
        raise NoFeatureHistoryError("No features generated")

    if "date" in features_df.columns and features_df.schema["date"] != pl.Date:
        features_df = features_df.with_columns(pl.col("date").cast(pl.Date))

    # ADR-0014: stamp the session on which each label became computable. Only
    # runs that compute the target carry label metadata; ``compute_target=False``
    # neither stamps availability nor finalizes labels.
    if compute_target:
        from equity_lake.features.labels import AVAILABILITY_COLUMN, TARGET_COLUMN, add_label_availability

        features_df = add_label_availability(features_df)
        if TARGET_COLUMN in features_df.columns:
            features_df = features_df.with_columns(pl.col(TARGET_COLUMN).cast(pl.Float64, strict=False))
        if AVAILABILITY_COLUMN in features_df.columns:
            features_df = features_df.with_columns(pl.col(AVAILABILITY_COLUMN).cast(pl.Date, strict=False))

    output_df = features_df.filter((pl.col("date") >= pl.lit(output_start_date)) & (pl.col("date") <= pl.lit(output_end_date)))

    if output_df.is_empty():
        raise NoFeatureHistoryError("No features generated for the requested output window")

    if diagnostics is not None:
        produced_instruments = (
            [InstrumentKey(**row) for row in output_df.select("market", "mic", "symbol").unique().sort(["market", "mic", "symbol"]).to_dicts()]
            if {"market", "mic", "symbol"}.issubset(output_df.columns)
            else []
        )
        diagnostics["produced"] = (
            [instrument.symbol for instrument in produced_instruments]
            if instruments is not None
            else sorted(output_df["ticker"].drop_nulls().unique().to_list())
            if "ticker" in output_df.columns
            else []
        )
        diagnostics["produced_instruments"] = [instrument.model_dump() for instrument in produced_instruments]
        if instruments is not None:
            produced_keys = {instrument.tuple for instrument in produced_instruments}
            missing_keys = [instrument for instrument in requested_instruments if instrument.tuple not in produced_keys]
            diagnostics["missing_instruments"] = {
                instrument.display_key: phase1_skipped.get(instrument.display_key, phase1_skipped.get(instrument.symbol, "missing_output_bar"))
                for instrument in missing_keys
            }
            diagnostics["missing"] = {
                instrument.symbol if requested_symbol_counts[instrument.symbol] == 1 else instrument.display_key: reason
                for instrument, reason in zip(missing_keys, diagnostics["missing_instruments"].values(), strict=True)
            }
        else:
            produced_symbols = set(diagnostics["produced"])
            diagnostics["missing"] = {t: phase1_skipped.get(t, "missing_output_bar") for t in tickers if t not in produced_symbols}

    from equity_lake.ingestion.writers import upsert_dataset

    # ADR-0014 §7: never erase a label a later session already finalized when
    # an older output session is replayed. Only label columns are carried.
    if compute_target:
        from equity_lake.features.labels import preserve_finalized_labels

        output_df = preserve_finalized_labels(output_df, lake_dir=lake_dir)

    write_ok = upsert_dataset(output_df, "03_gold/features", output_end_date, dry_run=dry_run, lake_dir=lake_dir)
    if not write_ok:
        # Surface persistence failure so the pipeline records a failed features
        # stage instead of reporting success with unwritten data.
        raise RuntimeError("Feature write to 03_gold/features failed")

    # ADR-0014: finalize the preceding observed session's label from the next
    # session's bar. Matched-only and label-column-only; never inserts keys.
    if compute_target:
        from equity_lake.features.labels import LabelFinalizationResult, finalize_preceding_labels

        try:
            label_result = finalize_preceding_labels(features_df, output_start_date, lake_dir=lake_dir, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - label finalization must not erase a written feature stage
            logger.error("label_finalization_failed", error=str(exc))
            label_result = LabelFinalizationResult(error=str(exc))
        if diagnostics is not None:
            diagnostics["label_finalization"] = label_result.to_payload()

    return output_df


__all__ = ["FeatureEngineer", "load_feature_engineer", "run_feature_job", "scope_action_inputs"]


def __getattr__(name: str) -> Any:
    """Defer optional ML imports until the symbol is actually used."""
    if name == "FeatureEngineer":
        return _load_feature_engineer()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
