# Handoff 01 — P0: Reconcile working tree + pipeline orchestrator bugs

Priority: P0 — blocks everything. Type: bug fix. Risk: high (uncommitted work).
Suggested agent: one `worker` (serial), then one `reviewer`.

## Situation

- HEAD is `203e9e4` ("Phase 2 — consistency, resilience, and architecture cleanup").
  The working tree holds a further uncommitted refactor: **33 modified files under
  `src/`** (+548/−1338), 2 modified test files, plus untracked `src/equity_lake/agent/`,
  `core/config_models.py`, `core/settings.py`, `dashboard/_common.py`.
- `uv run pytest tests/integration/test_pipeline_orchestrator.py` → **3 failures**:
  - `test_authorized_history_recovery_is_scoped_and_forwards_dry_run`
  - `test_bronze_to_silver_failure_only_disables_article_enrichment`
  - `test_sec_processing_failure_only_disables_sec_enrichment`

## Verified defects in the tree (fix, don't re-litigate)

1. **Wrong dict level for enrichment gates** — `src/equity_lake/pipeline.py:301-303`:
   `results.get("bronze_to_silver", {})` / `results.get("sec_to_silver", {})` read
   top-level keys, but `_run_ingestion_stage` nests them at
   `results["ingestion"]["bronze_to_silver"]` / `["sec_to_silver"]`
   (set in the stage dict, `pipeline.py:105-108` area). Result: `use_enriched` /
   `use_sec` are **always False** → `enriched_sentiment_merged` and
   `sec_extractions_enriched` DAG nodes are unreachable from the orchestrated pipeline.
   Fix: read from `results["ingestion"]`; keep the "stage absent → False" default for
   dry-run/skip paths.
2. **Backfill scoping broken for API callers** — `_backfill_feature_history`
   (`pipeline.py:27-46`) takes `tickers` (unused in body) and forwards only
   `explicit_tickers` to `backfill_date_range` (`pipeline.py:229`). A caller passing
   only `tickers` backfills **every configured ticker for 120 days** — violates the
   `--allow-history-backfill` scoping contract. The CLI masks this by passing both
   (`cli/commands/pipeline.py:55-59`).
   Fix: collapse to a single parameter (breaking internal API — update all callers and
   tests in the same change), or forward `explicit_tickers or tickers`.
3. **Stale failure contract in CLI** — `cli/commands/pipeline.py:16-25`
   (`_pipeline_succeeded`) still tolerates top-level `bronze_to_silver`/`sec_to_silver`
   keys that no longer exist; `tests/unit/test_cli_unified.py:223-245` mocks encode the
   same stale shape. Align CLI + tests with the nested shape in the same change.

## Tasks

- [ ] Decide: land this tree (recommended — it is the continuation of landed Phase 1/2
      commits) vs revert. Do **not** stash-and-forget; the tree contains fixes other
      handoffs depend on.
- [ ] Fix defect 1 (dict level). `results["ingestion"]` is a stage dict in success paths
      but `{"skipped": True, ...}` on dry-run/skip — make the lookup tolerant of both.
- [ ] Fix defect 2 (single scoped parameter). Grep all callers of
      `execute_eod_pipeline` and `_backfill_feature_history`.
- [ ] Fix defect 3 (CLI `_pipeline_succeeded` + its tests).
- [ ] Consider (small): `logger.warning("ingestion_partial_failure")` fires for
      required-market failures too — split required → error-level.
- [ ] Run full validation (below); also run `uv run pytest -n auto`.
- [ ] Commit with a message in the Phase-N series, e.g. `fix(pipeline): stage-result
      nesting, scoped history backfill, CLI contract alignment`.

## Acceptance criteria

- All 3 named integration tests pass; `uv run pytest` fully green.
- `equity pipeline --dry-run` works; a required-market failure still exits 1.
- Backfill called through the pipeline API receives the requested tickers (test asserts
  forward equality).

## Validation

```bash
uv run pytest tests/integration/test_pipeline_orchestrator.py tests/unit/test_cli_unified.py -q
uv run pytest -n auto && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Out of scope

Everything else in the index. Do not "improve" refactored files beyond the three defects.

## Reviewer checklist

- Enrichment gates now actually flip when bronze→silver succeeds/fails (integration tests
  assert both directions).
- No caller of `execute_eod_pipeline` can trigger an unscoped 120-day backfill.
- No unrelated diffs smuggled into the commit.

## Outcome (closed 2026-08-31)

- **Landed:** `f904276` (includes the in-flight Phase-3 refactor it reconciled).
- All three defects fixed: nested `results["ingestion"]` gate lookups (enrichment
  nodes reachable again), scoped history backfill (`explicit_tickers or tickers`
  forwarding), `_pipeline_succeeded` + CLI test mocks aligned to the nested shape.
- Beyond the brief: required-market failures now log at error level; gate
  flip-**on** tests added post-review (reviewer warning); `explicit_tickers=[]`
  semantics pinned by test.
- Note: this commit also swept in pre-existing uncommitted docs/plans material.
