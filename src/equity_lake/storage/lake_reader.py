from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import polars as pl
import structlog

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    AVAILABILITY_SCHEMA_VERSION,
    COVERAGE_BASES,
    PRICE_ADJUSTMENT_VERSION,
    ActionCoverageProof,
    AvailabilityContractError,
    CoverageBasis,
    CoverageProof,
    VendorEventCoverageProof,
    parse_coverage_proof,
    source_observable_session,
    validate_action_coverage_for_decision,
)
from equity_lake.core.instruments import IDENTITY_COLUMNS, InstrumentKey, normalize_identity_frame
from equity_lake.core.schemas import SYNTHETIC_COVERAGE_SOURCE

logger = structlog.get_logger(__name__)

AdjustmentMethod = Literal["split_only", "total_return"]

_OHLC_COLUMNS = ("open", "high", "low", "close")
_METHODS: tuple[str, ...] = ("split_only", "total_return")


def ensure_delta_extension(con: Any) -> None:
    """Idempotently install+load the DuckDB delta extension on *con*.

    Single home for the ``INSTALL delta; LOAD delta;`` bootstrap previously
    hand-rolled at ~10 call sites (storage, ingestion, monitoring, backtesting,
    ml/features). Cheap when already loaded; safe to call per-connection.
    """
    con.execute("INSTALL delta; LOAD delta;")


def create_market_views(
    con: Any,
    *,
    view_prefix: str = "",
    columns: list[str] | None = None,
) -> list[str]:
    """Create one per-market view over the price-market registry (ADR-0010).

    Single home for the per-market ``CREATE OR REPLACE VIEW`` loop previously
    duplicated in ``storage/duckdb.py`` (unified query views, ``view_prefix=""``,
    all columns via ``SELECT *``) and ``backtesting/data_loader.py`` (backtest-
    prefixed OHLCV views, ``view_prefix="backtest_"``, projected to the OHLCV
    columns). ``columns=None`` selects every column; otherwise the view projects
    exactly ``columns``. Returns the names of the views actually created.
    """
    from equity_lake.core.paths import PRICE_MARKETS, market_dir

    ensure_delta_extension(con)
    created: list[str] = []
    for market in PRICE_MARKETS:
        data_dir = market_dir(market)
        if not data_dir.exists():
            logger.warning("Data directory not found, skipping view", market=market, path=str(data_dir))
            continue
        view_name = f"{view_prefix}{market}"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", view_name):
            raise ValueError(f"Invalid market view identifier: {view_name!r}")
        if columns is not None and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) for column in columns):
            raise ValueError("Market view columns must be plain identifiers")
        try:
            from deltalake import DeltaTable

            if DeltaTable.is_deltatable(str(data_dir)):
                relation = con.from_query("SELECT * FROM delta_scan(?)", params=[str(data_dir)])
            else:
                relation = con.read_parquet(str(data_dir / "**" / "*.parquet"), hive_partitioning=1)
            projection = "*" if columns is None else ", ".join(f'"{column}"' for column in columns)
            market_literal = market.replace("'", "''")
            relation.project(f"{projection}, '{market_literal}' AS market").create_view(view_name, replace=True)
            created.append(view_name)
            logger.debug("Created market view", view=view_name)
        except Exception as e:
            logger.error("Failed to create view", view=view_name, error=str(e))
    return created


def duckdb_scan_for(market_path: Path) -> str:
    """Return a DuckDB scan expression for a Delta table or a hive Parquet tree.

    The path is emitted as a SQL string literal, so it is escaped (single quotes
    doubled) here — the one place every caller builds that literal. Callers must
    still bind row values as ``?`` parameters; only the scan target is inlined,
    because DuckDB table functions cannot take a bound identifier.
    """
    # SQL string-literal escaping (DuckDB/SQL standard): a quote in a path must not
    # be able to close the literal and inject SQL into the caller's statement.
    literal = str(market_path).replace("'", "''")
    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(market_path)):
            return f"delta_scan('{literal}')"
    except (ImportError, OSError, ValueError):
        pass
    return f"read_parquet('{literal}/**/*.parquet', hive_partitioning=1)"


