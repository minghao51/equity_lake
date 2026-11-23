"""R01 scale harness: 5/50/200 instruments over the three-year design, measured and bounded.

Residual stage RS-R01 (residual plan §5) requires measured computational scale — five, fifty
and two hundred instruments over a three-year design, with warmup and minimum-universe
disclosure, wall time, peak RSS, I/O counters, decision counts and output parity — and it
requires those measurements to be *bounded*: 30 minutes per scale size, 2 hours aggregate for
controls plus scale, and a peak RSS ceiling of ``min(8 GiB, 50% of host RAM)``.

The contract:

* **Frozen first** — :func:`run_scale` refuses unless the R01 protocol *and* its
  prespecification are already frozen (R00 :func:`require_frozen_protocol`), and it refuses a
  size beyond the frozen ``resource_budget.max_symbols``.
* **Ceilings refuse, never widen** — an exceeded per-size, aggregate or RSS ceiling raises
  :class:`ScaleError`; the run records the point and the remaining points as ``censored`` with
  the exact reason, and a censored point can never be read as a passing scale claim. When peak
  RSS cannot be monitored the run aborts instead of reporting a guess.
* **I/O is measured or reasoned, never invented** — the counters come from ``/proc/self/io``;
  a host that does not expose them yields ``available: false`` *with a reason*, never zeros.
* **Per-point isolation** — each point runs in its own child process, so peak RSS and wall time
  are attributable to that point alone rather than to a process high-water mark that only grows.
* **Parity is named** — the fixture for a shared instrument is byte-identical across sizes and
  its model-free decisions agree, which is what "output parity" means here. A *model-based*
  decision legitimately differs between sizes because the pooled fit differs; that is disclosed,
  not claimed as parity.
* **Scale is not capacity** — a measured point says what the harness cost on this host, never
  what a strategy could absorb (spec §7), and it is not economic evidence of any kind.

No shipped ticker pool holds 200 instruments (``us_equity`` 44, ``cn_ashare`` 36,
``hk_sg_equity`` 25), so the 5/50/200 fixtures are built from
:func:`~equity_lake.devtools.seeding.synthetic_ohlcv` with the ``MARKET_CONFIGS`` profile: the
shipped pools are used in order for the first 105 instruments and the remainder are
deterministic ``SCALE####`` ids on the ``us_equity`` profile. The fixture is a *computational*
scale design, not market data, and it carries a representative venue per market purely so the
identity columns are complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import structlog

from equity_lake.devtools.seeding import TEST_DATA_TICKERS, business_days, synthetic_ohlcv
from equity_lake.devtools.test_data import MARKET_CONFIGS, market_profile
from equity_lake.ml.experiment import DECISION_THRESHOLD, HOLDING_SESSIONS, ROUND_TRIP_COST, _economic_summary, _strategy_returns
from equity_lake.ml.r01_controls import (
    AGGREGATE_CEILING_SECONDS,
    CONTROL_FEATURES,
    PER_SIZE_CEILING_SECONDS,
    RSS_ABSOLUTE_CEILING_BYTES,
    RSS_HOST_FRACTION,
    SCALE_END,
    SCALE_SIZES,
    SCALE_START,
    SCALE_TREND,
    SCALE_VOLATILITY,
    SCALE_WARMUP_SESSIONS,
    TRAIN_FRACTION,
    Budget,
    R01Error,
    control_features,
    fit_control_weights,
    predict_probabilities,
    require_frozen_r01,
)

logger = structlog.get_logger(__name__)

#: The scale fixture's generator seed (a fixture identity, not a market seed).
SCALE_SEED = 20260602
#: A representative venue per market so identity columns are complete; no venue claim is made.
MARKET_MICS: dict[str, str] = {"us_equity": "XNAS", "cn_ashare": "XSHG", "hk_sg_equity": "XHKG"}
#: Minimum instruments a decision session must admit before the point counts as measured.
MINIMUM_UNIVERSE = 5
_IDENTITY_KEYS: list[str] = ["market", "mic", "symbol"]


class ScaleError(ValueError):
    """A scale point refused: unfrozen protocol, over-budget size, or an unmonitorable ceiling."""


@dataclass(frozen=True)
class ScaleCeilings:
    """The frozen single-host ceilings (§2.12)."""

    per_size_seconds: float = PER_SIZE_CEILING_SECONDS
    aggregate_seconds: float = AGGREGATE_CEILING_SECONDS
    rss_absolute_bytes: int = RSS_ABSOLUTE_CEILING_BYTES
    rss_host_fraction: float = RSS_HOST_FRACTION

    def rss_bytes(self, host_ram_bytes: int) -> int:
        """``min(8 GiB, 50% of host RAM)`` — the frozen peak-RSS ceiling."""
        if host_ram_bytes <= 0:
            raise ScaleError("host RAM cannot be measured, so the peak-RSS ceiling cannot be established; the run aborts")
        return min(self.rss_absolute_bytes, int(host_ram_bytes * self.rss_host_fraction))


# ---------------------------------------------------------------------------
# Host measurement (measured, or refused with a reason — never invented)
# ---------------------------------------------------------------------------


def host_ram_bytes() -> int:
    """Total host RAM; raises when the host does not report it."""
    memory = Path("/proc/meminfo")
    if memory.exists():
        for line in memory.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(str(line.split()[1])) * 1024
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError) as exc:
        raise ScaleError(f"host RAM cannot be measured ({exc}); the peak-RSS ceiling cannot be established, so the run aborts") from exc
    return int(pages) * int(page_size)


def peak_rss_bytes() -> int:
    """Peak resident set size of this process; raises when it cannot be monitored."""
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(str(line.split()[1])) * 1024
    try:
        usage = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ValueError, OSError) as exc:  # pragma: no cover - platform dependent
        raise ScaleError(f"peak RSS cannot be monitored on this host ({exc}); the run aborts rather than guessing") from exc
    return int(usage) * 1024


def io_counters() -> dict[str, Any]:
    """Process I/O counters, or a reason when the host does not expose them."""
    path = Path("/proc/self/io")
    if not path.exists():
        return {"available": False, "reason": "the host exposes no /proc/self/io, so I/O counters are unavailable (never reported as zero)"}
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, raw = line.partition(":")
        if key in ("read_bytes", "write_bytes", "rchar", "wchar"):
            try:
                values[key] = int(raw.strip())
            except ValueError:
                continue
    missing = [key for key in ("read_bytes", "write_bytes") if key not in values]
    if missing:
        return {"available": False, "reason": f"/proc/self/io lacks {missing}; unavailable, never reported as zero"}
    return {"available": True, **{key: values[key] for key in ("read_bytes", "write_bytes", "rchar", "wchar")}}


# ---------------------------------------------------------------------------
# The three-year scale fixture
# ---------------------------------------------------------------------------


def three_year_sessions() -> list[date]:
    """The frozen three-year design: Mon-Fri sessions from 2022-01-03 to 2024-12-31."""
    return business_days(SCALE_START, SCALE_END)


def shipped_pool() -> list[tuple[str, str]]:
    """The shipped ticker pools in a fixed order (44 + 36 + 25 = 105 instruments)."""
    return [(market, ticker) for market in MARKET_CONFIGS for ticker in TEST_DATA_TICKERS[market]]


def scale_universe(size: int) -> list[tuple[str, str]]:
    """The instruments of one scale point: shipped pools first, then derived fixture ids."""
    if size <= 0:
        raise ScaleError("a scale point needs at least one instrument")
    pool = shipped_pool()
    if size <= len(pool):
        return pool[:size]
    derived = [("us_equity", f"SCALE{index:04d}") for index in range(1, size - len(pool) + 1)]
    return pool + derived


def scale_panel(size: int, *, sessions: Sequence[date] | None = None) -> pl.DataFrame:
    """One scale point's OHLCV fixture, generated by the shipped synthetic generator.

    Instruments are generated market by market with a fixed per-market seed and in a fixed
    order, so the fixture for any instrument is identical across scale sizes — which is what
    makes the parity check meaningful.
    """
    days = list(sessions) if sessions is not None else three_year_sessions()
    universe = scale_universe(size)
    frames: list[pl.DataFrame] = []
    for order, market in enumerate(MARKET_CONFIGS):
        tickers = [ticker for group_market, ticker in universe if group_market == market]
        if not tickers:
            continue
        profile = market_profile(market, SCALE_VOLATILITY, SCALE_TREND)
        frame = synthetic_ohlcv(tickers, days, profile=profile, seed=SCALE_SEED + order)
        frames.append(
            frame.select("ticker", "date", "open", "close", "volume").with_columns(
                pl.lit(market).alias("market"),
                pl.lit(MARKET_MICS[market]).alias("mic"),
                pl.col("ticker").alias("symbol"),
            )
        )
    panel = pl.concat(frames).select(*_IDENTITY_KEYS, "date", "open", "close", "volume")
    return panel.sort([*_IDENTITY_KEYS, "date"])


# ---------------------------------------------------------------------------
# The measured pipeline
# ---------------------------------------------------------------------------


def vectorised_strategy_returns(
    panel: pl.DataFrame,
    decisions: pl.DataFrame,
    *,
    sessions: Sequence[date],
    cost: float = ROUND_TRIP_COST,
    holding: int = HOLDING_SESSIONS,
) -> pl.DataFrame:
    """Per-session net returns of the long/flat policy, vectorised over the whole panel.

    The batch-size-independent equivalent of
    :func:`~equity_lake.ml.experiment._strategy_returns` (which filters per session and is
    unusable at 200 instruments x 782 sessions). It reproduces that path's semantics
    exactly: signal dates are grouped into non-overlapping blocks of ``holding`` *signal*
    dates, a block's own first-date picks are held for the whole block, a session's return is
    the equal weight of its held names' close-to-close return to the *next evaluation
    session*, and the round-trip cost is charged once on the block's first session.
    :func:`economics_parity` proves the two agree, and the estimand is the same mean
    per-session net return.
    """
    ordered_sessions = list(sessions)
    successor = {ordered_sessions[index]: ordered_sessions[index + 1] for index in range(len(ordered_sessions) - 1)}
    picks = decisions.filter(pl.col("decision") == 1).select(*_IDENTITY_KEYS, "date")
    signal_dates = [session for session in picks["date"].unique().sort().to_list() if session in successor]
    empty = pl.DataFrame({"date": pl.Series([], dtype=pl.Date), "net_return": pl.Series([], dtype=pl.Float64), "held": pl.Series([], dtype=pl.Int64)})
    if not signal_dates:
        return empty
    block_start = {session: signal_dates[(index // holding) * holding] for index, session in enumerate(signal_dates)}
    blocks = pl.DataFrame({"date": pl.Series(signal_dates, dtype=pl.Date)}).with_columns(
        pl.Series("block_start", [block_start[session] for session in signal_dates], dtype=pl.Date),
        pl.Series("later", [successor[session] for session in signal_dates], dtype=pl.Date),
    )
    holdings = blocks.join(picks.rename({"date": "block_start"}), on="block_start", how="inner")
    prices = panel.select(*_IDENTITY_KEYS, "date", "close")
    realised = (
        holdings.join(prices.rename({"close": "close_now"}), on=[*_IDENTITY_KEYS, "date"], how="inner")
        .join(prices.rename({"date": "later", "close": "close_later"}), on=[*_IDENTITY_KEYS, "later"], how="inner")
        .filter((pl.col("close_now") > 0.0) & pl.col("close_later").is_not_null())
        .with_columns((pl.col("close_later") / pl.col("close_now") - 1.0).alias("realised"))
        .group_by("date")
        .agg(pl.col("realised").mean().alias("gross"), pl.len().alias("held"))
    )
    combined = blocks.select("date", "block_start").join(realised, on="date", how="left").sort("date")
    return (
        combined.with_columns(
            pl.col("gross").fill_null(0.0),
            pl.col("held").fill_null(0).cast(pl.Int64),
        )
        .with_columns(
            (pl.col("gross") - pl.when(pl.col("date") == pl.col("block_start")).then(cost).otherwise(0.0)).alias("net_return"),
        )
        .select("date", "net_return", "held")
    )


def _digest(frame: pl.DataFrame, columns: Sequence[str]) -> str:
    payload = json.dumps(frame.select(*columns).sort(list(columns)).to_dicts(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def smallest_universe(decisions: pl.DataFrame) -> int:
    """The smallest number of instruments held on any decision session."""
    held = decisions.group_by("date").agg(pl.len().alias("held"))["held"].min()
    return int(held) if isinstance(held, (int, float)) else 0


def scale_pipeline(
    panel: pl.DataFrame,
    *,
    train_fraction: float = TRAIN_FRACTION,
    threshold: float = DECISION_THRESHOLD,
    minimum_universe: int = MINIMUM_UNIVERSE,
    warmup_sessions: int = SCALE_WARMUP_SESSIONS,
) -> dict[str, Any]:
    """Generate features, fit once on the training window, decide, and measure the batch.

    The design discloses what the plan requires: the warmup sessions excluded before any
    decision, the minimum universe a decision session must hold, the decision counts and the
    per-session net returns whose mean is the recorded statistic.
    """
    sessions = sorted(set(panel["date"].to_list()))
    featured = control_features(panel)
    complete = pl.all_horizontal([pl.col(column).is_not_null() for column in CONTROL_FEATURES])
    usable = featured.filter(complete & pl.col("label").is_not_null()).sort([*_IDENTITY_KEYS, "date"])
    decision_sessions = sessions[warmup_sessions:]
    if len(decision_sessions) < 2:
        raise ScaleError(f"a scale point needs at least two decision sessions after {warmup_sessions} warmup sessions")
    decided = usable.filter(pl.col("date").is_in(decision_sessions))
    train_end = decision_sessions[max(1, int(len(decision_sessions) * train_fraction)) - 1]
    sample = decided.filter(pl.col("date") <= pl.lit(train_end))
    if sample["symbol"].n_unique() < minimum_universe:
        raise ScaleError(f"the training window holds {sample['symbol'].n_unique()} instruments, below the minimum universe {minimum_universe}")
    weights = fit_control_weights(sample)
    evaluation = decided.filter(pl.col("date") > pl.lit(train_end))
    probabilities = predict_probabilities(weights, evaluation)
    decisions = evaluation.select(*_IDENTITY_KEYS, "date").with_columns(
        pl.Series("probability", probabilities, dtype=pl.Float64),
        pl.Series("decision", (probabilities >= threshold).astype(np.int8), dtype=pl.Int8),
        pl.Series("technical", (evaluation["roc_20"].to_numpy() > 0.0).astype(np.int8), dtype=pl.Int8),
    )
    evaluation_sessions = sorted(set(decisions["date"].to_list()))
    returns = vectorised_strategy_returns(panel, decisions, sessions=evaluation_sessions)
    economics = _economic_summary(returns)
    return {
        "instruments": panel["symbol"].n_unique(),
        "sessions": len(sessions),
        "warmup_sessions": warmup_sessions,
        "decision_sessions": len(evaluation_sessions),
        "minimum_universe": minimum_universe,
        "minimum_universe_observed": smallest_universe(decisions),
        "feature_rows": usable.height,
        "long_decisions": int(decisions["decision"].sum()),
        "technical_long_decisions": int(decisions["technical"].sum()),
        "mean_per_session_net_return": float(economics["mean_daily_return"]),
        "compounded_net_return": float(economics["net_return"]),
        "sharpe_ratio": float(economics["sharpe_ratio"]),
        "producing_sessions": int(returns.height),
        "estimand": "mean_per_session_net_return (not the compounded net return)",
        "decisions": decisions,
        "returns": returns,
    }


def economics_parity(panel: pl.DataFrame, decisions: pl.DataFrame) -> dict[str, Any]:
    """Prove the vectorised economics equals R02's per-session reference path."""
    sessions = sorted(set(decisions["date"].to_list()))
    vectorised = vectorised_strategy_returns(panel, decisions, sessions=sessions)
    picks: dict[date, list[tuple[str, str, str]]] = {}
    for row in decisions.filter(pl.col("decision") == 1).select(*_IDENTITY_KEYS, "date").to_dicts():
        picks.setdefault(row["date"], []).append((str(row["market"]), str(row["mic"]), str(row["symbol"])))
    closes: dict[tuple[str, str, str], dict[date, float]] = {}
    for row in panel.select(*_IDENTITY_KEYS, "date", "close").to_dicts():
        closes.setdefault((str(row["market"]), str(row["mic"]), str(row["symbol"])), {})[row["date"]] = float(row["close"])
    reference = _strategy_returns(picks=picks, sessions=sessions, closes=closes, cost=ROUND_TRIP_COST, holding=HOLDING_SESSIONS)
    left = _economic_summary(vectorised)
    right = _economic_summary(reference)
    return {
        "vectorised_mean_per_session_net_return": float(left["mean_daily_return"]),
        "reference_mean_per_session_net_return": float(right["mean_daily_return"]),
        "absolute_difference": abs(float(left["mean_daily_return"]) - float(right["mean_daily_return"])),
        "equal_within_1e12": abs(float(left["mean_daily_return"]) - float(right["mean_daily_return"])) <= 1e-12,
    }


