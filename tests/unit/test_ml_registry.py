"""Tests for the W&B registry adapter (NO-OP path only).

W&B is never installed in the base/CI env and these tests deliberately exercise
only the best-effort no-op path (``WANDB_API_KEY`` unset / missing metadata).
They MUST pass without ``wandb`` installed — the lazy import means the no-op
path returns before any ``import wandb`` is ever reached.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from equity_lake.findings.models import FindingCard
from equity_lake.ml.registry import __all__, log_comparison, log_training_run


def _sample_card(card_id: str = "xgb-vs-lgbm") -> FindingCard:
    return FindingCard(
        id=card_id,
        axis="model",
        claim="LightGBM matches XGBoost OOS",
        verdict="inconclusive",
        conclusion="No significant difference after walk-forward CV.",
        metrics={"oos_accuracy": 0.54, "sharpe": 1.1},
        evidence_refs=["xgb-vs-lgbm/oos.parquet"],
        run_date=date(2026, 8, 6),
        scope={"tickers": 50, "window": "2021-2026"},
    )


@pytest.fixture(autouse=True)
def _no_wandb_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the no-op path for every test in this module."""
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    monkeypatch.delenv("WANDB_ENTITY", raising=False)
    monkeypatch.delenv("WANDB_PROJECT", raising=False)


class TestExports:
    def test_all_exports_both_functions(self) -> None:
        assert set(__all__) == {"log_comparison", "log_training_run"}


class TestTrainingRunNoOp:
    def test_returns_none_without_api_key(self, tmp_path: Path) -> None:
        meta = tmp_path / "AAPL_v1_direction.training_metadata.json"
        meta.write_text(
            '{"ticker":"AAPL","model_mode":"v1_direction","metrics":{"train_accuracy":0.9}}',
            encoding="utf-8",
        )
        assert log_training_run(meta) is None

    def test_safe_when_metadata_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.training_metadata.json"
        # Must not raise even though WANDB_API_KEY is unset; returns None.
        assert log_training_run(missing) is None

    def test_with_shap_artifact_is_noop(self, tmp_path: Path) -> None:
        meta = tmp_path / "model.training_metadata.json"
        meta.write_text(
            '{"ticker":"AAPL","model_mode":"v2_meta_label","metrics":{"val_accuracy":0.55}}',
            encoding="utf-8",
        )
        shap = tmp_path / "shap.json"
        shap.write_text("{}", encoding="utf-8")
        assert log_training_run(meta, shap_artifact=shap) is None


class TestComparisonNoOp:
    def test_returns_none_without_api_key(self) -> None:
        assert log_comparison([_sample_card()], name="xgb-vs-lgbm") is None

    def test_safe_with_empty_cards(self) -> None:
        assert log_comparison([], name="empty") is None

    def test_multi_card_is_noop(self) -> None:
        cards = [_sample_card("meta-label-vs-direction"), _sample_card("xgb-vs-lgbm")]
        assert log_comparison(cards) is None


class TestNoWandbTouch:
    """The no-op path must never import or touch the wandb module."""

    def test_no_attribute_access_on_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        accessed: list[str] = []

        class _WandbSentinel:
            def __getattr__(self, name: str) -> None:
                accessed.append(name)
                raise AssertionError(f"wandb.{name} must not be reached on the no-op path")

        # If the no-op path ever reached ``import wandb``, it would bind this
        # sentinel; any attribute access (``wandb.init``…) records + raises.
        monkeypatch.setitem(sys.modules, "wandb", _WandbSentinel())

        meta = tmp_path / "model.training_metadata.json"
        meta.write_text('{"ticker":"AAPL","model_mode":"v1_direction","metrics":{}}', encoding="utf-8")
        missing = tmp_path / "absent.training_metadata.json"

        assert log_training_run(meta) is None
        assert log_training_run(missing) is None
        assert log_comparison([_sample_card()], name="run-1") is None
        assert log_comparison([], name="empty") is None
        assert accessed == []
