"""Reset the local lake artifacts before real ingestion (handoff R7, decision D3).

The canonical lake may hold synthetic rows from ``demo seed`` / ``make preload``,
and only the corporate-action coverage proofs and Gold ``action_coverage_source``
carry a marker — Bronze price rows use ``identity_provenance =
current_ticker_config``, the same label a real config-resolved ingestion writes.
``make reset-lake`` is the supported way back to an empty lake:

- it removes exactly ``data/lake/01_bronze``, ``data/lake/02_silver``,
  ``data/lake/03_gold``, ``data/lake/04_platinum/predictions`` and ``data/models``
  (the predictions subpath only, never the whole platinum layer);
- ``--dry-run`` (``make reset-lake DRY=1``) lists those paths and writes nothing;
- without a dry run it never follows a symlinked target and requires confirmation
  (``--yes`` for non-interactive use), and refuses, deleting nothing, unless the
  read-only doctor observation
  (:func:`equity_lake.signals.readiness.observe_synthetic_provenance`) reports a
  ``synthetic`` or ``empty`` lake. ``unknown`` refuses because the existing markers
  cannot prove the lake is synthetic, and ``empty`` means no rows in *any* Delta
  dataset under the reset targets — every dataset in those trees is counted, not an
  enumerated subset — so a lake holding only ingested Bronze rows, or rows only in
  ``02_silver/news_sentiment``/``01_bronze/macro``, can never be treated as empty.
  ``data/models`` holds derived artifacts with no rows; it stays a reset target but
  takes no part in the emptiness judgement.

    uv run python -m equity_lake.devtools.reset_lake --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from equity_lake.core.paths import (
    BRONZE_DIR,
    GOLD_DIR,
    LAKE_DIR,
    MODELS_DIR,
    PLATINUM_PREDICTIONS_DIR,
    SILVER_DIR,
)
from equity_lake.signals.readiness import SyntheticProvenanceObservation, observe_synthetic_provenance

#: States a destructive reset may proceed from. ``real`` and ``unknown`` refuse:
#: the lake may hold real rows and the existing markers cannot prove otherwise.
RESETTABLE_STATES: frozenset[str] = frozenset({"synthetic", "empty"})

#: Lake-relative artifact directories, in removal order. ``PLATINUM_PREDICTIONS_DIR``
#: is a subpath: the rest of ``04_platinum`` (e.g. signal history) is never touched.
_LAKE_ARTIFACTS: tuple[Path, ...] = (BRONZE_DIR, SILVER_DIR, GOLD_DIR, PLATINUM_PREDICTIONS_DIR)


@dataclass(frozen=True)
class ResetTarget:
    """One artifact directory removed by a reset, with its documented label."""

    label: str
    path: Path


def reset_targets(*, lake_dir: Path | None = None, models_dir: Path | None = None) -> tuple[ResetTarget, ...]:
    """Return the exact artifact set removed by ``make reset-lake``.

    Paths derive from the ``core.paths`` constants; ``lake_dir``/``models_dir``
    only re-root them (tmp-dir tests, non-canonical lakes).
    """
    root = LAKE_DIR if lake_dir is None else Path(lake_dir)
    targets = [
        ResetTarget(label=artifact.relative_to(LAKE_DIR).as_posix(), path=root / artifact.relative_to(LAKE_DIR)) for artifact in _LAKE_ARTIFACTS
    ]
    models_root = MODELS_DIR if models_dir is None else Path(models_dir)
    targets.append(ResetTarget(label="models", path=models_root))
    return tuple(targets)


def _payload(*, dry_run: bool, targets: Sequence[ResetTarget], observation: SyntheticProvenanceObservation) -> dict[str, Any]:
    present = [str(target.path) for target in targets if target.path.exists() or target.path.is_symlink()]
    return {
        "command": "reset-lake",
        "ok": True,
        "dry_run": dry_run,
        "synthetic_provenance": observation.state,
        "synthetic_provenance_reason": observation.reason,
        "targets": [{"label": target.label, "path": str(target.path)} for target in targets],
        "present": present,
        "removed": [],
        "errors": [],
        "message": "",
    }


def reset_lake(
    *,
    lake_dir: Path | None = None,
    models_dir: Path | None = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    confirm: Callable[[str], str] = input,
) -> dict[str, Any]:
    """Remove the synthetic lake artifacts, guarded by the doctor provenance state.

    Returns the JSON-ready outcome payload; ``ok`` is false for a refusal or an
    unconfirmed abort, and nothing is deleted in either case.
    """
    targets = reset_targets(lake_dir=lake_dir, models_dir=models_dir)
    observation = observe_synthetic_provenance(lake_dir=lake_dir)
    payload = _payload(dry_run=dry_run, targets=targets, observation=observation)
    if dry_run:
        payload["message"] = f"dry run: listed {len(targets)} target(s), {len(payload['present'])} present; nothing was removed"
        return payload
    # Checked before the provenance gate: a symlinked target is refused on its own
    # merits, so the reason is unambiguous and nothing outside the lake is touched.
    constricted = [target.path for target in targets if target.path.is_symlink()]
    if constricted:
        payload["ok"] = False
        payload["message"] = f"refused: reset target is a symlink and is never followed: {constricted[0]}"
        return payload
    if observation.state not in RESETTABLE_STATES:
        payload["ok"] = False
        payload["message"] = (
            f"refused: the lake reports synthetic_provenance={observation.state!r}, not synthetic or empty; nothing was removed. {observation.reason}"
        )
        return payload
    if not payload["present"]:
        payload["message"] = "nothing to remove: the reset targets are already absent"
        return payload
    if not assume_yes and not _confirmed(confirm, payload["present"]):
        payload["ok"] = False
        payload["message"] = "aborted: no confirmation; nothing was removed"
        return payload

    removed: list[str] = []
    errors: list[str] = []
    for target in targets:
        try:
            if target.path.is_dir():
                shutil.rmtree(target.path)
            elif target.path.exists():
                target.path.unlink()
            else:
                continue
        except OSError as exc:
            errors.append(f"{target.path}: {exc}")
            continue
        removed.append(str(target.path))
    payload["removed"] = removed
    payload["errors"] = errors
    if errors:
        payload["ok"] = False
        payload["message"] = f"removed {len(removed)} of {len(targets)} target(s); {len(errors)} failed"
    else:
        payload["message"] = f"removed {len(removed)} of {len(targets)} target(s)"
    return payload


def _confirmed(confirm: Callable[[str], str], present: Sequence[str]) -> bool:
    """Ask for an explicit yes; a closed stdin or interrupt is a refusal."""
    prompt = f"Remove {len(present)} path(s) under the local lake and data/models? [y/N] "
    try:
        answer = confirm(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in {"y", "yes"}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by ``make reset-lake``."""
    parser = argparse.ArgumentParser(
        prog="python -m equity_lake.devtools.reset_lake",
        description="Remove the local Bronze/Silver/Gold, Platinum predictions, and model artifacts.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List the reset targets and write nothing")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation (non-interactive use)")
    parser.add_argument("--lake-dir", type=Path, default=None, help="Medallion lake root (default: core.paths.LAKE_DIR)")
    parser.add_argument("--models-dir", type=Path, default=None, help="Model artifact directory (default: core.paths.MODELS_DIR)")
    args = parser.parse_args(argv)

    payload = reset_lake(lake_dir=args.lake_dir, models_dir=args.models_dir, dry_run=args.dry_run, assume_yes=args.yes)
    print(json.dumps(payload))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
