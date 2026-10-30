# ADR-0021: Row-Bound Forecast Provenance and Prediction Usability

**Status:** Accepted (2026-10-30). Owner confirmed the E1–E4 evidence set as
proposed and the Decision 7 binding (key + versioned contract, not copied input
proofs). Implementation is tracked in the
[implementation handoff](../handoffs/20261030-02-row-bound-forecast-provenance-implementation.md)
and is not implied by acceptance.

**Recorded:** 2026-10-30

**Deciders:** Repository owner

**Related:** ADR-0014 (label lifecycle and availability), ADR-0015 (point-in-time
adjustment and predictor availability), ADR-0016 (canonical instrument identity),
ADR-0017 (bounded additive evolution), [20261030-01 R4](../handoffs/20261030-01-postpreload-findings-and-remaining-issues.md)

## Context

`GET /predictions` annotates every row with `api_provenance_status`,
`api_prediction_semantics_status`, and `api_usability_status`. All three are
hardcoded (`api/deps.py:295-324`): provenance and semantics are always `unknown`,
so usability is always `not_current_usable`. That is honest, and it is also
terminal — no row can ever become usable, because nothing row-bound is persisted.

Meanwhile the inference path already *verifies* a substantial evidence chain and
then throws it away. Before `predict()` returns it:

- validates `provenance_version == MODEL_PROVENANCE_VERSION` and re-computes the
  artifact SHA-256 against the sidecar (`_validate_inference_contract`,
  `_verify_model_artifact`),
- enforces the inclusive `max_model_age_days` bound against `refit_boundary`,
- checks the training feature-schema version against the inference frame, plus the
  price-adjustment and availability schema versions, the missing-value policy, and
  (for `v2_meta_label`) the candidate-strategy fingerprint,
- resolves the effective decision threshold and its source.

All of that lands in a `provenance` dict (`ml/forecasting.py:673-691`) and the
persisted row keeps eleven fields, dropping every one of those values.

The read-API guide states the current standard for what is *not* provenance:
“A model-version string, current schema number, filename, or current
configuration is not provenance.” That exclusion is correct — each of those is
either self-asserted or ambient. It says what is insufficient; it does not say
that no row-bound evidence exists. The verified artifact digest and the validated
inference contract are row-bound evidence that has simply never been stored.

The consequence for consumers is concrete: a reader cannot tell whether a stored
row came from a verified artifact under the current contracts, and cannot tell
whether `direction` means “next-session direction” or “execute the candidate”.
Both answers are known at write time and lost immediately afterwards.

## Decision

Owner acceptance (2026-10-30) confirmed two points raised in review: the E1–E4
evidence set above is sufficient for `v1_direction` (no training window or
hyperparameters are added to the row), and the Decision 7 binding stands (the row
is bound to its input evidence by key and versioned contract, not by copying
source-publication proofs per row).

1. **Definition.** A *supported row-bound forecast proof* is the following tuple of
   evidence, captured at inference time, persisted with the row, and re-derivable
   from the row alone:

   | Class | Columns | Meaning |
   |---|---|---|
   | **E1 artifact identity** | `model_artifact_sha256`, `model_provenance_version`, `model_trained_at` | the exact artifact, verified against its sidecar digest, and its refit boundary |
   | **E2 temporal validity** | `model_age_days`, `model_max_age_days` | the age bound that was actually applied, and the observed age |
   | **E3 contract compatibility** | `training_feature_schema_version`, `inference_feature_schema_version`, `price_adjustment_version`, `availability_schema_version`, `missing_value_policy`, `candidate_strategy_fingerprint` | the versioned contracts the score was computed under |
   | **E4 decision semantics** | `horizon_days`, `threshold_used`, `threshold_source`, `prediction_target`, `prediction_semantics_version` | what the score means and how `direction` was derived |

   Envelope: every row carries `forecast_provenance_version` (1 for this ADR).

