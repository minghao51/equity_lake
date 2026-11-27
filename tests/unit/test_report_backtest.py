"""E01b.2 tests: `equity report backtest` publishes immutable run + attempt evidence.

Scope: ADR-0025 Decisions 2, 3, 5, 6 and 8 as frozen for E01b.2 — the single-report
path is the last legacy writer migration. Every test states what falsifies it, and
every test uses a ``tmp_path`` findings root: nothing under ``data/`` is read or
written, and no provider/network call is made (the engine is substituted).

The command is exercised through the real Typer app, with only the engine
construction seam replaced, so the assertions are about what the *command* writes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import polars as pl
import pytest
from click.testing import Result
from typer.testing import CliRunner

from equity_lake.backtesting.arena import equal_weight_buyhold
from equity_lake.backtesting.attempts import (
    ATTEMPT_MANIFEST_NAME,
    validate_attempt_directory,
)
from equity_lake.backtesting.engine import VectorBacktestEngine, universe_size
from equity_lake.backtesting.evidence import (
    MANIFEST_NAME,
    digest_non_secret_config,
    validate_run_directory,
)
from equity_lake.backtesting.evidence_view import (
    backtest_rows,
    latest_success_by_slug,
)
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy.mean_reversion import BBMeanReversionStrategy
from equity_lake.backtesting.strategy.momentum import CrossSectionalMomentumStrategy
from equity_lake.cli.__main__ import app
from tests.helpers.cli import strip_ansi  # pyright: ignore[reportMissingImports] -- resolved by pytest's rootdir sys.path

runner = CliRunner()

#: Irregular backend sessions: a stored curve is provably not a length-derived calendar.
DATES = [date(2022, 1, 3), date(2022, 1, 4), date(2022, 1, 10), date(2022, 2, 15)]
VALUES = [100_000.0, 100_500.0, 99_250.0, 101_000.0]
METRICS = {
    "total_return": 0.01,
    "cagr": 0.02,
    "max_drawdown": -0.05,
    "volatility": 0.1,
    "sharpe_ratio": 1.1,
    "sortino_ratio": 1.2,
}
TICKERS = ["AAPL", "MSFT"]

#: A closed round-trip trade: the classifier's ``evaluated`` branch needs a trade, and
#: the dated writer refuses a non-finite measurement.
CLOSED_TRADE: dict[str, Any] = {
    "ticker": "AAPL",
    "entry_date": DATES[0],
    "exit_date": DATES[2],
    "entry_price": 100.0,
    "exit_price": 101.0,
    "shares": 10.0,
    "pnl": 10.0,
    "action": "SELL",
}


def _result(*, dated: bool = True, trades: list[dict[str, Any]] | None = None) -> BacktestResult:
    """A backtest result; ``dated=False`` is the backend supplying no timestamps."""
    return BacktestResult(
        strategy_name="mean_reversion",
        tickers=list(TICKERS),
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=100_000.0,
        final_cash=VALUES[-1],
        equity_curve=pl.Series("equity", VALUES, dtype=pl.Float64),
        trades=list(trades if trades is not None else [dict(CLOSED_TRADE)]),
        metrics=dict(METRICS),
        equity_dates=list(DATES) if dated else None,
    )


class _StubLoader:
    """A loader that already recorded read-seam snapshots (E01c).

    Duck-types the one method the command reads -- ``BacktestDataLoader.snapshots()``
    -- so the assertion is about what the *command* does with a loader's evidence, not
    about how the loader gathered it (that is covered by
    ``test_evidence_snapshots.py``).
    """

    def __init__(self, snapshots: list[dict[str, str | None]]) -> None:
        self._snapshots = snapshots

    def snapshots(self) -> list[dict[str, str | None]]:
        return [dict(snapshot) for snapshot in self._snapshots]


class _StubEngine:
    """Exactly the engine surface the command reads, with nothing re-derived.

    ``build_backtest_engine`` returns this instead of a real
    :class:`VectorBacktestEngine`, so the command's evidence is asserted without a
    lake load. Every recorded value (fee/tax pair, resample, markets, adjust, strategy
    parameters, loaded universe size) is a knob here: a command that re-resolved a
    regime default instead of reading the engine would fail the assertions below.
    """

    def __init__(
        self,
        result: BacktestResult,
        *,
        fee_ratio: float = 0.001425,
        tax_ratio: float = 0.003,
        resample: str = "D",
        markets: tuple[str, ...] = ("us_equity",),
        adjust: str = "splits",
        strategy: Any = None,
        loaded_universe_size: int | None = 2,
        run_error: BaseException | None = None,
        record_loaded_universe_size: bool = True,
        loaded_identities: list[str] | None = None,
        loader_snapshots: list[dict[str, str | None]] | None = None,
    ) -> None:
        self.result = result
        self.fee_ratio = fee_ratio
        self.tax_ratio = tax_ratio
        self.resample = resample
        self.markets = list(markets)
        self.adjust = adjust
        self.strategy = strategy if strategy is not None else BBMeanReversionStrategy()
        # A backend that never loaded through the engine's loader has no recorded size.
        if record_loaded_universe_size:
            self.loaded_universe_size = loaded_universe_size
        # E01c: the display keys the engine's *own* load recorded (``None`` for a
        # preloaded frame) and the loader's read-seam snapshots, both duck-typed exactly
        # as ``VectorBacktestEngine`` / ``BacktestDataLoader`` expose them.
        if loaded_identities is not None:
            self.loaded_identities = loaded_identities
        if loader_snapshots is not None:
            self.data_loader = _StubLoader(loader_snapshots)
        self._run_error = run_error

    def run(self) -> BacktestResult:
        if self._run_error is not None:
            raise self._run_error
        return self.result


def _invoke(root: Path, engine: _StubEngine, *extra: str) -> Result:
    args = [
        "report",
        "backtest",
        "--strategy",
        "mean_reversion",
        "--tickers",
        ",".join(TICKERS),
        "--start-date",
        DATES[0].isoformat(),
        "--end-date",
        DATES[-1].isoformat(),
        "--output-dir",
        str(root),
        *extra,
    ]
    with patch("equity_lake.backtesting.factory.build_backtest_engine", return_value=engine):
        return runner.invoke(app, args)


def _only(root: Path, dirname: str) -> Path:
    """The single directory a one-invocation root holds under ``dirname``."""
    entries = sorted((root / dirname).iterdir())
    assert len(entries) == 1, f"expected exactly one {dirname}/ directory, got {[path.name for path in entries]}"
    return entries[0]


def _manifest(run_dir: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8")))


def _attempt_record(attempt_dir: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((attempt_dir / ATTEMPT_MANIFEST_NAME).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Contract item 1: one run + one attempt, and no legacy path anywhere
# ---------------------------------------------------------------------------


def test_report_backtest_writes_run_and_attempt_evidence_only(tmp_path: Path) -> None:
    """One run + one attempt under the root; no slug directory, no flat artifact.

    Falsified by: writing a ``<strategy>__<regime>/`` directory, a flat
    ``benchmark__equity.parquet``, a flat card, or any file outside ``runs/`` and
    ``attempts/``; or by the run not being visible to the ``/backtests`` projection as
    the latest success for its slug.
    """
    result = _invoke(tmp_path, _StubEngine(_result()))
    assert result.exit_code == 0, result.stdout

    run_dir = _only(tmp_path, "runs")
    attempt_dir = _only(tmp_path, "attempts")
    slug = "mean_reversion__realistic"

    # No legacy path: no slug directory and no file outside runs/ + attempts/.
    assert not (tmp_path / slug).exists()
    assert not (tmp_path / "benchmark__equity.parquet").exists()
    stray = [path for path in tmp_path.rglob("*") if path.is_file() and path.relative_to(tmp_path).parts[0] not in {"runs", "attempts"}]
    assert stray == []

    # Both directories are finalized evidence, not half-written output.
    run_validation = validate_run_directory(run_dir)
    assert run_validation.finalized, run_validation.reasons
    attempt_validation = validate_attempt_directory(attempt_dir)
    assert attempt_validation.finalized, attempt_validation.reasons

    manifest = _manifest(run_dir)
    assert manifest["universe"] == sorted(TICKERS)
    assert manifest["universe_basis"] == "ticker_only"  # this stub engine recorded no loaded identity
    assert manifest["strategies"] == ["mean_reversion"]
    assert manifest["cost_regimes"] == ["realistic"]
    # E01c: this engine never loaded through a loader, so there is no read seam and no
    # snapshot — and provenance stays unknown either way (no price-source marker yet).
    assert manifest["data_provenance"] == "unknown"
    assert manifest["input_snapshots"] == []
    assert manifest["seed"] is None
    # The digest covers the bounded non-secret configuration actually in force.
    assert manifest["config_digest"] is not None

    record = _attempt_record(attempt_dir)
    assert record["status"] == "complete"
    assert [(pair["strategy"], pair["cost_regime"]) for pair in record["requested"]] == [("mean_reversion", "realistic")]
    [outcome] = record["outcomes"]
    assert outcome["outcome"] == "completed"
    assert outcome["run_id"] == manifest["run_id"]
    assert outcome["run_dir"] == run_dir.name
    assert record["markets"] == ["us_equity"]
    # No benchmark is written by the single-report path.
    assert not (attempt_dir / "benchmark__equity.parquet").exists()
    assert any("report backtest" in note for note in record["notes"]), record["notes"]

    # The read projection sees this invocation as the slug's latest success.
    rows = backtest_rows(tmp_path)
    assert [row["slug"] for row in rows] == [slug]
    assert rows[0]["last_success"] is not None
    assert rows[0]["last_success"]["run_dir"] == run_dir.name
    assert rows[0]["last_success"]["run_id"] == manifest["run_id"]

    # The command's own output names both published directories.
    assert str(run_dir) in result.stdout
    assert str(attempt_dir) in result.stdout


# ---------------------------------------------------------------------------
# Contract item 2: the applied fee/tax pair comes from the engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cost_regime", "fee_ratio", "tax_ratio"),
    [
        # The static COST_REGIMES pair for ``high`` — the non-default regime.
        ("high", 0.005, 0.003),
        ("zero", 0.0, 0.0),
        # Deliberately *not* the static ``realistic`` pair (0.001425/0.003) and not a
        # MARKET_COST_DEFAULTS value: only reading the engine can produce these.
        ("realistic", 0.0042, 0.0007),
    ],
)
def test_report_backtest_records_the_applied_fee_and_tax_from_the_engine(
    tmp_path: Path, cost_regime: str, fee_ratio: float, tax_ratio: float
) -> None:
    """The manifest and the attempt record both carry the engine's own fee/tax pair.

    Falsified by: recording a re-resolved ``COST_REGIMES``/``MARKET_COST_DEFAULTS``
    pair, recording zeros for a non-zero regime, or letting the manifest and the
    attempt disagree.
    """
    engine = _StubEngine(_result(), fee_ratio=fee_ratio, tax_ratio=tax_ratio)
    result = _invoke(tmp_path, engine, "--cost-regime", cost_regime)
    assert result.exit_code == 0, result.stdout

    expected = [{"regime": cost_regime, "fee_ratio": fee_ratio, "tax_ratio": tax_ratio}]
    manifest = _manifest(_only(tmp_path, "runs"))
    assert manifest["costs"] == expected
    assert manifest["cost_regimes"] == [cost_regime]
    record = _attempt_record(_only(tmp_path, "attempts"))
    assert record["costs"] == expected


# ---------------------------------------------------------------------------
# Contract items 2-4: parameters, execution timing and the bounded config digest
# ---------------------------------------------------------------------------


def test_report_backtest_records_parameters_execution_and_config_digest(tmp_path: Path) -> None:
    """Scope, execution and digest are read from the engine, never re-derived.

    Falsified by: recording a registry-default parameter set instead of the instance
    that ran, a hardcoded/absent resample, or a digest over a different (or wider)
    mapping than the bounded ``markets``/``adjust``/``cost_regime`` one in force.
    """
    strategy = BBMeanReversionStrategy(params={"period": 7, "num_std": 1.5})
    engine = _StubEngine(
        _result(),
        strategy=strategy,
        resample="W",
        markets=("us_equity", "cn_ashare"),
        adjust="total_return",
    )
    result = _invoke(tmp_path, engine)
    assert result.exit_code == 0, result.stdout

    manifest = _manifest(_only(tmp_path, "runs"))
    # The parameters of the instance that actually ran (period 7, not the default 20).
    assert manifest["parameters"] == dict(strategy.params)
    assert manifest["parameters"]["period"] == 7
    # The engine's own resample rule, stated as the execution assumption.
    assert manifest["execution"]["trade_at_price"] == "close"
    assert manifest["execution"]["resample"] == "W"
    # The digest is over exactly the bounded non-secret mapping in force here.
    expected_digest = digest_non_secret_config({"markets": ["us_equity", "cn_ashare"], "adjust": "total_return", "cost_regime": "realistic"})
    assert manifest["config_digest"] == expected_digest
    # The attempt is scoped to the engine's markets, not to a CLI default (the attempt
    # scope canonicalises market order, so compare as a set-ordered list).
    record = _attempt_record(_only(tmp_path, "attempts"))
    assert record["markets"] == ["cn_ashare", "us_equity"]


# ---------------------------------------------------------------------------
# E01c contract item 1-2: canonical universe and read-seam snapshots
# ---------------------------------------------------------------------------


def test_report_backtest_records_canonical_universe_from_the_engine_loader(tmp_path: Path) -> None:
    """The engine's own recorded display keys become the canonical universe.

    Falsified by: recording the requested bare tickers while the engine loaded
    canonical identities, declaring ``canonical`` for a ticker-only fallback, or
    recording an identity the engine never loaded.
    """
    identities = ["us_equity/XNYS/AAPL", "us_equity/XNYS/MSFT"]
    result = _invoke(tmp_path, _StubEngine(_result(), loaded_identities=identities))
    assert result.exit_code == 0, result.stdout

    manifest = _manifest(_only(tmp_path, "runs"))
    assert manifest["universe"] == sorted(identities)
    assert manifest["universe_basis"] == "canonical"
    # The attempt record carries the same universe (its scope is the run's scope).
    record = _attempt_record(_only(tmp_path, "attempts"))
    assert record["universe"] == sorted(identities)

    # Without loaded identities the same result records bare tickers and admits it.
    fallback_root = tmp_path / "fallback"
    fallback = _invoke(fallback_root, _StubEngine(_result()))
    assert fallback.exit_code == 0, fallback.stdout
    fallback_manifest = _manifest(_only(fallback_root, "runs"))
    assert fallback_manifest["universe"] == sorted(TICKERS)
    assert fallback_manifest["universe_basis"] == "ticker_only"


def test_report_backtest_records_snapshots_from_the_engine_loader(tmp_path: Path) -> None:
    """Read-seam snapshots reach the manifest, and provenance stays ``unknown``.

    Falsified by: dropping the loader's snapshots, losing their ``version``, or by a
    snapshot presence quietly upgrading ``data_provenance`` (no persisted price-source
    marker exists yet — open item E01-6).
    """
    snapshots: list[dict[str, str | None]] = [
        {"name": "price_marks:us_equity", "sha256": "e" * 64, "source": "lake:delta", "version": "4"},
        {"name": "action_events:us_equity", "sha256": "f" * 64, "source": "lake:delta", "version": "2"},
    ]
    result = _invoke(tmp_path, _StubEngine(_result(), loader_snapshots=snapshots))
    assert result.exit_code == 0, result.stdout

    manifest = _manifest(_only(tmp_path, "runs"))
    assert manifest["input_snapshots"] == snapshots
    assert manifest["data_provenance"] == "unknown"
    # The run is still finalized and accepted by the validator with snapshots present.
    assert validate_run_directory(_only(tmp_path, "runs")).finalized


# ---------------------------------------------------------------------------
# Contract item 7: an undated result is refused and writes nothing
# ---------------------------------------------------------------------------


def test_report_backtest_refuses_an_undated_result(tmp_path: Path) -> None:
    """Exit 1, a message naming dated evidence, and an empty root.

    Falsified by: exiting 0, writing a run/attempt (or a legacy slug directory) for an
    undated curve, or by the message not naming the missing dated evidence.
    """
    result = _invoke(tmp_path, _StubEngine(_result(dated=False)))
    assert result.exit_code == 1, result.stdout
    assert "dated evidence" in result.stdout
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "attempts").exists()
    assert not (tmp_path / "mean_reversion__realistic").exists()
    assert list(tmp_path.iterdir()) == []


def test_report_backtest_writes_nothing_when_the_backtest_raises(tmp_path: Path) -> None:
    """A raising run keeps today's behaviour: clean error, exit 1, nothing written.

    Falsified by: publishing any evidence for a run that never completed.
    """
    result = _invoke(tmp_path, _StubEngine(_result(), run_error=RuntimeError("no data for window")))
    assert result.exit_code == 1, result.stdout
    assert "backtest failed" in result.stdout
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Contract item 6: the attempt outcome carries the shared evaluation status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("strategy", "trades", "loaded_universe_size", "expected_status"),
    [
        # momentum declares min_stocks=10, so a 2-instrument load is ineligible.
        (CrossSectionalMomentumStrategy(), [dict(CLOSED_TRADE)], 2, "ineligible"),
        # A completed run that executed nothing is not evaluable evidence.
        (BBMeanReversionStrategy(), [], 2, "no_trades"),
        (BBMeanReversionStrategy(), [dict(CLOSED_TRADE)], 2, "evaluated"),
    ],
)
def test_report_backtest_attempt_outcome_is_classified(
    tmp_path: Path, strategy: Any, trades: list[dict[str, Any]], loaded_universe_size: int, expected_status: str
) -> None:
    """The outcome's ``evaluation_status`` is the shared classifier's verdict.

    Falsified by: recording ``evaluated`` for an under-sized universe or for a run
    with no trades, i.e. by guessing instead of calling the classifier the arena uses.
    """
    engine = _StubEngine(_result(trades=trades), strategy=strategy, loaded_universe_size=loaded_universe_size)
    result = _invoke(tmp_path, engine)
    assert result.exit_code == 0, result.stdout

    [outcome] = _attempt_record(_only(tmp_path, "attempts"))["outcomes"]
    assert outcome["outcome"] == "completed"
    assert outcome["evaluation_status"] == expected_status
    assert f"evaluation status: {expected_status}" in result.stdout


def test_report_backtest_discloses_the_requested_ticker_cohort_fallback(tmp_path: Path) -> None:
    """No recorded loaded size: the requested tickers are used *and* disclosed.

    Falsified by: silently substituting ``len(result.tickers)`` as the cohort, which
    would let an unrecorded (possibly smaller) load look like an eligible universe.
    """
    engine = _StubEngine(
        _result(),
        strategy=CrossSectionalMomentumStrategy(),
        record_loaded_universe_size=False,
    )
    result = _invoke(tmp_path, engine)
    assert result.exit_code == 0, result.stdout

    record = _attempt_record(_only(tmp_path, "attempts"))
    [outcome] = record["outcomes"]
    assert outcome["evaluation_status"] == "ineligible"  # 2 requested tickers < min_stocks 10
    assert any("requested tickers" in note for note in record["notes"]), record["notes"]


# ---------------------------------------------------------------------------
# ADR-0025 Decision 2: two identical-scope invocations coexist
# ---------------------------------------------------------------------------


def test_report_backtest_never_overwrites_previous_run_evidence(tmp_path: Path) -> None:
    """Two runs of the same scope coexist; the newest validated run is the success.

    Falsified by: reusing one run directory (an overwrite), by the second invocation
    changing the first run's manifest, or by the latest-success projection pointing at
    the older run.
    """
    first = _invoke(tmp_path, _StubEngine(_result()))
    assert first.exit_code == 0, first.stdout
    first_run = _only(tmp_path, "runs")
    first_manifest_bytes = (first_run / MANIFEST_NAME).read_bytes()
    first_manifest = _manifest(first_run)

    second = _invoke(tmp_path, _StubEngine(_result()))
    assert second.exit_code == 0, second.stdout

    run_dirs = sorted(path.name for path in (tmp_path / "runs").iterdir())
    assert len(run_dirs) == 2, run_dirs
    assert len(list((tmp_path / "attempts").iterdir())) == 2
    # The first run is untouched, byte for byte.
    assert (first_run / MANIFEST_NAME).read_bytes() == first_manifest_bytes

    newest = max((tmp_path / "runs").iterdir(), key=lambda path: _manifest(path)["finalized_at"])
    assert newest.name != first_run.name
    success = latest_success_by_slug(tmp_path)["mean_reversion__realistic"]
    assert success.directory.name == newest.name
    assert success.manifest.run_id != first_manifest["run_id"] or success.directory.name != first_run.name


# ---------------------------------------------------------------------------
# The shared universe-size helper (moved out of arena.py, semantics unchanged)
# ---------------------------------------------------------------------------


def test_universe_size_counts_canonical_identity_or_ticker() -> None:
    """Full ``(market, mic, symbol)`` identity wins; ticker-only fixtures still count.

    Falsified by: counting distinct tickers when composite identity is present (which
    would merge the same symbol on two venues into one instrument), by dropping the
    ticker-only fallback, or by inventing a size for a frame with neither column.
    """
    composite = pl.DataFrame(
        {
            "market": ["us_equity", "us_equity", "cn_ashare", "cn_ashare"],
            "mic": ["XNAS", "XNAS", "XSHG", "XSHG"],
            "symbol": ["AAPL", "AAPL", "600000", "600000"],
            "ticker": ["AAPL", "AAPL", "600000", "600000"],
        }
    )
    # AAPL appears twice on one venue (one identity) and 600000 once: two instruments.
    assert universe_size(composite) == 2
    # Same ticker on two venues is two instruments, not one.
    shared_ticker = pl.DataFrame(
        {
            "market": ["us_equity", "hk_sg_equity"],
            "mic": ["XNAS", "XHKG"],
            "symbol": ["AAPL", "AAPL"],
            "ticker": ["AAPL", "AAPL"],
        }
    )
    assert universe_size(shared_ticker) == 2

    assert universe_size(pl.DataFrame({"ticker": ["AAPL", "AAPL", "MSFT"]})) == 2
    assert universe_size(pl.DataFrame({"date": DATES})) == 0


def test_partial_identity_is_still_an_error_on_the_benchmark_path() -> None:
    """The moved helper does not loosen the benchmark's partial-identity refusal.

    Falsified by: the benchmark silently filling a missing ``mic``/``symbol`` (a venue
    guess) instead of raising, which would change arena behaviour outside this stage.
    """
    partial = pl.DataFrame(
        {
            "market": ["us_equity"],
            "ticker": ["AAPL"],
            "date": [DATES[0]],
            "close": [100.0],
        }
    )
    with pytest.raises(ValueError, match="partial canonical instrument identity"):
        equal_weight_buyhold(partial, 100_000.0)


# ---------------------------------------------------------------------------
# The engine records the size of the frame it actually loaded
# ---------------------------------------------------------------------------


@pytest.fixture()
def _engine() -> Iterator[VectorBacktestEngine]:
    engine = VectorBacktestEngine(
        strategy=BBMeanReversionStrategy(),
        tickers=TICKERS,
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
    )
    yield engine


def test_engine_records_loaded_universe_size_only_for_its_own_load(_engine: VectorBacktestEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """``loaded_universe_size`` is set from the loader's frame, never from a preload.

    Falsified by: leaving the size unset after a real load (the CLI would then disclose
    the requested-ticker fallback for a cohort the engine did know), or by reporting a
    size for a ``preloaded_data`` frame the engine did not load (a cohort guess).
    """
    loaded = pl.DataFrame({"ticker": ["AAPL", "AAPL", "MSFT", "GOOGL"], "date": [DATES[0]] * 4})

    class _StubLoader:
        def __init__(self, **_kwargs: Any) -> None:
            """Model the real loader's constructor (lake root + coverage basis)."""

        def load(self, **_kwargs: Any) -> pl.DataFrame:
            return loaded

    assert _engine.loaded_universe_size is None  # nothing loaded yet
    monkeypatch.setattr("equity_lake.backtesting.engine.BacktestDataLoader", _StubLoader)
    assert _engine._load_data().height == 4
    assert _engine.loaded_universe_size == 3

    preloaded = VectorBacktestEngine(
        strategy=BBMeanReversionStrategy(),
        tickers=TICKERS,
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=100_000.0,
        markets=["us_equity"],
        preloaded_data=loaded,
    )
    assert preloaded._load_data().height == 4
    assert preloaded.loaded_universe_size is None