def feature_parity(panel: pl.DataFrame, *, instruments: Sequence[str]) -> dict[str, str]:
    """Digests of one scale point's fixture and model-free decisions for shared instruments."""
    featured = control_features(panel.filter(pl.col("symbol").is_in(list(instruments))))
    complete = pl.all_horizontal([pl.col(column).is_not_null() for column in CONTROL_FEATURES])
    rows = (
        featured.filter(complete)
        .sort([*_IDENTITY_KEYS, "date"])
        .with_columns(
            (pl.col("roc_20") > 0.0).cast(pl.Int8).alias("technical"),
        )
    )
    return {
        "features": _digest(rows.select(*_IDENTITY_KEYS, "date", *CONTROL_FEATURES), [*_IDENTITY_KEYS, "date"]),
        "technical_decisions": _digest(rows.select(*_IDENTITY_KEYS, "date", "technical"), [*_IDENTITY_KEYS, "date"]),
    }


# ---------------------------------------------------------------------------
# One scale point, measured under the ceilings
# ---------------------------------------------------------------------------


def _enforce_ceilings(
    *,
    size: int,
    elapsed: float,
    peak_rss: int,
    ceilings: ScaleCeilings,
    aggregate_elapsed: float,
    host_ram: int,
) -> None:
    if elapsed > ceilings.per_size_seconds:
        raise ScaleError(
            f"scale point {size} took {elapsed:.1f}s, beyond the frozen per-size ceiling {ceilings.per_size_seconds:.0f}s; "
            "an over-budget point is censored, never widened"
        )
    rss_ceiling = ceilings.rss_bytes(host_ram)
    if peak_rss > rss_ceiling:
        raise ScaleError(f"scale point {size} peaked at {peak_rss} bytes of RSS, beyond the frozen ceiling {rss_ceiling} bytes; the run aborts")
    if aggregate_elapsed > ceilings.aggregate_seconds:
        raise ScaleError(
            f"controls plus scale took {aggregate_elapsed:.1f}s, beyond the frozen aggregate ceiling {ceilings.aggregate_seconds:.0f}s; "
            "the remaining points are censored, never silently continued"
        )


