# Quant ML Next Steps Handoff

## Context

The three-phase quant ML upgrade is now implemented in the current codebase:

- Phase 1: feature generation supports news sentiment, social sentiment, and a minimal cross-modal feature set.
- Phase 2: ML validation uses a purged and embargoed walk-forward splitter.
- Phase 3: the repo supports both `v1_direction` and opt-in `v2_meta_label` model modes.

The current implementation is functionally verified, but it is still concentrated in a few large modules and would benefit from a follow-up polish pass.

Primary implementation areas:

- `src/equity_lake/features/engineering.py`
- `src/equity_lake/ml/forecasting.py`
- `src/equity_lake/signals/scanner.py`
- `src/equity_lake/signals/generators/meta_label.py`
- `config/signals.yaml`

## Suggested Next Steps

### 1. Split the ML stack into smaller units

Current issue:

- `src/equity_lake/ml/forecasting.py` now owns feature loading, candidate generation, triple-barrier labeling, validation, training, inference, model-path resolution, and metadata persistence.

Recommended follow-up:

- Extract the purged/embargoed validation logic into a dedicated validation-facing module if more metrics or split modes are expected.
- Extract v2 target creation into a separate labeling module.
- Extract candidate generation into a reusable service that can be tested independently from model training.
- Keep `PriceForecaster` as the orchestration layer, not the implementation layer for every concern.

Definition of done:

- `forecasting.py` becomes substantially smaller.
- v1 and v2 logic remain behaviorally unchanged.
- Existing tests still pass with only minor fixture updates.

### 2. Add model-quality reporting that is easier to read than raw metadata

Current issue:

- Validation details are persisted in training metadata JSON, but there is no concise operator-facing summary for comparing v1 and v2 runs.

Recommended follow-up:

- Add a lightweight report output for training results.
- At minimum include:
  - model mode
  - train/validation row counts
  - mean accuracy
  - mean precision
  - mean recall
  - validation fold count
  - barrier settings for v2
- Expose this through either:
  - a CLI summary from `forecast --mode train`
  - or a small report file written next to model artifacts

Definition of done:

- A user can train a model and immediately see whether the run was usable without opening raw JSON manually.

### 3. Make v2 training inputs auditable

Current issue:

- Candidate events and triple-barrier labels are created in-process during training, but they are not persisted as first-class artifacts.

Recommended follow-up:

- Persist the v2 candidate and labeled training frame before model fitting.
- Include at least:
  - `ticker`
  - `date`
  - `candidate_action`
  - `candidate_source`
  - `meta_label`
  - `barrier_outcome`
  - `upper_barrier_return`
  - `lower_barrier_return`
  - `vertical_barrier_days`
- Store it in a predictable location under `data/` so failed or surprising training runs can be audited after the fact.

Definition of done:

- A trained v2 model can be traced back to the concrete candidate events and labels used to build it.

### 4. Expand edge-case coverage

Current issue:

- The new unit coverage is good for the implementation scope, but several realistic failure modes are still only implicitly covered.

Recommended follow-up:

- Add targeted tests for:
  - sparse or missing social sentiment coverage
  - no-candidate days in `v2_meta_label`
  - low-volatility barrier calculations
  - mixed-market feature generation paths
  - legacy and new artifact naming across repeated retraining
  - training/inference consistency when feature columns evolve

Definition of done:

- The new ML paths are resilient to common operational edge cases, not just happy-path fixtures.

### 5. Improve user-facing explanation of feature and ML modes

Current issue:

- The implementation is ahead of the docs. A contributor can discover the new behavior by reading code, but not quickly from a single user-facing explanation.

Recommended follow-up:

- Add a short docs page or extend existing user/developer docs with:
  - feature groups now available
  - what `v1_direction` does
  - what `v2_meta_label` does
  - which config knobs affect training
  - which config knobs affect inference
  - when a user should prefer v1 vs v2

Suggested placement:

- `docs/user-guide/signals.md`
- `docs/user-guide/pipeline.md`
- or a new dated doc if the team prefers incremental documentation

Definition of done:

- A contributor can understand the new feature and ML modes without reading implementation files first.

## Recommended Order

1. Split the ML stack into smaller units.
2. Add operator-facing model-quality reporting.
3. Persist v2 candidate and label artifacts for auditability.
4. Expand edge-case coverage around the refactored pieces.
5. Improve user-facing docs once the code layout stabilizes.

## Notes for the Next Engineer

- Preserve backward compatibility for `v1_direction`.
- Do not remove support for legacy v1 model artifact names unless a migration is added.
- Keep the repo on the current pandas + Hamilton path unless there is an explicit decision to migrate.
- Prefer small refactors with tests after each step rather than another large rewrite inside `forecasting.py`.