2. **Explicit non-provenance.** None of the following, alone or combined, constitutes
   a proof: `model_version` (derived from the artifact filename), the model file's
   mtime, the Delta snapshot version, current configuration, the current schema
   constants, or the stored `direction`/`probability` values. A row carrying only
   those is `unknown`, never `valid`.

3. **Semantics per `model_mode`.**
   - `v1_direction` establishes: `probability` = P(next observed session
     close-to-close return > 0) with `horizon_days = 1` and
     `prediction_target = "next_session_return_positive"`; `direction = "up"` iff
     `probability >= threshold_used`, where `threshold_used`/`threshold_source` are
     the values actually applied.
   - `v2_meta_label` persists the same E1–E3 evidence and
     `prediction_target = "candidate_execute"`, but **does not establish a
     directional forecast**: `probability` is P(candidate execution) and the
     `up`/`down` mapping is a representation of that decision. Its
     `api_prediction_semantics_status` stays `unknown` and it is not
     `current_usable` until a separate ADR defines meta-label usability.

4. **Derivation rules.** The API derives, and never hardcodes:
   - `api_provenance_status`
     - `valid` — E1–E4 all present with valid types, `forecast_provenance_version == 1`,
       `model_trained_at <= date`, `0 <= model_age_days <= model_max_age_days`,
       `training_feature_schema_version == inference_feature_schema_version ==
       feature_schema_version`, `threshold_used` in [0, 1], and the E3 contract
       versions (`price_adjustment_version`, `availability_schema_version`,
       `prediction_semantics_version`) equal the current constants.
     - `historical` — complete and internally consistent, but bound to a superseded
       contract version. Such a row is honest evidence of a forecast produced under
       an older contract; it is not current, and it is not invalid.
     - `invalid` — present but contradictory (for example age beyond the applied
       bound, digest not 64 hex characters, `model_trained_at` after the prediction
       date, mismatched feature-schema versions, `threshold_used` outside [0, 1],
       `prediction_target` inconsistent with `model_mode`).
     - `unknown` — the proof columns are absent or null (legacy/foreign rows).
   - `api_prediction_semantics_status` — `established` only for `v1_direction` with E4
     present, consistent, and on the current `prediction_semantics_version`;
     `superseded` when E4 is complete but the semantics version is older; `unknown`
     otherwise (including every `v2_meta_label` row).
   - `api_usability_status` — `current_usable` only when identity is `valid`, schema is
     `current`, stored values are `valid`, provenance is `valid`, and semantics is
     `established`; otherwise `not_current_usable`, with the reason vocabulary
     extended by `forecast_provenance_unknown`, `forecast_provenance_historical`,
     `forecast_provenance_invalid`, `prediction_semantics_unknown`, and
     `prediction_semantics_superseded` alongside the existing reasons.

   Rows are never filtered by status: unknown and invalid rows stay visible with
   their stored fields, exactly as today.

5. **Write-boundary validation.** `validate_predictions` requires, per row, either
   `forecast_provenance_version == 1` with a complete and self-consistent E1–E4 set,
   or an explicit null version (a documented “no proof”). A row that claims version 1
   while missing evidence, or whose evidence contradicts the row, rejects the batch.
   The writer never defaults, infers, or fabricates a proof field; inference paths
   that cannot supply one write a null version, which is never usable.

6. **Evolution and legacy rows.** Columns are added by native nullable additive
   evolution (ADR-0017) — no table rewrite, no backfill. `forecast_provenance_version`
   versions the proof set and its rules: changing either requires a new ADR and a
   version bump, and rows keep the semantics of their own version. Existing rows
   remain `unknown` and visible; they are never upgraded by inference, filename
   parsing, or current configuration.

