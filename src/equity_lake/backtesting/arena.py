"""Strategy arena — run N strategies x cost regimes on a single shared data load.

Produces :class:`BacktestResult` runs plus a fixed-share equal-weight buy-and-hold
benchmark (:func:`equal_weight_buyhold`), the inputs for the Phase 1 FindingCards
(strategy / cost / benchmark axes). Reuses :class:`VectorBacktestEngine` with
``preloaded_data`` so data is loaded once and each run only re-evaluates the engine.

The meta-labeled ensemble is intentionally **not** here — it requires a trained
v2 model (Phase 2). This module is structured so a ``meta_label`` strategy can be
added to :data:`STRATEGY_REGISTRY` later without changing callers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import polars as pl
import structlog

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.engine import DEFAULT_FEE_RATIO, DEFAULT_TAX_RATIO, VectorBacktestEngine
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy import (
    BaseStrategy,
    BBMeanReversionStrategy,
    CrossSectionalMomentumStrategy,
    SMACrossoverStrategy,
)
from equity_lake.core.instruments import IDENTITY_COLUMNS

logger = structlog.get_logger(__name__)

# Benchmark construction policy (F01/A01, owner-approved §3.1). Disclosed in
# FindingCard scope so a corrected baseline is never read as strategy evidence.
BENCHMARK_POLICY = (
    "fixed-share equal-weight buy-and-hold: equal capital is allocated once at the first evaluation date "
    "(q_i = initial_cash / (N * price_i(inception))), equity(t) = sum(q_i * price_i(t)); no daily rebalancing, "
    "no survivor renormalization, no late admission, no dividend/FX assumption"
)
BENCHMARK_MISSING_MARK_POLICY = (
    "complete panel required: every scoped instrument needs a verified split-neutral mark at inception and at "
    "every date in the cohort's date union; incomplete panels are rejected, never carried stale, zero-filled, "
    "dropped or started late"
)

# Cost regimes — per-leg fee_ratio + tax_ratio. "zero" and "high" are market-agnostic;
# "realistic" is resolved per market via MARKET_COST_DEFAULTS (single-market runs).
COST_REGIMES: dict[str, dict[str, float]] = {
    "zero": {"fee_ratio": 0.0, "tax_ratio": 0.0},
    "realistic": {"fee_ratio": DEFAULT_FEE_RATIO, "tax_ratio": DEFAULT_TAX_RATIO},
    "high": {"fee_ratio": 0.005, "tax_ratio": DEFAULT_TAX_RATIO},
}

# Per-market "realistic" cost defaults (sell-side tax where the venue charges one).
# The previous blanket 0.3% sell tax is Taiwan-style and wrong for most markets here.
#   us_equity:    no securities transaction tax
#   cn_ashare:    stamp duty on sells, 0.05% since 2023-08-28 (halved from 0.1%)
#   hk_sg_equity: HK stamp duty 0.1% per side since 2023-11-17 (Singapore has none — blended)
#   jpx_equity: no sell-side securities tax in these defaults
#   krx_equity: ~0.15% sell-side securities transaction tax (KOSPI/KOSDAQ, incl. rural
#   development levy; on a legislated phase-down schedule — check current rates)
# The engine applies one fee/tax pair per run, so this is a per-venue default for
# single-market runs, not per-ticker precision — override via run_arena(market_costs=...).
# Keys are the canonical long market keys (ADR-0010).
MARKET_COST_DEFAULTS: dict[str, dict[str, float]] = {
    "us_equity": {"fee_ratio": DEFAULT_FEE_RATIO, "tax_ratio": 0.0},
    "cn_ashare": {"fee_ratio": DEFAULT_FEE_RATIO, "tax_ratio": 0.0005},
    "hk_sg_equity": {"fee_ratio": DEFAULT_FEE_RATIO, "tax_ratio": 0.001},
    "jpx_equity": {"fee_ratio": DEFAULT_FEE_RATIO, "tax_ratio": 0.0},
    "krx_equity": {"fee_ratio": DEFAULT_FEE_RATIO, "tax_ratio": 0.0015},
}

# Strategy registry — name -> zero-arg factory (strategies carry their own defaults).
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "momentum": CrossSectionalMomentumStrategy,
    "mean_reversion": BBMeanReversionStrategy,
    "trend_following": SMACrossoverStrategy,
}


# A failed (strategy, regime) pair. The reason is sanitized before it is stored:
# logs, cards and CLI output must never leak absolute paths or credentials.
_MAX_FAILURE_REASON = 200

# Sanitizer rule order (each rule sees the previous rule's output):
#   1. ``Authorization`` header -> ``authorization=<redacted>`` to the *end of the
#      message*. A plain ``key=value`` rule stops at the first space and would keep
#      the credential of ``Authorization: Bearer abc``.
#   2. A bare auth scheme (``Bearer``/``Basic``/``Digest``) with an optional separator
#      -> ``<redacted>``. A message can carry a scheme and credential without any
#      header (``Bearer abc``, ``bearer=abc``), which rule 1 does not see.
#   3. Remaining credential keys keep the narrow (single whitespace token) rule and
#      preserve the key name (``api_key=<redacted>``).
#   4. Every whitespace token carrying a path separator -- ``/`` or ``\`` (POSIX
#      absolute/relative, Windows drive/UNC, ``~/...``) -- or a drive-letter prefix
#      becomes ``<path>``, so a path split by spaces cannot leak its tail.
#   5. Repeated ``<path>`` markers and trailing separators are collapsed, so a
#      path-only message reads ``<path>`` instead of empty garbage.
_AUTHORIZATION_RE = re.compile(r"(?i)\bauthorization\b(?:\s*[=:]\s*|\s+(?=(?:bearer|basic|digest|token)\b)).*")
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(?:bearer|basic|digest)\b(?:\s*[=:]\s*|\s+)\S+")
_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b\s*[=:]\s*\S+")
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_PATH_SEPARATOR_RE = re.compile(r"[\\/]")
_REPEATED_PATH_RE = re.compile(r"<path>(?:\s*[,;:]?\s*<path>)+")
_TRAILING_PATH_SEPARATOR_RE = re.compile(r"<path>[\\/]+")


def _is_path_token(token: str) -> bool:
    """True when a whitespace token carries a path separator or a drive prefix.

    Both separators count: a backslash-only relative path such as
    ``relative\\private\\secret.txt`` and a drive path such as ``C:\\Users\\x`` are
    paths even though they contain no ``/``.
    """
    return bool(_PATH_SEPARATOR_RE.search(token)) or bool(_DRIVE_LETTER_RE.match(token))


def _sanitize_failure_reason(exc: BaseException) -> str:
    """Short, path-free, credential-free failure reason (``<= _MAX_FAILURE_REASON``).

    Rules, in order: whitespace is normalized; an authorization header consumes the
    rest of the message; a bare ``Bearer``/``Basic``/``Digest`` scheme consumes its
    credential; other credential keys keep only ``key=<redacted>``; every token
    containing a path separator (``/`` or ``\\``) or a drive prefix becomes
    ``<path>`` (repeats collapsed); the result is capped at
    :data:`_MAX_FAILURE_REASON` characters without leaving a partially cut marker.
    The exception *type* is never rewritten.
    """
    text = " ".join(str(exc).split())
    text = _AUTHORIZATION_RE.sub("authorization=<redacted>", text)
    text = _AUTH_SCHEME_RE.sub("<redacted>", text)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = " ".join("<path>" if _is_path_token(token) else token for token in text.split())
    text = _TRAILING_PATH_SEPARATOR_RE.sub("<path>", _REPEATED_PATH_RE.sub("<path>", text))
    if len(text) > _MAX_FAILURE_REASON:
        limit = _MAX_FAILURE_REASON - 3
        head = text[:limit]
        if not text[limit].isspace() and " " in head:
            # Never end with a partially cut token/marker.
            head = head.rsplit(" ", 1)[0]
        text = head.rstrip() + "..."
    return text or type(exc).__name__


def _universe_size(data: pl.DataFrame) -> int:
    """Distinct canonical identities in the shared load (``ticker`` for fixtures)."""
    if set(IDENTITY_COLUMNS).issubset(data.columns):
        return data.select(*IDENTITY_COLUMNS).unique().height
    if "ticker" in data.columns:
        return data["ticker"].n_unique()
    return 0


def _evaluation_status(
    strategy: BaseStrategy,
    result: BacktestResult,
    universe_size: int,
) -> tuple[Literal["evaluated", "no_trades", "ineligible"], tuple[str, ...]]:
    """Classify a *completed* run: completed computation is not evaluable evidence.

    ``ineligible`` when the strategy declares an integer ``min_stocks`` parameter
    that the loaded universe cannot satisfy (the default momentum/5-ticker case),
    ``no_trades`` when the strategy executed nothing, ``evaluated`` otherwise.
    """
    min_stocks = strategy.get_param("min_stocks")
    if isinstance(min_stocks, int) and not isinstance(min_stocks, bool) and universe_size < min_stocks:
        return (
            "ineligible",
            (
                f"universe {universe_size} < min_stocks {min_stocks}",
                f"{strategy.name} declares min_stocks={min_stocks}; the loaded universe is below its minimum "
                "cross-section, so it cannot take positions on this load",
            ),
        )
    if not result.trades:
        return ("no_trades", (f"{strategy.name} executed no trades on this load; the result carries no position evidence",))
    return ("evaluated", ())


@dataclass
class ArenaFailure:
    """One requested (strategy, cost regime) pair that failed, with a sanitized reason."""

    strategy: str
    cost_regime: str
    error_type: str
    reason: str


@dataclass
class ArenaRun:
    """One backtest in the arena: a single strategy under one cost regime."""

    strategy: str
    cost_regime: str
    result: BacktestResult
    #: Whether the completed run is usable as card evidence (F04). Only
    #: ``evaluated`` runs feed the strategy/cost/benchmark cards.
    evaluation_status: Literal["evaluated", "no_trades", "ineligible"] = "evaluated"
    evaluation_notes: tuple[str, ...] = ()
    #: The fee/tax pair this run's engine was **actually configured with** — the
    #: :func:`_resolve_regime_costs` result handed to
    #: :class:`VectorBacktestEngine`, recorded at the run's completion so a writer
    #: records the applied cost application instead of re-deriving one. ``None``
    #: means "not recorded" (a legacy or hand-built run): a writer must never guess
    #: a default for it, because a guessed cost application is worse than an
    #: admitted unknown (E01a fix round 3).
    applied_costs: dict[str, float] | None = None


@dataclass
class ArenaOutcome:
    """All arena runs plus the shared data, benchmark, and starting capital."""

    runs: list[ArenaRun]
    data: pl.DataFrame
    benchmark: pl.DataFrame  # columns: [date, equity]
    initial_cash: float
    #: Every requested (strategy, cost regime) pair, in request order.
    requested: list[tuple[str, str]] = field(default_factory=list)
    #: Requested pairs that raised, with a sanitized type/reason.
    failed: list[ArenaFailure] = field(default_factory=list)


def _benchmark_identity_columns(data: pl.DataFrame) -> list[str]:
    """Full ``(market, MIC, symbol)`` identity when present, else ticker-only.

    Partial canonical identity (some but not all of the three columns) is an
    error: filling the gap would be a venue guess, not a fixture convenience.
    """
    present = [column for column in IDENTITY_COLUMNS if column in data.columns]
    if not present:
        return ["ticker"]
    missing = [column for column in IDENTITY_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError(
            "Buy-and-hold benchmark input has partial canonical instrument identity: "
            f"present {present}, missing {missing}. Supply all of {list(IDENTITY_COLUMNS)} or none "
            "(a ticker-only fixture whose ticker/date rows are unique)."
        )
    return list(IDENTITY_COLUMNS)


def _identity_label(row: dict[str, Any], identity: list[str]) -> str:
    """Readable identity for error messages: ``market/MIC/symbol`` or ``ticker``."""
    return "/".join(str(row.get(column)) for column in identity)


def _require_positive_finite_cash(initial_cash: float) -> float:
    """Validate benchmark starting capital and return it as ``float``."""
    try:
        cash = float(initial_cash)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"initial_cash must be a positive finite number, got {initial_cash!r}") from exc
    if not math.isfinite(cash) or cash <= 0:
        raise ValueError(f"initial_cash must be a positive finite number, got {initial_cash!r}")
    return cash


def _validate_benchmark_marks(marks: pl.DataFrame, identity: list[str]) -> None:
    """Reject incomplete identity/date keys and unusable marks, naming the offender."""
    blank_identity = marks.filter(
        pl.any_horizontal([pl.col(column).is_null() | (pl.col(column).cast(pl.String).str.strip_chars() == "") for column in identity])
    )
    if not blank_identity.is_empty():
        row = blank_identity.row(0, named=True)
        if identity == list(IDENTITY_COLUMNS):
            raise ValueError(
                "Buy-and-hold benchmark input has partial canonical instrument identity on "
                f"{row['date']}: market={row['market']!r}, mic={row['mic']!r}, symbol={row['symbol']!r}. "
                "All three are required when identity columns are present."
            )
        raise ValueError(f"Buy-and-hold benchmark input has a missing or blank ticker on {row['date']}; every mark needs an identity.")

    if marks["date"].null_count():
        row = marks.filter(pl.col("date").is_null()).row(0, named=True)
        raise ValueError(f"Buy-and-hold benchmark input has a null date for {_identity_label(row, identity)}; every mark needs a date.")

    duplicate_keys = marks.group_by([*identity, "date"]).len().filter(pl.col("len") > 1).sort([*identity, "date"])
    if not duplicate_keys.is_empty():
        row = duplicate_keys.row(0, named=True)
        raise ValueError(
            f"Duplicate identity/date key in buy-and-hold benchmark input: {_identity_label(row, identity)} on {row['date']} "
            f"appears {row['len']} times; the benchmark needs unique identity/date rows."
        )

    invalid_marks = marks.filter(pl.col("close").is_null() | ~pl.col("close").is_finite() | (pl.col("close") <= 0))
    if not invalid_marks.is_empty():
        row = invalid_marks.row(0, named=True)
        value = row["close"]
        reason = "missing" if value is None else ("non-finite" if not math.isfinite(float(value)) else "non-positive")
        raise ValueError(
            f"Invalid buy-and-hold price mark for {_identity_label(row, identity)} on {row['date']}: close={value!r} ({reason}); "
            "the benchmark requires a finite positive mark at every evaluation date."
        )


def _require_usable_benchmark(benchmark: pl.DataFrame, data: pl.DataFrame) -> None:
    """Enforce the benchmark curve's post-conditions after construction.

    A backstop for :func:`equal_weight_buyhold`'s contract: the curve must expose
    exactly ``[date, equity]``, carry unique, ascending dates that equal the
    load's date union (``data["date"].unique().sort()``), and hold only finite
    equity. The arena must never return an outcome whose benchmark is short of
    the load's calendar, mis-dated, or non-finite, because the cards and
    ``benchmark__equity.parquet`` would then be read as evidence.

    Every failure here is an internal inconsistency in the benchmark
    construction or its wiring (not user-input error), so the messages say so
    and name the first offending, extra or missing date.
    """
    if list(benchmark.columns) != ["date", "equity"]:
        raise ValueError(
            f"Arena benchmark exposes columns {list(benchmark.columns)} instead of exactly ['date', 'equity']. "
            "The benchmark construction or its wiring is inconsistent (internal post-condition, not an input error); "
            "refusing to return the arena outcome."
        )

    expected_dates = data["date"].unique().sort() if "date" in data.columns else pl.Series("date", [], dtype=pl.Date)
    if benchmark.is_empty():
        raise ValueError(
            "Arena benchmark is empty or incomplete: "
            f"0 curve point(s) for {expected_dates.len()} date(s) in the load. "
            "The benchmark construction or its wiring is inconsistent; refusing to return an unusable benchmark."
        )

    curve_dates = benchmark["date"]
    if curve_dates.null_count():
        raise ValueError(
            "Arena benchmark contains a null date. "
            f"The benchmark construction or its wiring is inconsistent ({benchmark.height} curve point(s) "
            f"for {expected_dates.len()} date(s) in the load); refusing to return the arena outcome."
        )

    repeated = curve_dates.filter(curve_dates.is_duplicated()).unique().sort()
    if not repeated.is_empty():
        raise ValueError(
            f"Arena benchmark repeats date {repeated[0]}; benchmark dates must be unique. "
            "The benchmark construction or its wiring is inconsistent; refusing to return the arena outcome."
        )

    unordered = next((index for index in range(1, curve_dates.len()) if curve_dates[index] < curve_dates[index - 1]), None)
    if unordered is not None:
        raise ValueError(
            f"Arena benchmark date {curve_dates[unordered]} is out of ascending order after {curve_dates[unordered - 1]}. "
            "The benchmark construction or its wiring is inconsistent; refusing to return the arena outcome."
        )

    known_dates = set(expected_dates.to_list())
    extra = [value for value in curve_dates.to_list() if value not in known_dates]
    if extra:
        raise ValueError(
            f"Arena benchmark date {extra[0]} is not in the load's date union ({expected_dates.len()} date(s)). "
            "The benchmark construction or its wiring is inconsistent; refusing to return the arena outcome."
        )

    covered_dates = set(curve_dates.to_list())
    missing = [value for value in expected_dates.to_list() if value not in covered_dates]
    if missing:
        raise ValueError(
            "Arena benchmark is empty or incomplete: "
            f"missing load date {missing[0]} ({len(missing)} of {expected_dates.len()} date(s) absent, "
            f"{benchmark.height} curve point(s)). "
            "The benchmark construction or its wiring is inconsistent; refusing to return an unusable benchmark."
        )

    equity = benchmark["equity"]
    if equity.null_count() or not all(math.isfinite(value) for value in equity.to_list()):
        raise ValueError(
            "Arena benchmark contains null or non-finite equity values. "
            "The benchmark construction or its wiring is inconsistent; refusing to return the arena outcome."
        )


def equal_weight_buyhold(data: pl.DataFrame, initial_cash: float = 100_000.0) -> pl.DataFrame:
    """Fixed-share equal-weight buy-and-hold equity curve from long-format prices.

    Equal capital is allocated once at the first evaluation date to the fixed
    cohort in ``data``: ``q_i = initial_cash / (N * close_i(inception))`` and
    ``equity(t) = sum_i(q_i * close_i(t))``. There is no daily rebalancing, no
    survivor renormalization, no late admission and no dividend/FX assumption.

    ``close`` is used exactly as provided (the loader's verified split-neutral
    marks); raw prices are never substituted here.

    Identity is ``(market, MIC, symbol)`` whenever those columns exist, so the
    same ticker on two venues stays two instruments; ticker-only fixtures with
    unique ticker/date rows remain supported and partial canonical identity
    raises. Every scoped identity needs a mark at inception and at every date in
    the cohort's date union — an incomplete panel raises rather than carrying a
    stale mark, starting late or filling zero (no mixed-calendar promise).

    Returns ``[date, equity]`` sorted by date. Only a truly empty frame keeps the
    empty ``{"date": Date, "equity": Float64}`` schema. A non-empty frame without
    a ``close`` column raises an actionable :class:`ValueError` naming the missing
    column and the columns present (it is never an empty benchmark), and any other
    invalid non-empty input raises a :class:`ValueError` naming the offending
    identity/date.

    Floating point: the inception point is not bit-exactly ``initial_cash``.
    ``sum_i q_i * close_i(inception)`` is summed in floating point, so the first
    equity value can differ from ``initial_cash`` by about one ULP (observed: one
    ULP at ``initial_cash=4002996831.8095174`` is 4.77e-07 absolute, 1.19e-16
    relative). Compare the inception value with a relative tolerance, not ``==``;
    the arithmetic is deliberately not clamped to ``initial_cash`` so the curve
    stays a pure fixed-share series.
    """
    if data.is_empty():
        # A genuinely empty frame is the only case with an empty-benchmark promise.
        return pl.DataFrame(schema={"date": pl.Date, "equity": pl.Float64})
    if "close" not in data.columns:
        raise ValueError(
            "Buy-and-hold benchmark input is missing the 'close' column "
            f"(present columns: {sorted(data.columns)}). The benchmark is built from verified split-neutral "
            "close marks; a non-empty frame without 'close' is never an empty benchmark."
        )

    cash = _require_positive_finite_cash(initial_cash)
    identity = _benchmark_identity_columns(data)
    missing = [column for column in (*identity, "date") if column not in data.columns]
    if missing:
        raise ValueError(
            f"Buy-and-hold benchmark input is missing required column(s) {missing} "
            f"(expected {[*identity, 'date', 'close']}, got {sorted(data.columns)})."
        )

    marks = data.select(
        *identity,
        pl.col("date").cast(pl.Date).alias("date"),
        pl.col("close").cast(pl.Float64).alias("close"),
    ).sort([*identity, "date"])
    _validate_benchmark_marks(marks, identity)

    dates = marks["date"].unique().sort()
    inception = dates[0]
    identities = marks.select(*identity).unique().sort(identity)
    inception_marks = marks.filter(pl.col("date") == inception).select(*identity, pl.col("close").alias("inception_close"))

    late_admissions = identities.join(inception_marks.select(*identity), on=identity, how="anti").sort(identity)
    if not late_admissions.is_empty():
        row = late_admissions.row(0, named=True)
        first_mark = str(marks.filter(pl.any_horizontal([pl.col(column) == row[column] for column in identity]))["date"].min())
        raise ValueError(
            f"{_identity_label(row, identity)} has no mark at benchmark inception {inception} (first mark {first_mark}); "
            "late admission is not supported — every scoped instrument must be priced from the first evaluation date, "
            "and a mixed-calendar cohort would need an owner-approved carry/delisting policy."
        )

    expected_panel = identities.join(pl.DataFrame({"date": dates}), how="cross")
    missing_marks = expected_panel.join(marks.select(*identity, "date"), on=[*identity, "date"], how="anti").sort([*identity, "date"])
    if not missing_marks.is_empty():
        row = missing_marks.row(0, named=True)
        raise ValueError(
            f"Missing price mark for {_identity_label(row, identity)} on {row['date']}; "
            f"{missing_marks.height} identity/date mark(s) are absent from the cohort's date union. "
            "The benchmark requires a complete panel — missing marks are never carried forward, zero-filled or dropped."
        )

    curve = (
        marks.join(inception_marks, on=identity, how="left")
        .with_columns((pl.lit(cash / identities.height) / pl.col("inception_close") * pl.col("close")).alias("equity"))
        # Sorting inside the aggregate keeps the summation order independent of
        # input row order, so equal panels always yield identical equity values.
        .group_by("date")
        .agg(pl.col("equity").sort().sum())
        .sort("date")
    )
    return curve.select("date", "equity")


def _resolve_regime_costs(
    regime_name: str,
    markets: tuple[str, ...],
    market_costs: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Resolve a cost regime's fee/tax pair for the run's markets.

    "realistic" uses the venue's defaults when the run covers exactly one known
    market; multi-market or unknown-market runs fall back to the (US-style)
    engine defaults in ``COST_REGIMES`` with a log note.
    """
    base = dict(COST_REGIMES[regime_name])
    if regime_name != "realistic":
        return base
    if len(markets) == 1 and markets[0] in market_costs:
        return dict(market_costs[markets[0]])
    logger.warning(
        "arena_realistic_costs_fallback_multi_market",
        markets=list(markets),
        note="using engine default fee/tax; pass market_costs= for per-venue realism",
    )
    return base