# ---------------------------------------------------------------------------
# R01: the root / basis flags and the frozen-protocol binding
# ---------------------------------------------------------------------------


def _frozen(tmp_path: Path, **overrides: Any) -> Any:
    """Freeze one R00 protocol whose window contains the stub run, under ``tmp_path``.

    The stub engine records no loaded identity, so the manifest universe is the bare
    requested tickers (``ticker_only``) — the frozen universe is stated the same way.
    """
    from equity_lake.backtesting.evidence import CostApplication
    from equity_lake.backtesting.protocol import (
        ResearchProtocol,
        ResourceBudget,
        freeze_protocol,
    )

    values: dict[str, Any] = {
        "experiment": "R01-baseline-us-2024",
        "universe": list(TICKERS),
        "start_date": DATES[0],
        "end_date": date(2022, 12, 31),
        "holdout_start": date(2022, 11, 1),
        "holdout_end": date(2022, 12, 31),
        "label_horizon_days": 5,
        "label_maturity_days": 5,
        "purge_days": 5,
        "embargo_days": 1,
        "costs": [CostApplication(regime="realistic", fee_ratio=0.0005, tax_ratio=0.0)],
        "metrics": ["net_return", "sharpe_ratio", "max_drawdown"],
        "baselines": ["buy_and_hold", "equal_weight"],
        "acceptance_tolerances": {"net_return": 0.0, "sharpe_ratio": 0.05},
        "resource_budget": ResourceBudget(max_runtime_seconds=600.0, max_symbols=5, max_sessions=252),
    }
    values.update(overrides)
    frozen, path = freeze_protocol(ResearchProtocol(**values), root=tmp_path / "research")
    return frozen, path


