# ML Label Availability and Model Provenance

This guide documents the enforced inference-availability and local artifact
integrity gates. Passing synthetic tests does not certify predictive
performance, historical predictor vintages, corporate-action handling, costs,
or investment returns. ADR-0015/0016/0017 implementation and C10 offline
contracts were independently reviewed and accepted on 2026-10-18. This does
not establish live source availability, production readiness, or alpha.

## Split-only adjustment and source-availability proof

Shared feature and model inputs use canonical `(market, MIC, symbol)` identity.
The Gold feature contract is version 7 and persists action proof fields:
`action_coverage_market`, `action_coverage_mic`, `action_coverage_symbol`,
`action_coverage_source`, `action_coverage_start`, `action_coverage_end`,
`action_source_available_at`, `action_source_valid_until`,
`action_coverage_status`, and `action_coverage_schema_version`. The three
`action_coverage_*` identity fields bind the source proof's own canonical
identity and must exactly match the feature row's `(market, MIC, symbol)`.
Strict rows also carry `availability_schema_version` (version 4),
`action_coverage`, and `action_source_age_days`. Older feature rows/models without the compatible
identity, adjustment, availability, and proof contracts fail closed; they are
not automatically rewritten or retrained.

Adjustment is a shared point-in-time, split-only read-time transform. It leaves
raw Bronze prices unadjusted and excludes cash dividends. `source_valid_until`
is the validity endpoint issued by the source; no fixed TTL is guessed. Each
feature row checks that expiry against its own decision-session close. A later
training cutoff does not invalidate historically valid rows retroactively.
Source publication time is mapped to the first observable session for the instrument's
MIC, not ingestion time or a market-union calendar. The shipped native
yfinance corporate-action path currently supplies neither verified action
publication timestamps nor complete coverage/expiry proofs. Therefore adjusted
outputs and consumers that require them are unavailable until verified source
proof is supplied. This describes a missing proof in this implementation, not
a native provider-availability claim. Unsupported external enrichments (including
non-US enrichment paths) remain null/unavailable, not neutral values.

Rolling backtests rebuild the price history visible at each decision cutoff,
apply only split actions observable by that cutoff, and exclude instrument-
session decisions with unavailable action proof. Their price marks use a
split-neutral return index; they do not include cash dividends.

## Per-refit label availability

Walk-forward refits use only labels that are complete by that refit's decision
boundary, for both estimator fitting and threshold selection. The backtest
purges fit events whose outcome interval reaches the first validation event.
The cutoff is applied separately at each refit; an inner train/validation split
alone is not evidence that labels are available at the outer test boundary.

- V1 direction labels require a known `label_available_date` no later than the
  refit boundary. Missing availability remains unknown and is excluded.
- V2 meta-label events mature on their first barrier hit or after the full
  configured vertical barrier. A no-hit event at the right edge stays pending
  until its full horizon exists; it is not converted into a shorter time-loss
  or time-profit label. Pending rows are excluded from fitting, threshold
  selection, and reported test metrics.
- Reported backtest actuals contain only complete labels available by the
  requested outer end date. Each result records `refit_boundary` (also exposed
  as `model_trained_at`) separately from `training_start`.
- `train_model(validate=True)` and hyperparameter tuning carry the loaded
  v1 `label_available_date` or v2 `barrier_end_date` values, plus decision
  dates, into walk-forward folds. Labels must be known by the requested
  training cutoff; fit events ending **on or after** a fold's first test date
  are purged. The date/interval fields are splitter metadata, never predictors.
- Direct splitter callers may omit interval metadata for compatibility. In that
  case the splitter purges the full configured label horizon in rows (not
  `horizon - 1`), a conservative approximation. When interval dates are
  supplied, pending rows and labels after an explicit decision cutoff are
  excluded; actual interval purging uses both event-end and decision dates.

## Prediction threshold and horizon fields

`PriceForecaster.predict()` returns `threshold_used` and `threshold_source`:

1. A valid stored `optimized_threshold` keeps precedence.
2. V2 falls back to the configured `meta_label_threshold`; V1 falls back to
   its existing `0.5` default.
3. A present but invalid stored threshold (non-numeric, non-finite, or outside
   `[0, 1]`) raises visibly. Configuration does not silently override it.

