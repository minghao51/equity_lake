from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
import structlog

from equity_lake.backtesting.data_loader import _PIT_ACTION_CONTEXT, _PIT_RAW_COLUMNS, BacktestDataLoader
from equity_lake.backtesting.metrics import DEFAULT_RISK_FREE_RATE, equity_curve_metrics
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy.base import BaseStrategy
from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    COVERAGE_BASES,
    AvailabilityContractError,
    CoverageBasis,
)
from equity_lake.core.instruments import IDENTITY_COLUMNS, InstrumentIdentityError, InstrumentKey
from equity_lake.storage.lake_reader import with_price_adjustment

try:
    import polars_backtest  # noqa: F401

    POLARS_BACKTEST_AVAILABLE = True
except ImportError:
    POLARS_BACKTEST_AVAILABLE = False

logger = structlog.get_logger(__name__)

DEFAULT_FEE_RATIO = 0.001425
DEFAULT_TAX_RATIO = 0.003

#: Warning recorded when the backend report carries no usable timestamps, so the
#: curve stays undated rather than gaining a calendar invented from its length
#: (ADR-0025 Decision 6).
UNDATED_EQUITY_WARNING = (
    "equity dates unavailable: the backend report carries no usable date column, so the curve stays undated (no calendar is inferred from its length)"
)


def _warn_if_undated(warnings: list[str], equity_len: int, equity_dates: list[date] | None) -> None:
    """Record the undated-evidence warning for a non-empty curve without dates."""
    if equity_dates is None and equity_len > 0 and UNDATED_EQUITY_WARNING not in warnings:
        warnings.append(UNDATED_EQUITY_WARNING)


