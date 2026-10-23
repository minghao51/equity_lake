"""Synthetic tests for bounded replay, durable checkpoints, and zero-paid defaults."""

from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from functools import wraps
from importlib import import_module
from pathlib import Path
from typing import Any, TypedDict
from unittest.mock import MagicMock

import polars as pl
import pytest
from deltalake import DeltaTable
from pydantic import ValidationError

from equity_lake.storage.delta import merge_delta, read_delta  # type: ignore[import-untyped]

replay = import_module("equity_lake.ingestion.replay")
_TEST_STATE_NAMESPACE = "bounded-replay-test-silver"
MAX_REPLAY_BATCH_SIZE = replay.MAX_REPLAY_BATCH_SIZE
MAX_REPLAY_ITEMS = replay.MAX_REPLAY_ITEMS
PaidWorkBudget = replay.PaidWorkBudget
ReplayAuthorizationError = replay.ReplayAuthorizationError
ReplayCheckpoint = replay.ReplayCheckpoint
ReplayCheckpointError = replay.ReplayCheckpointError
ReplayDurabilityError = replay.ReplayDurabilityError
ReplayItem = replay.ReplayItem
ReplayBatchResult = replay.ReplayBatchResult
ReplayScope = replay.ReplayScope
_replay_bounded = replay.replay_bounded


@wraps(_replay_bounded)
def replay_bounded(**kwargs: Any) -> Any:
    kwargs.setdefault("state_namespace", _TEST_STATE_NAMESPACE)
    return _replay_bounded(**kwargs)


@pytest.fixture(autouse=True)
def _isolate_replay_auxiliary_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(replay, "DATA_DIR", tmp_path / "data")


_TRADING_DATE = date(2026, 10, 17)


def _scope() -> Any:
    return ReplayScope(
        source="synthetic-news",
        trading_date=_TRADING_DATE,
        market="us_equity",
        instruments=("AAPL", "MSFT"),
    )


def _item(item_id: str, *, source: str = "synthetic-news", instrument: str = "AAPL") -> Any:
    return ReplayItem(
        item_id=item_id,
        source=source,
        trading_date=_TRADING_DATE,
        market="us_equity",
        instrument=instrument,
        payload={"synthetic": True},
    )


class _AuthorizationOptions(TypedDict):
    paid_processing_authorized: bool
    approved_estimate: Decimal
    actual_spend_cap: Decimal


def _authorized() -> _AuthorizationOptions:
    return {
        "paid_processing_authorized": True,
        "approved_estimate": Decimal("10.00"),
        "actual_spend_cap": Decimal("20.00"),
    }


def _estimate(batch: Sequence[Any]) -> Decimal:
    return Decimal("0.10") * len(batch)


def _synthetic_callbacks(
    processed: set[str], batch_sizes: list[int] | None = None, budgets: list[Any] | None = None
) -> tuple[MagicMock, MagicMock, MagicMock]:
    def process(batch: Sequence[Any], budget: Any) -> Any:
        assert budget.approved_estimate == Decimal("10.00")
        assert budget.actual_spend_cap == Decimal("20.00")
        if batch_sizes is not None:
            batch_sizes.append(len(batch))
        if budgets is not None:
            budgets.append(budget)
        return ReplayBatchResult(
            rows=[
                {
                    "item_id": item.item_id,
                    "source": item.source,
                    "date": item.trading_date,
                    "market": item.market,
                    "instrument": item.instrument,
                }
                for item in batch
            ],
            actual_cost=Decimal("0.01") * len(batch),
        )

    def write(rows: Sequence[Mapping[str, Any]]) -> None:
        processed.update(str(row["item_id"]) for row in rows)

    def read(_scope: Any) -> set[str]:
        return set(processed)

    return MagicMock(side_effect=process), MagicMock(side_effect=write), MagicMock(side_effect=read)


def test_replay_scope_requires_all_dimensions_and_nonempty_instruments() -> None:
    complete_scope = {
        "source": "synthetic-news",
        "trading_date": _TRADING_DATE,
        "market": "us_equity",
        "instruments": ("AAPL",),
    }
    for dimension in complete_scope:
        with pytest.raises(ValidationError):
            ReplayScope.model_validate({key: value for key, value in complete_scope.items() if key != dimension})
    with pytest.raises(ValueError, match="at least one"):
        ReplayScope(source="synthetic-news", trading_date=_TRADING_DATE, market="us_equity", instruments=())