The prediction also reports `horizon_days`: `1` for V1 and the configured
`vertical_barrier_days` for V2. The legacy V2 `meta_label_threshold` field is
retained for existing callers. V2 candidate-rule fingerprints and barrier
semantics are checked strictly; the fallback execution threshold is separate
from candidate identity and does not change the fingerprint.

## Enforced inference availability

Inference defaults to a maximum model age of **7 calendar days**, inclusive,
measured from the sidecar's actual `refit_boundary` to the requested inference
date. A caller may tighten the bound to any integer from `0` through `7`; values
above `7`, negative values, booleans, and non-integers are rejected before
feature/model loading. A model refit after the requested date or older than the
selected bound is rejected. Sidecar dates must agree: `trained_on` equals
`refit_boundary`, `training_start` is within the recorded training range, and
the training range end equals the actual refit cutoff.

The default behavior is fail-closed: if no current, compatible, integrity-
verified model is available, the ML opportunity is unavailable. Prediction and
pipeline inference never train or retrain a model automatically. An explicit
`train_model()` call may reuse an artifact only when it is within the caller's
0–7-day age bound and its complete training/inference contracts match. Otherwise
that explicit call proceeds with a new fit; setting the bound to `0` forces a
new fit. This does not authorize any automatic history repair or backfill.

The saved model must match the sidecar's canonical instrument, model mode and
backend, current identity/feature/adjustment/availability contracts, exact
feature names and dtypes, and `native_nan-v1` missing-value policy. Inference
accepts a model at exactly seven calendar days of age (inclusive) by default;
its age is based on the actual sidecar `refit_boundary`, never the filename or
file timestamps. V2 models
also require matching candidate-rule fingerprints and triple-barrier settings.
Unknown or incompatible contracts block inference before model loading. A V2
strategy evaluation with no candidate condition raises typed
`NoCandidateConditionError` with status `no_condition` before model inference;
it is not reported as a generic model failure.

Unavailable model outcomes have stable statuses for callers:

| Status | Meaning |
|---|---|
| `missing_model` | No identity-compatible saved model is available. |
| `model_provenance_unavailable` | Sidecar provenance or artifact integrity is missing, malformed, or untrusted. |
| `model_incompatible` | Identity, schema, feature, adjustment, missing-value, candidate, or barrier contracts differ. |
| `model_expired` | The actual refit boundary exceeds the requested age bound. |
| `model_from_future` | The actual refit boundary is later than the inference date. |
| `no_condition` | V2 candidate evaluation found no condition for the requested date. |

Legacy sidecars without current provenance and artifact integrity are rejected;
there is no automatic migration or silent legacy inference. In-memory estimator
objects are also rejected because their bytes cannot be verified against a
saved-artifact digest.

## Model sidecar provenance and integrity

New sidecars use provenance version `3` and record the actual refit boundary,
separate training start and date range, training feature-schema version(s),
feature names and source dtypes, identity, adjustment/availability versions,
missing-value policy, candidate rules, barrier settings, effective training
configuration, and the saved model's `artifact_sha256`. New model filenames
include a hashed `(market, MIC, symbol)` identity token rather than a bare
symbol; the adjacent sidecar records the full key and selected filename. The
separate training-data hash is not an artifact integrity check.

Before a saved model is deserialized or used for inference, the runtime hashes
the artifact bytes and requires an exact match with the sidecar SHA-256. This
detects swapped or stale model bytes when the sidecar remains unchanged. This is
a local artifact/sidecar trust assumption, **not** a signing framework: an
actor who can maliciously modify both the model and its sidecar can replace the
model and digest together. Store model artifacts and sidecars in a trusted local
workspace.

Predictions report `training_feature_schema_version`,
`inference_feature_schema_version`, `feature_schema_compatibility`,
`adjustment_compatibility`, model age, and the effective age bound. Gold uses
feature schema version 7; the previously documented claim that the feature
schema was unchanged is superseded. Legacy rows without current required proof
remain unavailable rather than being upgraded in place.

These rules improve availability and provenance accounting; they do not
validate historical predictor vintages, source-native corporate-action
coverage, costs, or investment returns. No production inventory, migration,
repair, backfill, retraining, provider fetch, or deployment is implied or
authorized. Passing synthetic tests does not establish alpha, calibrated
probabilities, or profitable signals.