def _float_scalar(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def universe_size(data: pl.DataFrame) -> int:
    """Distinct canonical identities in a load (``ticker`` for fixtures).

    The full ``(market, mic, symbol)`` identity is counted whenever all three columns
    are present, so the same ticker on two venues is two instruments; a ticker-only
    fixture falls back to distinct tickers, and a frame carrying neither identity
    yields ``0`` (the classifier then reports an unusable cross-section rather than
    inventing one). This is the one place the loaded-cohort size is defined: the
    arena's evaluation classifier and the single-report CLI both use it.
    """
    if set(IDENTITY_COLUMNS).issubset(data.columns):
        return data.select(*IDENTITY_COLUMNS).unique().height
    if "ticker" in data.columns:
        return data["ticker"].n_unique()
    return 0


def loaded_identity_keys(data: pl.DataFrame) -> list[str] | None:
    """Canonical display keys of a loaded frame, or ``None`` when it cannot prove them.

    The universe a run *consulted* is a fact of the frame the engine read, so it is
    derived from that frame's full ``(market, mic, symbol)`` identity through the
    identity model — never from the requested tickers and never from a guessed MIC
    (ADR-0016 Decision 1/2). A frame without full identity returns ``None``: an
    unknown universe is disclosed, not upgraded, so a caller records its bare
    tickers with ``ticker_only`` instead.
    """
    if not set(IDENTITY_COLUMNS).issubset(data.columns):
        return None
    keys = sorted({InstrumentKey(**row).display_key for row in data.select(*IDENTITY_COLUMNS).unique().to_dicts()})
    return keys or None


def _join_strategy_weights(data: pl.DataFrame, weights: pl.DataFrame) -> pl.DataFrame:
    """Join strategy signals on date plus full instrument identity when present."""
    has_composite_identity = set(IDENTITY_COLUMNS).issubset(data.columns)
    join_keys = ["date", *IDENTITY_COLUMNS] if has_composite_identity else ["date", "ticker"]
    if has_composite_identity and not set(join_keys).issubset(weights.columns):
        raise InstrumentIdentityError("Strategy weights dropped composite instrument identity keys")
    return data.join(weights, on=join_keys, how="left").with_columns(pl.col("weight").fill_null(0.0))


class VectorBacktestEngine:
    """
    Vectorized backtesting engine using polars-backtest.

    Uses long-format Polars DataFrames with target weight signals.
    Strategy.generate_weights() returns (date, ticker, weight) frames.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        tickers: Sequence[str | InstrumentKey],
        start_date: date,
        end_date: date,
        initial_cash: float = 100_000.0,
        markets: list[str] | None = None,
        config: dict[str, Any] | None = None,
        preloaded_data: pl.DataFrame | None = None,
        adjust: Literal["none", "splits", "total_return"] = "splits",
        lake_dir: Path | None = None,
        coverage_basis: CoverageBasis = "source_clock",
    ):
        self.strategy = strategy
        self.tickers = [ticker if isinstance(ticker, str) else ticker.display_key for ticker in tickers]
        self._requested_tickers = list(tickers)
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.markets = markets or ["us_equity", "cn_ashare", "hk_sg_equity"]
        self.config = config or {}
        self.preloaded_data = preloaded_data
        self.adjust: Literal["none", "splits", "total_return"] = adjust
        #: The one lake root this engine reads (``None`` keeps the canonical lake) and the
        #: ADR-0032 basis its adjusted reads are admitted under. One root and one basis per
        #: run: the basis is an explicit request, never inferred from the data (a frame read
        #: on another basis is refused, not reinterpreted).
        self.lake_dir = Path(lake_dir) if lake_dir is not None else None
        if coverage_basis not in COVERAGE_BASES:
            raise ValueError(f"Unknown coverage basis: {coverage_basis!r} (expected one of {COVERAGE_BASES})")
        self.coverage_basis: CoverageBasis = coverage_basis

        self.fee_ratio = self.config.get("fee_ratio", DEFAULT_FEE_RATIO)
        self.tax_ratio = self.config.get("tax_ratio", DEFAULT_TAX_RATIO)
        self.resample = self.config.get("resample", "D")
        self.stop_loss = self.config.get("stop_loss", 1.0)
        self.take_profit = self.config.get("take_profit", float("inf"))

        #: Size of the frame this engine actually loaded through its own loader, or
        #: ``None`` before a load and for a ``preloaded_data`` frame the engine did not
        #: load (the caller owns that cohort, so it must disclose its own size rather
        #: than have this engine guess one). Set by :meth:`_load_data`.
        self.loaded_universe_size: int | None = None

        #: Canonical display keys of the frame this engine actually loaded through its
        #: own loader (``market/MIC/percent-quoted-symbol``), or ``None`` before a load
        #: and for a ``preloaded_data`` frame — the engine did not read it, so it does
        #: not claim its universe. Only these keys (not the requested tickers) prove a
        #: ``canonical`` universe basis (E01c contract item 1). Set by
        #: :meth:`_load_data`.
        self.loaded_identities: list[str] | None = None

        # Created lazily on first _load_data() call without preloaded_data — the
        # loader opens a DuckDB connection, and arena runs must not pay for it.
        self.data_loader: BacktestDataLoader | None = None
        self._backend_identity_map: dict[str, dict[str, str]] = {}
        self._warnings: list[str] = []

        self._report: Any = None
        self.metrics: dict[str, Any] = {}

        logger.info(
            "VectorBacktestEngine initialized",
            strategy=strategy.name,
            tickers=len(tickers),
            start_date=str(start_date),
            end_date=str(end_date),
            initial_cash=initial_cash,
        )

    def run(self) -> BacktestResult:
        if not POLARS_BACKTEST_AVAILABLE:
            raise ImportError("polars-backtest is required. Install with: uv sync --group backtesting")

        logger.info("Starting vectorized backtest", strategy=self.strategy.name)

        data = self._load_data()
        if data.is_empty():
            raise ValueError("No data available for backtest")

        data, weights_df = self._prepare_point_in_time_inputs(data)
        if data.is_empty():
            raise AvailabilityContractError("No backtest decisions have verified point-in-time action coverage")
        has_composite_identity = set(IDENTITY_COLUMNS).issubset(data.columns)
        data_with_weights = _join_strategy_weights(data, weights_df)

        symbol_column = "ticker"
        if has_composite_identity:
            identity_rows = data_with_weights.select(*IDENTITY_COLUMNS).unique().iter_rows(named=True)
            self._backend_identity_map = {InstrumentKey(**identity).artifact_token: identity for identity in identity_rows}
            data_with_weights = data_with_weights.with_columns(
                pl.struct(*IDENTITY_COLUMNS)
                .map_elements(lambda row: InstrumentKey(**row).artifact_token, return_dtype=pl.String)
                .alias("__backtest_symbol")
            )
            symbol_column = "__backtest_symbol"

        bt_kwargs: dict[str, Any] = {
            "trade_at_price": "close",
            "position": pl.col("weight").cast(pl.Float64),
            "symbol": symbol_column,
            "fee_ratio": self.fee_ratio,
            "tax_ratio": self.tax_ratio,
        }
        if self.resample:
            bt_kwargs["resample"] = self.resample
        if self.stop_loss < 1.0:
            bt_kwargs["stop_loss"] = self.stop_loss
        if self.take_profit < float("inf"):
            bt_kwargs["take_profit"] = self.take_profit

        report = cast(Any, data_with_weights).bt.backtest_with_report(**bt_kwargs)
        self._report = report

        try:
            stats = report.get_stats(riskfree_rate=DEFAULT_RISK_FREE_RATE)
        except Exception as exc:
            logger.warning("backtest_stats_unavailable", error=str(exc))
            self._warnings.append(f"stats unavailable: {exc}")
            stats = pl.DataFrame()

        trades = self._extract_trades(report)
        equity_curve = self._extract_equity_curve(report)
        equity_dates = self._extract_equity_dates(report, equity_curve)
        _warn_if_undated(self._warnings, equity_curve.len(), equity_dates)
        self._compute_metrics(stats, report, equity_curve)

        result = BacktestResult(
            strategy_name=self.strategy.name,
            tickers=self.tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_cash=self.initial_cash,
            final_cash=_float_scalar(equity_curve.last(), self.initial_cash) if equity_curve.len() > 0 else self.initial_cash,
            equity_curve=equity_curve,
            trades=trades,
            metrics=self.metrics,
            warnings=self._warnings,
            equity_dates=equity_dates,
        )

        logger.info(
            "Vectorized backtest completed",
            strategy=self.strategy.name,
            total_return=result.total_return,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            num_trades=len(trades),
        )

        return result

    def _load_data(self) -> pl.DataFrame:
        if self.preloaded_data is not None:
            # The caller loaded this cohort; ``loaded_universe_size`` and
            # ``loaded_identities`` stay ``None`` so a classifier or a writer cannot
            # mistake the caller's frame for one this engine read.
            return self.preloaded_data.clone()
        if self.data_loader is None:
            # The engine's own loader reads exactly one root under exactly one basis;
            # a root or basis given to this engine is never dropped on the floor.
            self.data_loader = BacktestDataLoader(lake_dir=self.lake_dir, coverage_basis=self.coverage_basis)
        loaded = self.data_loader.load(
            tickers=self._requested_tickers,
            start_date=self.start_date,
            end_date=self.end_date,
            markets=self.markets,
            adjust=self.adjust,
        )
        self.loaded_universe_size = universe_size(loaded)
        self.loaded_identities = loaded_identity_keys(loaded)
        logger.info(
            "backtest_data_loaded",
            universe_size=self.loaded_universe_size,
            canonical_identities=len(self.loaded_identities or ()),
        )
        return loaded

    @staticmethod
    def _decode_action_context(payload: str) -> tuple[pl.DataFrame, pl.DataFrame, CoverageBasis]:
        """Rehydrate the source-issued event and coverage frames from loader handoff."""
        try:
            decoded = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AvailabilityContractError("Preloaded backtest action context is corrupt") from exc
        recorded_basis = decoded.get("coverage_basis", "source_clock")
        if recorded_basis not in COVERAGE_BASES:
            raise AvailabilityContractError(f"Preloaded backtest action context records an unknown coverage basis {recorded_basis!r}")
        if not isinstance(decoded, dict):
            raise AvailabilityContractError("Preloaded backtest action context is corrupt")

        def restore(rows: list[dict[str, Any]], date_columns: tuple[str, ...], time_columns: tuple[str, ...]) -> pl.DataFrame:
            for row in rows:
                for column in date_columns:
                    if isinstance(row.get(column), str):
                        row[column] = date.fromisoformat(row[column])
                for column in time_columns:
                    if isinstance(row.get(column), str):
                        row[column] = datetime.fromisoformat(row[column])
            return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

        action_rows = decoded.get("actions", [])
        coverage_rows = decoded.get("coverage", [])
        actions = (
            restore(action_rows, ("ex_date",), ("source_available_at",))
            if action_rows
            else pl.DataFrame(
                schema={
                    "ticker": pl.String,
                    "ex_date": pl.Date,
                    "action": pl.String,
                    "value": pl.Float64,
                    "source_available_at": pl.Datetime("us", "UTC"),
                }
            )
        )
        coverage = (
            restore(coverage_rows, ("coverage_start", "coverage_end"), ("source_available_at", "source_valid_until"))
            if coverage_rows
            else pl.DataFrame(
                schema={
                    "market": pl.String,
                    "mic": pl.String,
                    "symbol": pl.String,
                    "source": pl.String,
                    "coverage_start": pl.Date,
                    "coverage_end": pl.Date,
                    "source_available_at": pl.Datetime("us", "UTC"),
                    "source_valid_until": pl.Datetime("us", "UTC"),
                }
            )
        )
        return actions, coverage, cast(CoverageBasis, recorded_basis)

    def _prepare_point_in_time_inputs(self, loaded: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Rebuild each decision window and its split-neutral return mark as-of that session.

        Strategies are reinitialized on the current cutoff's adjusted history,
        never on a full-window adjustment. The backend gets a synthetic mark
        chain accumulated from returns computed within each cutoff snapshot;
        therefore split adjustment changes neither earlier signals nor returns
        and a later split cannot create a phantom execution loss. Trade prices
        from this engine are these split-neutral marks, not raw vendor quotes.
        """
        required = {*IDENTITY_COLUMNS, "ticker", "date", *_PIT_RAW_COLUMNS.values(), _PIT_ACTION_CONTEXT}
        if not required.issubset(loaded.columns):
            raise AvailabilityContractError("Preloaded backtest data lacks verified point-in-time action context; evaluation is unavailable")

        hidden_to_raw = {hidden: column for column, hidden in _PIT_RAW_COLUMNS.items()}
        drop_columns = [
            *_PIT_RAW_COLUMNS.values(),
            _PIT_ACTION_CONTEXT,
            "price_adjustment_version",
            "availability_schema_version",
            "action_coverage",
            "action_source_age_days",
            "action_coverage_basis",
            "point_in_time_available",
            "adj_close",
        ]
        raw = loaded.with_columns([pl.col(hidden).alias(column) for hidden, column in hidden_to_raw.items()]).drop(
            [column for column in drop_columns if column in loaded.columns]
        )
        raw = raw.with_columns(pl.col("date").cast(pl.Date)).sort([*IDENTITY_COLUMNS, "date"])

        context_by_identity: dict[tuple[str, str, str], str] = {}
        for row in loaded.select(*IDENTITY_COLUMNS, _PIT_ACTION_CONTEXT).unique().to_dicts():
            key = (row["market"], row["mic"], row["symbol"])
            payload = row[_PIT_ACTION_CONTEXT]
            if not isinstance(payload, str) or (key in context_by_identity and context_by_identity[key] != payload):
                raise AvailabilityContractError(f"Conflicting or missing point-in-time action context for {key}")
            context_by_identity[key] = payload

        histories: dict[tuple[str, str, str], pl.DataFrame] = {}
        for identity in raw.select(*IDENTITY_COLUMNS).unique().iter_rows():
            key = cast(tuple[str, str, str], tuple(identity))
            filters = [pl.col(column) == value for column, value in zip(IDENTITY_COLUMNS, identity, strict=True)]
            histories[key] = raw.filter(pl.all_horizontal(filters))
        action_contexts = {key: self._decode_action_context(payload) for key, payload in context_by_identity.items()}
        # One run admits one basis, and a basis is never inferred from the frame it was
        # handed: a context read on another basis refuses here, before any decision is
        # silently excluded (ADR-0032 decisions 1/4).
        for key, (_, _, recorded_basis) in action_contexts.items():
            if recorded_basis != self.coverage_basis:
                raise AvailabilityContractError(
                    f"point-in-time action context for {key} was read on the {recorded_basis!r} coverage basis, "
                    f"but this run requests {self.coverage_basis!r}: one run admits one basis and a basis is never inferred"
                )
        last_marks: dict[tuple[str, str, str], tuple[date, float]] = {}
        strategy_weight_parts: list[pl.DataFrame] = []
        execution_rows: list[dict[str, Any]] = []
        excluded = 0
        decision_dates = raw.filter((pl.col("date") >= self.start_date) & (pl.col("date") <= self.end_date))["date"].unique().sort().to_list()

        for decision_date in decision_dates:
            window_parts: list[pl.DataFrame] = []
            snapshots: dict[tuple[str, str, str], pl.DataFrame] = {}
            for key, history in histories.items():
                if history.filter(pl.col("date") == decision_date).is_empty():
                    continue
                actions, coverage, _ = action_contexts[key]
                window = history.filter(pl.col("date") <= decision_date)
                try:
                    if actions.is_empty():
                        proof_rows = window.head(1) if window.height == 1 else pl.concat([window.head(1), window.tail(1)], how="vertical_relaxed")
                        verified = with_price_adjustment(
                            proof_rows, actions, coverage=coverage, as_of=decision_date, coverage_basis=self.coverage_basis
                        )
                        verified_metadata = verified.row(-1, named=True)
                        adjusted = window.with_columns(
                            pl.lit(verified_metadata["price_adjustment_version"]).alias("price_adjustment_version"),
                            pl.lit(verified_metadata["availability_schema_version"], dtype=pl.Int8).alias("availability_schema_version"),
                            pl.lit(verified_metadata["action_coverage"], dtype=pl.Int8).alias("action_coverage"),
                            pl.lit(verified_metadata["action_source_age_days"], dtype=pl.Int32).alias("action_source_age_days"),
                            pl.lit(verified_metadata["action_coverage_basis"]).alias("action_coverage_basis"),
                        )
                    else:
                        adjusted = with_price_adjustment(window, actions, coverage=coverage, as_of=decision_date, coverage_basis=self.coverage_basis)
                except AvailabilityContractError as exc:
                    excluded += 1
                    logger.warning(
                        "backtest_decision_window_excluded",
                        identity=key,
                        decision_date=str(decision_date),
                        reason=str(exc),
                    )
                    continue
                snapshots[key] = adjusted
                window_parts.append(adjusted)

            if not window_parts:
                continue
            strategy_window = pl.concat(window_parts, how="diagonal_relaxed").sort([*IDENTITY_COLUMNS, "date"])
            decision_strategy = copy.deepcopy(self.strategy)
            try:
                decision_strategy.initialize(strategy_window)
                generated = decision_strategy.generate_weights(strategy_window)
            finally:
                decision_strategy.finalize()
            if not {"date", "weight", *IDENTITY_COLUMNS}.issubset(generated.columns):
                raise InstrumentIdentityError("Strategy weights dropped date, weight, or canonical identity keys")
            current_weights = generated.filter(pl.col("date").cast(pl.Date) == decision_date)
            if not current_weights.is_empty():
                strategy_weight_parts.append(current_weights)

            for key, adjusted in snapshots.items():
                current = adjusted.filter(pl.col("date") == decision_date)
                if current.is_empty():
                    continue
                current_row = current.row(0, named=True)
                current_close = float(current_row["close"])
                previous = last_marks.get(key)
                if previous is None:
                    index_close = current_close
                else:
                    prior_date, prior_index_close = previous
                    prior_rows = adjusted.filter(pl.col("date") == prior_date)
                    if prior_rows.is_empty():
                        raise AvailabilityContractError(f"No as-of price for prior evaluated session {prior_date} in {key}")
                    prior_close = float(prior_rows["close"][0])
                    if prior_close <= 0:
                        raise AvailabilityContractError(f"Non-positive prior adjusted close for {key} on {prior_date}")
                    index_close = prior_index_close * current_close / prior_close
                if current_close <= 0:
                    raise AvailabilityContractError(f"Non-positive adjusted close for {key} on {decision_date}")
                multiplier = index_close / current_close
                execution_row = {column: current_row[column] for column in (*IDENTITY_COLUMNS, "ticker", "date", "volume") if column in current_row}
                for column in ("open", "high", "low", "close"):
                    execution_row[column] = float(current_row[column]) * multiplier
                execution_rows.append(execution_row)
                last_marks[key] = (decision_date, index_close)

        if excluded:
            self._warnings.append(f"Excluded {excluded} instrument-session decisions with unavailable point-in-time action coverage")
        self._warnings.append("Backtest execution and trade prices use a split-neutral return index, not raw vendor quotes")
        if not execution_rows:
            return raw.head(0), pl.DataFrame()

        execution = pl.DataFrame(execution_rows).sort([*IDENTITY_COLUMNS, "date"])
        if strategy_weight_parts:
            weights = pl.concat(strategy_weight_parts, how="diagonal_relaxed")
            duplicate_weights = weights.select([*IDENTITY_COLUMNS, "date"]).is_duplicated().any()
            if duplicate_weights:
                raise InstrumentIdentityError("Strategy emitted duplicate date/instrument weights")
        else:
            weights = execution.select([*IDENTITY_COLUMNS, "date"]).with_columns(pl.lit(0.0).alias("weight"))
        return execution, weights

    def _compute_metrics(self, stats: pl.DataFrame, report: Any, equity_curve: pl.Series) -> None:
        if stats.is_empty():
            self.metrics = {}
        else:
            row = stats.row(0, named=True)
            self.metrics = {
                "total_return": _float_scalar(row.get("total_return", 0.0)),
                "cagr": _float_scalar(row.get("cagr", 0.0)),
                "max_drawdown": _float_scalar(row.get("max_drawdown", 0.0)),
                "volatility": _float_scalar(row.get("daily_vol", 0.0)),
                "sharpe_ratio": _float_scalar(row.get("daily_sharpe", 0.0)),
                "sortino_ratio": _float_scalar(row.get("daily_sortino", 0.0)),
            }

        # Headline metrics go through the ONE shared equity-curve helper
        # (backtesting.metrics) so engine-side and report-side numbers share a
        # single explicit rf/annualization convention.
        if equity_curve.len() >= 2:
            shared = equity_curve_metrics(equity_curve, self.initial_cash)
            self.metrics.update(shared)

        trades_df = report.trades
        if trades_df is not None and not trades_df.is_empty():
            pnls = trades_df["pnl"].to_list() if "pnl" in trades_df.columns else []
            if not pnls and "return" in trades_df.columns:
                pnls = trades_df["return"].to_list()
            # Positions still open at the end of the window carry a null return.
            pnls = [p for p in pnls if p is not None]
            if pnls:
                wins = sum(1 for p in pnls if p > 0)
                self.metrics["win_rate"] = wins / len(pnls) if pnls else 0.0
                self.metrics["num_trades"] = float(len(pnls))

    def _extract_trades(self, report: Any) -> list[dict[str, Any]]:
        trades_df = report.trades
        if trades_df is None or trades_df.is_empty():
            return []

        trades: list[dict[str, Any]] = []
        for row in trades_df.iter_rows(named=True):
            stock_id = row.get("stock_id", row.get("symbol", ""))
            entry_price = row.get("entry_price") or 0
            exit_price = row.get("exit_price") or 0
            position = row.get("position") or 0
            ret = row.get("return") or 0
            try:
                pnl = _float_scalar(ret) * _float_scalar(entry_price) * _float_scalar(position) if entry_price and position else 0.0
            except (TypeError, ValueError):
                pnl = 0.0

            trades.append(
                {
                    "ticker": str(self._backend_identity_map.get(str(stock_id), {}).get("symbol", stock_id)),
                    **{key: value for key, value in self._backend_identity_map.get(str(stock_id), {}).items() if key in IDENTITY_COLUMNS},
                    "entry_date": row.get("entry_date"),
                    "exit_date": row.get("exit_date"),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "shares": position,
                    "pnl": pnl,
                    "action": "SELL",
                }
            )

        return trades

    @staticmethod
    def _creturn_value_column(creturn: pl.DataFrame) -> str | None:
        """The backend's equity-value column, chosen exactly as the curve extraction does."""
        for candidate in ["creturn", "equity", "portfolio_value", "value"]:
            if candidate in creturn.columns:
                return candidate
        if creturn.width >= 2:
            return creturn.columns[-1]
        return None

    def _extract_equity_curve(self, report: Any) -> pl.Series:
        creturn = report.creturn
        if creturn is None:
            return pl.Series("equity", [], dtype=pl.Float64)

        if isinstance(creturn, pl.DataFrame):
            value_col = self._creturn_value_column(creturn)

            if value_col:
                return (creturn[value_col] * self.initial_cash).rename("equity")

        if isinstance(creturn, pl.Series):
            return (creturn * self.initial_cash).rename("equity")

        return pl.Series("equity", [], dtype=pl.Float64)

    @staticmethod
    def _extract_equity_dates(report: Any, equity_curve: pl.Series) -> list[date] | None:
        """Backend session dates for ``equity_curve``, or ``None`` when unavailable.

        The backend report's ``creturn`` frame carries a real ``date`` column next
        to the scaled value (ADR-0025 Decision 6). Dates are read, never rebuilt:
        a positional calendar is never synthesised from the series length, and a
        date count that does not match the curve yields ``None`` rather than a
        reindex. Timestamp columns are normalised to ``date``; non-temporal
        columns (e.g. strings) are treated as unavailable instead of parsed.
        """
        creturn = report.creturn
        if not isinstance(creturn, pl.DataFrame):
            return None

        value_col = VectorBacktestEngine._creturn_value_column(creturn)
        date_col: str | None = None
        for candidate in ("date", "timestamp", "time", "datetime"):
            if candidate in creturn.columns and candidate != value_col:
                date_col = candidate
                break
        if date_col is None:
            for name, dtype in creturn.schema.items():
                if name != value_col and dtype in (pl.Date, pl.Datetime, pl.Datetime("us", "UTC")):
                    date_col = name
                    break
        if date_col is None:
            return None

        dtype = creturn.schema[date_col]
        column = creturn[date_col]
        if dtype == pl.Date:
            values = column.to_list()
        elif isinstance(dtype, pl.Datetime):
            values = column.dt.date().to_list()
        else:
            return None
        if len(values) != equity_curve.len():
            logger.warning("equity_dates_length_mismatch", dates=len(values), curve=equity_curve.len())
            return None
        if any(value is None or isinstance(value, datetime) for value in values):
            return None
        return [value for value in values if isinstance(value, date)]


__all__ = ["VectorBacktestEngine", "universe_size"]
