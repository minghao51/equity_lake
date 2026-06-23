# Project Streamlining Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Streamline the repo for new users by removing the mkdocs-jupyter support stack, relocating notebooks to a top-level folder, migrating the live CI script into the package, and archiving one-time migrations.

**Architecture:** Notebooks become standalone runnable artifacts at `notebooks/`. The `scripts/` directory is eliminated: the one CI-active script moves into `src/equity_lake/devtools/` (joining `test_data.py`), and the two spent migration scripts move to `docs/developer/history/`. All stale doc references are corrected in the same pass.

**Tech Stack:** uv, Typer CLI, mkdocs Material, structlog, ruff/mypy.

## Pre-flight risk

Notebooks currently live at `docs/notebooks/` and import source via relative paths like `../../src/equity_lake/...` (visible in saved outputs of `04-hamilton-features.ipynb`). Moving to top-level `notebooks/` changes the depth by one level. Since `mkdocs-jupyter` is configured with `execute: false`, build is unaffected — but local re-execution of notebooks would need `sys.path` lines updated. Flag as a verification step.

## Dispatch grouping

This is a refactor/cleanup (no new logic). Subagents will run the verification suite (ruff, mypy, pytest, mkdocs build) as their "tests". Tasks are grouped by coupling:

- **Group A (Tasks 1+2):** Move notebooks + remove mkdocs-jupyter stack
- **Group B (Task 3):** Migrate sync_pages_schedule into devtools
- **Group C (Tasks 4+5):** Archive migration scripts + remove scripts/ dir + config exclusions
- **Group D (Tasks 6+7):** Fix stale doc references + update developer README
- **Task 8:** Final verification (controller-run)

---

### Task 1: Move notebooks to top-level `notebooks/`

**Files:**
- Move: `docs/notebooks/` -> `notebooks/` (10 `.ipynb` + `data/` subfolder)

**Step 1:** `git mv docs/notebooks notebooks`
**Step 2:** Verify: `ls notebooks/` shows 10 `.ipynb` files + `data/`
**Step 3:** Commit: `git commit -m "refactor: move notebooks to top-level notebooks/"`

---

### Task 2: Remove the mkdocs-jupyter support stack

**Files:**
- Modify: `mkdocs.yml` (lines 10, 38-48, 63-73, 93-97)
- Delete: `overrides/main.html`
- Delete: `docs/javascripts/jupyter-theme-bridge.js`
- Delete: `docs/stylesheets/jupyter-fix.css`

**Step 1: Edit `mkdocs.yml`** — remove these blocks:
- Line 10: `custom_dir: overrides` (delete line)
- Lines 38-48: entire `Notebooks:` nav block
- Lines 63-73: entire `mkdocs-jupyter:` plugin block
- Lines 93-94: `extra_css:` + `- stylesheets/jupyter-fix.css`
- Lines 96-97: `extra_javascript:` + `- javascripts/jupyter-theme-bridge.js`

**Step 2:** `rm -rf overrides/ docs/javascripts/ docs/stylesheets/`
**Step 3:** Verify docs still build: `uv run mkdocs build --strict`
**Step 4:** Commit: `git commit -m "refactor: drop mkdocs-jupyter stack (overrides/js/css)"`

---

### Task 3: Migrate `sync_pages_schedule.py` into the package

**Files:**
- Move: `scripts/sync_pages_schedule.py` -> `src/equity_lake/devtools/sync_schedule.py`
- Modify: `src/equity_lake/devtools/sync_schedule.py:12` (path computation)
- Modify: `.github/workflows/pages.yml:41`
- Modify: `docs/user-guide/20260406-dashboard-hosting.md:39-40`
- Modify: `docs/20260406-roadmap-coverage.md:12`

**Step 1:** `git mv scripts/sync_pages_schedule.py src/equity_lake/devtools/sync_schedule.py`

**Step 2: Fix ROOT path** in the moved file. Current (line 12):
```python
ROOT = Path(__file__).resolve().parents[1]
```
New (depth changes from `scripts/` -> `src/equity_lake/devtools/`):
```python
ROOT = Path(__file__).resolve().parents[3]
```

**Step 3: Update CI** — `.github/workflows/pages.yml:41`:
```yaml
run: uv run python -m equity_lake.devtools.sync_schedule --check
```

**Step 4: Update `docs/user-guide/20260406-dashboard-hosting.md:39-40`:**
```bash
uv run python -m equity_lake.devtools.sync_schedule
uv run python -m equity_lake.devtools.sync_schedule --check
```

**Step 5: Update `docs/20260406-roadmap-coverage.md:12`:**
```
- GitHub Actions schedule drift is checked with `uv run python -m equity_lake.devtools.sync_schedule --check`.
```

**Step 6: Verify:** `uv run python -m equity_lake.devtools.sync_schedule --check`
**Step 7:** Commit: `git commit -m "refactor: move sync_pages_schedule into devtools package"`

