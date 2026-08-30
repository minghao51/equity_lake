# ADR-0005: Native Typer CLI wiring contract

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled)

## Context

The `equity` command was unified from scattered entrypoints. A passthrough
style (one command delegating to argparse scripts) lost Typer help generation
and made exit codes inconsistent.

## Decision

- The CLI is native Typer (no passthrough). Sub-apps are declared in
  `cli/_app.py` and wired with `app.add_typer(<x>_app, name="…")` in
  `cli/__main__.py` **before** importing the command module that decorates
  commands onto them.
- Every command carries docstring help text,
  `Annotated[..., typer.Option("--flag", help="…")]`, and
  `raise typer.Exit(1)` on required failure for deterministic exit status.
- Every command is covered by a help-scan test in
  `tests/unit/test_cli_unified.py`.
- `backtest` is a flat top-level command; sub-commands go under a dedicated
  sub-app (e.g. `report`), never `backtest <sub>`.

## Consequences

- Help output, flags, and exit codes are uniform and testable.
- The import-order constraint is load-bearing; wiring order regressions break
  command registration.
- New commands require the change matrix's CLI row: help text, CLI test,
  user guide.