class TestReportBacktestRootAndBasis:
    """The evidence path carries one lake root and one ADR-0032 basis (Stage 2a/R01)."""

    def test_help_lists_the_root_and_basis_flags(self) -> None:
        result = runner.invoke(app, ["report", "backtest", "--help"], env={"COLUMNS": "220"})
        assert result.exit_code == 0
        help_stdout = strip_ansi(result.stdout)
        for flag in ("--lake-dir", "--coverage-basis", "--protocol-experiment", "--protocol-digest", "--protocol-root"):
            assert flag in help_stdout
        assert "vendor_events_no_clock" in help_stdout

    def test_an_unknown_basis_exits_non_zero(self, tmp_path: Path) -> None:
        """Falsified by: passing an unvalidated basis string down to the engine."""
        result = _invoke(tmp_path, _StubEngine(_result()), "--coverage-basis", "guessed_clock")
        assert result.exit_code == 1
        assert "Unknown --coverage-basis" in result.stdout
        assert not (tmp_path / "runs").exists()

    def test_the_root_and_basis_reach_the_engine_construction(self, tmp_path: Path) -> None:
        """Falsified by: the command keeping the canonical root or the strict basis."""
        from equity_lake.core.paths import DATA_DIR

        captured: dict[str, Any] = {}

        def _build(**kwargs: Any) -> _StubEngine:
            captured.update(kwargs)
            return _StubEngine(_result())

        real_root = DATA_DIR / "lake_real"
        args = [
            "report",
            "backtest",
            "--strategy",
            "mean_reversion",
            "--tickers",
            ",".join(TICKERS),
            "--start-date",
            DATES[0].isoformat(),
            "--end-date",
            DATES[-1].isoformat(),
            "--output-dir",
            str(tmp_path),
            "--lake-dir",
            str(real_root),
            "--coverage-basis",
            "vendor_events_no_clock",
        ]
        with patch("equity_lake.backtesting.factory.build_backtest_engine", side_effect=_build):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, result.stdout
        assert captured["coverage_basis"] == "vendor_events_no_clock"
        assert captured["lake_dir"] == real_root.resolve()
        assert captured["lake_dir"].name == "lake_real"

    def test_the_defaults_stay_canonical_and_strict(self, tmp_path: Path) -> None:
        """Falsified by: a new default that changes an existing invocation."""
        captured: dict[str, Any] = {}

        def _build(**kwargs: Any) -> _StubEngine:
            captured.update(kwargs)
            return _StubEngine(_result())

        args = [
            "report",
            "backtest",
            "--strategy",
            "mean_reversion",
            "--tickers",
            ",".join(TICKERS),
            "--start-date",
            DATES[0].isoformat(),
            "--end-date",
            DATES[-1].isoformat(),
            "--output-dir",
            str(tmp_path),
        ]
        with patch("equity_lake.backtesting.factory.build_backtest_engine", side_effect=_build):
            assert runner.invoke(app, args).exit_code == 0
        assert captured["coverage_basis"] == "source_clock"
        assert captured["lake_dir"] is None


