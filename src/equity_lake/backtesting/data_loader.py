from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, Self

import polars as pl
import structlog

from equity_lake.core.availability import AvailabilityContractError  # pyright: ignore[reportMissingImports] -- source import verified by uv
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_COLUMNS,
    InstrumentIdentityError,
    InstrumentKey,
    normalize_identity_frame,
)
from equity_lake.core.paths import PRICE_MARKETS, market_dir
from equity_lake.storage.lake_reader import with_price_adjustment

logger = structlog.get_logger(__name__)

_PIT_RAW_COLUMNS = {column: f"__pit_raw_{column}" for column in ("open", "high", "low", "close", "volume")}
_PIT_ACTION_CONTEXT = "__pit_action_context"


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__} in point-in-time action context")


AdjustmentMode = Literal["none", "splits", "total_return"]
_ADJUST_METHODS: dict[str, Literal["split_only", "total_return"]] = {
    # ``none`` remains accepted for old callers but is no longer an unadjusted
    # escape hatch: every default backtest uses the shared split-only contract.
    "none": "split_only",
    "splits": "split_only",
    "total_return": "total_return",
}


class BacktestDataLoader:
    """Load local price bars with canonical instrument identity (ADR-0016)."""

    def __init__(
        self,
        db_path: str = ":memory:",
        cache_enabled: bool = True,
        action_inputs: Mapping[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]] | None = None,
    ):
        # Kept as compatibility settings for callers; local scans need no SQL
        # connection and never interpolate lake paths or ticker values into SQL.
        self.db_path = db_path
        self.cache_enabled = cache_enabled
        self.action_inputs = action_inputs
        self._action_input_cache: dict[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]] = {}
        logger.info("BacktestDataLoader initialized", cache_enabled=cache_enabled)

    def load(
        self,
        tickers: Sequence[str | InstrumentKey],
        start_date: date,
        end_date: date,
        markets: list[str] | None = None,
        columns: list[str] | None = None,
        adjust: AdjustmentMode = "splits",
        trusted_legacy_provenance: dict[str, list[InstrumentKey]] | None = None,
    ) -> pl.DataFrame:
        """Load identity-scoped OHLCV bars, forward-filling within each instrument.

        Bare symbols are accepted only when stored or explicitly trusted
        historical provenance resolves them to one canonical identity. Current
        configuration and ticker spelling are never historical provenance.
        """
        selected_markets = list(PRICE_MARKETS) if markets is None else markets
        requested_columns = columns or ["ticker", "date", "open", "high", "low", "close", "volume"]
        symbols = [ticker if isinstance(ticker, str) else ticker.symbol for ticker in tickers]
        logger.info(
            "Loading backtest data",
            tickers=len(symbols),
            start_date=str(start_date),
            end_date=str(end_date),
            markets=selected_markets,
        )

        frames = [
            self._read_market(market, symbols, start_date, end_date, requested_columns) for market in selected_markets if market in PRICE_MARKETS
        ]
        frames = [frame for frame in frames if not frame.is_empty()]
        if not frames:
            logger.warning("No data found for query", tickers=symbols)
            return pl.DataFrame()

        raw = pl.concat(frames, how="diagonal_relaxed")
        data = normalize_identity_frame(raw, trusted_legacy_provenance=trusted_legacy_provenance, deduplicate=True)
        requested_keys: set[tuple[str, str, str]] = set()
        for ticker in tickers:
            if isinstance(ticker, str):
                matches = data.filter(pl.col("symbol") == ticker).select(*IDENTITY_COLUMNS).unique()
                if matches.height > 1:
                    raise InstrumentIdentityError(f"Bare symbol {ticker!r} resolves to multiple backtest instruments; pass InstrumentKey")
                if matches.height == 1:
                    requested_keys.add(tuple(matches.row(0)))
            else:
                requested_keys.add(ticker.tuple)

        if requested_keys:
            filters = [(pl.col("market") == market) & (pl.col("mic") == mic) & (pl.col("symbol") == symbol) for market, mic, symbol in requested_keys]
            data = data.filter(pl.any_horizontal(filters))
        else:
            data = data.head(0)

        output_columns = list(dict.fromkeys([*requested_columns, *IDENTITY_COLUMNS, "ticker", "date"]))
        data = data.select([column for column in output_columns if column in data.columns])
        data = self._clean_data(data)
        data = self._attach_point_in_time_context(data, selected_markets)
        data = self._point_in_time_marks(data, selected_markets, adjust)

        logger.debug("Returned long format", shape=data.shape, tickers=data["ticker"].n_unique() if "ticker" in data.columns else 0)
        return data

    @staticmethod
    def _read_market(market: str, symbols: list[str], start_date: date, end_date: date, columns: list[str]) -> pl.DataFrame:
        """Read a bounded market/date scope, retaining explicit stored identity."""
        data_dir = market_dir(market)
        if not data_dir.exists():
            logger.warning("Market data directory not found", market=market, path=str(data_dir), hint="Run equity ingest command first")
            return pl.DataFrame()

        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(data_dir)):
            table = DeltaTable(str(data_dir))
            available = {field.name for field in table.schema().fields}
            if "date" not in available:
                return pl.DataFrame()
            filters: list[tuple[str, str, object]] = [("date", ">=", start_date), ("date", "<=", end_date)]
            raw = pl.DataFrame(table.to_pyarrow_table(filters=filters))
        else:
            raw = pl.scan_parquet(str(data_dir / "**" / "*.parquet"), hive_partitioning=True).collect()
            if "date" not in raw.columns:
                return pl.DataFrame()
            raw = raw.filter(pl.col("date").cast(pl.Date).is_between(start_date, end_date, closed="both"))

        ticker_column = "ticker" if "ticker" in raw.columns else "symbol" if "symbol" in raw.columns else None
        if ticker_column is None:
            return pl.DataFrame()
        raw = raw.filter(pl.col(ticker_column).cast(pl.String).is_in(symbols))
        if raw.is_empty():
            return raw
        if "market" in raw.columns:
            mismatched_market = raw.filter(pl.col("market").cast(pl.String) != market)
            if mismatched_market.height:
                raise InstrumentIdentityError(f"Price rows under {market!r} contain conflicting market identity")
        else:
            raw = raw.with_columns(pl.lit(market).alias("market"))
        if "symbol" not in raw.columns:
            raw = raw.with_columns(pl.col(ticker_column).cast(pl.String).alias("symbol"))
        if "ticker" not in raw.columns:
            raw = raw.with_columns(pl.col("symbol").alias("ticker"))

        keep = list(dict.fromkeys([*columns, *IDENTITY_COLUMNS, "ticker", "date", "mic", "symbol", "identity_schema_version"]))
        return raw.select([column for column in keep if column in raw.columns])

    def _action_inputs_for(self, instrument: InstrumentKey) -> tuple[pl.DataFrame, pl.DataFrame]:
        cached = self._action_input_cache.get(instrument.tuple)
        if cached is not None:
            return cached
        if self.action_inputs is None:
            from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs

            result = load_corporate_action_inputs(instrument.market)
        else:
            try:
                result = self.action_inputs[instrument.tuple]
            except KeyError as exc:
                raise AvailabilityContractError(f"No action input fixture for {instrument.display_key}") from exc
        self._action_input_cache[instrument.tuple] = result
        return result

    def _scope_action_inputs(self, instrument: InstrumentKey) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Scope market-wide action tables to one identity without guessing collisions."""
        actions, coverage = self._action_inputs_for(instrument)
        if set(IDENTITY_COLUMNS).issubset(coverage.columns):
            same_symbol = coverage.filter(pl.col("symbol") == instrument.symbol).select(*IDENTITY_COLUMNS).unique()
            if "ticker" in actions.columns:
                symbol_events = actions.filter(pl.col("ticker").cast(pl.String) == instrument.symbol)
            elif "symbol" in actions.columns:
                symbol_events = actions.filter(pl.col("symbol").cast(pl.String) == instrument.symbol)
            else:
                symbol_events = actions
            if same_symbol.height > 1 and not symbol_events.is_empty() and not set(IDENTITY_COLUMNS).issubset(actions.columns):
                raise AvailabilityContractError(f"Ticker-only corporate actions are ambiguous for {instrument.symbol!r}")
            coverage = coverage.filter(
                (pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol)
            )
        if set(IDENTITY_COLUMNS).issubset(actions.columns):
            actions = actions.filter(
                (pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol)
            )
        elif "ticker" in actions.columns:
            actions = actions.filter(pl.col("ticker").cast(pl.String) == instrument.symbol)
        elif "symbol" in actions.columns:
            actions = actions.filter(pl.col("symbol").cast(pl.String) == instrument.symbol)
        return actions, coverage

    def _attach_point_in_time_context(self, data: pl.DataFrame, markets: list[str]) -> pl.DataFrame:
        """Carry raw bars and source action inputs through the arena preload seam.

        The private handoff columns let the engine recompute every historical
        decision from raw bars and only source facts observable at that session;
        the displayed OHLC remains a compatible end-of-window adjustment view.
        """
        parts: list[pl.DataFrame] = []
        for identity_row in data.select(*IDENTITY_COLUMNS).unique().to_dicts():
            instrument = InstrumentKey(**identity_row)
            if instrument.market not in markets:
                raise InstrumentIdentityError(f"Backtest identity {instrument.display_key} is outside the selected market scope")
            subset = data.filter(
                (pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol)
            )
            actions, coverage = self._scope_action_inputs(instrument)
            payload = json.dumps({"actions": actions.to_dicts(), "coverage": coverage.to_dicts()}, default=_json_default, sort_keys=True)
            copies = [pl.col(column).alias(hidden) for column, hidden in _PIT_RAW_COLUMNS.items() if column in subset.columns]
            parts.append(subset.with_columns([*copies, pl.lit(payload).alias(_PIT_ACTION_CONTEXT)]))
        return pl.concat(parts, how="vertical_relaxed") if parts else data.head(0)

    def _point_in_time_marks(self, data: pl.DataFrame, markets: list[str], adjust: AdjustmentMode) -> pl.DataFrame:
        """Return split-neutral marks calculated from a separate as-of snapshot per session.

        A missing/expired proof masks that session's public marks and sets an
        explicit availability flag. The raw bars and source inputs stay in
        private handoff columns for the engine to rebuild each rolling strategy
        window; no end-of-window adjustment is applied to earlier decisions.
        """
        if not {"ticker", "date", "close", *IDENTITY_COLUMNS}.issubset(data.columns):
            raise InstrumentIdentityError("Adjusted backtest input is missing canonical identity/date/close columns")
        method: Literal["split_only", "total_return"] = _ADJUST_METHODS[adjust]
        mark_parts: list[pl.DataFrame] = []
        excluded = 0

        for identity_row in data.select(*IDENTITY_COLUMNS).unique().to_dicts():
            instrument = InstrumentKey(**identity_row)
            if instrument.market not in markets:
                raise InstrumentIdentityError(f"Backtest identity {instrument.display_key} is outside the selected market scope")
            subset = data.filter(
                (pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol)
            ).sort("date")
            actions, coverage = self._scope_action_inputs(instrument)
            dates = subset["date"].to_list()
            if not dates:
                continue

            marks: dict[str, list[object]] = {column: [] for column in ("open", "high", "low", "close", "volume") if column in subset.columns}
            versions: list[str | None] = []
            schema_versions: list[int | None] = []
            coverage_flags: list[int] = []
            source_ages: list[int | None] = []
            available_flags: list[int] = []
            last_valid_date: date | None = None
            last_index_close: float | None = None

            for decision_date in dates:
                history = subset.filter(pl.col("date") <= decision_date)
                adjusted = pl.DataFrame()
                try:
                    if actions.is_empty():
                        proof_rows = history.head(1) if history.height == 1 else pl.concat([history.head(1), history.tail(1)], how="vertical_relaxed")
                        verified = with_price_adjustment(proof_rows, actions, coverage=coverage, method=method, as_of=decision_date)
                        metadata_row = verified.row(-1, named=True)
                        current = subset.filter(pl.col("date") == decision_date)
                    else:
                        adjusted = with_price_adjustment(history, actions, coverage=coverage, method=method, as_of=decision_date)
                        current = adjusted.filter(pl.col("date") == decision_date)
                        metadata_row = None
                except AvailabilityContractError as exc:
                    excluded += 1
                    logger.warning(
                        "backtest_price_session_unavailable",
                        identity=instrument.tuple,
                        decision_date=str(decision_date),
                        reason=str(exc),
                    )
                    for values in marks.values():
                        values.append(None)
                    versions.append(None)
                    schema_versions.append(None)
                    coverage_flags.append(0)
                    source_ages.append(None)
                    available_flags.append(0)
                    continue

                if current.is_empty():
                    raise AvailabilityContractError(f"No adjusted price row for {instrument.display_key} on {decision_date}")
                current_row = current.row(0, named=True)
                if metadata_row is None:
                    metadata_row = current_row
                current_close = float(current_row["close"])
                if current_close <= 0:
                    raise AvailabilityContractError(f"Non-positive adjusted close for {instrument.display_key} on {decision_date}")

                if last_valid_date is None or last_index_close is None:
                    index_close = current_close
                else:
                    previous = (
                        subset.filter(pl.col("date") == last_valid_date) if actions.is_empty() else adjusted.filter(pl.col("date") == last_valid_date)
                    )
                    if previous.is_empty():
                        raise AvailabilityContractError(f"No as-of price for prior valid session {last_valid_date} in {instrument.display_key}")
                    previous_close = float(previous["close"][0])
                    if previous_close <= 0:
                        raise AvailabilityContractError(f"Non-positive prior adjusted close for {instrument.display_key} on {last_valid_date}")
                    index_close = last_index_close * current_close / previous_close

                multiplier = index_close / current_close
                for column in marks:
                    value = current_row.get(column)
                    marks[column].append(float(value) * multiplier if value is not None and column in {"open", "high", "low", "close"} else value)
                versions.append(str(metadata_row["price_adjustment_version"]))
                schema_versions.append(int(metadata_row["availability_schema_version"]))
                coverage_flags.append(1)
                source_ages.append(int(metadata_row["action_source_age_days"]))
                available_flags.append(1)
                last_valid_date = decision_date
                last_index_close = index_close

            expressions = [pl.Series(column, values, dtype=pl.Float64) for column, values in marks.items()]
            expressions.extend(
                [
                    pl.Series("price_adjustment_version", versions, dtype=pl.String),
                    pl.Series("availability_schema_version", schema_versions, dtype=pl.Int8),
                    pl.Series("action_coverage", coverage_flags, dtype=pl.Int8),
                    pl.Series("action_source_age_days", source_ages, dtype=pl.Int32),
                    pl.Series("point_in_time_available", available_flags, dtype=pl.Int8),
                ]
            )
            mark_parts.append(subset.with_columns(expressions))

        if excluded:
            logger.warning("backtest_price_sessions_excluded", count=excluded, reason="source action proof unavailable or expired")
        return pl.concat(mark_parts, how="vertical_relaxed") if mark_parts else data.head(0)

    @staticmethod
    def _clean_data(data: pl.DataFrame) -> pl.DataFrame:
        """Dedupe and forward-fill prices independently within each identity."""
        if "date" in data.columns:
            data = data.with_columns(pl.col("date").cast(pl.Date))
        identity_date = [*IDENTITY_COLUMNS, "date"]
        data = data.unique(subset=identity_date, keep="last").sort(identity_date)
        price_cols = [column for column in ["open", "high", "low", "close", "volume"] if column in data.columns]
        if price_cols:
            data = data.with_columns([pl.col(column).forward_fill().over(list(IDENTITY_COLUMNS)) for column in price_cols])
        price_cols = [column for column in ["open", "high", "low", "close"] if column in data.columns]
        if price_cols:
            data = data.filter(~pl.all_horizontal([pl.col(column).is_null() for column in price_cols]))
        return data

    def close(self) -> None:
        """Compatibility no-op; scans own no persistent connection."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


__all__ = ["BacktestDataLoader"]