def test_default_zero_paid_authorization_stops_before_processor(tmp_path: Path) -> None:
    processor = MagicMock(return_value=[])
    writer = MagicMock()

    with pytest.raises(ReplayAuthorizationError, match="zero-paid-authorization"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-001")],
            checkpoint_path=tmp_path / "checkpoint.json",
            process_batch=processor,
            write_silver=writer,
            read_processed_ids=MagicMock(return_value=set()),
        )

    processor.assert_not_called()
    writer.assert_not_called()


def test_paid_processing_requires_explicit_estimate_and_per_invocation_cap(tmp_path: Path) -> None:
    processor = MagicMock(return_value=[])

    with pytest.raises(ReplayAuthorizationError, match="approved estimate"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-001")],
            checkpoint_path=tmp_path / "no-budget.json",
            process_batch=processor,
            write_silver=MagicMock(),
            read_processed_ids=MagicMock(return_value=set()),
            paid_processing_authorized=True,
        )
    with pytest.raises(ReplayAuthorizationError, match="no greater than the spend cap"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-001")],
            checkpoint_path=tmp_path / "over-budget.json",
            process_batch=processor,
            write_silver=MagicMock(),
            read_processed_ids=MagicMock(return_value=set()),
            paid_processing_authorized=True,
            approved_estimate=Decimal("2"),
            actual_spend_cap=Decimal("1"),
        )

    processor.assert_not_called()


def test_replay_is_scope_filtered_deterministic_and_bounded_to_100_items(tmp_path: Path) -> None:
    processed: set[str] = set()
    batches: list[int] = []
    budgets: list[Any] = []
    estimated_ids: list[str] = []
    processor, writer, reader = _synthetic_callbacks(processed, batches, budgets)

    def estimate(batch: Sequence[Any]) -> Decimal:
        estimated_ids.extend(item.item_id for item in batch)
        return Decimal("0.10") * len(batch)

    scoped_items = [_item(f"item-{index:03d}") for index in reversed(range(130))]
    scoped_items.extend(
        [
            ReplayItem(
                item_id="other-market",
                source="synthetic-news",
                trading_date=_TRADING_DATE,
                market="hk_sg_equity",
                instrument="AAPL",
            ),
            _item("other-source", source="different-source"),
            ReplayItem(
                item_id="other-date",
                source="synthetic-news",
                trading_date=date(2026, 10, 16),
                market="us_equity",
                instrument="AAPL",
            ),
            _item("other-instrument", instrument="TSLA"),
        ]
    )

    result = replay_bounded(
        scope=_scope(),
        items=scoped_items,
        checkpoint_path=tmp_path / "bounded.json",
        process_batch=processor,
        estimate_batch_cost=estimate,
        write_silver=writer,
        read_processed_ids=reader,
        **_authorized(),
    )

    expected = tuple(f"item-{index:03d}" for index in range(MAX_REPLAY_ITEMS))
    assert result.selected_item_ids == expected
    assert result.processed_item_ids == expected
    assert result.deferred_count == 30
    assert result.batch_count == 4
    assert batches == [25, 25, 25, 25]
    assert max(batches) <= MAX_REPLAY_BATCH_SIZE
    assert len(processed) == MAX_REPLAY_ITEMS
    assert tuple(estimated_ids) == expected  # cost estimates exclude out-of-scope and deferred input rows
    assert result.estimated_cost == Decimal("10.00")
    assert result.actual_spend == Decimal("1.00")
    assert [budget.remaining_allowance for budget in budgets] == [Decimal("20.00"), Decimal("19.75"), Decimal("19.50"), Decimal("19.25")]
    checkpoint = ReplayCheckpoint.model_validate_json((tmp_path / "bounded.json").read_text())
    assert checkpoint.completed_item_ids == [f"item-{index:03d}" for index in range(75, 100)]
    assert checkpoint.scope == _scope()