def parity_instruments() -> list[str]:
    """The five-instrument reference set every scale point's parity digest is taken over."""
    return [symbol for _market, symbol in scale_universe(SCALE_SIZES[0])]


def run_scale_point(
    size: int,
    *,
    ceilings: ScaleCeilings | None = None,
    sessions: Sequence[date] | None = None,
    aggregate_elapsed: float = 0.0,
    peak_rss_reader: Callable[[], int] | None = None,
    host_ram_reader: Callable[[], int] | None = None,
    io_reader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Measure one scale point in this process, refusing when a frozen ceiling is exceeded."""
    frozen_ceilings = ceilings if ceilings is not None else ScaleCeilings()
    rss_of = peak_rss_reader if peak_rss_reader is not None else peak_rss_bytes
    ram_of = host_ram_reader if host_ram_reader is not None else host_ram_bytes
    io_of = io_reader if io_reader is not None else io_counters
    host_ram = ram_of()
    frozen_ceilings.rss_bytes(host_ram)  # an unmeasurable host RAM aborts before any work
    before = io_of()
    started = time.monotonic()
    panel = scale_panel(size, sessions=sessions)
    measured = scale_pipeline(panel)
    elapsed = time.monotonic() - started
    after = io_of()
    peak_rss = rss_of()
    _enforce_ceilings(
        size=size, elapsed=elapsed, peak_rss=peak_rss, ceilings=frozen_ceilings, aggregate_elapsed=aggregate_elapsed + elapsed, host_ram=host_ram
    )
    record: dict[str, Any] = {
        "size": size,
        "wall_seconds": elapsed,
        "peak_rss_bytes": peak_rss,
        "rss_ceiling_bytes": frozen_ceilings.rss_bytes(host_ram),
        "host_ram_bytes": host_ram,
        "io": _io_delta(before, after),
        "instruments": measured["instruments"],
        "sessions": measured["sessions"],
        "warmup_sessions": measured["warmup_sessions"],
        "decision_sessions": measured["decision_sessions"],
        "minimum_universe": measured["minimum_universe"],
        "minimum_universe_observed": measured["minimum_universe_observed"],
        "feature_rows": measured["feature_rows"],
        "long_decisions": measured["long_decisions"],
        "technical_long_decisions": measured["technical_long_decisions"],
        "mean_per_session_net_return": measured["mean_per_session_net_return"],
        "compounded_net_return": measured["compounded_net_return"],
        "estimand": measured["estimand"],
        "parity": feature_parity(panel, instruments=parity_instruments()),
        "censored": False,
    }
    if size == SCALE_SIZES[0]:
        record["economics_parity"] = economics_parity(panel, measured["decisions"])
    return record


def _io_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """I/O counters for one point, or the unchanged reason when the host withholds them."""
    if not before.get("available") or not after.get("available"):
        return {"available": False, "reason": after.get("reason") or before.get("reason") or "I/O counters unavailable"}
    return {
        "available": True,
        "read_bytes": int(after["read_bytes"]) - int(before["read_bytes"]),
        "write_bytes": int(after["write_bytes"]) - int(before["write_bytes"]),
        "rchar": int(after["rchar"]) - int(before["rchar"]),
        "wchar": int(after["wchar"]) - int(before["wchar"]),
    }


_POINT_PREFIX = "R01-SCALE-POINT "


def measure_scale_point_in_subprocess(size: int, *, timeout_seconds: float) -> dict[str, Any]:
    """Measure one point in a child process, so its peak RSS belongs to that point alone."""
    completed = subprocess.run(
        [sys.executable, "-m", "equity_lake.ml.r01_scale", "--single-point", str(size)],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_POINT_PREFIX):
            parsed: dict[str, Any] = json.loads(line[len(_POINT_PREFIX) :])
            return parsed
    raise ScaleError(
        f"the scale child for size {size} produced no measurement (exit {completed.returncode}): "
        f"{(completed.stderr or completed.stdout).strip().splitlines()[-1:] or ['no output']}"
    )


# ---------------------------------------------------------------------------
# The whole scale sweep
# ---------------------------------------------------------------------------


def run_scale(
    *,
    protocol_root: Path | None = None,
    budget: Budget | None = None,
    sizes: Sequence[int] | None = None,
    ceilings: ScaleCeilings | None = None,
    subprocess_isolation: bool = True,
    in_process: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Measure the requested scale sizes under the frozen ceilings and record every refusal.

    The run refuses an unfrozen protocol and an over-budget size outright. A ceiling breach on
    a point stops the sweep and records that point and every later point as ``censored``: the
    sweep never continues past a ceiling and never widens one.
    """
    frozen, protocol, _spec = require_frozen_r01(protocol_root=protocol_root)
    frozen_ceilings = ceilings if ceilings is not None else ScaleCeilings()
    requested = list(sizes) if sizes is not None else list(SCALE_SIZES)
    maximum = protocol.resource_budget.max_symbols
    over = [size for size in requested if size > maximum]
    if over:
        raise ScaleError(f"scale sizes {over} exceed the frozen resource budget of {maximum} instruments; a run never widens its own budget")
    allocation = budget if budget is not None else Budget(frozen_ceilings.aggregate_seconds)
    started = time.monotonic()
    points: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    for index, size in enumerate(requested):
        remaining = frozen_ceilings.aggregate_seconds - allocation.spent_seconds
        if remaining <= 0.0:
            for skipped in requested[index:]:
                censored.append(
                    {
                        "size": skipped,
                        "reason": (
                            f"the frozen aggregate ceiling {frozen_ceilings.aggregate_seconds:.0f}s was already spent before this point started"
                        ),
                    }
                )
            break
        point_started = time.monotonic()
        try:
            if subprocess_isolation:
                point = measure_scale_point_in_subprocess(size, timeout_seconds=frozen_ceilings.per_size_seconds)
            else:
                measure = in_process if in_process is not None else run_scale_point
                point = measure(size, ceilings=frozen_ceilings, aggregate_elapsed=allocation.spent_seconds)
            _enforce_ceilings(
                size=size,
                elapsed=float(point["wall_seconds"]),
                peak_rss=int(point["peak_rss_bytes"]),
                ceilings=frozen_ceilings,
                aggregate_elapsed=allocation.spent_seconds + float(point["wall_seconds"]),
                host_ram=int(point["host_ram_bytes"]),
            )
        except (ScaleError, subprocess.TimeoutExpired) as exc:
            reason = str(exc) or "scale point timed out"
            censored.append({"size": size, "reason": reason})
            for skipped in requested[index + 1 :]:
                censored.append({"size": skipped, "reason": f"not attempted after the {size}-instrument refusal: {reason}"})
            break
        allocation.spend(time.monotonic() - point_started)
        points.append(point)
    parity: dict[str, Any] = {}
    if points:
        reference = points[0]
        parity = {
            "reference_size": reference["size"],
            "shared_instrument_features_equal": True,
            "shared_instrument_decisions_equal": True,
            "details": [],
        }
        for point in points[1:]:
            shared_features = point["parity"]["features"] == reference["parity"]["features"]
            shared_decisions = point["parity"]["technical_decisions"] == reference["parity"]["technical_decisions"]
            parity["shared_instrument_features_equal"] = bool(parity["shared_instrument_features_equal"] and shared_features)
            parity["shared_instrument_decisions_equal"] = bool(parity["shared_instrument_decisions_equal"] and shared_decisions)
            parity["details"] = [
                *parity["details"],
                {"size": point["size"], "features_equal": shared_features, "technical_decisions_equal": shared_decisions},
            ]
        parity["model_based_decisions_disclosure"] = (
            "model-based decisions are not required to agree across sizes: the pooled fit differs by construction, "
            "so only the fixture and the model-free technical rule are compared"
        )
    complete = len(points) == len(requested)
    status = "verified" if complete and not censored else "censored"
    return {
        "protocol": {"experiment": frozen.protocol.experiment, "digest": frozen.protocol_digest},
        "ceilings": {
            "per_size_seconds": frozen_ceilings.per_size_seconds,
            "aggregate_seconds": frozen_ceilings.aggregate_seconds,
            "rss_rule": "min(8 GiB, 50% of host RAM)",
            "rss_absolute_bytes": frozen_ceilings.rss_absolute_bytes,
        },
        "three_year_design": {"start": SCALE_START.isoformat(), "end": SCALE_END.isoformat(), "sessions": len(three_year_sessions())},
        "universe_provenance": (
            "shipped ticker pools in order (us_equity 44, cn_ashare 36, hk_sg_equity 25 = 105) then deterministic SCALE#### ids; "
            "no shipped pool reaches 200 instruments and the fixture is computational scale only, not market data"
        ),
        "requested_sizes": requested,
        "points": points,
        "censored": censored,
        "parity": parity,
        "seconds": time.monotonic() - started,
        "budget_spent_seconds": allocation.spent_seconds,
        "verification_status": status,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m equity_lake.ml.r01_scale``: one measured point, or the frozen sweep."""
    import argparse

    parser = argparse.ArgumentParser(description="R01 scale harness (measured, ceiling-bounded)")
    parser.add_argument("--single-point", type=int, default=None, help="measure one size in this process and print its record")
    parser.add_argument("--protocol-root", default=None)
    parser.add_argument("--results", default=None)
    parser.add_argument("--sizes", default=None, help="comma separated scale sizes (default: the frozen set)")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.single_point is not None:
        try:
            record = run_scale_point(arguments.single_point)
        except (ScaleError, R01Error) as exc:
            print(f"scale point refused: {exc}", file=sys.stderr)
            return 2
        print(_POINT_PREFIX + json.dumps(record, sort_keys=True, default=str))
        return 0
    sizes = [int(part) for part in arguments.sizes.split(",")] if arguments.sizes else None
    payload = run_scale(protocol_root=Path(arguments.protocol_root) if arguments.protocol_root else None, sizes=sizes)
    if arguments.results:
        target = Path(arguments.results)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verification_status": payload["verification_status"], "censored": payload["censored"]}, indent=2))
    return 0 if payload["verification_status"] == "verified" else 1


__all__ = [
    "MARKET_MICS",
    "MINIMUM_UNIVERSE",
    "SCALE_SEED",
    "ScaleCeilings",
    "ScaleError",
    "economics_parity",
    "feature_parity",
    "parity_instruments",
    "host_ram_bytes",
    "io_counters",
    "main",
    "measure_scale_point_in_subprocess",
    "peak_rss_bytes",
    "run_scale",
    "run_scale_point",
    "scale_panel",
    "scale_pipeline",
    "scale_universe",
    "shipped_pool",
    "smallest_universe",
    "three_year_sessions",
    "vectorised_strategy_returns",
]


if __name__ == "__main__":
    raise SystemExit(main())