def _dividend_factor_steps(prices: pl.DataFrame, dividends: pl.DataFrame) -> pl.DataFrame:
    """Factor step per dividend event: ``1 - value / prev_close`` (CRSP-style).

    ``prev_close`` is the last close strictly before the ex-date (the ex-date's
    own close is post-event and must never feed the step). Events without a
    prior close are dropped with a warning rather than guessed.
    """
    prev_close = prices.select(pl.col("ticker"), pl.col("date"), pl.col("close")).sort("date")
    steps = (
        dividends.with_columns((pl.col("ex_date") - pl.duration(days=1)).alias("_asof_key"))
        .sort("_asof_key")
        .join_asof(
            prev_close.rename({"date": "_prev_date"}).sort("_prev_date"),
            left_on="_asof_key",
            right_on="_prev_date",
            by="ticker",
            strategy="backward",
            check_sortedness=False,
        )
        .drop_nulls("close")
        .filter(pl.col("close") > 0)
        .select(pl.col("ticker"), pl.col("ex_date"), (1.0 - pl.col("value") / pl.col("close")).alias("factor_step"))
    )
    dropped = dividends.height - steps.height
    if dropped:
        logger.warning("dividend_events_dropped_no_prior_close", count=dropped)
    return steps


def _factor_on_expr() -> pl.Expr:
    """Cumulative split factor for the current canonical instrument."""
    return pl.col("factor_step").reverse().cum_prod().reverse().over("__adjust_key").fill_null(1.0).alias("factor_on")


def _refuse_mixed_roots(prices: pl.DataFrame, proofs: Sequence[CoverageProof]) -> None:
    """Refuse pairing synthetic demo rows with vendor evidence in one read.

    ADR-0032 decision 4 keeps the D00/ADR-0025 failure mode closed: the canonical
    root's ``synthetic_demo_seed`` proof and a real root's vendor proof are never
    interchangeable. Classification comes from the row-bound provenance envelope
    (ADR-0027), never from a path or a ticker; a frame without the envelope is left
    to the basis checks alone.
    """
    if "price_provenance" not in prices.columns:
        return
    provenance = set(prices["price_provenance"].drop_nulls().unique().to_list())
    if not provenance:
        return
    synthetic = provenance == {"synthetic"}
    if "synthetic" in provenance and not synthetic:
        raise AvailabilityContractError("price rows mix synthetic and non-synthetic provenance in one read")
    proof_is_demo = all(proof.source == SYNTHETIC_COVERAGE_SOURCE for proof in proofs)
    if synthetic != proof_is_demo:
        raise AvailabilityContractError("synthetic demo rows and vendor coverage proofs cannot be mixed in one read; one read admits one root")