def test_checkpoint_is_written_only_after_authoritative_silver_confirmation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    durable_ids: set[str] = set()
    processor, writer, reader = _synthetic_callbacks(durable_ids)
    original_checkpoint_write = replay._write_checkpoint_atomic
    observed: list[tuple[str, ...]] = []

    def check_durable_before_checkpoint(path: Path, checkpoint: Any) -> None:
        assert set(checkpoint.completed_item_ids) <= durable_ids
        observed.append(tuple(checkpoint.completed_item_ids))
        original_checkpoint_write(path, checkpoint)

    monkeypatch.setattr(replay, "_write_checkpoint_atomic", check_durable_before_checkpoint)
    result = replay_bounded(
        scope=_scope(),
        items=[_item("item-002"), _item("item-001")],
        checkpoint_path=tmp_path / "ordered.json",
        process_batch=processor,
        estimate_batch_cost=_estimate,
        write_silver=writer,
        read_processed_ids=reader,
        **_authorized(),
    )

    assert result.processed_item_ids == ("item-001", "item-002")
    assert observed == [("item-001", "item-002")]


def test_stale_checkpoint_never_skips_an_unwritten_silver_item(tmp_path: Path) -> None:
    scope = _scope()
    checkpoint_path = tmp_path / "stale.json"
    replay._write_checkpoint_atomic(checkpoint_path, ReplayCheckpoint(scope=scope, completed_item_ids=["item-001"]))
    processed: set[str] = set()
    processor, writer, reader = _synthetic_callbacks(processed)

    result = replay_bounded(
        scope=scope,
        items=[_item("item-001")],
        checkpoint_path=checkpoint_path,
        process_batch=processor,
        estimate_batch_cost=_estimate,
        write_silver=writer,
        read_processed_ids=reader,
        **_authorized(),
    )

    assert processor.call_count == 1
    assert result.processed_item_ids == ("item-001",)
    assert processed == {"item-001"}


