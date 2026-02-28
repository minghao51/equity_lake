# Handoff: Finish Post-Migration Cleanup

## Current State

The repo has been migrated off `scripts/` as the execution surface.

Completed in the prior thread:
- Moved the remaining live modules into `src/equity_pipeline/`.
- Moved the `PriceForecaster` domain class into `src/equity_pipeline/forecasting.py`.
- Added a package-native CLI wrapper at `src/equity_pipeline/price_forecaster.py`.
- Repointed package entry points in `pyproject.toml` to `equity_pipeline.*`.
- Updated active tests to import from `equity_pipeline.*`.
- Removed the `scripts/` directory from the workspace.

Validation already run:
- `uv run pytest tests/test_ingest.py tests/test_fetch_macro.py tests/test_query.py tests/test_run_pipeline.py tests/test_ml.py`
- Result: `58 passed`

## Remaining Work

The package migration is functionally complete, but there is still follow-up cleanup to finish.

Known residual items:
- Historical/generated docs still contain `scripts.*` references.
- The copied modules under `src/equity_pipeline/` still need quality cleanup after the mechanical move.
- Packaging/test config should be reviewed for any remaining assumptions from the old layout.
- The full test suite has not been run after the migration.

## Concrete Next Steps

1. Run a full repo-wide search for stale references.
   - Command:
   - `rg -n "from scripts|import scripts|scripts\\." -S .`
   - Triage results into:
   - active code/tests/config
   - active user docs
   - archived/historical/generated docs

2. Clean active docs and tooling references.
   - Update any current-facing docs, shell scripts, Docker configs, or examples that still mention `scripts.*`.
   - Leave archive/history docs alone unless the user wants a full historical rewrite.

3. Refactor the migrated `src/equity_pipeline/*` modules for package quality.
   - Remove stale “script” wording in docstrings where it is now misleading.
   - Normalize imports and remove unused imports introduced by the copy.
   - Consolidate duplicate logging wrappers if both `runtime.py` and `logging_utils.py` expose overlapping behavior.
   - Check path logic in migrated modules for assumptions that were valid only under `scripts/`.

4. Review packaging and coverage configuration.
   - Confirm `pyproject.toml` reflects the package-only layout everywhere.
   - Verify coverage paths are correct for `src/equity_pipeline`.
   - Check whether any extra console entry points should be added now that package modules exist (for example monitor/backfill/feature-engineering if desired).

5. Run the full test suite.
   - Command:
   - `uv run pytest`
   - Fix any regressions outside the previously targeted tests.

6. Run lint if the user wants codebase hygiene tightened after the move.
   - Suggested:
   - `uv run ruff check src tests`

7. Final verification before closing the migration.
   - Ensure `rg --files scripts` returns nothing.
   - Ensure no active code imports `scripts.*`.
   - Summarize any intentionally untouched historical references.

## Risks / Watch Items

- Some migrated modules were copied wholesale before targeted cleanup. Expect style issues and possibly duplicated utility surfaces.
- Archived/generated docs may still intentionally preserve old command examples.
- `uv.lock` and other user-modified files were already dirty before the migration; do not revert unrelated changes.

## Recommended Starting Point For The Next Thread

Start by running:

```sh
rg -n "from scripts|import scripts|scripts\\." -S .
uv run pytest
```

Then clean only the still-live references and test failures first. Treat archival docs as optional unless the user explicitly asks for a full historical scrub.
