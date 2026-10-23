from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import polars as pl
import structlog

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AVAILABILITY_SCHEMA_VERSION,
    FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    PRICE_ADJUSTMENT_VERSION,
    AvailabilityContractError,
    validate_feature_action_coverage,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_COLUMNS,
    IDENTITY_SCHEMA_VERSION,
    InstrumentIdentityError,
    InstrumentKey,
    normalize_identity_frame,
)
from equity_lake.core.paths import GOLD_FEATURES_DIR
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION

logger = structlog.get_logger(__name__)


class FeatureLoader:
    def __init__(self) -> None:
        """Stateless reader; each request reads only the requested local Delta scope."""

    def load_features(
        self,
        ticker: str | InstrumentKey,
        start_date: date,
        end_date: date,
        *,
        require_available_by: date | None = None,
        trusted_legacy_provenance: Mapping[str, Sequence[InstrumentKey]] | None = None,
    ) -> pl.DataFrame:
        """Read Gold features by canonical identity.

        A bare string is a compatibility selector only: matching stored rows
        must already carry identity and resolve to exactly one instrument. A
        ticker-only legacy row requires a caller-supplied trusted historical
        provenance map. Current ticker configuration is never consulted.
        """
        symbol = ticker if isinstance(ticker, str) else ticker.symbol
        if not GOLD_FEATURES_DIR.exists():
            return pl.DataFrame()

        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(GOLD_FEATURES_DIR)):
            table = DeltaTable(str(GOLD_FEATURES_DIR))
            columns = {field.name for field in table.schema().fields}
            symbol_column = "ticker" if "ticker" in columns else "symbol"
            filters: list[tuple[str, str, object]] = [
                (symbol_column, "=", symbol),
                ("date", ">=", start_date),
                ("date", "<=", end_date),
            ]
            raw = pl.DataFrame(table.to_pyarrow_table(filters=filters))
        else:
            parquet = pl.scan_parquet(str(GOLD_FEATURES_DIR / "**" / "*.parquet"), hive_partitioning=True)
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
        try:
            for row in frame.to_dicts():
                validate_feature_action_coverage(row)
        except AvailabilityContractError as exc:
            raise AvailabilityContractError(f"Gold feature rows have unavailable action-coverage provenance: {exc}") from exc

        if require_available_by is not None:
            from equity_lake.features.labels import filter_as_of_available

            frame = filter_as_of_available(frame, require_available_by)
        return frame

    def close(self) -> None:
        """Compatibility no-op: this reader owns no persistent connection."""


__all__ = ["FeatureLoader"]
