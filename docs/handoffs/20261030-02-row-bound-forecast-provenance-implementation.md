# 20261030-02 — Row-Bound Forecast Provenance: Implementation Plan

**Status:** [ADR-0021](../decisions/0021-row-bound-forecast-provenance.md) is **accepted**;
this plan is the execution contract. Work is delegated to worker subagents in the
order P1 → P2 → P3 → P4 → P5, with an adversarial reviewer gate before handoff.
**Baseline commit:** `6adf24d` (branch `main`)
**Recorded:** 2026-10-30
**Related:** ADR-0021 (the decision), ADR-0017 (additive evolution), ADR-0015
(point-in-time proofs), [20261030-01](20261030-01-postpreload-findings-and-remaining-issues.md)
(R4, R5)

## 1. Objective and definition of done

Persist the evidence the inference path already verifies, derive the `/predictions`
statuses from it, and stop hardcoding `not_current_usable`.

Done when:

- A freshly written `v1_direction` prediction row reports `api_provenance_status:
  valid`, `api_prediction_semantics_status: established`, and
  `api_usability_status: current_usable`.
- A row without the proof columns still reports `unknown` / `not_current_usable`
  and stays visible (no filtering, no backfill).
- A row claiming version 1 with missing or contradictory evidence is rejected at the
  write boundary.