def with_price_adjustment(
    prices: pl.DataFrame,
    actions: pl.DataFrame,
    *,
    coverage: pl.DataFrame | Sequence[ActionCoverageProof | VendorEventCoverageProof | Mapping[str, object]] | None = None,
    method: AdjustmentMethod = "split_only",
    as_of: date | None = None,
    coverage_basis: CoverageBasis = "source_clock",
) -> pl.DataFrame:
    """Apply the shared point-in-time split-only transform (ADR-0015, ADR-0032).

    Coverage is mandatory, including when ``actions`` is empty: only a
    source-scoped, complete proof can establish that an instrument had no
    actions. Prices remain immutable in Bronze. OHLC is multiplied by the
    cumulative split ratio; volume is adjusted by its reciprocal. Dividends
    and vendor ``adj_close`` are excluded. Event source clocks are mapped to
    the exact MIC session; ingestion time and ex-date are not availability.

    ``coverage_basis`` selects the admitted proof. The default ``source_clock`` is
    the strict, point-in-time basis and is unchanged. ``vendor_events_no_clock`` is
    an explicit opt-in (ADR-0032 decision 1) that admits the weaker vendor-event
    completeness proof: no clock is synthesized, output rows carry null clocks and
    ``action_coverage_status = "vendor_events_complete"``, and one read admits one
    basis. Consumers that need point-in-time availability must never opt in.

    Legacy ticker-only price/action frames, unknown clocks, incomplete or
    source-expired coverage, invalid/conflicting events, and unproven empty
    action data raise :class:`AvailabilityContractError` rather than returning
    raw prices. ``coverage_end`` bounds event dates; freshness is established
    only by the source-issued ``source_valid_until`` timestamp. On the weak basis
    freshness ends at the action store's watermark and is reported, not promised.
    """
    if coverage_basis not in COVERAGE_BASES:
        raise ValueError(f"Unknown coverage basis: {coverage_basis!r} (expected one of {COVERAGE_BASES})")
    if method not in _METHODS:
        raise ValueError(f"Unknown adjustment method: {method!r} (expected one of {_METHODS})")
    if method == "total_return":
        raise ValueError("ADR-0015 supports split-only adjustment; dividends are excluded")
    if prices.is_empty():
        return prices.clone()
    if "price_adjustment_version" in prices.columns:
        raise AvailabilityContractError("price frame is already stamped as adjusted; refusing a second adjustment")
    scale_cols = [column for column in _OHLC_COLUMNS if column in prices.columns]
    if not scale_cols:
        raise AvailabilityContractError("adjusted price frame has no OHLC columns")
    for column in ("date",):
        if column not in prices.columns:
            raise ValueError(f"prices frame missing required column: {column}")
    for column in ("ticker", "ex_date", "action", "value"):
        if column not in actions.columns:
            raise ValueError(f"actions frame missing required column: {column}")
    if coverage is None:
        raise AvailabilityContractError("corporate-action coverage is unknown; adjusted read is unavailable")

    try:
        identified = normalize_identity_frame(prices)
    except ValueError as exc:
        raise AvailabilityContractError(f"price identity is unknown: {exc}") from exc
    if identified["date"].null_count():
        raise AvailabilityContractError("price dates contain null values")
    if identified.select(pl.struct(*IDENTITY_COLUMNS, "date").is_duplicated().any()).item():
        raise AvailabilityContractError("duplicate canonical instrument/date price rows")
    identified = identified.with_columns(pl.col("date").cast(pl.Date))
    price_dates = identified["date"].to_list()
    decision_date = as_of or max(price_dates)
    if any(price_day > decision_date for price_day in price_dates):
        raise AvailabilityContractError("adjustment cutoff precedes a price row")

    raw_proofs: list[CoverageProof]
    try:
        if isinstance(coverage, pl.DataFrame):
            raw_proofs = [parse_coverage_proof(row) for row in coverage.to_dicts()]
        else:
            raw_proofs = [
                parse_coverage_proof(proof.model_dump())
                if isinstance(proof, (ActionCoverageProof, VendorEventCoverageProof))
                else parse_coverage_proof(proof)
                for proof in coverage
            ]
    except (TypeError, ValueError) as exc:
        raise AvailabilityContractError(f"corporate-action coverage proof is invalid or lacks source validity: {exc}") from exc
    if not raw_proofs:
        raise AvailabilityContractError("corporate-action coverage is empty and unverified")
    # One read admits one basis (ADR-0032 decision 4): a mixed frame refuses under
    # either requested basis, so neither side can silently reinterpret the other.
    mismatched = [proof for proof in raw_proofs if proof.coverage_basis != coverage_basis]
    if mismatched:
        found = sorted({proof.coverage_basis for proof in mismatched})
        raise AvailabilityContractError(
            f"coverage proof basis {found} does not match the requested {coverage_basis!r} read; one read admits one basis"
        )
    # Exact duplicate source rows are one proof, not competing identity evidence.
    raw_proofs = list({proof.model_dump_json(): proof for proof in raw_proofs}.values())
    _refuse_mixed_roots(identified, raw_proofs)
    proofs: dict[tuple[str, str, str], CoverageProof] = {}
    for identity_row in identified.select(*IDENTITY_COLUMNS).unique().to_dicts():
        key = InstrumentKey(**identity_row)
        matches = [proof for proof in raw_proofs if proof.identity == key.tuple]
        instrument_dates = identified.filter((pl.col("market") == key.market) & (pl.col("mic") == key.mic) & (pl.col("symbol") == key.symbol))[
            "date"
        ].to_list()
        instrument_start = min(instrument_dates)
        covering = [proof for proof in matches if proof.coverage_start <= instrument_start and proof.coverage_end >= decision_date]
        observable: list[CoverageProof] = []
        for proof in covering:
            if coverage_basis == "source_clock":
                if not isinstance(proof, ActionCoverageProof):
                    continue
                try:
                    validate_action_coverage_for_decision(proof, key, decision_date)
                except AvailabilityContractError:
                    continue
            observable.append(proof)
        if len(observable) != 1:
            detail = (
                "no source coverage proof available and explicitly valid through cutoff"
                if not observable
                else "conflicting overlapping coverage proofs"
            )
            raise AvailabilityContractError(f"{key.display_key}: {detail} for {decision_date}")
        proofs[key.tuple] = observable[0]

    normalized_events: list[dict[str, object]] = []
    for row in actions.to_dicts():
        action = row.get("action")
        symbol = row.get("symbol")
        ticker = symbol if symbol is not None else row.get("ticker")
        if action not in {"split", "dividend"}:
            raise AvailabilityContractError(f"unknown corporate action type {action!r}")
        if not isinstance(ticker, str) or not ticker:
            raise AvailabilityContractError("corporate-action row has no symbol/ticker")
        if symbol is not None and row.get("ticker") is not None and symbol != row["ticker"]:
            raise AvailabilityContractError(f"corporate-action ticker alias differs from canonical symbol {symbol!r}")
        ex_date = row.get("ex_date")
        value = row.get("value")
        if not isinstance(ex_date, date) or not isinstance(value, (int, float)):
            raise AvailabilityContractError(f"invalid action date/value for {ticker}")
        import math

        if not math.isfinite(float(value)) or (action == "split" and float(value) <= 0) or (action == "dividend" and float(value) < 0):
            raise AvailabilityContractError(f"invalid {action} ratio/value for {ticker} on {ex_date}")
        if ex_date > decision_date:
            continue

        event_source = row.get("source")
        if event_source is not None and not isinstance(event_source, str):
            raise AvailabilityContractError(f"invalid corporate-action source for {ticker!r}")
        if any(row.get(column) is not None for column in IDENTITY_COLUMNS):
            if not all(isinstance(row.get(column), str) and row[column] for column in IDENTITY_COLUMNS):
                raise AvailabilityContractError(f"partial identity on corporate action row for {ticker!r}")
            event_key = InstrumentKey(market=str(row["market"]), mic=str(row["mic"]), symbol=str(row["symbol"]))
            candidate_keys = {event_key.tuple}
        else:
            # Resolve legacy ticker-only events from the full source-proof set,
            # never from proofs narrowed to the instruments in this price query.
            candidate_keys = {
                proof.identity
                for proof in raw_proofs
                if proof.symbol == ticker
                and proof.coverage_start <= ex_date <= proof.coverage_end
                and (event_source is None or proof.source == event_source.strip())
            }
        if len(candidate_keys) != 1:
            detail = "ambiguous" if candidate_keys else "unverified"
            raise AvailabilityContractError(f"corporate action identity is {detail} for {ticker!r} on {ex_date}")
        identity = next(iter(candidate_keys))
        key = InstrumentKey(market=identity[0], mic=identity[1], symbol=identity[2])
        selected_proof = proofs.get(key.tuple)
        event_proofs = [
            proof
            for proof in raw_proofs
            if proof.identity == key.tuple
            and proof.coverage_start <= ex_date <= proof.coverage_end
            and (event_source is None or proof.source == event_source.strip())
        ]
        if not event_proofs:
            if selected_proof is not None and event_source is not None and event_source.strip() != selected_proof.source:
                raise AvailabilityContractError(f"{key.display_key}: action source does not match its coverage proof on {ex_date}")
            raise AvailabilityContractError(f"{key.display_key}: no source coverage proof matches action on {ex_date}")

        # Every event is identity-resolved against full-source evidence before
        # filtering to requested price instruments; unrelated events then drop.
        if selected_proof is None:
            continue
        if not (selected_proof.coverage_start <= ex_date <= selected_proof.coverage_end):
            raise AvailabilityContractError(f"{key.display_key}: action date is outside the requested coverage window on {ex_date}")
        if event_source is not None and event_source.strip() != selected_proof.source:
            raise AvailabilityContractError(f"{key.display_key}: action source does not match its coverage proof on {ex_date}")
        source_time = row.get("source_available_at")
        if coverage_basis == "source_clock":
            if not isinstance(source_time, datetime) or source_time.tzinfo is None or source_time.utcoffset() is None:
                raise AvailabilityContractError(f"{key.display_key}: action source clock is unknown on {ex_date}")
            if source_observable_session(key, source_time) > decision_date:
                raise AvailabilityContractError(f"{key.display_key}: action was unavailable at cutoff on {ex_date}")
        else:
            # The basis names the absence: no clock is validated, and none is invented.
            source_time = None
        normalized_events.append(
            {
                "market": key.market,
                "mic": key.mic,
                "symbol": key.symbol,
                "ticker": key.symbol,
                "ex_date": ex_date,
                "action": action,
                "value": float(value),
                "source_available_at": source_time,
            }
        )

    event_frame = (
        pl.DataFrame(normalized_events)
        if normalized_events
        else pl.DataFrame(
            schema={
                "market": pl.String,
                "mic": pl.String,
                "symbol": pl.String,
                "ticker": pl.String,
                "ex_date": pl.Date,
                "action": pl.String,
                "value": pl.Float64,
            }
        )
    )
    if not event_frame.is_empty():
        event_frame = event_frame.with_columns(pl.col("ex_date").cast(pl.Date))
        selected_events = event_frame.filter(pl.col("ex_date") <= pl.lit(decision_date))
        duplicate_keys = [*IDENTITY_COLUMNS, "ex_date", "action"]
        groups = selected_events.group_by(duplicate_keys).agg(
            pl.col("value").n_unique().alias("values"),
            pl.len().alias("rows"),
        )
        for group in groups.iter_rows(named=True):
            if group["values"] > 1:
                identity = (group["market"], group["mic"], group["symbol"])
                raise AvailabilityContractError(f"conflicting action revisions for {identity} on {group['ex_date']}")
        selected_events = selected_events.unique(subset=duplicate_keys, keep="last")
        event_rows = selected_events.to_dicts()
    else:
        event_rows = []

    splits = [row for row in event_rows if row["action"] == "split"]
    indexed = identified.with_row_index("_adjust_row_idx").with_columns(
        (pl.col("market") + pl.lit("/") + pl.col("mic") + pl.lit("/") + pl.col("symbol")).alias("__adjust_key"),
        (pl.col("date") + pl.duration(days=1)).alias("_asof_key"),
    )
    if splits:
        step_rows = [
            {
                "__adjust_key": f"{row['market']}/{row['mic']}/{row['symbol']}",
                "ex_date": row["ex_date"],
                "factor_step": row["value"],
            }
            for row in splits
        ]
        steps = (
            pl.DataFrame(step_rows)
            .group_by(["__adjust_key", "ex_date"])
            .agg(pl.col("factor_step").product())
            .sort(["__adjust_key", "ex_date"])
            .with_columns(_factor_on_expr())
        )
        adjusted = (
            indexed.sort(["__adjust_key", "_asof_key"])
            .join_asof(steps, by="__adjust_key", left_on="_asof_key", right_on="ex_date", strategy="forward", check_sortedness=False)
            .with_columns(pl.col("factor_on").fill_null(1.0))
            .with_columns([pl.col(column) * pl.col("factor_on") for column in scale_cols])
        )
        if "volume" in adjusted.columns:
            adjusted = adjusted.with_columns(pl.col("volume") / pl.col("factor_on"))
    else:
        adjusted = indexed.with_columns(pl.lit(1.0).alias("factor_on"))
    drop_columns = [column for column in ("_adjust_row_idx", "__adjust_key", "_asof_key", "factor_on", "ex_date") if column in adjusted.columns]
    ordered = adjusted.sort("_adjust_row_idx").drop(drop_columns)
    row_proofs: list[CoverageProof] = []
    for row in ordered.select(*IDENTITY_COLUMNS, "date").to_dicts():
        instrument = InstrumentKey(**{column: row[column] for column in IDENTITY_COLUMNS})
        proof = proofs[(row["market"], row["mic"], row["symbol"])]
        row_proofs.append(proof)
    if coverage_basis == "source_clock":
        source_ages: list[int | None] = []
        for instrument_row, proof in zip(ordered.select(*IDENTITY_COLUMNS, "date").to_dicts(), row_proofs, strict=True):
            if not isinstance(proof, ActionCoverageProof):
                raise AvailabilityContractError("strict read resolved a weak coverage proof")
            instrument = InstrumentKey(**{column: instrument_row[column] for column in IDENTITY_COLUMNS})
            source_ages.append((decision_date - source_observable_session(instrument, proof.source_available_at)).days)
    else:
        source_ages = [None] * ordered.height
    return ordered.with_columns(
        pl.lit(PRICE_ADJUSTMENT_VERSION).alias("price_adjustment_version"),
        pl.lit(AVAILABILITY_SCHEMA_VERSION, dtype=pl.Int8).alias("availability_schema_version"),
        pl.lit(1, dtype=pl.Int8).alias("action_coverage"),
        pl.Series("action_source_age_days", source_ages, dtype=pl.Int32),
        pl.Series("action_coverage_source", [proof.source for proof in row_proofs], dtype=pl.String),
        pl.Series("action_coverage_start", [proof.coverage_start for proof in row_proofs], dtype=pl.Date),
        pl.Series("action_coverage_end", [proof.coverage_end for proof in row_proofs], dtype=pl.Date),
        pl.Series(
            "action_source_available_at",
            [getattr(proof, "source_available_at", None) for proof in row_proofs],
            dtype=pl.Datetime("us", "UTC"),
        ),
        pl.Series(
            "action_source_valid_until",
            [getattr(proof, "source_valid_until", None) for proof in row_proofs],
            dtype=pl.Datetime("us", "UTC"),
        ),
        pl.Series("action_coverage_status", [proof.coverage_status for proof in row_proofs], dtype=pl.String),
        pl.Series("action_coverage_schema_version", [proof.coverage_schema_version for proof in row_proofs], dtype=pl.Int8),
        pl.Series("action_coverage_basis", [proof.coverage_basis for proof in row_proofs], dtype=pl.String),
    )


def factor_snapshot(actions: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """Cumulative split factor per ticker as of *as_of* (split-only).

    Dividend factors need price context, so the snapshot covers splits only —
    the deterministic, assumption-free adjustment (ADR-0011 default).
    """
    for col in ("ticker", "ex_date", "action", "value"):
        if col not in actions.columns:
            raise ValueError(f"actions frame missing required column: {col}")
    return (
        actions.filter((pl.col("ex_date") <= as_of) & (pl.col("action") == "split"))
        .group_by("ticker")
        .agg(pl.col("value").product().alias("factor"))
        .sort("ticker")
    )
