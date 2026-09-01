"""Intel oneAPI runtime preset for the ML backends (opt-in acceleration).

On Intel CPUs this module presets the threading environment **before**
``xgboost``/``scikit-learn`` are imported and optionally patches scikit-learn
with ``scikit-learn-intelex`` (sklearnex), so MKL/oneDAL-backed kernels are
used by the CPU paths that surround model training (preprocessing, metrics,
calibration). XGBoost/LightGBM themselves get explicit per-backend thread
counts via :func:`equity_lake.ml.backends.build_estimator`.

Design constraints (AGENTS.md):

* Optional dependency — ``scikit-learn-intelex`` lives in the optional
  ``intel`` dependency group; this module degrades to a no-op without it
  (lazy import, friendly absence, never raises on a non-Intel machine).
* Detection is read-only and cached: ``/proc/cpuinfo`` ``vendor_id``
  (``GenuineIntel``). Non-Linux or non-Intel CPUs are a conservative False —
  the pipeline runs unchanged with stock backends.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

#: Thread-related environment variables preset for Intel oneAPI/MKL runtimes.
_THREAD_ENV_VARS: Final[tuple[str, ...]] = ("OMP_NUM_THREADS", "MKL_NUM_THREADS")


@dataclass(frozen=True)
class IntelRuntimeInfo:
    """Outcome of :func:`configure_intel_runtime` (for logging and tests)."""

    intel_cpu: bool
    sklearnex_patched: bool
    threads: int


def _read_cpuinfo() -> str:
    """Return raw ``/proc/cpuinfo`` content (empty string when unavailable)."""
    try:
        return Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _cpu_is_intel(cpuinfo_text: str) -> bool:
    """True when the cpuinfo text reports an Intel vendor/CPU."""
    if "GenuineIntel" in cpuinfo_text:
        return True
    # Some kernels omit vendor_id; fall back to the model name.
    return any(line.lower().startswith("model name") and "intel" in line.lower() for line in cpuinfo_text.splitlines())


@lru_cache(maxsize=1)
def is_intel_cpu() -> bool:
    """Cached CPU-vendor detection (True only for Intel CPUs on Linux)."""
    return _cpu_is_intel(_read_cpuinfo())


def _patch_sklearnex() -> bool:
    """Patch scikit-learn with Intel Extension for Scikit-learn, if available.

    Best-effort by contract: the ``intel`` dependency group is optional, and
    ``patch_sklearn`` is only fully effective when scikit-learn has not been
    imported yet (it logs a warning otherwise). Never raises.
    """
    try:
        import sklearnex  # optional group; mypy override covers stubs

        sklearnex.patch_sklearn()
        return True
    except ImportError:
        logger.debug("sklearnex_not_installed", hint="uv sync --group intel")
        return False
    except Exception as exc:  # pragma: no cover - defensive: never block training
        logger.warning("sklearnex_patch_failed", error=str(exc))
        return False


def configure_intel_runtime() -> IntelRuntimeInfo:
    """Preset the Intel runtime once per process; safe to call repeatedly.

    On Intel CPUs: presets ``OMP_NUM_THREADS``/``MKL_NUM_THREADS`` (only when
    unset — an explicit environment always wins) and patches scikit-learn via
    sklearnex when the optional group is installed. On other CPUs this is a
    no-op that returns a False-flagged :class:`IntelRuntimeInfo`.
    """
    intel = is_intel_cpu()
    if not intel:
        info = IntelRuntimeInfo(intel_cpu=False, sklearnex_patched=False, threads=os.cpu_count() or 1)
        logger.debug("intel_runtime_not_applicable")
        return info

    threads = os.cpu_count() or 1
    preset: dict[str, str] = {}
    for var in _THREAD_ENV_VARS:
        if var not in os.environ:
            os.environ[var] = str(threads)
            preset[var] = str(threads)

    sklearnex_patched = _patch_sklearnex()
    info = IntelRuntimeInfo(intel_cpu=True, sklearnex_patched=sklearnex_patched, threads=threads)
    logger.info(
        "intel_runtime_configured",
        threads=threads,
        sklearnex_patched=sklearnex_patched,
        env_preset=preset or "already-set",
    )
    return info


def intel_thread_count(info: IntelRuntimeInfo | None) -> int | None:
    """Thread count to inject into backend estimators, or None when not Intel.

    ``None`` keeps backend defaults untouched on non-Intel machines (and lets a
    user-provided value win via ``setdefault`` semantics at the call site).
    Pass the :class:`IntelRuntimeInfo` captured from
    :func:`configure_intel_runtime`.
    """
    if info is None or not info.intel_cpu:
        return None
    return info.threads


__all__ = [
    "IntelRuntimeInfo",
    "configure_intel_runtime",
    "intel_thread_count",
    "is_intel_cpu",
]