class TestReportBacktestProtocolBinding:
    """R01: the run is bound to a frozen R00 protocol, or it refuses."""

    def test_a_bound_run_records_the_frozen_digest_in_its_scope(self, tmp_path: Path) -> None:
        """Falsified by: publishing a protocol-bound run without the digest in scope."""
        frozen, _ = _frozen(tmp_path)
        result = _invoke(
            tmp_path / "findings",
            _StubEngine(_result()),
            "--protocol-experiment",
            "R01-baseline-us-2024",
            "--protocol-root",
            str(tmp_path / "research"),
        )
        assert result.exit_code == 0, result.stdout
        manifest = _manifest(_only(tmp_path / "findings", "runs"))
        assert manifest["parameters"]["protocol_digest"] == frozen.protocol_digest
        assert manifest["parameters"]["protocol_experiment"] == "R01-baseline-us-2024"
        assert str(frozen.protocol_digest) in result.stdout

    def test_the_digest_is_part_of_the_run_identity(self, tmp_path: Path) -> None:
        """Falsified by: a bound and an unbound run sharing one run ID."""
        frozen, _ = _frozen(tmp_path)
        bound = _invoke(
            tmp_path / "bound",
            _StubEngine(_result()),
            "--protocol-experiment",
            "R01-baseline-us-2024",
            "--protocol-root",
            str(tmp_path / "research"),
        )
        assert bound.exit_code == 0, bound.stdout
        unbound = _invoke(tmp_path / "unbound", _StubEngine(_result()))
        assert unbound.exit_code == 0, unbound.stdout
        bound_id = _manifest(_only(tmp_path / "bound", "runs"))["run_id"]
        unbound_id = _manifest(_only(tmp_path / "unbound", "runs"))["run_id"]
        assert bound_id != unbound_id
        assert frozen.protocol_digest in _manifest(_only(tmp_path / "bound", "runs"))["parameters"].values()

    def test_an_absent_freeze_refuses_before_the_run(self, tmp_path: Path) -> None:
        """Falsified by: running first and discovering the missing freeze afterwards."""
        engine = _StubEngine(_result())
        with patch("equity_lake.backtesting.factory.build_backtest_engine", return_value=engine) as build:
            result = _invoke(tmp_path, engine, "--protocol-experiment", "R01-baseline-us-2024", "--protocol-root", str(tmp_path / "research"))
        assert result.exit_code == 1
        assert "assumptions are absent" in result.stdout
        assert build.call_count == 0, "the run must not start without a frozen protocol"
        assert not (tmp_path / "runs").exists()

    def test_a_digest_claim_that_no_freeze_carries_refuses(self, tmp_path: Path) -> None:
        """Falsified by: accepting a run whose stated protocol digest is not frozen."""
        _frozen(tmp_path)
        result = _invoke(
            tmp_path,
            _StubEngine(_result()),
            "--protocol-experiment",
            "R01-baseline-us-2024",
            "--protocol-root",
            str(tmp_path / "research"),
            "--protocol-digest",
            "0" * 64,
        )
        assert result.exit_code == 1
        assert "does not match a frozen record" in result.stdout
        assert not (tmp_path / "runs").exists()

    def test_a_digest_without_an_experiment_refuses(self, tmp_path: Path) -> None:
        result = _invoke(tmp_path, _StubEngine(_result()), "--protocol-digest", "0" * 64)
        assert result.exit_code == 1
        assert "--protocol-digest requires --protocol-experiment" in result.stdout
        assert not (tmp_path / "runs").exists()

    def test_a_run_entering_the_holdout_refuses_and_writes_nothing(self, tmp_path: Path) -> None:
        """Falsified by: publishing a run whose window overlaps the frozen holdout."""
        _frozen(tmp_path, holdout_start=date(2022, 2, 1), holdout_end=date(2022, 2, 15))
        result = _invoke(
            tmp_path,
            _StubEngine(_result()),
            "--protocol-experiment",
            "R01-baseline-us-2024",
            "--protocol-root",
            str(tmp_path / "research"),
        )
        assert result.exit_code == 1
        assert "refusing to publish this run" in result.stdout
        assert "overlaps the frozen holdout" in result.stdout
        assert not (tmp_path / "runs").exists()
        assert not (tmp_path / "attempts").exists()

    def test_a_universe_outside_the_frozen_universe_refuses(self, tmp_path: Path) -> None:
        """Falsified by: admitting a selection change made after the freeze."""
        _frozen(tmp_path, universe=["AAPL"])
        result = _invoke(
            tmp_path,
            _StubEngine(_result()),
            "--protocol-experiment",
            "R01-baseline-us-2024",
            "--protocol-root",
            str(tmp_path / "research"),
        )
        assert result.exit_code == 1
        assert "universe differs from the frozen universe" in result.stdout
        assert not (tmp_path / "runs").exists()

    def test_a_differing_applied_cost_is_disclosed_in_the_evidence(self, tmp_path: Path) -> None:
        """Falsified by: recording costs silently when they differ from the frozen pair."""
        _frozen(tmp_path)
        result = _invoke(
            tmp_path,
            _StubEngine(_result(), fee_ratio=0.001425, tax_ratio=0.003),
            "--protocol-experiment",
            "R01-baseline-us-2024",
            "--protocol-root",
            str(tmp_path / "research"),
        )
        assert result.exit_code == 0, result.stdout
        assert "cost asymmetry (disclosed)" in result.stdout
        notes = _attempt_record(_only(tmp_path, "attempts"))["notes"]
        assert any("cost asymmetry (disclosed)" in str(note) for note in notes), notes
