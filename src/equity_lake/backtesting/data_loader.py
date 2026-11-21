"""Load local price bars for backtesting, recording read-seam input snapshots.

Snapshot binding (ADR-0025 Decision 4, frozen decision E01-4) happens **here**,
because this is the seam where a backtest's prices and corporate-action inputs are
actually read. Nothing is looked up afterwards: a Delta version fetched after the
computation would describe the table as it is then, not the snapshot consumed.

The snapshots are accumulated as **plain mappings**
(``{"name", "sha256", "source", "version"}``) and exposed by
:meth:`BacktestDataLoader.snapshots`; this module deliberately does not import
:mod:`equity_lake.backtesting.evidence` (the live chain is
``evidence -> report -> arena -> data_loader``, so importing it back would be a
cycle). The caller converts the mappings into ``InputSnapshot`` models.

Digest conventions:

- **Delta price marks** (``name="price_marks:<market>"``, ``source="lake:delta"``):
  ``sha256(f"delta:{table_path}@v{version}")`` — a digest of the immutable
  ``(path, version)`` **reference** the read consumed, not a content hash of every
  data file in the table. Re-reading the same version yields the same digest; any
  later commit invalidates nothing, it simply is a different snapshot.
- **Non-Delta (parquet) price marks** (``name="price_marks:<market>"``,
  ``source="lake:parquet"``, ``version=None``): the loader enumerates the same
  ``<root>/**/*.parquet`` glob the scan uses, sorted and before the scan, streams
  each file to hash it, and records **one aggregate** snapshot per market,
  ``sha256(canonical JSON of sorted [(relative_path, bytes, file_sha256)])``. This
  costs an extra read pass over the files. The digest is a **reproducibility
  reference only**: it cannot be bound to the bytes a later scan reads (a
  replacement restored before any re-check defeats even a post-scan comparison), so
  a non-Delta price read never asserts a price origin — its rows are summarised with
  ``incomplete_binding`` and the aggregate stays ``unknown`` (ADR-0027 §4).
- **Corporate-action inputs** (``name="action_events:<market>"`` /
  ``name="action_coverage:<market>"``, ``source="lake:delta"``): one snapshot per
  table actually read, same ``(path, version)`` digest convention, from the version
  captured by the read itself
  (:func:`equity_lake.ingestion.corporate_actions.load_corporate_action_inputs_with_versions`).
  An absent table was not read, so it records no snapshot rather than a fabricated
  hash. In-memory ``action_inputs`` fixtures have no read seam at all and therefore
  record nothing — a documented residual, never an invented digest.

``preloaded_data`` means the loader never ran, so :meth:`snapshots` returns ``[]``.

Scope is **one load, not one loader**: :meth:`BacktestDataLoader.load` starts by
clearing the accumulated snapshot map (and the action-input read cache) so a loader
reused for a second load re-reads the seam and :meth:`snapshots` then describes the
second read — a snapshot captured during an earlier load can never be reported
alongside newer data. Within one load each ``name`` is recorded once: a market's
price table is read once per load, and action tables are read once per identity per
load, so one snapshot per name is the whole story.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Self

import polars as pl
import structlog

from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    COVERAGE_BASES,
    AvailabilityContractError,
    CoverageBasis,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_COLUMNS,
    InstrumentIdentityError,
    InstrumentKey,
    normalize_identity_frame,
)
from equity_lake.core.paths import PRICE_MARKETS, market_dir
from equity_lake.core.price_provenance import (
    PriceOriginClassification,
    classify_price_origin,
)
from equity_lake.core.schemas import ACTION_COVERAGE_SNAPSHOT_NAME_PREFIX, PRICE_PROVENANCE_VERSION
from equity_lake.ingestion.types import MARKET_DIR_MAP
from equity_lake.storage.delta import delta_table_path
from equity_lake.storage.lake_reader import with_price_adjustment

logger = structlog.get_logger(__name__)

_PIT_RAW_COLUMNS = {column: f"__pit_raw_{column}" for column in ("open", "high", "low", "close", "volume")}
_PIT_ACTION_CONTEXT = "__pit_action_context"

#: Read-seam snapshot sources. One string per seam, so a manifest can be read back
#: to the code path that produced the digest.
SNAPSHOT_SOURCE_DELTA = "lake:delta"
SNAPSHOT_SOURCE_PARQUET = "lake:parquet"


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__} in point-in-time action context")


def _stream_digest(path: Path) -> tuple[str, int]:
    """SHA-256 and byte count of a file, streamed so a large parquet file is not held."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _delta_reference_digest(table_path: Path, version: int) -> str:
    """Digest of an immutable ``(table path, version)`` reference (not file contents)."""
    return hashlib.sha256(f"delta:{table_path}@v{version}".encode()).hexdigest()