def _loaded_identity_labels(data: pl.DataFrame) -> list[str]:
    """Readable sorted labels for the identities actually present in a load."""
    if set(IDENTITY_COLUMNS).issubset(data.columns):
        return sorted("/".join(str(value) for value in row) for row in data.select(*IDENTITY_COLUMNS).unique().iter_rows())
    if "ticker" in data.columns:
        return sorted({str(value) for value in data["ticker"].to_list() if value is not None})
    return []


def _absent_requested_instruments(requested: list[str], data: pl.DataFrame) -> list[str]:
    """Requested instruments with no loaded rows; the arena never drops them silently."""
    if not requested:
        return []
    has_identity = set(IDENTITY_COLUMNS).issubset(data.columns)
    loaded_labels = {str(value) for value in data["symbol"].to_list()} if has_identity else set()
    if "ticker" in data.columns:
        loaded_labels |= {str(value) for value in data["ticker"].to_list() if value is not None}
    return [instrument for instrument in requested if instrument not in loaded_labels]


def run_arena(
    tickers: list[str],
    start_date: date,
    end_date: date,
    *,
    markets: tuple[str, ...] = ("us_equity",),
    initial_cash: float = 100_000.0,
    strategies: list[str] | None = None,
    cost_regimes: list[str] | None = None,
    preloaded_data: pl.DataFrame | None = None,
    market_costs: dict[str, dict[str, float]] | None = None,
    lake_dir: Path | None = None,
) -> ArenaOutcome:
    """Run strategies x cost regimes on one shared data load.

    Args:
        tickers: Tickers to backtest.
        start_date/end_date: Backtest window.
        markets: Market codes forwarded to :class:`BacktestDataLoader`.
        initial_cash: Starting capital per run (and the benchmark scale).
        strategies: Subset of :data:`STRATEGY_REGISTRY` keys; default all. A
            repeated name is not an error: duplicates are collapsed to one
            requested attempt, keeping the first-occurrence order.
        cost_regimes: Subset of :data:`COST_REGIMES` keys; default all. Repeated
            names collapse the same way, so ``cost_regimes=["zero", "zero"]``
            requests one regime. The ``realistic`` regime resolves per-market
            sell-tax defaults (:data:`MARKET_COST_DEFAULTS`) on single-market runs.
        preloaded_data: Pre-loaded long OHLCV frame; skips the loader if given.
        market_costs: Optional per-market overrides for the ``realistic`` regime
            (``{"us_equity": {"fee_ratio": ..., "tax_ratio": ...}, ...}``); defaults to
            :data:`MARKET_COST_DEFAULTS`.
        lake_dir: Lake root to read prices and action coverage from; defaults to
            the canonical lake. Set this to backtest an auxiliary lake (e.g. the
            sample lake written by ``equity demo seed``).

    Returns:
        :class:`ArenaOutcome` with one :class:`ArenaRun` per (strategy, regime)
        that completed, plus every requested pair in ``requested`` (deduplicated,
        first-occurrence order — a duplicate selection is one attempt, not an
        error) and every pair
        that raised in ``failed`` (sanitized ``error_type``/``reason``). A run that
        raises is logged and recorded rather than aborting the whole arena, so one
        failed strategy/regime never prevents the other runs from executing. Each
        completed run carries an ``evaluation_status``: ``ineligible`` when the
        strategy declares an integer ``min_stocks`` the loaded universe cannot
        satisfy, ``no_trades`` when it executed nothing, ``evaluated`` otherwise.
        Only ``evaluated`` runs are admitted as FindingCard evidence. Each completed
        run also carries ``applied_costs``: the fee/tax pair its engine was actually
        configured with (the resolved regime, including any ``market_costs``
        override), so a writer records what was applied rather than a default.

    Raises:
        ValueError: when the load is empty, when a requested instrument is
            absent from the load (never silently dropped), or when the shared
            frame cannot form a fixed-share benchmark cohort (partial identity,
            duplicate identity/date, incomplete mark panel, invalid mark or
            non-positive ``initial_cash``).
    """
    data = (
        preloaded_data
        if preloaded_data is not None
        else BacktestDataLoader(lake_dir=lake_dir).load(tickers=tickers, start_date=start_date, end_date=end_date, markets=list(markets))
    )
    if data.is_empty():
        raise ValueError(
            "Arena has no data to backtest (empty load). "
            f"No Bronze price rows matched {', '.join(markets)} in the requested window. "
            "Ingest the window first (``equity ingest``) or point at an auxiliary lake with ``--lake`` "
            "(e.g. ``equity demo seed`` then ``--lake data/sample``)."
        )

    absent = _absent_requested_instruments(tickers, data)
    if absent:
        raise ValueError(
            f"Arena cohort mismatch: requested instrument(s) {absent} have no loaded rows for "
            f"{', '.join(markets)} in {start_date}..{end_date}. Loaded identities: {_loaded_identity_labels(data)}. "
            "An absent requested instrument is never silently dropped from the benchmark; check --tickers/--markets/the "
            "window or ingest the missing data."
        )

    # Built before the strategy sweep: an unusable benchmark cohort must fail
    # fast instead of after every engine run, and the arena and its cards must
    # consume this one corrected curve rather than reconstructing a benchmark.
    benchmark = equal_weight_buyhold(data, initial_cash=initial_cash)
    _require_usable_benchmark(benchmark, data)

    strat_names = strategies or list(STRATEGY_REGISTRY)
    regime_names = cost_regimes or list(COST_REGIMES)
    resolved_market_costs = market_costs if market_costs is not None else MARKET_COST_DEFAULTS

    unknown_strats = set(strat_names) - set(STRATEGY_REGISTRY)
    if unknown_strats:
        raise ValueError(f"Unknown strategies: {sorted(unknown_strats)}. Available: {list(STRATEGY_REGISTRY)}")
    unknown_regimes = set(regime_names) - set(COST_REGIMES)
    if unknown_regimes:
        raise ValueError(f"Unknown cost regimes: {sorted(unknown_regimes)}. Available: {list(COST_REGIMES)}")

    runs: list[ArenaRun] = []
    failed: list[ArenaFailure] = []
    # Duplicate strategy/regime selections collapse to one requested attempt
    # (first-occurrence order) so the request cannot claim more pairs than were
    # actually attempted — ``--strategies momentum,momentum`` is one run, not two.
    requested_pairs: list[tuple[str, str]] = list(
        dict.fromkeys((strat_name, regime_name) for strat_name in strat_names for regime_name in regime_names)
    )
    universe_size = _universe_size(data)
    for strat_name, regime_name in requested_pairs:
        strategy_cls = STRATEGY_REGISTRY[strat_name]
        config = _resolve_regime_costs(regime_name, markets, resolved_market_costs)
        try:
            # Strategy *and* engine construction sit inside the isolation boundary: a
            # constructor that raises is one failed pair, not an arena abort, so the
            # remaining pairs are still attempted and the failure is recorded.
            strategy = strategy_cls()
            engine = VectorBacktestEngine(
                strategy=strategy,
                tickers=tickers,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                markets=list(markets),
                config=config,
                preloaded_data=data,
            )
            result = engine.run()
        except Exception as exc:  # noqa: BLE001 — one failing run must not kill the arena
            failure = ArenaFailure(
                strategy=strat_name,
                cost_regime=regime_name,
                error_type=type(exc).__name__,
                reason=_sanitize_failure_reason(exc),
            )
            failed.append(failure)
            logger.warning(
                "arena_run_failed",
                strategy=strat_name,
                cost_regime=regime_name,
                error_type=failure.error_type,
                reason=failure.reason,
            )
            continue
        evaluation_status, evaluation_notes = _evaluation_status(strategy, result, universe_size)
        runs.append(
            ArenaRun(
                strategy=strat_name,
                cost_regime=regime_name,
                result=result,
                evaluation_status=evaluation_status,
                evaluation_notes=evaluation_notes,
                # The same resolved pair the engine was built with (one source of
                # truth, no re-derivation): the copy keeps the record independent of
                # any later mutation of the config the engine holds.
                applied_costs=dict(config),
            )
        )
        logger.info(
            "arena_run_completed",
            strategy=strat_name,
            cost_regime=regime_name,
            sharpe=round(result.sharpe_ratio, 3),
            total_return=round(result.total_return, 4),
            evaluation_status=evaluation_status,
        )

    return ArenaOutcome(
        runs=runs,
        data=data,
        benchmark=benchmark,
        initial_cash=initial_cash,
        requested=requested_pairs,
        failed=failed,
    )


__all__ = [
    "BENCHMARK_MISSING_MARK_POLICY",
    "BENCHMARK_POLICY",
    "COST_REGIMES",
    "MARKET_COST_DEFAULTS",
    "STRATEGY_REGISTRY",
    "ArenaFailure",
    "ArenaOutcome",
    "ArenaRun",
    "equal_weight_buyhold",
    "run_arena",
]
