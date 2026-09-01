"""Intel runtime preset tests (``ml/_intel.py`` + backend param injection)."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from equity_lake.ml import _intel
from equity_lake.ml.backends import build_estimator

_INTEL_ON = _intel.IntelRuntimeInfo(intel_cpu=True, sklearnex_patched=False, threads=8)
_INTEL_OFF = _intel.IntelRuntimeInfo(intel_cpu=False, sklearnex_patched=False, threads=4)

_INTEL_CPUINFO = "vendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Core(TM) Ultra 5\n"
_AMD_CPUINFO = "vendor_id\t: AuthenticAMD\nmodel name\t: AMD Ryzen 9\n"


@pytest.fixture(autouse=True)
def _fresh_cpu_cache() -> Iterator[None]:
    """Keep the cached CPU detection from leaking across tests."""
    _intel.is_intel_cpu.cache_clear()
    yield
    _intel.is_intel_cpu.cache_clear()


@pytest.fixture
def _clean_thread_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove thread env vars; anything the code writes directly is undone."""
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.delenv(var, raising=False)


def _with_cpuinfo(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(_intel, "_read_cpuinfo", lambda: text)


# --- detection ---------------------------------------------------------------


def test_detects_intel_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_cpuinfo(monkeypatch, _INTEL_CPUINFO)
    assert _intel.is_intel_cpu() is True


def test_rejects_amd_and_missing_cpuinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_cpuinfo(monkeypatch, _AMD_CPUINFO)
    assert _intel.is_intel_cpu() is False
    _with_cpuinfo(monkeypatch, "")
    assert _intel.is_intel_cpu() is False


def test_detection_falls_back_to_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_cpuinfo(monkeypatch, "model name\t: 13th Gen Intel(R) Core(TM) i7\n")
    assert _intel.is_intel_cpu() is True


# --- configure ---------------------------------------------------------------


def test_configure_noop_on_non_intel(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_cpuinfo(monkeypatch, _AMD_CPUINFO)
    info = _intel.configure_intel_runtime()
    assert info.intel_cpu is False
    assert info.sklearnex_patched is False
    assert _intel.intel_thread_count(info) is None


def test_configure_presets_unset_env_on_intel(
    monkeypatch: pytest.MonkeyPatch,
    _clean_thread_env: None,
) -> None:
    _with_cpuinfo(monkeypatch, _INTEL_CPUINFO)
    info = _intel.configure_intel_runtime()
    assert info.intel_cpu is True
    assert info.threads >= 1
    assert os.environ["OMP_NUM_THREADS"] == str(info.threads)
    assert os.environ["MKL_NUM_THREADS"] == str(info.threads)


def test_configure_respects_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_cpuinfo(monkeypatch, _INTEL_CPUINFO)
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    monkeypatch.setenv("MKL_NUM_THREADS", "4")
    info = _intel.configure_intel_runtime()
    assert os.environ["OMP_NUM_THREADS"] == "3"  # explicit environment wins
    assert os.environ["MKL_NUM_THREADS"] == "4"
    assert info.threads >= 1


def test_sklearnex_absence_is_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_cpuinfo(monkeypatch, _INTEL_CPUINFO)
    monkeypatch.setitem(sys.modules, "sklearnex", None)  # import raises ImportError
    info = _intel.configure_intel_runtime()
    assert info.intel_cpu is True
    assert info.sklearnex_patched is False


# --- backend param injection --------------------------------------------------


def test_build_estimator_user_nthread_wins_on_intel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.ml.backends._INTEL_INFO", _INTEL_ON)

    model: Any = build_estimator("xgboost", {"n_estimators": 10, "nthread": 7})
    assert model.get_params()["nthread"] == 7  # user-passed value beats the preset


def test_build_estimator_injects_default_nthread_on_intel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.ml.backends._INTEL_INFO", _INTEL_ON)

    model: Any = build_estimator("xgboost", {"n_estimators": 10})
    assert model.get_params()["nthread"] == _INTEL_ON.threads


def test_build_estimator_leaves_defaults_on_non_intel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.ml.backends._INTEL_INFO", _INTEL_OFF)

    model: Any = build_estimator("xgboost", {"n_estimators": 10})
    assert model.get_params().get("nthread") is None


def test_build_estimator_injects_lightgbm_threads_on_intel(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("lightgbm")
    monkeypatch.setattr("equity_lake.ml.backends._INTEL_INFO", _INTEL_ON)

    model: Any = build_estimator("lightgbm", {"n_estimators": 10})
    assert model.get_params()["num_threads"] == _INTEL_ON.threads