7. **Binding to input evidence.** E3 binds each row to the schema versions and to the
   Gold feature contract; the per-row input publication proof remains the property of
   the Gold feature row (ADR-0015), reachable through the stored identity and date.
   This ADR does not copy source-publication proofs into prediction rows, and a
   `current_usable` row is not a validated trading signal: identity, schema, values,
   provenance, and semantics can all be sound while the forecast itself is wrong.

## Consequences

**Enables.** `/predictions` can report usability that is derived and reviewable;
the API stops hardcoding; the bounded refit preload (R5) writes rows that already
carry their proofs; an auditor can re-derive the evidence from the row without
access to model files, sidecars, or the configuration that produced them.

**Costs and required work.**

- The predictions table grows by roughly eighteen nullable columns; readers must
  tolerate their absence (that tolerance is already the row-status design).
- `predict()` must return `model_artifact_sha256` and `model_provenance_version`
  (both already verified, currently dropped).
- `validate_predictions` grows the version-1 consistency rules.
- `api/deps.py` replaces three hardcoded constants with derivations.
- The read-API guide's exclusion paragraph must be rewritten to distinguish
  insufficient evidence (its current examples) from sufficient row-bound evidence,
  and the pipeline/CLI docs must stop implying predictions cannot be usable.
- Catalog regeneration and a doc pass wherever `/predictions` is advertised.

**Residual limits (accepted).**

1. The proof covers the *model and the contract*, not the input source's publication
   at the instant of the forecast. That evidence belongs to the Gold row; Gold rows
   can in principle be rewritten (bounded by ADR-0014's availability/preserve rules
   and ADR-0015's fail-closed reads), so the binding is by versioned contract plus
   key, not by copy.
2. A model that is stale but still inside the configured bound is `current_usable`.
   Freshness is policy, not proof; `model_age_days` is visible so a consumer can
   apply its own tighter bound.
3. `v2_meta_label` rows remain non-usable by design until their semantics are
   established in a separate record.
4. None of these statuses is a quality claim. A usable row says the forecast was
   produced by a verified artifact under current contracts; it says nothing about
   accuracy, and the API must not imply otherwise.

## Implementation plan (not started; gated on acceptance)

1. `src/equity_lake/ml/forecasting.py` — add `model_artifact_sha256` and
   `model_provenance_version` to the inference payload; define
   `FORECAST_PROVENANCE_VERSION`, the v1 `prediction_target`, and
   `PREDICTION_SEMANTICS_VERSION`.
2. `src/equity_lake/ml/__init__.py` — persist E1–E4 in `prediction_rows`; extend
   `validate_predictions` with the version-1 rules; keep the probability clamp.
3. `src/equity_lake/api/deps.py` — derive the three statuses and extend the reason
   vocabulary.
4. Docs — read-API guide (rewrite the exclusion paragraph), pipeline preload
   section, catalog regeneration if the predictions dataset is cataloged.
5. Tests — writer round-trip (payload → table → annotation `current_usable`),
   validator rejection for a claimed-but-absent proof, `invalid` vs `unknown`
   distinction, v2 stays `unknown`, legacy row untouched, and the existing
   filtered-read regression (`?ticker=`) stays green.
6. Ordering — land before R5 so the bounded refit preload writes proofs from the
   first backfilled date.

## Alternatives considered

- **Keep `not_current_usable` hardcoded.** Honest but terminal, and it discards
  evidence the system already verifies. Rejected by owner decision D1.
- **Persist model metadata without defining the standard.** Would flip status on a
  basis the read-API guide explicitly excludes, silently weakening a documented
  guarantee and making “usable” unreviewable. Rejected.
- **Copy full input-source proofs into every prediction row.** Duplicates ADR-0015's
  evidence per row, grows the table substantially, and creates two sources of truth
  that can drift. Rejected in favour of the key + versioned-contract binding
  (Decision 7).
- **Derive provenance at read time from model files.** The API serves a pinned Delta
  snapshot and must not stat model artifacts per request; historical artifacts can be
  pruned. Rejected.
