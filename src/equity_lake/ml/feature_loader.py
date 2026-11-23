from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import polars as pl
import structlog

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AVAILABILITY_SCHEMA_VERSION,
    COVERAGE_BASES,
    FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    PRICE_ADJUSTMENT_VERSION,
    AvailabilityContractError,
    CoverageBasis,
    validate_feature_action_coverage,
    validate_weak_feature_action_coverage,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_COLUMNS,
    IDENTITY_SCHEMA_VERSION,
    InstrumentIdentityError,
    InstrumentKey,
    normalize_identity_frame,
)
from equity_lake.core.paths import GOLD_DIR, GOLD_FEATURES_DIR, LAKE_DIR
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION
from equity_lake.core.source_clock import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    SourceClockRecord,
    SourceClockUnavailable,
    require_qualified_source_clock,
)

logger = structlog.get_logger(__name__)


class FeatureLoader:
    def __init__(self, lake_dir: Path | str | None = None, coverage_basis: CoverageBasis = "source_clock") -> None:
        """Stateless reader; each request reads only the requested local Delta scope.

        ``lake_dir`` selects the lake root the reader reads (``None`` is the canonical
        lake). A root is never mixed with another root inside one read, and an
        alternate root (ADR-0030, e.g. ``data/lake_real``) is only ever selected
        explicitly.

        ``coverage_basis`` selects the ADR-0032 action-coverage basis this read
        admits, one per read (ADR-0034 decision 1): ``source_clock`` is the strict
        point-in-time default and every existing call site keeps it.
        ``vendor_events_no_clock`` is an explicit research-only opt-in that admits
        the weak vendor-event-completeness rows and is never inferred, defaulted or
        selected by an environment switch.
        """
        if coverage_basis not in COVERAGE_BASES:
            raise ValueError(f"Unknown coverage basis: {coverage_basis!r} (expected one of {COVERAGE_BASES})")
        self.lake_dir = Path(lake_dir) if lake_dir is not None else None
        self.coverage_basis: CoverageBasis = coverage_basis

    def features_dir(self) -> Path:
        """The Gold feature table this reader is scoped to (one root, resolved lazily)."""
        if self.lake_dir is None:
            return GOLD_FEATURES_DIR
        return self.lake_dir / GOLD_DIR.relative_to(LAKE_DIR) / "features"

    def load_features(
        self,
        ticker: str | InstrumentKey,
        start_date: date,
        end_date: date,
        *,
        require_available_by: date | None = None,
        trusted_legacy_provenance: Mapping[str, Sequence[InstrumentKey]] | None = None,
        source_clock_source: str | None = None,
        source_clock_records: Sequence[SourceClockRecord | Mapping[str, object]] | None = None,
    ) -> pl.DataFrame:
        """Read Gold features by canonical identity.

        A bare string is a compatibility selector only: matching stored rows
        must already carry identity and resolve to exactly one instrument. A
        ticker-only legacy row requires a caller-supplied trusted historical
        provenance map. Current ticker configuration is never consulted.

        The read admits exactly the basis selected at construction (ADR-0034
        decision 1): a row stamped with a different basis refuses, so a mixed-basis
        frame is never reinterpreted. Under the default strict ``source_clock``
        basis the weak validator below is unreachable and weak rows refuse exactly
        as before (ADR-0034 decision 2).
        """
        symbol = ticker if isinstance(ticker, str) else ticker.symbol
        features_dir = self.features_dir()
        if not features_dir.exists():
            return pl.DataFrame()

        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(features_dir)):
            table = DeltaTable(str(features_dir))
            columns = {field.name for field in table.schema().fields}
            symbol_column = "ticker" if "ticker" in columns else "symbol"
            # Push down only the date range. Label finalization rewrites matched
            # files through delta-rs, which stores string columns as Arrow
            # string_view; pyarrow has no string_view comparison kernels, so a
            # pushed-down symbol filter raises ArrowNotImplementedError on such
            # a table (pyarrow 21/22). The exact symbol scope is applied below.
            filters: list[tuple[str, str, object]] = [
                ("date", ">=", start_date),
                ("date", "<=", end_date),
            ]
            raw = pl.DataFrame(table.to_pyarrow_table(filters=filters))
            if symbol_column in raw.columns:
                raw = raw.filter(pl.col(symbol_column) == symbol)
        else:
            parquet = pl.scan_parquet(str(features_dir / "**" / "*.parquet"), hive_partitioning=True)
            columns = set(parquet.collect_schema().names())
            symbol_column = "ticker" if "ticker" in columns else "symbol"
            raw = parquet.filter(
                (pl.col(symbol_column) == symbol) & pl.col("date").cast(pl.Date).is_between(start_date, end_date, closed="both")
            ).collect()

        if raw.is_empty():
            logger.warning("features_not_found", ticker=symbol, start_date=str(start_date), end_date=str(end_date))
            return raw

        legacy_identity_rows = any(column not in raw.columns for column in ("market", "mic")) or any(
            raw[column].null_count() > 0 for column in ("market", "mic") if column in raw.columns
        )
        frame = normalize_identity_frame(
            raw,
            trusted_legacy_provenance=trusted_legacy_provenance,
            deduplicate=True,
        )
        if isinstance(ticker, str):
            identities = frame.select(*IDENTITY_COLUMNS).unique()
            if identities.height != 1:
                raise InstrumentIdentityError(f"Bare symbol {ticker!r} resolves to {identities.height} instruments; pass an InstrumentKey")
        else:
            requested = InstrumentKey.model_validate(ticker)
            requested_market, requested_mic, requested_symbol = requested.tuple
            frame = frame.filter((pl.col("market") == requested_market) & (pl.col("mic") == requested_mic) & (pl.col("symbol") == requested_symbol))
        if frame.is_empty():
            logger.warning("features_not_found_for_instrument", instrument=str(ticker))
            return frame
        if "identity_schema_version" not in frame.columns:
            if trusted_legacy_provenance is None or not legacy_identity_rows:
                raise InstrumentIdentityError("Gold features have no identity_schema_version; compatibility read requires trusted legacy provenance")
            frame = frame.with_columns(pl.lit(IDENTITY_SCHEMA_VERSION, dtype=pl.Int8).alias("identity_schema_version"))
        elif legacy_identity_rows and trusted_legacy_provenance is not None:
            frame = frame.with_columns(pl.col("identity_schema_version").fill_null(IDENTITY_SCHEMA_VERSION))
        versions = frame["identity_schema_version"].drop_nulls().unique().to_list()
        if versions != [IDENTITY_SCHEMA_VERSION] or frame["identity_schema_version"].null_count() > 0:
            raise InstrumentIdentityError(f"Unsupported or unknown identity schema versions: {versions}")

        required_contract = {
            "feature_schema_version",
            "price_adjustment_version",
            "availability_schema_version",
            "action_coverage",
            "action_source_age_days",
            *FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
        }
        missing_contract = required_contract - set(frame.columns)
        if missing_contract:
            missing_proof = sorted(set(FEATURE_ACTION_COVERAGE_PROOF_COLUMNS) & missing_contract)
            if missing_proof:
                raise AvailabilityContractError(f"Gold feature source-availability proof is unavailable (legacy rows lack {missing_proof})")
            raise InstrumentIdentityError(f"Gold feature availability/adjustment contract is unknown: missing {sorted(missing_contract)}")
        feature_versions = frame["feature_schema_version"].drop_nulls().unique().to_list()
        adjustment_versions = frame["price_adjustment_version"].drop_nulls().unique().to_list()
        availability_versions = frame["availability_schema_version"].drop_nulls().unique().to_list()
        if (
            feature_versions != [FEATURE_SCHEMA_VERSION]
            or adjustment_versions != [PRICE_ADJUSTMENT_VERSION]
            or availability_versions != [AVAILABILITY_SCHEMA_VERSION]
            or frame["action_coverage"].null_count()
            or any(value != 1 for value in frame["action_coverage"].to_list())
        ):
            raise InstrumentIdentityError("Gold feature rows have unknown or incompatible adjustment/availability semantics")
        # One read admits one basis (ADR-0032 decision 4, ADR-0034 decision 1): rows
        # stamped with any other basis refuse here, so a weak row can never be
        # reinterpreted as point-in-time evidence or the reverse.
        recorded_bases = sorted({str(value) for value in frame["action_coverage_basis"].to_list() if value is not None})
        mismatched = [basis for basis in recorded_bases if basis != self.coverage_basis]
        if mismatched:
            raise AvailabilityContractError(
                f"Gold feature rows carry action-coverage basis {mismatched} but this read requested {self.coverage_basis!r}; "
                "one read admits one basis, so a mixed or mismatched basis is refused (ADR-0032 decision 4)"
            )
        try:
            if self.coverage_basis == "vendor_events_no_clock":
                for row in frame.to_dicts():
                    validate_weak_feature_action_coverage(row)
            else:
                for row in frame.to_dicts():
                    validate_feature_action_coverage(row)
        except AvailabilityContractError as exc:
            raise AvailabilityContractError(f"Gold feature rows have unavailable action-coverage provenance: {exc}") from exc

        if source_clock_source is not None or source_clock_records is not None:
            if source_clock_source is None or source_clock_records is None:
                raise SourceClockUnavailable("a qualified source clock needs both the source name and its append-only qualification records")
            for instrument in frame.select(*IDENTITY_COLUMNS).unique().to_dicts():
                require_qualified_source_clock(
                    source=source_clock_source,
                    market=str(instrument["market"]),
                    series=str(instrument["symbol"]),
                    as_of=end_date,
                    records=source_clock_records,
                )
        if require_available_by is not None:
            from equity_lake.features.labels import filter_as_of_available

            frame = filter_as_of_available(frame, require_available_by)
        return frame

    def close(self) -> None:
        """Compatibility no-op: this reader owns no persistent connection."""


__all__ = ["FeatureLoader"]
