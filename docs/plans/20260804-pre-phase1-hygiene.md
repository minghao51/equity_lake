# Pre-Phase-1 Hygiene — Bug Fix + Doc Drift

**Date:** 2026-08-04
**Scope:** Small, low-risk fixes surfaced by the integration recon
([`20260804-integration-recon.md`](./20260804-integration-recon.md) §B7, §B12) that
should land **before** Phase 2 RAG work and the doc-refresh in Phase 1.
**Do this as one or two standalone PRs**, unconnected to feature work.

## H1 — Fix `silver/` → `02_silver/` write-path divergence (B7) 🔴 blocks RAG

**Problem:** the bronze→silver LLM writers pass **non-numbered** `market=` strings,
so they write under `data/lake/silver/` while every other surface (catalog,
`core/paths.py` constants, health checks, `MARKET_DIR_MAP`) uses `02_silver/`.

| File | Current | Fix to |
|---|---|---|
| `src/equity_lake/ingestion/bronze_silver.py` (~L38) | `merge_delta(df, "silver/processed_articles", …)` | `"02_silver/processed_articles"` |
| `src/equity_lake/ingestion/sec_processor.py` (~L217) | `silver_table_name="silver/sec_extractions"` | `"02_silver/sec_extractions"` |

`storage/delta.py:delta_table_path()` does **no** normalization, so the string is
the on-disk path. The lake is currently empty (~60K), so **no data migration** is
needed — just fix the two strings.

**Verification:**
```bash
# add a unit test asserting bronze→silver writes land under 02_silver/
uv run pytest tests/unit -q -k "silver or bronze or sec_processor"
uv run equity catalog-generate        # catalog already references 02_silver — diff should be empty
uv run ruff check . && uv run mypy src
```
Optionally add an idempotency test (re-run `process_unstructured_to_silver` /
`process_sec_bronze_to_silver` → no duplicate keys, same path).

**Why before Phase 2:** `agent/index.py` reads SEC/news via path constants
(`SILVER_SEC_EXTRACTIONS_DIR` = `02_silver/...`). If the bug persists, the RAG
index silently indexes nothing (writes and reads disagree). Reading via constants
works regardless, but fixing the write keeps the lake self-consistent.

## H2 — Doc drift (B12)

| File | Drift | Fix |
|---|---|---|
| `docs/technical_roadmap.md` | references Click (project uses Typer) and a "v0.4.0" that contradicts `pyproject.toml` (0.1.0) | Either correct in place or add a one-line banner pointing to `docs/plans/20260804-portfolio-roadmap.md` as the current plan and mark this file superseded. |
| `docs/developer/architecture/STACK.md` | cites Python 3.11 (actual 3.12+); ruff line-length 88 (actual 150) | Correct versions; verify each tool claim against `pyproject.toml`. |
| `docs/developer/architecture/CONVENTIONS.md` | references nonexistent `core/constants.py`, `core/runtime.py`, `storage/parquet.py` | Replace with current paths (`core/schemas.py`, `storage/delta.py`, etc.). |
| `.env.example` | dead `EQUITY_STORAGE__*` block — no `StorageSettings` model exists; would raise under `extra="forbid"` if set | Remove the block (or implement `StorageSettings` if genuinely wanted). |

These are accuracy fixes only — no behavior change. Re-run `uv run mkdocs build`
after editing to confirm internal links still resolve.

## H3 — Optional: extend `sync_schedule` (B10-adjacent)

Not required now, but note for Phase 3: `devtools/sync_schedule.py` only validates
the **first** `- cron:` in `pages.yml`. When `snapshot.yml` is added (Phase 3),
either reuse `schedule.cron` verbatim or extend the tool with `--workflow <path>`
so both workflows stay in sync with `config/settings.yaml`.

## Out of scope

Everything in the portfolio roadmap/handoffs. This doc is strictly the cleanup
that makes the lake self-consistent and the docs trustworthy before feature work
begins.