def test_absent_checkpoint_is_allowed_but_corrupt_checkpoint_fails_closed(tmp_path: Path) -> None:
    absent_path = tmp_path / "absent.json"
    processed: set[str] = set()
    processor, writer, reader = _synthetic_callbacks(processed)
    result = replay_bounded(
        scope=_scope(),
        items=[_item("item-001")],
        checkpoint_path=absent_path,
        process_batch=processor,
        estimate_batch_cost=_estimate,
        write_silver=writer,
        read_processed_ids=reader,
        **_authorized(),
    )
    assert result.processed_item_ids == ("item-001",)

    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{not valid json", encoding="utf-8")
    blocked_processor = MagicMock(return_value=[])
    with pytest.raises(ReplayCheckpointError, match="Invalid replay checkpoint"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-002")],
            checkpoint_path=corrupt_path,
            process_batch=blocked_processor,
            estimate_batch_cost=_estimate,
            write_silver=MagicMock(),
            read_processed_ids=MagicMock(return_value=set()),
            **_authorized(),
        )
    blocked_processor.assert_not_called()


def test_scope_mismatched_checkpoint_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "wrong-scope.json"
    other_scope = ReplayScope(source="other", trading_date=_TRADING_DATE, market="us_equity", instruments=("AAPL",))
    replay._write_checkpoint_atomic(path, ReplayCheckpoint(scope=other_scope, completed_item_ids=[]))
    processor = MagicMock(return_value=[])

    with pytest.raises(ReplayCheckpointError, match="scope does not match"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-001")],
            checkpoint_path=path,
            process_batch=processor,
            estimate_batch_cost=_estimate,
            write_silver=MagicMock(),
            read_processed_ids=MagicMock(return_value=set()),
            **_authorized(),
        )
    processor.assert_not_called()


def test_crash_after_durable_silver_before_checkpoint_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    table = "02_silver/replay_evidence"
    table_path = tmp_path / table
    checkpoint_path = tmp_path / "replay-checkpoint.json"

    def read_durable(_scope: Any) -> set[str]:
        if not DeltaTable.is_deltatable(str(table_path)):
            return set()
        return set(read_delta(table, lake_dir=tmp_path)["item_id"].to_list())

    def write_durable(rows: Sequence[Mapping[str, Any]]) -> None:
        frame = pl.DataFrame(rows)
        merge_delta(frame, table, key_columns=["item_id"], lake_dir=tmp_path)

    processor, _, _ = _synthetic_callbacks(set())
    monkeypatch.setattr(replay, "_write_checkpoint_atomic", MagicMock(side_effect=OSError("simulated crash before checkpoint rename")))
    with pytest.raises(OSError, match="simulated crash"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-001")],
            checkpoint_path=checkpoint_path,
            process_batch=processor,
            estimate_batch_cost=_estimate,
            write_silver=write_durable,
            read_processed_ids=read_durable,
            **_authorized(),
        )

    assert read_durable(_scope()) == {"item-001"}
    assert not checkpoint_path.exists()
    assert read_delta(table, lake_dir=tmp_path).height == 1

    retry_processor = MagicMock(return_value=[])
    retry = replay_bounded(
        scope=_scope(),
        items=[_item("item-001")],
        checkpoint_path=checkpoint_path,
        process_batch=retry_processor,
        estimate_batch_cost=_estimate,
        write_silver=write_durable,
        read_processed_ids=read_durable,
        **_authorized(),
    )
    assert retry_processor.call_count == 0
    assert retry.processed_item_ids == ()
    assert retry.already_processed_count == 1
    assert read_delta(table, lake_dir=tmp_path).height == 1


def test_writer_failure_or_unconfirmed_silver_never_advances_checkpoint(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "not-advanced.json"
    processor, _, _ = _synthetic_callbacks(set())

    with pytest.raises(ReplayDurabilityError, match="reported failure"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-001")],
            checkpoint_path=checkpoint_path,
            process_batch=processor,
            estimate_batch_cost=_estimate,
            write_silver=MagicMock(return_value=False),
            read_processed_ids=MagicMock(return_value=set()),
            **_authorized(),
        )
    assert not checkpoint_path.exists()

    with pytest.raises(ReplayDurabilityError, match="not durable"):
        replay_bounded(
            scope=_scope(),
            items=[_item("item-002")],
            checkpoint_path=tmp_path / "unconfirmed.json",
            state_namespace=f"{_TEST_STATE_NAMESPACE}-unconfirmed",
            process_batch=processor,
            estimate_batch_cost=_estimate,
            write_silver=MagicMock(return_value=None),
            read_processed_ids=MagicMock(return_value=set()),
            **_authorized(),
        )
    assert not checkpoint_path.exists()


def test_non_finite_cap_estimate_and_batch_estimates_fail_closed(tmp_path: Path) -> None:
    processor = MagicMock()
    for cap, estimate in (
        (Decimal("Infinity"), Decimal("0.10")),
        (Decimal("1.00"), Decimal("NaN")),
    ):
        with pytest.raises(ReplayAuthorizationError, match="finite"):
            replay_bounded(
                scope=_scope(),
                items=[_item("item-001")],
                checkpoint_path=tmp_path / f"invalid-{cap}-{estimate}.json",
                process_batch=processor,
                estimate_batch_cost=lambda _batch, value=estimate: value,
                write_silver=MagicMock(),
                read_processed_ids=MagicMock(return_value=set()),
                paid_processing_authorized=True,
                approved_estimate=Decimal("1.00"),
                actual_spend_cap=cap,
            )
    processor.assert_not_called()


def test_provider_actual_cost_must_be_finite_and_within_invocation_cap(tmp_path: Path) -> None:
    scope = _scope()
    writer = MagicMock()
    processor = MagicMock(
        return_value=ReplayBatchResult(
            rows=[
                {
                    "item_id": "item-001",
                    "source": scope.source,
                    "date": scope.trading_date,
                    "market": scope.market,
                    "instrument": "AAPL",
                }
            ],
            actual_cost=Decimal("Infinity"),
        )
    )

    with pytest.raises(ReplayAuthorizationError, match="actual batch cost.*finite"):
        replay_bounded(
            scope=scope,
            items=[_item("item-001")],
            checkpoint_path=tmp_path / "non-finite-actual.json",
            process_batch=processor,
            estimate_batch_cost=lambda _batch: Decimal("0.10"),
            write_silver=writer,
            read_processed_ids=MagicMock(return_value=set()),
            paid_processing_authorized=True,
            approved_estimate=Decimal("0.10"),
            actual_spend_cap=Decimal("1.00"),
        )
    writer.assert_not_called()
    ledger = replay.ReplaySpendLedger.model_validate_json(replay._spend_ledger_path(scope, _TEST_STATE_NAMESPACE).read_text())
    assert ledger.pending_item_ids == ["item-001"]

    overspend_processor = MagicMock(
        return_value=ReplayBatchResult(
            rows=[
                {
                    "item_id": "item-002",
                    "source": scope.source,
                    "date": scope.trading_date,
                    "market": scope.market,
                    "instrument": "AAPL",
                }
            ],
            actual_cost=Decimal("0.20"),
        )
    )
    with pytest.raises(ReplayAuthorizationError, match="actual spend exceeds"):
        replay_bounded(
            scope=scope,
            items=[_item("item-002")],
            checkpoint_path=tmp_path / "overspend.json",
            state_namespace=f"{_TEST_STATE_NAMESPACE}-overspend",
            process_batch=overspend_processor,
            estimate_batch_cost=lambda _batch: Decimal("0.10"),
            write_silver=writer,
            read_processed_ids=MagicMock(return_value=set()),
            paid_processing_authorized=True,
            approved_estimate=Decimal("0.10"),
            actual_spend_cap=Decimal("0.15"),
        )
    writer.assert_not_called()
    overspend_ledger = replay.ReplaySpendLedger.model_validate_json(
        replay._spend_ledger_path(scope, f"{_TEST_STATE_NAMESPACE}-overspend").read_text()
    )
    assert overspend_ledger.actual_spend == Decimal("0.20")


def test_cumulative_remaining_budget_survives_restart(tmp_path: Path) -> None:
    scope = _scope()
    processed: set[str] = set()
    items = [_item(f"item-{index:02d}") for index in range(26)]
    budgets: list[Any] = []
    actual_costs = iter((Decimal("0.20"), Decimal("0.10")))

    def process(batch: Sequence[Any], budget: Any) -> Any:
        budgets.append(budget)
        return ReplayBatchResult(
            rows=[
                {
                    "item_id": item.item_id,
                    "source": item.source,
                    "date": item.trading_date,
                    "market": item.market,
                    "instrument": item.instrument,
                }
                for item in batch
            ],
            actual_cost=next(actual_costs),
        )

    def write(rows: Sequence[Mapping[str, Any]]) -> None:
        processed.update(str(row["item_id"]) for row in rows)

    def read(_scope: Any) -> set[str]:
        return set(processed)

    def estimate(batch: Sequence[Any]) -> Decimal:
        return Decimal("0.01") * len(batch)

    checkpoint_path = tmp_path / "cumulative.json"
    first = replay_bounded(
        scope=scope,
        items=items,
        checkpoint_path=checkpoint_path,
        process_batch=process,
        estimate_batch_cost=estimate,
        write_silver=write,
        read_processed_ids=read,
        paid_processing_authorized=True,
        approved_estimate=Decimal("0.35"),
        actual_spend_cap=Decimal("0.35"),
    )

    assert first.actual_spend == Decimal("0.30")
    assert first.remaining_allowance == Decimal("0.05")
    assert [budget.remaining_allowance for budget in budgets] == [Decimal("0.35"), Decimal("0.15")]
    assert [budget.prior_actual_spend for budget in budgets] == [Decimal("0"), Decimal("0.20")]

    restart_processor = MagicMock()
    resumed = replay_bounded(
        scope=scope,
        items=items,
        checkpoint_path=checkpoint_path,
        process_batch=restart_processor,
        estimate_batch_cost=estimate,
        write_silver=write,
        read_processed_ids=read,
        paid_processing_authorized=True,
        approved_estimate=Decimal("0.35"),
        actual_spend_cap=Decimal("0.35"),
    )
    restart_processor.assert_not_called()
    assert resumed.actual_spend == Decimal("0.30")
    assert resumed.remaining_allowance == Decimal("0.05")
    assert resumed.already_processed_count == 26


def test_interrupted_paid_call_reservation_blocks_unaccounted_restart(tmp_path: Path) -> None:
    scope = _scope()
    checkpoint_path = tmp_path / "interrupted.json"
    interrupted = MagicMock(side_effect=RuntimeError("synthetic callback interruption"))
    kwargs = {
        "scope": scope,
        "items": [_item("item-001")],
        "checkpoint_path": checkpoint_path,
        "estimate_batch_cost": lambda _batch: Decimal("0.10"),
        "write_silver": MagicMock(),
        "read_processed_ids": MagicMock(return_value=set()),
        "paid_processing_authorized": True,
        "approved_estimate": Decimal("1.00"),
        "actual_spend_cap": Decimal("2.00"),
    }

    with pytest.raises(RuntimeError, match="interruption"):
        replay_bounded(process_batch=interrupted, **kwargs)
    retry_processor = MagicMock()
    with pytest.raises(ReplayAuthorizationError, match="interrupted paid batch is pending"):
        replay_bounded(process_batch=retry_processor, **kwargs)
    retry_processor.assert_not_called()


def test_crash_after_provider_cost_before_silver_keeps_cost_and_pending_reservation(tmp_path: Path) -> None:
    scope = _scope()
    checkpoint_path = tmp_path / "crash-before-silver.json"
    processor = MagicMock(
        return_value=ReplayBatchResult(
            rows=[
                {
                    "item_id": "item-001",
                    "source": scope.source,
                    "date": scope.trading_date,
                    "market": scope.market,
                    "instrument": "AAPL",
                }
            ],
            actual_cost=Decimal("0.60"),
        )
    )
    kwargs = {
        "scope": scope,
        "items": [_item("item-001")],
        "checkpoint_path": checkpoint_path,
        "process_batch": processor,
        "estimate_batch_cost": lambda _batch: Decimal("0.60"),
        "write_silver": MagicMock(side_effect=OSError("simulated crash before Silver write")),
        "read_processed_ids": MagicMock(return_value=set()),
        "paid_processing_authorized": True,
        "approved_estimate": Decimal("1.00"),
        "actual_spend_cap": Decimal("1.00"),
    }

    with pytest.raises(OSError, match="before Silver"):
        replay_bounded(**kwargs)

    ledger_path = replay._spend_ledger_path(scope, _TEST_STATE_NAMESPACE)
    ledger = replay.ReplaySpendLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    assert ledger.actual_spend == Decimal("0.60")
    assert ledger.estimated_cost == Decimal("0.60")
    assert ledger.pending_item_ids == ["item-001"]

    retry_processor = MagicMock()
    kwargs["process_batch"] = retry_processor
    with pytest.raises(ReplayAuthorizationError, match="interrupted paid batch is pending"):
        replay_bounded(**kwargs)
    retry_processor.assert_not_called()


def test_durable_silver_recovers_pending_ledger_without_reprocessing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scope = _scope()
    checkpoint_path = tmp_path / "recover-pending.json"
    processed: set[str] = set()
    processor, writer, reader = _synthetic_callbacks(processed)
    original_write_ledger = replay._write_spend_ledger_atomic

    def crash_before_reconcile(path: Path, ledger: Any) -> None:
        if not ledger.pending_item_ids:
            raise OSError("simulated crash before ledger reconciliation")
        original_write_ledger(path, ledger)

    with monkeypatch.context() as patcher:
        patcher.setattr(replay, "_write_spend_ledger_atomic", crash_before_reconcile)
        with pytest.raises(OSError, match="before ledger reconciliation"):
            replay_bounded(
                scope=scope,
                items=[_item("item-001")],
                checkpoint_path=checkpoint_path,
                process_batch=processor,
                estimate_batch_cost=_estimate,
                write_silver=writer,
                read_processed_ids=reader,
                **_authorized(),
            )

    assert processed == {"item-001"}
    ledger_path = replay._spend_ledger_path(scope, _TEST_STATE_NAMESPACE)
    interrupted = replay.ReplaySpendLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    assert interrupted.pending_item_ids == ["item-001"]
    assert interrupted.actual_spend == Decimal("0.01")

    retry_processor = MagicMock()
    resumed = replay_bounded(
        scope=scope,
        items=[_item("item-001")],
        checkpoint_path=checkpoint_path,
        process_batch=retry_processor,
        estimate_batch_cost=_estimate,
        write_silver=writer,
        read_processed_ids=reader,
        **_authorized(),
    )
    retry_processor.assert_not_called()
    assert resumed.actual_spend == Decimal("0.01")
    repaired = replay.ReplaySpendLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    assert repaired.pending_item_ids == []


def test_concurrent_processes_share_one_cumulative_scope_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    auxiliary_root = tmp_path / "canonical-data"
    monkeypatch.setattr(replay, "DATA_DIR", auxiliary_root)
    silver = tmp_path / "silver.ids"
    calls = tmp_path / "provider.calls"
    ready_dir = tmp_path / "barrier"
    ready_dir.mkdir()
    worker_script = """
import json, sys, time
from decimal import Decimal
from pathlib import Path
import equity_lake.ingestion.replay as replay_module
from equity_lake.ingestion.replay import ReplayBatchResult, ReplayItem, ReplayScope, replay_bounded
checkpoint_dir, silver, calls, ready_dir, auxiliary_root, item_id = sys.argv[1:]
replay_module.DATA_DIR = Path(auxiliary_root)
scope = ReplayScope(source="synthetic-news", trading_date="2026-10-17", market="us_equity", instruments=("AAPL", "MSFT"))
(ready_dir_path := Path(ready_dir) / f"ready-{item_id}").touch()
deadline = time.monotonic() + 10
while len(list(Path(ready_dir).glob("ready-*"))) < 2:
    if time.monotonic() >= deadline:
        raise SystemExit("barrier timeout")
    time.sleep(0.01)
item = ReplayItem(
    item_id=item_id, source=scope.source, trading_date=scope.trading_date,
    market=scope.market, instrument="AAPL")
def process(batch, _budget):
    with open(calls, "a", encoding="utf-8") as handle:
        handle.write(batch[0].item_id + "\\n")
    rows = [
        {"item_id": row.item_id, "source": row.source, "date": row.trading_date,
         "market": row.market, "instrument": row.instrument}
        for row in batch
    ]
    return ReplayBatchResult(rows=rows, actual_cost=Decimal("0.60"))
def write(rows):
    with open(silver, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row["item_id"] + "\\n")
def read(_scope):
    try:
        return set(Path(silver).read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return set()
try:
    result = replay_bounded(scope=scope, items=[item], checkpoint_path=Path(checkpoint_dir) / f"{item_id}.json",
        state_namespace="synthetic-silver", process_batch=process,
        estimate_batch_cost=lambda _batch: Decimal("0.60"), write_silver=write, read_processed_ids=read,
        paid_processing_authorized=True, approved_estimate=Decimal("1.00"), actual_spend_cap=Decimal("1.00"))
    print(json.dumps({"status": "ok", "actual_spend": str(result.actual_spend)}))
except Exception as exc:
    print(json.dumps({"status": "error", "error": str(exc)}))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker_script, str(checkpoint_dir), str(silver), str(calls), str(ready_dir), str(auxiliary_root), item_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for item_id in ("item-001", "item-002")
    ]
    outcomes = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        outcomes.append(json.loads(stdout.strip().splitlines()[-1]))

    assert sum(outcome["status"] == "ok" for outcome in outcomes) == 1
    assert sum(outcome.get("error", "").find("cumulative scoped batch estimate") >= 0 for outcome in outcomes) == 1
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 1
    assert len(silver.read_text(encoding="utf-8").splitlines()) == 1
    ledger_path = replay._spend_ledger_path(_scope(), "synthetic-silver")
    assert ledger_path.parent == auxiliary_root / "replay" / hashlib.sha256(b"synthetic-silver").hexdigest()
    assert ledger_path.with_name(f".{ledger_path.name}.lock").is_file()
    ledger = replay.ReplaySpendLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    assert ledger.actual_spend == Decimal("0.60")
    assert ledger.estimated_cost == Decimal("0.60")
    assert ledger.pending_item_ids == []


def test_process_crash_releases_scope_lock_but_keeps_reservation(tmp_path: Path) -> None:
    checkpoint = tmp_path / "process-crash-before.json"
    retry_checkpoint = tmp_path / "process-crash-after.json"
    auxiliary_root = replay.DATA_DIR
    crash_script = """
import os, sys
from decimal import Decimal
from pathlib import Path
import equity_lake.ingestion.replay as replay_module
from equity_lake.ingestion.replay import ReplayItem, ReplayScope, replay_bounded
replay_module.DATA_DIR = Path(sys.argv[2])
scope = ReplayScope(source="synthetic-news", trading_date="2026-10-17", market="us_equity", instruments=("AAPL",))
def crash(_batch, _budget):
    os._exit(73)
item = ReplayItem(item_id="crash-item", source=scope.source, trading_date=scope.trading_date,
    market=scope.market, instrument="AAPL")
replay_bounded(scope=scope, items=[item], checkpoint_path=Path(sys.argv[1]), state_namespace="synthetic-silver",
    process_batch=crash, estimate_batch_cost=lambda _batch: Decimal("0.10"),
    write_silver=lambda _rows: None, read_processed_ids=lambda _scope: set(), paid_processing_authorized=True,
    approved_estimate=Decimal("1.00"), actual_spend_cap=Decimal("2.00"))
"""
    crashed = subprocess.run([sys.executable, "-c", crash_script, str(checkpoint), str(auxiliary_root)], timeout=10, check=False)
    assert crashed.returncode == 73

    verify_script = """
import sys
from decimal import Decimal
from pathlib import Path
import equity_lake.ingestion.replay as replay_module
from equity_lake.ingestion.replay import ReplayAuthorizationError, ReplayItem, ReplayScope, replay_bounded
replay_module.DATA_DIR = Path(sys.argv[2])
scope = ReplayScope(source="synthetic-news", trading_date="2026-10-17", market="us_equity", instruments=("AAPL",))
try:
    replay_bounded(scope=scope, items=[ReplayItem(item_id="crash-item", source=scope.source,
        trading_date=scope.trading_date, market=scope.market, instrument="AAPL")],
        checkpoint_path=Path(sys.argv[1]), state_namespace="synthetic-silver", process_batch=lambda _batch, _budget: None,
        estimate_batch_cost=lambda _batch: Decimal("0.10"), write_silver=lambda _rows: None,
        read_processed_ids=lambda _scope: set(), paid_processing_authorized=True,
        approved_estimate=Decimal("1.00"), actual_spend_cap=Decimal("2.00"))
except ReplayAuthorizationError as exc:
    print(exc)
else:
    raise SystemExit("ambiguous provider call was not blocked")
"""
    verified = subprocess.run(
        [sys.executable, "-c", verify_script, str(retry_checkpoint), str(auxiliary_root)],
        timeout=10,
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert "interrupted paid batch is pending" in verified.stdout


def test_checkpoint_symlink_and_dangling_parent_fail_closed(tmp_path: Path) -> None:
    scope = _scope()
    processor = MagicMock()
    target = tmp_path / "checkpoint-target.json"
    replay._write_checkpoint_atomic(target, ReplayCheckpoint(scope=scope, completed_item_ids=[]))
    checkpoint_link = tmp_path / "checkpoint-link.json"
    checkpoint_link.symlink_to(target)
    dangling_parent = tmp_path / "dangling-parent"
    dangling_parent.symlink_to(tmp_path / "missing-checkpoint-parent", target_is_directory=True)

    for path in (checkpoint_link, dangling_parent / "checkpoint.json"):
        with pytest.raises(ReplayCheckpointError):
            replay_bounded(
                scope=scope,
                items=[_item("item-001")],
                checkpoint_path=path,
                process_batch=processor,
                estimate_batch_cost=_estimate,
                write_silver=MagicMock(),
                read_processed_ids=MagicMock(return_value=set()),
                **_authorized(),
            )
    processor.assert_not_called()


def test_checkpoint_permission_error_fails_before_paid_processing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scope = _scope()
    checkpoint_path = tmp_path / "permission.json"
    checkpoint_path.write_text("{}", encoding="utf-8")
    original_lstat = Path.lstat

    def deny_checkpoint_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == checkpoint_path:
            raise PermissionError("synthetic checkpoint permission denied")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", deny_checkpoint_stat)
    processor = MagicMock()

    with pytest.raises(ReplayCheckpointError, match="Unable to inspect"):
        replay_bounded(
            scope=scope,
            items=[_item("item-001")],
            checkpoint_path=checkpoint_path,
            process_batch=processor,
            estimate_batch_cost=_estimate,
            write_silver=MagicMock(),
            read_processed_ids=MagicMock(return_value=set()),
            **_authorized(),
        )
    processor.assert_not_called()


def test_corrupt_paid_spend_ledger_fails_closed(tmp_path: Path) -> None:
    scope = _scope()
    checkpoint_path = tmp_path / "ledger.json"
    corrupt_ledger_path = replay._spend_ledger_path(_scope(), _TEST_STATE_NAMESPACE)
    corrupt_ledger_path.parent.mkdir(parents=True)
    corrupt_ledger_path.write_text("{invalid ledger", encoding="utf-8")
    processor = MagicMock()

    with pytest.raises(ReplayAuthorizationError, match="Invalid paid-work spend ledger"):
        replay_bounded(
            scope=scope,
            items=[_item("item-001")],
            checkpoint_path=checkpoint_path,
            process_batch=processor,
            estimate_batch_cost=_estimate,
            write_silver=MagicMock(),
            read_processed_ids=MagicMock(return_value=set()),
            **_authorized(),
        )
    processor.assert_not_called()


def test_historical_replay_exposes_no_notification_callback() -> None:
    parameters = inspect.signature(replay_bounded).parameters
    assert not {"notify", "notify_fn", "send_notifications"}.intersection(parameters)