def _parquet_file_set_digest(files: Sequence[Path], *, root: Path) -> str:
    """One aggregate digest over an enumerated, sorted parquet file set.

    The payload is canonical JSON of ``sorted [(relative_path, bytes, file_sha256)]``,
    so the digest is stable regardless of enumeration order and changes when a file
    is added, removed, renamed or rewritten.
    """
    entries = sorted([path.relative_to(root).as_posix(), *reversed(_stream_digest(path))] for path in files)
    payload = json.dumps(entries, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        lake_dir: Path | None = None,
        coverage_basis: CoverageBasis = "source_clock",
    ):
        # Kept as compatibility settings for callers; local scans need no SQL
        # connection and never interpolate lake paths or ticker values into SQL.
        self.db_path = db_path
        self.cache_enabled = cache_enabled
        self.action_inputs = action_inputs
        self.lake_dir = Path(lake_dir) if lake_dir is not None else None
        #: The ADR-0032 basis this loader admits for every adjusted read it performs
        #: (one basis per load). ``source_clock`` is the strict default; the weak
        #: ``vendor_events_no_clock`` is an explicit opt-in and is never inferred.
        if coverage_basis not in COVERAGE_BASES:
            raise ValueError(f"Unknown coverage basis: {coverage_basis!r} (expected one of {COVERAGE_BASES})")
        self.coverage_basis: CoverageBasis = coverage_basis
        self._action_input_cache: dict[tuple[str, str, str], tuple[pl.DataFrame, pl.DataFrame]] = {}
        #: Read-seam input snapshots of the current load, keyed by name so one
        #: market's two action tables and one price table are each recorded once.
        #: Cleared at the start of every :meth:`load` (see the module docstring).
        self._snapshots: dict[str, dict[str, str | None]] = {}
        #: Row-bound price-origin summaries of the current load, keyed by price snapshot
        #: name. Plain mappings for the same reason as ``_snapshots``: the classification
        #: happens here (before any projection drops the marker columns) and the evidence
        #: writer turns it into a validated ``PriceOriginSummary``.
        self._price_origin_summaries: dict[str, dict[str, object]] = {}
        #: Snapshots whose claim must be refused for this load (a non-Delta file set that
        #: changed between the pre-scan digest and the scan itself), keyed by snapshot name
        #: with the closed-set reason it is recorded under.
        self._origin_refusals: dict[str, str] = {}
        logger.info("BacktestDataLoader initialized", cache_enabled=cache_enabled, lake_dir=str(self.lake_dir) if self.lake_dir else None)

    def snapshots(self) -> list[dict[str, str | None]]:
        """The inputs the most recent load actually read, as plain mappings, name-sorted.

        Each entry is ``{"name", "sha256", "source", "version"}``; ``version`` is the
        Delta version string captured by the read (``None`` for a non-Delta file-set
        digest). Scoped to the latest :meth:`load`: that call clears the map before
        reading, so these describe the data the caller is about to use and never an
        earlier load's read of the same table name. Empty when nothing was read by
        the latest load — ``preloaded_data`` never reaches here, and in-memory fixture
        action inputs have no read seam.

        Plain mappings, not ``InputSnapshot`` models, so this module keeps no import
        of :mod:`equity_lake.backtesting.evidence` (which imports ``report``, which
        imports ``arena``, which imports this module).
        """
        return [dict(entry) for entry in sorted(self._snapshots.values(), key=lambda entry: str(entry["name"]))]

    def price_origin_summaries(self) -> list[dict[str, object]]:
        """Row-bound origin evidence for the price snapshots the latest load consumed.

        One entry per price snapshot, describing **every row this read consumed** for that
        market in the requested date window — including rows a strategy later filters out
        — which is the conservative complete-input classification ADR-0027 §4.5 permits
        (and which the manifest discloses through ``consumed_rows``). Scoped to the latest
        load and empty when nothing was read (``preloaded_data``, an in-memory frame).

        Plain mappings, not ``PriceOriginSummary`` models, keeping this module free of the
        ``backtesting.evidence`` import (see :meth:`snapshots`).
        """
        return [dict(entry) for entry in sorted(self._price_origin_summaries.values(), key=lambda entry: str(entry.get("name")))]

    def _record_delta_snapshot(self, name: str, table_path: Path, version: int, *, coverage_basis: CoverageBasis | None = None) -> None:
        """Record an immutable ``(path, version)`` reference digest for one table read.

        One entry per ``name`` per load: the first read of a given table in the
        current load wins, and the map is cleared when the next load starts. A
        corporate-action coverage snapshot additionally records the ADR-0032 basis
        its proofs were admitted under, so the manifest binds the basis it read and
        a scope mixing bases can be refused (ADR-0032 decision 9). The basis is a
        read parameter stamped here, never part of the ``(path, version)`` digest.
        """
        if name in self._snapshots:
            return
        snapshot: dict[str, str | None] = {
            "name": name,
            "sha256": _delta_reference_digest(table_path, version),
            "source": SNAPSHOT_SOURCE_DELTA,
            "version": str(version),
        }
        if coverage_basis is not None:
            snapshot["coverage_basis"] = coverage_basis
        self._snapshots[name] = snapshot

    def _record_parquet_snapshot(self, name: str, root: Path) -> None:
        """Record one aggregate content digest over the parquet file set at ``root``.

        Enumerated with the same glob the scan uses, sorted, **before** the scan; a
        file-set change after this enumeration is a documented residual. One entry
        per ``name`` per load, like the Delta variant.
        """
        if name in self._snapshots:
            return
        files = sorted(root.glob("**/*.parquet"))
        self._snapshots[name] = {
            "name": name,
            "sha256": _parquet_file_set_digest(files, root=root),
            "source": SNAPSHOT_SOURCE_PARQUET,
            "version": None,
        }

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

        Read-seam snapshots are **scoped to this call**: the accumulated map and the
        action-input read cache are cleared before anything is read, so
        :meth:`snapshots` afterwards describes this load's reads and a loader reused
        for a later load never reports the earlier one's version/digest. Within the
        call each snapshot name is recorded once, and distinct markets contribute
        distinct names.
        """
        # Snapshot scope is the load, not the loader: reset before any read so a stale
        # entry can never be reported next to data this load fetched.
        self._snapshots.clear()
        self._price_origin_summaries.clear()
        self._origin_refusals.clear()
        self._action_input_cache.clear()
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
            self._read_market(market, symbols, start_date, end_date, requested_columns, self.lake_dir)
            for market in selected_markets
            if market in PRICE_MARKETS
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
    def _market_data_dir(market: str, lake_dir: Path | None) -> Path:
        """Resolve a market's Bronze directory, honoring an explicit lake root.

        Without an override the registry lookup is used so runtime patches of the
        ``*_DIR`` constants stay effective; with one, the medallion-relative table
        path is resolved against that root.
        """
        if lake_dir is None:
            return market_dir(market)
        return delta_table_path(MARKET_DIR_MAP[market], lake_dir)

    def _read_market(
        self, market: str, symbols: list[str], start_date: date, end_date: date, columns: list[str], lake_dir: Path | None = None
    ) -> pl.DataFrame:
        """Read a bounded market/date scope, retaining explicit stored identity.

        Records this market's read-seam snapshot here, inside the read: the Delta
        version is taken from the constructed :class:`DeltaTable` before the scan,
        and the non-Delta path digests the enumerated file set before its scan.
        """
        data_dir = BacktestDataLoader._market_data_dir(market, lake_dir)
        snapshot_name = f"price_marks:{market}"
        if not data_dir.exists():
            logger.warning("Market data directory not found", market=market, path=str(data_dir), hint="Run equity ingest command first")
            return pl.DataFrame()

        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(data_dir)):
            table = DeltaTable(str(data_dir))
            # Read-seam capture: the version of the table object that will produce the
            # frame, taken before the scan (never a post-computation lookup).
            self._record_delta_snapshot(snapshot_name, data_dir, int(table.version()))
            available = {field.name for field in table.schema().fields}
            if "date" not in available:
                # Nothing was consumed, but the snapshot exists: record the empty scope so
                # the manifest discloses it as `unknown`/`empty_scope` rather than omitting it.
                self._record_price_origin(snapshot_name, pl.DataFrame())
                return pl.DataFrame()
            filters: list[tuple[str, str, object]] = [("date", ">=", start_date), ("date", "<=", end_date)]
            raw = pl.DataFrame(table.to_pyarrow_table(filters=filters))
        else:
            # Enumerated and digested before the scan, with the scan's own glob.
            self._record_parquet_snapshot(snapshot_name, data_dir)
            raw = pl.scan_parquet(str(data_dir / "**" / "*.parquet"), hive_partitioning=True).collect()
            # A non-Delta read cannot support an origin claim. The snapshot digest covers the
            # file set as *enumerated*, while the scan reads the bytes later, so a concurrent
            # replacement — including one that is restored before any re-check (an ABA) —
            # could leave a known origin bound to different bytes. ADR-0027 §4 forbids that,
            # and demands either a freeze of the scanned bytes or a refused claim: this path
            # refuses, and keeps the row count plus the envelope digest visible instead.
            self._origin_refusals[snapshot_name] = "incomplete_binding"
            logger.info("price_origin_unbound_file_set", market=market, path=str(data_dir))
            if "date" not in raw.columns:
                self._record_price_origin(snapshot_name, pl.DataFrame())
                return pl.DataFrame()
            raw = raw.filter(pl.col("date").cast(pl.Date).is_between(start_date, end_date, closed="both"))

        ticker_column = "ticker" if "ticker" in raw.columns else "symbol" if "symbol" in raw.columns else None
        if ticker_column is None:
            self._record_price_origin(snapshot_name, raw)
            return pl.DataFrame()
        raw = raw.filter(pl.col(ticker_column).cast(pl.String).is_in(symbols))
        if raw.is_empty():
            self._record_price_origin(snapshot_name, raw)
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

        # Origin evidence is classified here, on the rows this market contributed to the
        # shared frame, and **before** the projection below can discard the marker columns.
        # The snapshot it describes is the same one the read just recorded, so a table that
        # advances afterwards cannot change what this load claims it consumed.
        self._record_price_origin(snapshot_name, raw)

        return raw.select([column for column in keep if column in raw.columns])

    def _record_price_origin(self, snapshot_name: str, frame: pl.DataFrame) -> None:
        """Classify one market's consumed rows and bind the result to its snapshot.

        A malformed version-1 claim raises out of the load: ADR-0027 §4.5 makes that an
        input validation failure, so the pair fails instead of being published with a
        defect classified as a healthy ``unknown``.
        """
        snapshot = self._snapshots.get(snapshot_name)
        if snapshot is None:  # pragma: no cover - the read always records its snapshot first
            return
        # Always validate the consumed rows first: a malformed supported claim is an input
        # validation failure (ADR-0027 §4.5), and refusing the *claim* must never suppress
        # that check. The classification is then discarded for a refused snapshot.
        classification = classify_price_origin(frame)
        refusal = self._origin_refusals.get(snapshot_name)
        if refusal is not None:
            # A refused *claim* loses its label, but the rows keep their own diagnostics:
            # a row that was already unproved for its own reason (a missing envelope, or an
            # unsupported future version that must stay visible, ADR-0027 §4.5) is not
            # relabelled as a binding defect. Only rows whose claim cannot be bound get the
            # refusal reason, so the reasons still add up to the consumed rows.
            carried = dict(classification.unknown_reasons)
            unbound = frame.height - sum(carried.values())
            if unbound > 0:
                carried[refusal] = carried.get(refusal, 0) + unbound
            classification = PriceOriginClassification(
                consumed_rows=frame.height,
                synthetic=0,
                real=0,
                unknown=frame.height,
                unknown_reasons=dict(sorted(carried.items())),
                sources=[],
                envelope_sha256=classification.envelope_sha256,
            )
        self._price_origin_summaries[snapshot_name] = {
            "schema_version": PRICE_PROVENANCE_VERSION,
            "snapshot": dict(snapshot),
            **classification.as_summary_fields(),
        }

    def _action_inputs_for(self, instrument: InstrumentKey) -> tuple[pl.DataFrame, pl.DataFrame]:
        """One instrument's action inputs, recording the lake read's versions once.

        A lake read has a read seam, so the version each table was read at is
        recorded here (``action_events:<market>`` / ``action_coverage:<market>``,
        one snapshot per market). An absent table was not read and records nothing;
        in-memory fixture inputs (``self.action_inputs``) have no read seam at all
        and likewise record nothing — never a fabricated hash.
        """
        cached = self._action_input_cache.get(instrument.tuple)
        if cached is not None:
            return cached
        if self.action_inputs is None:
            from equity_lake.ingestion.corporate_actions import (
                corporate_action_coverage_table,
                corporate_actions_table,
                load_corporate_action_inputs_with_versions,
            )

            actions, coverage, versions = load_corporate_action_inputs_with_versions(instrument.market, lake_dir=self.lake_dir)
            result = (actions, coverage)
            self._record_action_snapshots(
                instrument.market,
                events_table=corporate_actions_table(instrument.market, "silver"),
                coverage_table=corporate_action_coverage_table(instrument.market),
                versions=versions,
            )
        else:
            try:
                result = self.action_inputs[instrument.tuple]
            except KeyError as exc:
                raise AvailabilityContractError(f"No action input fixture for {instrument.display_key}") from exc
        self._action_input_cache[instrument.tuple] = result
        return result

    def _record_action_snapshots(
        self,
        market: str,
        *,
        events_table: str,
        coverage_table: str,
        versions: Mapping[str, int | None],
    ) -> None:
        """Record one ``(path, version)`` snapshot per action table actually read.

        ``versions`` maps lake-relative table name -> the version its read consumed,
        or ``None`` when the table is absent. An absent table records no snapshot at
        all: there is no read to bind, and a hash for an unread table would be
        invented evidence.
        """
        snapshot_scope: tuple[tuple[str, str, CoverageBasis | None], ...] = (
            (f"action_events:{market}", events_table, None),
            (f"{ACTION_COVERAGE_SNAPSHOT_NAME_PREFIX}{market}", coverage_table, self.coverage_basis),
        )
        for name, table, basis in snapshot_scope:
            version = versions.get(table)
            if version is None:
                continue
            self._record_delta_snapshot(name, delta_table_path(table, self.lake_dir), version, coverage_basis=basis)

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

    def _require_one_basis(self, instrument: InstrumentKey, coverage: pl.DataFrame) -> None:
        """Refuse a read whose coverage proofs were issued on another basis.

        The per-session loop in :meth:`_point_in_time_marks` turns a session whose
        proof cannot establish availability into an excluded session. A **basis**
        mismatch is not a session condition: it affects every session of the read,
        so excluding them one by one would report a refused read as a successful
        empty one. One basis per read is enforced here instead, by name (ADR-0032
        decision 4). A row with no basis stamp predates the column and implies
        ``source_clock`` exactly as before, so legacy evidence is never reinterpreted
        as weak and a weak request against it refuses.
        """
        if coverage.is_empty():
            return
        declared = coverage["coverage_basis"].to_list() if "coverage_basis" in coverage.columns else [None] * coverage.height
        recorded = {"source_clock" if value is None else str(value) for value in declared}
        foreign = sorted(recorded - {self.coverage_basis})
        if foreign:
            raise AvailabilityContractError(
                f"{instrument.display_key}: coverage proofs were read on {foreign} but this read requests "
                f"{self.coverage_basis!r}; one read admits one basis"
            )

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
            payload = json.dumps(
                {"actions": actions.to_dicts(), "coverage": coverage.to_dicts(), "coverage_basis": self.coverage_basis},
                default=_json_default,
                sort_keys=True,
            )
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
            self._require_one_basis(instrument, coverage)
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
                        verified = with_price_adjustment(
                            proof_rows, actions, coverage=coverage, method=method, as_of=decision_date, coverage_basis=self.coverage_basis
                        )
                        metadata_row = verified.row(-1, named=True)
                        current = subset.filter(pl.col("date") == decision_date)
                    else:
                        adjusted = with_price_adjustment(
                            history, actions, coverage=coverage, method=method, as_of=decision_date, coverage_basis=self.coverage_basis
                        )
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
                # A weak (``vendor_events_no_clock``) row has no source clock, so its
                # age is null by design (ADR-0032 decision 5) — never defaulted to 0.
                source_age = metadata_row["action_source_age_days"]
                source_ages.append(None if source_age is None else int(source_age))
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


__all__ = ["SNAPSHOT_SOURCE_DELTA", "SNAPSHOT_SOURCE_PARQUET", "BacktestDataLoader"]
