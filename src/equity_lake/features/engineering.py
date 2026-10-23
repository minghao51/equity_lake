#!/usr/bin/env python3
"""
Feature Engineering for Equity ML Models

Computes technical indicators, momentum features, and time-based features
from raw OHLCV data for machine learning models.

Usage:
    python -m equity_lake.features.engineering --date 2026-01-23
    python -m equity_lake.features.engineering --tickers AAPL,GOOGL --start 2024-01-01 --end 2024-12-31
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from types import TracebackType
from typing import Any, cast

import duckdb
import polars as pl
import structlog
from tqdm import tqdm

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AVAILABILITY_SCHEMA_VERSION,
    FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
    PRICE_ADJUSTMENT_VERSION,
    ActionCoverageProof,
    AvailabilityContractError,
    action_coverage_feature_fields,
    source_observable_session,
    validate_action_coverage_for_decision,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_COLUMNS,
    IDENTITY_SCHEMA_VERSION,
    InstrumentIdentityError,
    InstrumentKey,
    LegacyIdentityUnavailableError,
    instrument_from_record,
)
from equity_lake.core.paths import LAKE_DIR, PRICE_MARKETS
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION
from equity_lake.features.pipeline import FeaturePipeline
from equity_lake.storage.lake_reader import ensure_delta_extension

logger = structlog.get_logger()

NUMERIC_DTYPES = {pl.Float32, pl.Float64, pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64}
NON_NUMERIC_FOR_ZSCORE = {
    "next_day_return",
    "label_available_date",
    "target",
    "meta_label",
    "barrier_outcome",
    "upper_barrier_return",
    "lower_barrier_return",
    "vertical_barrier_days",
    "candidate_score",
}


class FeatureEngineer:
    """Computes ML features from raw OHLCV data."""

    def __init__(self, db_path: str | None = ":memory:"):
        self.db_path: str = db_path if db_path is not None else ":memory:"
        self.conn = duckdb.connect(str(self.db_path))
        self.feature_pipeline = FeaturePipeline()
        self._setup_views()

    def __enter__(self) -> FeatureEngineer:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def _setup_views(self) -> None:
        # Keep DuckDB for the identity-aware enrichment DAG, but read price
        # sources through Delta/Parquet APIs so local paths are never composed
        # into SQL and source MIC/exchange columns remain inspectable.
        ensure_delta_extension(self.conn)
        self.market_sources = [
            (market, LAKE_DIR / "01_bronze" / "market_data" / market)
            for market in PRICE_MARKETS
            if (LAKE_DIR / "01_bronze" / "market_data" / market).exists()
        ]
        logger.info("feature_price_sources_ready", market_count=len(self.market_sources))

    def _load_price_rows(self, tickers: list[str], start_date: date, end_date: date) -> pl.DataFrame:
        from deltalake import DeltaTable

        source_columns = ("market", "ticker", "symbol", "mic", "exchange", "date", "open", "high", "low", "close", "volume")
        frames: list[pl.DataFrame] = []
        for market, path in self.market_sources:
            symbol_column: str | None = None
            if DeltaTable.is_deltatable(str(path)):
                table = DeltaTable(str(path))
                available = {field.name for field in table.schema().fields}
                source = None
                if "date" in available:
                    symbol_column = "symbol" if "symbol" in available else "ticker" if "ticker" in available else None
                    if symbol_column is None:
                        logger.warning("feature_price_source_missing_columns", market=market, columns=sorted(available))
                        continue
                    selected = [name for name in source_columns if name in available]
                    source = cast(
                        pl.DataFrame,
                        pl.from_arrow(
                            table.to_pyarrow_table(
                                columns=selected,
                                filters=[("date", ">=", start_date), ("date", "<=", end_date), (symbol_column, "in", tickers)],
                            )
                        ),
                    )
            else:
                try:
                    parquet = pl.scan_parquet(str(path / "**" / "*.parquet"), hive_partitioning=True)
                    available = set(parquet.collect_schema().names())
                    if "date" not in available:
                        continue
                    symbol_column = "symbol" if "symbol" in available else "ticker" if "ticker" in available else None
                    if symbol_column is None:
                        logger.warning("feature_price_source_missing_columns", market=market, columns=sorted(available))
                        continue
                    selected = [name for name in source_columns if name in available]
                    source = (
                        parquet.filter(
                            pl.col(symbol_column).is_in(tickers) & pl.col("date").cast(pl.Date).is_between(start_date, end_date, closed="both")
                        )
                        .select(selected)
                        .collect()
                    )
                except (OSError, pl.exceptions.PolarsError):
                    logger.warning("feature_price_source_unreadable", market=market, path=str(path))
                    continue
            if source is None or source.is_empty():
                continue
            required_columns = {"date", "open", "high", "low", "close", "volume"}
            if symbol_column is None or symbol_column not in source.columns or not required_columns.issubset(source.columns):
                logger.warning("feature_price_source_missing_columns", market=market, columns=source.columns)
                continue
            if source.is_empty():
                continue
            expressions: list[pl.Expr] = [
                (pl.col("market").cast(pl.String) if "market" in source.columns else pl.lit(market)).alias("market"),
                pl.col(symbol_column).cast(pl.String).alias("ticker"),
                pl.col(symbol_column).cast(pl.String).alias("symbol"),
            ]
            expressions.extend(
                [
                    pl.col(name).cast(pl.String) if name in source.columns else pl.lit(None, dtype=pl.String).alias(name)
                    for name in ("mic", "exchange")
                ]
            )
            expressions.extend([pl.col(name) for name in ("date", "open", "high", "low", "close", "volume")])
            frames.append(source.select(expressions))
        if not frames:
            return pl.DataFrame(
                schema={
                    "market": pl.String,
                    "ticker": pl.String,
                    "symbol": pl.String,
                    "mic": pl.String,
                    "exchange": pl.String,
                    "date": pl.Date,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Float64,
                }
            )
        return pl.concat(frames, how="diagonal_relaxed")

    def generate_features(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        compute_target: bool = True,
        include_sentiment: bool = False,
        include_social_sentiment: bool = False,
        include_macro: bool = True,
        include_enriched_sentiment: bool = False,
        include_analyst_ratings: bool = False,
        include_sec_features: bool = False,
        normalize_cross_sectional: bool = False,
        diagnostics: dict[str, Any] | None = None,
        instruments: Sequence[InstrumentKey] | None = None,
        action_inputs: Mapping[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]] | None = None,
    ) -> pl.DataFrame:
        """Generate all features for specified tickers and date range.

        Two-phase execution:
        1. Per-ticker technical indicators via Hamilton DAG (``compute_technical``)
        2. Batch external-data enrichments via Hamilton DAG (``compute_enriched``)

        Enrichments (sentiment, social, analyst, SEC, macro) are handled
        automatically by :meth:`FeaturePipeline.compute_enriched` based on the
        ``include_*`` flags. The legacy ``merge_*`` methods on this class are
        deprecated and should not be called directly.

        Args:
            diagnostics: Optional out-dict filled with per-ticker exclusion
                reasons under ``"phase1_skipped"`` (stable codes:
                ``no_price_rows``, ``insufficient_history``,
                ``invalid_output``). W02 (F07): exclusions are observable at
                the caller instead of disappearing into a narrower frame.
        """
        logger.info("feature_generation_start", ticker_count=len(tickers), start_date=str(start_date), end_date=str(end_date))

        logger.debug("Loading identity-scoped source rows for %d tickers", len(tickers))
        df = self._load_price_rows(tickers, start_date, end_date)

        if df.is_empty():
            logger.warning("no_data_for_tickers", tickers=tickers)
            if diagnostics is not None:
                diagnostics["phase1_skipped"] = (
                    {instrument.display_key: "no_price_rows" for instrument in instruments}
                    if instruments is not None
                    else {ticker: "no_price_rows" for ticker in tickers}
                )
            return df

        logger.info("ohlcv_data_loaded", row_count=df.height)

        if "date" in df.columns and df.schema["date"] != pl.Date:
            df = df.with_columns(pl.col("date").cast(pl.Date))

        requested_keys = {instrument.tuple for instrument in instruments or ()}
        requested_markets = {instrument.market for instrument in instruments or ()}
        identified_rows: list[dict[str, Any]] = []
        for row in df.to_dicts():
            # A source partition is market-scoped. Do not inspect legacy rows
            # from unrelated markets when an identity-scoped request is made.
            if requested_markets and row.get("market") not in requested_markets:
                continue
            try:
                instrument = instrument_from_record(row)
            except (InstrumentIdentityError, LegacyIdentityUnavailableError) as exc:
                raise LegacyIdentityUnavailableError(f"Cannot compute shared features for legacy row {row.get('ticker')!r}: {exc}") from exc
            if instruments is not None and instrument.tuple not in requested_keys:
                continue
            row.update(instrument.as_columns())
            identified_rows.append(row)

        if instruments is None:
            symbol_keys: dict[str, set[tuple[str, str, str]]] = {}
            for row in identified_rows:
                key = instrument_from_record(row)
                symbol_keys.setdefault(key.symbol, set()).add(key.tuple)
            ambiguous = {symbol: keys for symbol, keys in symbol_keys.items() if len(keys) > 1 and symbol in tickers}
            if ambiguous:
                details = "; ".join(f"{symbol}: {sorted(keys)}" for symbol, keys in sorted(ambiguous.items()))
                raise InstrumentIdentityError(f"Bare feature ticker scope is ambiguous; pass InstrumentKey values ({details})")
            requested_keys = {key for symbol in tickers for key in symbol_keys.get(symbol, set())}
            identified_rows = [row for row in identified_rows if instrument_from_record(row).tuple in requested_keys]
        elif not requested_keys:
            identified_rows = []

        if not identified_rows:
            if diagnostics is not None:
                diagnostics["phase1_skipped"] = (
                    {instrument.display_key: "no_price_rows" for instrument in instruments or ()}
                    if instruments is not None
                    else {ticker: "no_price_rows" for ticker in tickers}
                )
            return pl.DataFrame()
        df = pl.DataFrame(identified_rows, infer_schema_length=None).with_columns(pl.col("date").cast(pl.Date))

        # Phase 1: compute each canonical instrument independently. The DAG
        # with a stable reason code (W02/F07); a per-ticker compute failure is
        # isolated to that ticker (the stage-level coverage policy decides the
        # run outcome) instead of erasing every other ticker's output.
        result_dfs: list[pl.DataFrame] = []
        phase1_skipped: dict[str, str] = {}
        available_symbols = set(df["symbol"].drop_nulls().unique().to_list())
        for ticker in tickers:
            if ticker not in available_symbols:
                phase1_skipped[ticker] = "no_price_rows"
        instrument_rows = df.select(*IDENTITY_COLUMNS).unique().sort(list(IDENTITY_COLUMNS)).to_dicts()
        symbol_counts = df.group_by("symbol").agg(pl.struct(*IDENTITY_COLUMNS).n_unique().alias("identity_count"))
        ambiguous_symbols = set(symbol_counts.filter(pl.col("identity_count") > 1)["symbol"].to_list())
        if instruments is not None:
            requested_symbol_counts: dict[str, int] = {}
            for instrument in instruments:
                requested_symbol_counts[instrument.symbol] = requested_symbol_counts.get(instrument.symbol, 0) + 1
            ambiguous_symbols.update(symbol for symbol, count in requested_symbol_counts.items() if count > 1)
            available_key_set = {tuple(row) for row in instrument_rows}
            for instrument in instruments:
                if instrument.tuple not in available_key_set:
                    label = instrument.display_key if instrument.symbol in ambiguous_symbols else instrument.symbol
                    phase1_skipped[label] = "no_price_rows"
        for identity_row in tqdm(instrument_rows, desc="Computing features"):
            instrument = InstrumentKey(**identity_row)
            label = instrument.display_key if instrument.symbol in ambiguous_symbols else instrument.symbol
            if requested_keys and instrument.tuple not in requested_keys:
                continue
            ticker_df = (
                df.filter((pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol))
                .select("market", "mic", "symbol", "ticker", "date", "open", "high", "low", "close", "volume")
                .sort("date")
            )
            if ticker_df.is_empty():
                phase1_skipped[label] = "no_price_rows"
                continue
            if ticker_df.height < 60:
                logger.warning("instrument_insufficient_history", instrument=instrument.display_key, height=ticker_df.height)
                phase1_skipped[label] = "insufficient_history"
                continue

            try:
                if action_inputs is not None and instrument.tuple in action_inputs:
                    actions, coverage = action_inputs[instrument.tuple]
                else:
                    from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs

                    actions, coverage = load_corporate_action_inputs(instrument.market)
                computed = self._compute_point_in_time_technical(
                    ticker_df,
                    instrument,
                    actions,
                    coverage,
                    compute_target=compute_target,
                )
            except AvailabilityContractError as exc:
                logger.error("instrument_action_availability_unavailable", instrument=instrument.display_key, error=str(exc))
                phase1_skipped[label] = "action_coverage_unavailable"
                continue
            except Exception as exc:
                logger.error("instrument_compute_failed", instrument=instrument.display_key, error=str(exc))
                phase1_skipped[label] = "invalid_output"
                continue
            result_dfs.append(computed)

        if diagnostics is not None:
            diagnostics["phase1_skipped"] = dict(phase1_skipped)

        if not result_dfs:
            logger.error("No features generated - all tickers were skipped (insufficient data)")
            return pl.DataFrame()

        features_df = pl.concat(result_dfs, how="vertical_relaxed")
        critical_cols = ["close", "volume", "rsi_14", "macd"]
        features_df = features_df.filter(~pl.any_horizontal([pl.col(column).is_null() for column in critical_cols if column in features_df.columns]))

        # Tick whose surviving rows were all dropped by the critical-null
        # filter never reach the output: record them as invalid output.
        if diagnostics is not None and "symbol" in features_df.columns:
            surviving = set(features_df.select(*IDENTITY_COLUMNS).unique().iter_rows())
            skipped_identities = {
                (instrument.market, instrument.mic, instrument.symbol)
                for instrument in (InstrumentKey(**identity_row) for identity_row in instrument_rows)
                if (instrument.display_key if instrument.symbol in ambiguous_symbols else instrument.symbol) in phase1_skipped
            }
            for identity_row in instrument_rows:
                identity = (identity_row["market"], identity_row["mic"], identity_row["symbol"])
                label = InstrumentKey(**identity_row).display_key if identity_row["symbol"] in ambiguous_symbols else identity_row["symbol"]
                if identity not in surviving and identity not in skipped_identities:
                    diagnostics["phase1_skipped"][label] = "invalid_output"

        # Phase 2: batch external-data enrichments via DAG
        any_enrichment = any(
            [
                include_sentiment,
                include_social_sentiment,
                include_macro,
                include_enriched_sentiment,
                include_analyst_ratings,
                include_sec_features,
            ]
        )
        # Enrichment readers require composite source identity and trusted
        # availability clocks. Legacy/unproven source rows become explicit
        # unavailable context in the DAG; they are never ticker-joined or
        # represented as neutral zeros.
        if any_enrichment:
            enriched_parts: list[pl.DataFrame] = []
            for identity_row in features_df.select(*IDENTITY_COLUMNS).unique().to_dicts():
                instrument = InstrumentKey(**identity_row)
                single_instrument = features_df.filter(
                    (pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol)
                )
                enriched_parts.append(
                    self.feature_pipeline.compute_enriched(
                        single_instrument,
                        duckdb_conn=self.conn,
                        start_date=start_date,
                        end_date=end_date,
                        enable_news_sentiment=include_sentiment,
                        enable_social_sentiment=include_social_sentiment,
                        enable_enriched_sentiment=include_enriched_sentiment,
                        enable_analyst_ratings=include_analyst_ratings,
                        enable_sec_features=include_sec_features,
                        enable_macro=include_macro,
                    )
                )
            features_df = pl.concat(enriched_parts, how="vertical_relaxed")

        if normalize_cross_sectional:
            features_df = self.zscore_cross_sectional(features_df)

        logger.info("feature_generation_complete", row_count=features_df.height, column_count=len(features_df.columns))
        logger.debug("feature_columns", columns=list(features_df.columns))
        return features_df

    def _compute_point_in_time_technical(
        self,
        prices: pl.DataFrame,
        instrument: InstrumentKey,
        actions: pl.DataFrame,
        coverage: pl.DataFrame,
        *,
        compute_target: bool,
    ) -> pl.DataFrame:
        """Compute each output row using only split/action data known by that session."""
        from equity_lake.storage.lake_reader import with_price_adjustment

        dates = prices["date"].to_list()
        # Keep source proofs unscoped for ticker-only action identity resolution;
        # price-scope selection happens only after the event identity is known.
        source_proofs = [ActionCoverageProof.model_validate(row) for row in coverage.to_dicts()]
        instrument_proofs = [proof for proof in source_proofs if proof.identity == instrument.tuple]
        result_rows: list[pl.DataFrame] = []
        for index, anchor in enumerate(dates):
            history = prices.filter(pl.col("date") <= pl.lit(anchor))
            adjusted_history = with_price_adjustment(history, actions, coverage=source_proofs, as_of=anchor)
            visible_proofs = [
                proof
                for proof in instrument_proofs
                if proof.coverage_start <= min(dates)
                and proof.coverage_end >= anchor
                and source_observable_session(instrument, proof.source_available_at) <= anchor
            ]
            if len(visible_proofs) != 1:
                raise AvailabilityContractError("point-in-time action coverage proof is ambiguous")
            proof = visible_proofs[0]
            source_session = validate_action_coverage_for_decision(proof, instrument, anchor)
            technical = self.feature_pipeline.compute_technical(
                adjusted_history.select("ticker", "date", "open", "high", "low", "close", "volume"),
                include_target=False,
            ).tail(1)
            values: dict[str, object] = {
                "market": instrument.market,
                "mic": instrument.mic,
                "symbol": instrument.symbol,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
                "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
                "action_coverage": 1,
                "action_source_age_days": (anchor - source_session).days,
                **action_coverage_feature_fields(proof),
            }
            if compute_target:
                next_return: float | None = None
                label_available_date: date | None = None
                if index + 1 < len(dates):
                    next_day = dates[index + 1]
                    pair = prices.filter(pl.col("date").is_in([anchor, next_day]))
                    adjusted_pair = with_price_adjustment(pair, actions, coverage=source_proofs, as_of=next_day).sort("date")
                    if adjusted_pair.height == 2:
                        previous_close, next_close = adjusted_pair["close"].to_list()
                        if previous_close is not None and next_close is not None and previous_close != 0:
                            next_return = float(next_close / previous_close - 1.0)
                            label_available_date = next_day
                values["next_day_return"] = next_return
                values["label_available_date"] = label_available_date
            metadata_dtypes: dict[str, Any] = {
                "identity_schema_version": pl.Int8,
                "feature_schema_version": pl.Int8,
                "price_adjustment_version": pl.String,
                "availability_schema_version": pl.Int8,
                "action_coverage": pl.Int8,
                "action_source_age_days": pl.Int32,
                "action_coverage_market": pl.String,
                "action_coverage_mic": pl.String,
                "action_coverage_symbol": pl.String,
                "action_coverage_source": pl.String,
                "action_coverage_start": pl.Date,
                "action_coverage_end": pl.Date,
                "action_source_available_at": pl.Datetime("us", "UTC"),
                "action_source_valid_until": pl.Datetime("us", "UTC"),
                "action_coverage_status": pl.String,
                "action_coverage_schema_version": pl.Int8,
            }
            result_rows.append(technical.with_columns([pl.lit(value, dtype=metadata_dtypes.get(name)).alias(name) for name, value in values.items()]))
        return pl.concat(result_rows, how="vertical_relaxed") if result_rows else pl.DataFrame()

    def zscore_cross_sectional(
        self,
        features_df: FrameLike,
        *,
        columns: list[str] | None = None,
        suffix: str = "_zscore",
        eps: float = 1e-8,
    ) -> pl.DataFrame:
        """Z-score numeric features cross-sectionally across tickers per date.

        For each date, compute (x - mean) / (std + eps) over all tickers.
        Per-date mean/std are computed on **non-null values only** (nulls never
        bias the cross-sectional statistics); null inputs remain null in the
        result so unavailable predictors cannot masquerade as neutral values.
        Adds new columns with ``suffix`` appended;
        original columns are kept unchanged. Skips columns that are non-numeric
        or absent from the frame.
        """
        frame = ensure_polars(features_df)
        if frame.is_empty():
            return frame

        skip = {
            "ticker",
            "symbol",
            "market",
            "mic",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "feature_schema_version",
            "identity_schema_version",
            "availability_schema_version",
            "action_coverage",
            "action_source_age_days",
            *FEATURE_ACTION_COVERAGE_PROOF_COLUMNS,
            "price_adjustment_version",
            "label_available_date",
            "candidate_action",
            "candidate_source",
        }
        if columns is None:
            columns = [col for col in frame.columns if col not in skip and col not in NON_NUMERIC_FOR_ZSCORE and frame.schema[col] in NUMERIC_DTYPES]

        expressions: list[pl.Expr] = []
        for col_name in columns:
            if col_name not in frame.columns:
                continue
            # Aggregations skip nulls, while the row expression intentionally
            # preserves them as unavailable instead of turning them into zero.
            mean_expr = pl.col(col_name).mean().over("date")
            std_expr = pl.col(col_name).std().over("date")
            z_expr = ((pl.col(col_name) - mean_expr) / (std_expr + eps)).alias(f"{col_name}{suffix}")
            expressions.append(z_expr)

        if not expressions:
            return frame
        return frame.with_columns(expressions)

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