---

### Task 4: Archive one-time migration scripts

**Files:**
- Create: `docs/developer/history/` directory
- Move: `scripts/migrate_to_delta.py` -> `docs/developer/history/`
- Move: `scripts/migrate_to_medallion.py` -> `docs/developer/history/`
- Create: `docs/developer/history/README.md`

**Step 1:** `mkdir -p docs/developer/history`
**Step 2:** `git mv scripts/migrate_to_delta.py docs/developer/history/`
**Step 3:** `git mv scripts/migrate_to_medallion.py docs/developer/history/`

**Step 4: Create `docs/developer/history/README.md`:**
```markdown
# History

Superseded one-time migration scripts and archived implementation artifacts.

- `migrate_to_delta.py` — one-time Hive->Delta Lake migration (executed 2026-06)
- `migrate_to_medallion.py` — one-time flat->Medallion layout migration (executed 2026-06; see `docs/plans/20260615-medallion-architecture-migration.md`)

These scripts are retained for audit provenance and are not run in normal operation.
```

**Step 5:** Commit: `git commit -m "refactor: archive spent migration scripts to docs/developer/history/"`

---

### Task 5: Remove empty `scripts/` dir + clean stale config

**Files:**
- Delete: `scripts/` (now empty, except `__pycache__/`)
- Modify: `.dockerignore:34`
- Modify: `pyproject.toml:216`

**Step 1:** `rm -rf scripts/`
**Step 2: `.dockerignore:34`** — remove the line `scripts/`
**Step 3: `pyproject.toml:216`** — change:
```toml
exclude = ["scripts/", "tests/"]
```
to:
```toml
exclude = ["tests/"]
```
**Step 4:** Commit: `git commit -m "chore: remove empty scripts/ dir and stale exclusions"`

---

### Task 6: Fix stale doc references to deleted scripts

**Files:**
- Modify: `docs/developer/architecture/STRUCTURE.md` (lines 3, 33, 144-146, 739)
- Modify: `docs/developer/architecture/CONCERNS.md` (lines 89-91)
- Modify: `docs/developer/architecture/INTEGRATIONS.md` (lines 214-215, 269)
- Modify: `docs/developer/architecture/TESTING.md` (lines 812, 820)

These reference deleted scripts: `scripts/ingest_daily.py`, `scripts/query_example.py`, `scripts/sync_from_s3.py`, `scripts/generate_test_data.py`.

**Replacements:**
| Old | New |
|-----|-----|
| `scripts/ingest_daily.py` | `uv run equity ingest` / `src/equity_lake/ingestion/` |
| `scripts/query_example.py` | `uv run equity query` |
| `scripts/sync_from_s3.py` | `src/equity_lake/storage/s3.py` |
| `scripts/generate_test_data.py` | `uv run equity bootstrap sample` / `src/equity_lake/devtools/test_data.py` |

**Note:** `STRUCTURE.md` already has a stale-warning at line 3 pointing to `developer-guide/project-structure.md`. Update the script references inline but leave the "stale" banner intact (it's accurate).

**Step 1:** Edit each file with the table's replacements.
**Step 2:** Commit: `git commit -m "docs: fix stale script references in architecture docs"`

---

### Task 7: Update `docs/developer/README.md` History section

**Files:**
- Modify: `docs/developer/README.md:20-25`

The History section currently only mentions `reports/`. Add the new `history/` folder.

**Step 1:** Edit the History section:
```markdown
## History

Superseded notes, implementation reports, and archived scripts:

- [Archived Scripts](history/README.md) — One-time migration scripts retained for audit
- [Reports](../reports/README.md) — Operational analyses and handoff notes kept in the repo
- Git history — superseded implementation details and older experiments
```

**Step 2:** Commit: `git commit -m "docs: add history/ to developer README"`

---

### Task 8: Verification (no commit)

**Step 1:** `uv run ruff check .`
**Step 2:** `uv run mypy src`
**Step 3:** `uv run pytest -q -m "not integration"`
**Step 4:** `uv run mkdocs build --strict`
**Step 5:** `uv run python -m equity_lake.devtools.sync_schedule --check`
**Step 6:** Spot-check a notebook still opens (manual)

---

## Summary of net changes

| Before | After |
|--------|-------|
| `docs/notebooks/` (10 files) | `notebooks/` (top-level) |
| `overrides/main.html` | deleted |
| `docs/javascripts/` | deleted |
| `docs/stylesheets/jupyter-fix.css` | deleted |
| `scripts/sync_pages_schedule.py` | `src/equity_lake/devtools/sync_schedule.py` |
| `scripts/migrate_to_delta.py` | `docs/developer/history/migrate_to_delta.py` |
| `scripts/migrate_to_medallion.py` | `docs/developer/history/migrate_to_medallion.py` |
| `scripts/` dir | deleted |
| mkdocs-jupyter plugin | removed |
| 4 stale architecture docs | corrected |
