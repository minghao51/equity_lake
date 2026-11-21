# ADR-0034 — Vendor-stamped ML research rows with mandatory disclosure

**Status:** **Accepted (2026-11-21)** by the owner's explicit delegation ("close the last 2,
proceed without my assistance but with your best judgement"). It authorizes ML *research* on
the real root's weak-basis rows; it authorizes no promotion, no deployment and no
point-in-time claim.

**Recorded:** 2026-11-21

**Amends:** [ADR-0032](0032-adjusted-read-basis-for-vendor-sourced-slices.md) decision 10's
blanket exclusion of "any point-in-time claim, feature or label produced from vendor-stamped
rows" **for research evaluation only**. ADR-0032 decisions 1-9, ADR-0015's strict basis,
ADR-0027 (origin `unknown`) and ADR-0030 (root isolation) are unchanged.

## Context

ADR-0032 gave the real slice a weak, clockless basis and deliberately kept its ML loaders
refusing vendor-stamped rows (decision 4), because a model score would otherwise read as a
point-in-time claim. That leaves R02 — an incremental ML experiment — with no lawful input:
the only Gold rows in `data/lake_real/` are `vendor_events_no_clock`, and
`ml/feature_loader.py` is both canonical-root-only and strict-only.

The owner has directed that the remaining stages be closed without further assistance. The
research question R02 asks ("does one incremental model beat a technical-rule baseline on
these features, out of sample?") is answerable on weak evidence **if the weakness travels
with every number**. This record makes that condition explicit rather than relaxing the
strict contract.

## Decision

**Decision 1 — Research-only admission, explicit and opt-in.** A training/evaluation run may
read `vendor_events_no_clock` Gold rows only when the caller passes an explicit
`coverage_basis="vendor_events_no_clock"` (and a lake root). `source_clock` remains the
default, and every existing call site is unchanged. No fallback, inference or environment
switch selects the weak basis.

**Decision 2 — No point-in-time claim; strict validators unchanged.** The PIT validators
(`validate_feature_action_coverage`, `validate_action_coverage_for_decision`), the feature
per-session visibility check and the strict ML path keep refusing weak rows exactly as
before. R02 may not claim an action was or was not known at a past decision date. The weak
basis is not synthesised into a clock anywhere.

**Decision 3 — The basis and the disclosure travel with the artifacts.** Every R02 artifact
(protocol, metrics, fold predictions, report) records `coverage_basis =
vendor_events_no_clock` and this fixed disclosure: *"Computed on a vendor-event-completeness
basis with no source clock (ADR-0032/0034). Not point-in-time evidence, not indicative of any
real strategy's live performance, and not a promotion."* Prediction rows are never written to
`04_platinum/predictions`; R02 writes under `data/research/r02/` only. A manifest or artifact
scope mixing bases refuses.

**Decision 4 — Research shape is frozen before the run.** One target/policy, one frozen R00
protocol (window, pre-holdout split, costs, metrics, tolerances, budget), a technical-rule
baseline plus no-skill and simple-model baselines on matched out-of-sample dates, a
prespecified uncertainty method (block bootstrap interval), and promotion criteria stated in
advance — with **nothing promoted**. No automated refit, no holdout tuning, no deployment.

**Decision 5 — Strength is never upgraded.** A vendor omission below the action watermark
remains undetectable; reports must say so. A weak-basis result is research evidence, not
operational readiness.

## Consequences

- R02 becomes runnable on the real root without inventing a clock or weakening any strict
  consumer; the default path, the demo lake and the PIT contract are untouched.
- The real-root ML reader gains `lake_dir` and `coverage_basis`; both default to today's
  behaviour, and mixed-basis reads refuse.
- The disclosure and "not a promotion" rule are machine-checkable strings in the artifacts,
  so a future reader cannot mistake R02 for evidence of live performance.

## Alternatives considered

- **Keep R02 blocked until a source clock exists.** Rejected by the owner's direction; it
  would leave R02 permanently unavailable for data that can never carry such a clock.
- **Relax the strict validators.** Rejected: it would erase the distinction ADR-0015/0032
  exist to preserve.
- **Write weak predictions into the Platinum table.** Rejected: it would put a research
  artifact on a consumer surface with no basis column and no disclosure, and require a
  schema change for no benefit.
