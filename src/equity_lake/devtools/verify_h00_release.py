"""Verify the composed H00 release artifact before it is uploaded (plan §2.4).

Module CLI entrypoint (``python -m``, invoked by ``.github/workflows/h00-publish.yml``):
uses plain ``print``/``stderr`` output instead of structlog by devtool convention.

The check runs on the *last* bytes that would become public, after the builder has
already failed closed, so it re-proves the allowlist instead of trusting the build:

* exactly the three allowlisted names, no nested directory, no other entry;
* no symbolic link anywhere (the directory itself or any entry);
* size bounds per file and in total;
* path containment — every entry resolves to a direct child of the release root;
* the ``sanitization-report.json`` claims are internally consistent with
  ``payload.json`` and with the needle classes :func:`h00.forbidden_needles` defines
  now, so a report that dropped a class cannot be used as its own evidence;
* a fresh negative rescan of the extracted bytes, reusing the builder's needles, so a
  canary inserted anywhere in the proposed public bytes fails.

Failure messages are value-free on purpose: they name a position, a count or a
forbidden *class*, never a needle, a file's contents or an unexpected name. CI logs of
this job must not become the leak the scan exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

from equity_lake.dashboard import h00

#: The release is exactly these three files and nothing else (plan §2.1).
ALLOWED_FILENAMES: Final = frozenset(h00.ARTIFACT_FILENAMES)
#: Per-file and total bounds: the composed page is a few dozen kilobytes; anything at
#: these limits is not this artifact, so the check refuses rather than uploads it.
MAX_FILE_BYTES: Final = 4 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 8 * 1024 * 1024
MIN_FILE_BYTES: Final = 2


class ReleaseVerificationError(RuntimeError):
    """The release directory is not exactly the verified H00 artifact."""


def _refuse(reason: str) -> ReleaseVerificationError:
    """Build one value-free refusal."""
    return ReleaseVerificationError(f"refused: {reason}")


def _check_entries(root: Path) -> dict[str, Path]:
    """Return the allowlisted entries, refusing symlinks, directories and escapes."""
    if root.is_symlink():
        raise _refuse("the release directory is a symbolic link")
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise _refuse("the release path is not a directory")
    entries: dict[str, Path] = {}
    for position, entry in enumerate(sorted(root.iterdir(), key=lambda path: path.name), start=1):
        if entry.is_symlink():
            raise _refuse(f"entry {position} is a symbolic link")
        if entry.is_dir():
            raise _refuse(f"entry {position} is a nested directory; the release is flat")
        if not entry.is_file():
            raise _refuse(f"entry {position} is not a regular file")
        name = entry.name
        if name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise _refuse(f"entry {position} does not have a plain file name")
        if (resolved_root / name).resolve() != resolved_root / name:
            raise _refuse(f"entry {position} does not resolve to a direct child of the release directory")
        if name not in entries:
            entries[name] = entry
    unexpected = [name for name in entries if name not in ALLOWED_FILENAMES]
    missing = sorted(ALLOWED_FILENAMES.difference(entries))
    if unexpected or missing:
        raise _refuse(
            f"the file set is not the allowlist (unexpected entries: {len(unexpected)}, missing entries: {len(missing)}, "
            f"allowlist: {', '.join(sorted(ALLOWED_FILENAMES))})"
        )
    return entries


def _check_sizes(entries: dict[str, Path]) -> int:
    """Refuse an empty, oversized or over-budget file; return the total byte count."""
    total = 0
    for position, name in enumerate(sorted(entries), start=1):
        size = entries[name].stat().st_size
        if size < MIN_FILE_BYTES or size > MAX_FILE_BYTES:
            raise _refuse(f"entry {position} is outside the size bounds")
        total += size
    if total > MAX_TOTAL_BYTES:
        raise _refuse("the release total is outside the size bounds")
    return total


def _read_texts(entries: dict[str, Path]) -> dict[str, str]:
    """Decode every release file as UTF-8, or refuse."""
    texts: dict[str, str] = {}
    for position, name in enumerate(sorted(entries), start=1):
        try:
            texts[name] = entries[name].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _refuse(f"entry {position} could not be read as UTF-8 text") from exc
    return texts


def _load_json(texts: dict[str, str], name: str, position: int) -> Any:
    try:
        return json.loads(texts[name])
    except (KeyError, ValueError) as exc:
        raise _refuse(f"entry {position} is not valid JSON") from exc


def _check_report(texts: dict[str, str]) -> None:
    """The report must be consistent with the payload and with today's needle classes."""
    payload = _load_json(texts, h00.PAYLOAD_FILENAME, 1)
    report = _load_json(texts, h00.REPORT_FILENAME, 2)
    if not isinstance(payload, dict) or not isinstance(report, dict):
        raise _refuse("the payload or the report is not a JSON object")
    if report.get("passed") is not True:
        raise _refuse("the sanitization report does not record a passing scan")
    if report.get("root") != "demo_lake_only":
        raise _refuse("the sanitization report does not record the demo-lake root")
    if report.get("provenance") != "synthetic":
        raise _refuse("the sanitization report does not record synthetic provenance")
    if list(report.get("files") or []) != list(h00.ARTIFACT_FILENAMES):
        raise _refuse("the sanitization report does not list the allowlist")
    if report.get("build_utc") != payload.get("built_at"):
        raise _refuse("the sanitization report and the payload disagree on the build instant")
    recorded = report.get("negative_scan")
    classes = sorted(h00.forbidden_needles(Path(".")).keys())
    if not isinstance(recorded, dict) or recorded.get("state") != "clean" or sorted(recorded.get("classes") or []) != classes:
        raise _refuse("the sanitization report does not carry the current forbidden classes")


def verify_release(directory: Path | str) -> dict[str, Any]:
    """Verify one composed release directory; raise :class:`ReleaseVerificationError`.

    Returns a small value-free summary (counts and bytes) for the CI log.
    """
    root = Path(directory).expanduser()
    if not root.exists():
        raise _refuse("the release directory does not exist")
    entries = _check_entries(root)
    total = _check_sizes(entries)
    texts = _read_texts(entries)
    _check_report(texts)
    # The scan is computed over the bytes that would be uploaded, with the builder's
    # own needles: a canary inserted after the build is caught here, not trusted.
    hits = h00.scan_contents(texts, h00.forbidden_needles(root.resolve()))
    if hits:
        raise _refuse(f"the release bytes hit forbidden classes: {', '.join(sorted(hits))}")
    return {"files": len(entries), "bytes": total}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Verify the composed H00 release artifact before upload")
    parser.add_argument("--dir", required=True, help="Release directory to verify (the artifact to be uploaded)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint: exit 1 on any violation, with a value-free message."""
    args = parse_args(argv)
    try:
        summary = verify_release(args.dir)
    except ReleaseVerificationError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"release verification passed: {summary['files']} allowlisted files, {summary['bytes']} bytes, negative scan clean")


if __name__ == "__main__":
    main()
