# 20260821-phase3-handoff.md

**Title:** Phase 3 — Consolidation & architectural integrity
**Status:** Planned (not started). Phase 1 committed (`b0fe5a5`); Phase 2 handoff at `20260821-phase2-handoff.md`.
**Author:** Derived from the same `explore`-subagent audit that produced Phases 1–2.
**Depends on:** Phase 1 + Phase 2 (especially **C3** unified path map, **A3** shared Delta reader, **C8** httpx-only, **C4** pydantic validators). Several Phase 3 items are *blocked* until those land.

---

## Context

Phases 1–2 fix bugs and unify the obvious duplicated patterns (paths, retry stack, CLI entrypoints,
Delta reads). Phase 3 is the **cross-cutting consolidation** that those unifications unblock: there
are still *three* validation paradigms, *two* schema sources of truth, and several dead
architectural code paths left in `features/`/`ml/` that were too entangled to safely touch in
Phase 1. These are not silent data-corruption bugs (those were Phase 1), but they are the root
cause of the kind of drift that produced the Phase 1 leaks, and they make the lake's contracts
hard to reason about.

Goal: **one validation contract, one schema source, zero dead architectural code.** This is the
L-effort tier — do it only after Phase 2's uniformity exists, or you will be refactoring against
moving targets.

---

## Prerequisites (must land in Phase 2 first)

- **A3** shared `storage/lake_reader` (so `validation` can read both Delta + parquet uniformly).
- **C3** single market→dir map (so `validation` knows canonical table paths).
- **C8** `httpx`-only sources (so any new validation I/O uses one HTTP stack).
- **C4** pydantic `TickersConfig` (so config-derived schemas are already pydantic).

---

## Backlog (effort / impact)

| ID | Finding | Location | Effort | Impact | Notes / Blocker |
|----|---------|----------|--------|--------|-----------------|
| C2 | **Three divergent validation paradigms.** Ingestion uses pointblank (`validation/schemas.py`); `features/dag/schemas.py` uses unenforced Pydantic row-sampling; `ml/__init__.validate_predictions` is inline pointblank. No single source of truth → the Phase-1 label leak class of bug recurs. | `validation/`, `features/dag/clean_02.py:64-94`, `features/dag/features_03.py:234-282`, `ml/__init__.py` | L | Med-High | **Anchor item.** Migrate `features` boundary checks to `validation/` pointblank contracts; make them *fail-closed* (currently they sample + return the original frame unchanged). |
| C2a | Features boundary validation is dead/unenforced (`validated_ohlcv`, `validated_features`) — only kept alive by tests | `features/dag/clean_02.py`, `features/dag/features_03.py` | M (within C2) | Med | Wire into `compute_technical` + enforce, or delete + remove their tests. |
| S1 | **Schema single source of truth.** `core/schemas.py` column lists (`STANDARD_COLUMNS`, `NEWS_COLUMNS`, …) diverge from `catalog/datasets.py` Delta schemas; a column change can silently drift between catalog contract and `.select()`. | `core/schemas.py`, `catalog/datasets.py` | M | Med | Derive pointblank/catalog schemas from one canonical column registry. |
| D1 | Dead architectural code in `features`/`ml`: `compute_features` (`pipeline.py:190-195`), `FeatureEngineer.db_path` param, `PredictionModel` (`features/dag/schemas.py:42-52`), `compute_concurrency_matrix` (`sample_weights.py:20-34`, has its own test), `DataProfiler.validate_structure` (`profiling.py:92-104`). | multiple | S–M | Low-Med | Remove with their tests; fold `compute_concurrency_matrix`'s math into `compute_sample_uniqueness` if still wanted. |
| F14 | `finbert` is advertised (enum + CLI `--sentiment-method finbert`) but raises `NotImplementedError`. False API surface. | `sentiment/analyzer.py`, `cli/commands/intelligence.py:145` | S (remove) / M (implement) | Med | Decide: implement FinBERT or delete enum value + CLI option + `finbert` branches in `analyze_sentiment_scores`. |
| S2 | `Profiling` always persists a JSON on every call (incl. `ValidationPipeline.validate` in-memory path) — surprising disk side-effect. | `validation/profiling.py:81-82` | S | Low | Separate compute from persist; only write when explicitly requested. |
| S3 | `DriftReport` only detects numeric mean/std/min/max drift; ignores categorical/null-rate shifts (`ticker` distribution, null-rate). | `validation/profiling.py:172-222` | M | Med | Add cardinality / null-rate drift signals. |
| R1 | `ml/forecasting.py:train_model` hand-computes the train/val split instead of reusing `PurgedEmbargoedWalkForwardSplitter` (used by the `validate=True` path). | `ml/forecasting.py:186-194` | M | Med | Route training split through the splitter for consistency. |
| R2 | `storage/delta.py` brittle schema-fallback: substring match on `str(exc)` (`"schema"`/`"column"`) → wrong `merge` fallback on unrelated errors. | `storage/delta.py:110-112` | S | Low-Med | Inspect `deltalake` exception types instead of substring. Requires A3/B5 context. |
| X1 | `signals/generators/__init__.py` omits `MetaLabelSignalGenerator` from `__all__`; `backtest` hardcoded params ignore `ml_config`. | `signals/generators/__init__.py`, `ml/forecasting.py:448-456` | S | Low | Consistency cleanup; fold into C2/R1 pass. |

---

## Recommended execution order

1. **C2 + C2a** — the anchor. Centralize all boundary contracts in `validation/` as pointblank
   schemas; make `features` validation fail-closed. This is the highest-leverage structural fix and
   prevents the Phase-1 leak class from recurring. Requires A3/C3 from Phase 2.
2. **S1** — schema single source of truth (pairs naturally with C2; both define "what a table is").
3. **D1** — remove the dead architectural code (do *after* C2 so you don't delete something C2
   wants to reuse). Remove the matching tests.
4. **R1 + R2 + X1** — ML/storage consistency fixes (medium; safe once C2/S1 exist).
5. **F14** — FinBERT decision (product call: implement or delete).
6. **S2 + S3** — validation profiling polish (lowest urgency; can slip to a later point release).

---

## Definition of done / verification

- Exactly **one** validation entrypoint (`validation/`) is invoked at every ingestion/feature/ML
  boundary; no Pydantic-sampling or inline `validate_*` remains outside `validation/`.
- `uv run pytest tests/unit/test_validation.py tests/unit/test_clean_02.py tests/unit/test_features_03.py`
  green; add a test asserting `features` boundary validation **fails** on a malformed frame
  (guards C2a fail-closed).
- `core/schemas.py` columns and `catalog/datasets.py` schemas are generated from one registry
  (a test asserts they agree).
- `uv run ruff check .` clean; `uv run pytest tests/unit -q` green.
- No `validate_*` or `PredictionModel`/`compute_concurrency_matrix` symbols remain outside their
  intended home (grep gate).

## Handoff notes for the next agent
- Baseline is Phase 1 `b0fe5a5`; assume Phase 2 is merged first (A3, C3, C4, C8 at minimum).
- Keep `validation/` as the **only** boundary-contract location; do not introduce new per-module
  Pydantic samplers.
- The RAG-agent work (`src/equity_lake/agent/`) remains a separate track — do not couple validation
  contracts to it.
- AGENTS.md still binds: Typer-native CLI, structlog, polars primary, tenacity, pointblank at
  ingestion boundaries, `YYYYMMDD-*.md` handoffs.
- Commit per ID (`refactor(validation): C2 centralize boundary contracts in validation/`).