- `v2_meta_label` rows carry E1–E3 but stay `api_prediction_semantics_status: unknown`.
- The read-API guide, pipeline docs, and catalog describe the new contract.
- Gates: `uv run pytest -n auto`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy`, `uv run equity catalog-generate`
  (no unexpected diff), `pymarkdown -d md013` on changed docs.

## 2. Frozen contract

### 2.1 Constants

Add to `src/equity_lake/core/schemas.py` (and to its `__all__`), beside
`FEATURE_SCHEMA_VERSION`:

```python
FORECAST_PROVENANCE_VERSION = 1
PREDICTION_SEMANTICS_VERSION = 1
PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE = "next_session_return_positive"
PREDICTION_TARGET_CANDIDATE_EXECUTE = "candidate_execute"
FORECAST_PROVENANCE_COLUMNS = [ ...the 17 names from §2.2... ]
```

### 2.2 Columns (all nullable, additive — ADR-0017)

| Column | dtype | Source at write time |
|---|---|---|
| `forecast_provenance_version` | int16 | `FORECAST_PROVENANCE_VERSION` |
| `model_provenance_version` | int16 | `metadata["provenance_version"]` |
| `model_artifact_sha256` | string | `metadata["artifact_sha256"]` (verified digest) |
| `model_trained_at` | date | payload `model_trained_at` (= refit boundary) |
| `model_age_days` | int16 | payload `model_age_days` |
| `model_max_age_days` | int16 | payload `model_max_age_days` |
| `training_feature_schema_version` | int16 | payload `training_feature_schema_version` |
| `inference_feature_schema_version` | int16 | payload `inference_feature_schema_version` |
| `price_adjustment_version` | string | payload `price_adjustment_version` |
| `availability_schema_version` | int16 | payload `availability_schema_version` |
| `missing_value_policy` | string | payload `missing_value_policy` |
| `candidate_strategy_fingerprint` | string | payload value; **null for `v1_direction`** |
| `horizon_days` | int16 | payload `horizon_days` |
| `threshold_used` | float64 | payload `threshold_used` |
| `threshold_source` | string | payload `threshold_source` |
| `prediction_target` | string | v1 → `next_session_return_positive`; v2 → `candidate_execute` |
| `prediction_semantics_version` | int16 | `PREDICTION_SEMANTICS_VERSION` |

`model_version` stays as the filename-derived string and is **not** proof.
`feature_schema_compatibility` is deliberately **not** persisted: it is derivable
from the two schema-version columns plus the stored `feature_schema_version`.

### 2.3 Write-boundary validation (`validate_predictions`)

Per row, either `forecast_provenance_version` is null (documented “no proof”) or it
equals `FORECAST_PROVENANCE_VERSION` and then **all** of:

- the 16 non-fingerprint columns are non-null, and `candidate_strategy_fingerprint`
  is non-null iff `model_mode == "v2_meta_label"`;
- `model_artifact_sha256` matches `^[0-9a-f]{64}$`;
- `model_trained_at <= date`;
- `0 <= model_age_days <= model_max_age_days`;
- `training_feature_schema_version == inference_feature_schema_version == feature_schema_version`;
- `0.0 <= threshold_used <= 1.0`;
- `horizon_days >= 1`;
- `prediction_target` equals the constant expected for `model_mode`;
- `prediction_semantics_version == PREDICTION_SEMANTICS_VERSION`.

A version-1 row failing any rule rejects the batch (fail closed). The writer never
defaults or infers a proof field.

### 2.4 API derivation (`api/deps.py`)

Replace the three hardcoded constants in `_annotate_prediction`.

- `api_provenance_status` ∈ `valid | historical | invalid | unknown`
  - `valid` — §2.3 rules hold **and** `price_adjustment_version`,
    `availability_schema_version`, `prediction_semantics_version` equal the current
    constants.
  - `historical` — §2.3 rules hold but a contract version is superseded.
  - `invalid` — present but contradictory (§2.3 rules violated).
  - `unknown` — proof columns absent or null.
- `api_prediction_semantics_status` ∈ `established | superseded | unknown`
  - `established` — `v1_direction`, E4 complete and consistent, current semantics version.
  - `superseded` — `v1_direction`, E4 complete, older semantics version.
  - `unknown` — everything else, including **every** `v2_meta_label` row.
- `api_usability_status` — `current_usable` only when identity `valid`, schema
  `current`, values `valid`, provenance `valid`, semantics `established`. Otherwise
  `not_current_usable` with the existing reasons plus:
  `forecast_provenance_unknown`, `forecast_provenance_historical`,
  `forecast_provenance_invalid`, `prediction_semantics_unknown`,
  `prediction_semantics_superseded`.

Rows are never filtered by status.

## 3. Tasks

### P1 — inference payload exposes the verified evidence

**Files:** `src/equity_lake/ml/forecasting.py`, `src/equity_lake/core/schemas.py`.
**Change:** add `model_artifact_sha256` and `model_provenance_version` to the
`provenance` dict in `predict()` (values already verified by
`_verify_model_artifact` / `_validate_inference_contract`); add the §2.1 constants.
**Acceptance:** a real `predict()` payload contains a 64-hex `model_artifact_sha256`
and `model_provenance_version == MODEL_PROVENANCE_VERSION`; no provenance value is
invented when metadata is missing (that path already raises).
**Tests:** `tests/unit/test_price_forecasting.py` (or the provenance test file) —
payload includes both fields; a tampered digest still raises `ModelProvenanceError`.

### P2 — writer persists the proof and validates it

**Files:** `src/equity_lake/ml/__init__.py`.
**Change:** build the 17 columns in `prediction_rows` from the payload; extend
`validate_predictions` with §2.3; keep the probability clamp and existing
identity/date checks. Null version for any inference path that cannot supply a proof.
**Acceptance:** a persisted row carries all 17 columns with §2.2 values; a
version-1 row missing `model_artifact_sha256` rejects the batch (logged
`predictions_validation_failed`, nothing written); a null-version row writes and is
never usable.
**Tests:** new `tests/unit/test_forecast_provenance.py` — round-trip through
`run_prediction_job` with a stubbed forecaster payload; validator rejection cases;
null-version acceptance.

### P3 — API derives the statuses

**Files:** `src/equity_lake/api/deps.py`.
**Change:** implement §2.4 as small helpers (`_forecast_provenance_status`,
`_prediction_semantics_status`) and wire the usability derivation; keep the
`api_*` field names and the existing reason style.
**Acceptance:** five fixture rows map to: usable (v1 complete/current), historical
(older `price_adjustment_version`), invalid (`model_age_days > model_max_age_days`),
unknown (no proof columns), v2 (provenance `valid`, semantics `unknown`,
usability `not_current_usable`).
**Tests:** `tests/unit/test_api.py` — one case per state, asserting status **and**
reason; assert a historical/invalid row is still returned (never filtered).

### P4 — catalog

**Files:** `src/equity_lake/catalog/datasets.py` (predictions `ColumnInfo` list),
`data/catalog.jsonl` (regenerated).
**Change:** add the 17 columns with dtypes and short descriptions.
**Acceptance:** `uv run equity catalog-generate` produces a diff only in the
predictions dataset.

### P5 — docs

**Files:** `docs/user-guide/20260811-read-api.md` (rewrite the “writer currently does
not persist…” paragraph into the new contract, keep the non-provenance examples),
`docs/user-guide/pipeline.md` (preload/prediction note), handoff 20261030-01 R4
status.
**Acceptance:** no doc still claims predictions cannot be usable; the ADR is linked
from the read-API guide; `pymarkdown -d md013` clean on changed files.

## 4. Non-goals (do not change)

- No table rewrite, no migration, no backfill of existing rows (ADR-0017/0021).
- No `v2_meta_label` semantics decision — those rows stay `unknown` by design.
- No change to `direction`/`probability` derivation, thresholds, or model selection.
- No new provenance label strings; `model_version` stays filename-derived.
- Do not touch `read_delta`'s residual filter policy (R1, already landed).

## 5. Dispatch

1. **worker** — P1 + P2 (ml layer), then report changed files and the targeted gates.
2. **worker** — P3 (API), building on P1/P2 column names exactly as in §2.2.
3. **worker** — P4 + P5 (catalog + docs) and the full verification sequence.
4. **reviewer** — adversarial audit against ADR-0021 acceptance criteria (read-only).

Workers must not commit; the tree is reviewed and committed by the orchestrating
agent.

## 6. Verification

```bash
uv run pytest tests/unit/test_forecast_provenance.py tests/unit/test_api.py \
  tests/unit/test_price_forecasting.py tests/unit/test_ml_jobs.py -q
uv run pytest -n auto
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run equity catalog-generate && git diff --stat data/catalog.jsonl
uv run pymarkdown -d md013 scan docs/user-guide/20260811-read-api.md docs/user-guide/pipeline.md
```

Live check after the change (optional but preferred, and only with the local lake
that already holds one prediction date):

```bash
uv run equity pipeline --skip-ingestion --date 2026-10-28 --markets us \
  --tickers AAPL,AMZN,GOOGL,MSFT,NVDA
curl -s 'http://127.0.0.1:8000/predictions?ticker=AAPL&limit=1' | python -c \
  "import json,sys; r=json.load(sys.stdin)[0]; print(r['api_provenance_status'], r['api_prediction_semantics_status'], r['api_usability_status'])"
```
