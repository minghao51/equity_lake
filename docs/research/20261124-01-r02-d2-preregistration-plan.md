# RS-R02-D2 preregistration plan — second ML experiment

**Status: planned — not run — blocked on the qualified data packet.** Nothing in this document
has been executed: no protocol frozen, no model fitted, no result produced. **No positive result
is promised** — a negative or inconclusive outcome is a complete result. This is the planning
half of the owner decision of 2026-11-24 ([residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md)
§2.7 and the §11 owner-decision entry: a second ML experiment is approved **for planning only**),
and it exists so a later run needs no further decisions: every choice below is fixed here or is
fixed by the freeze machinery before any run, never after.

D2 is the second ML experiment after R02 (`docs/reports/20261121-r02-result.md`, `promoted=false`,
1/5 criteria, weak basis). Where R02 was a weak-basis feasibility run, D2 is the first experiment
that may speak about strict coverage — and only if the gating inputs below actually exist.

## 1. Strict-vs-weak separation (stated up front)

- The D2 experiment runs on **strict** coverage only: `source_clock` basis
  (`ml/feature_loader.py:39`, the default), fed by a qualified source packet (§2). A weak
  `vendor_events_no_clock` basis is **never** enough for a promoted claim (ADR-0032 decisions 3–4;
  ADR-0034).
- Weak rows may not enter a strict read: a frame whose coverage mixes bases refuses
  (`ml/feature_loader.py:181-189`), and D2 does not attempt to bypass that. If a weak-basis look
  is ever wanted for context, it is a **separately labelled exploratory appendix** carrying
  ADR-0034 decision 3's fixed disclosure verbatim, never part of the experiment's numbers, never
  compared against the strict arm's intervals.
- A promoted claim requires strict evidence. §7 states what production would additionally need.

## 2. Gating inputs (both required before anything runs)

**G1 — a qualified source packet (RS-D01).** Source-issued publication/vintage clocks,
vintage/expiry evidence and rights, supplied and qualified through the RS-D01 machinery —
`core/source_clock.py`'s `SourceClockRecord` (`evidence_kind` closed to
`source_publication|source_vintage`; an ingest-time clock is refused by construction), the
append-only qualification store at `DATA_DIR/source_clock_qualification/records.jsonl`, and
`require_qualified_source_clock`. The packet must cover D2's whole scope: `coverage_start`/
`coverage_end` spanning the windows of §3 and an unexpired `expires_on`. Passing synthetic guard
tests cannot substitute for it (residual plan RS-D01: "passing synthetic guards alone cannot
unblock a live consumer or claim qualified data"). Until a source is qualified, D2 stays blocked.

**G2 — a NEW untouched scope.** Universe, train/validation/test windows and holdout that R01 and
R02 never used, fixed under the disjointness rule of §3. R02's five names
(`us_equity/XNAS/AAPL`, `MSFT`, `NVDA`; `us_equity/XNYS/JPM`, `KO` — `ml/experiment.py:82-88`),
its folds 2024-01-02..2024-09-30, its declared holdout 2024-10-01..2024-12-31 (declared for R02,
therefore not "new" even though unread) and R01's fixture design 2022-01-03..2024-12-31 are all
off limits. If the qualified packet cannot supply G1+G2 together, D2 remains blocked; the scope is
not relaxed to fit the data without a new preregistration.

## 3. Scope instantiation rule (fixed here; concrete values fixed at freeze time)

The concrete universe and dates depend on what the qualified packet covers, so they are
instantiated at freeze time — but only under this rule, with no further decision:

- **Universe:** instruments covered by G1, excluding R02's five names and the shipped R01
  fixture pools. Canonical keys in the ADR-0016 form used by `ml/experiment.py`.
- **Windows:** train < validation < test, ordered and non-overlapping, every fold ending before
  the holdout starts (the `R02Spec` ordering contract, `ml/experiment.py:232-240`); the whole
  span lies outside 2022-01-03..2024-12-31, i.e. starts no earlier than 2025-01-01. The holdout
  is the last contiguous block and is **never read** during the experiment (R02's discipline:
  `rows_read=0` on its holdout).
- **Matched dates:** the primary arm and every baseline are compared on the identical set of
  out-of-sample decision dates and identical holding blocks and costs — no arm is ever evaluated
  on dates another arm lacks, and no interval is computed across unmatched dates.
- **Features/label/models:** carried verbatim from R02's frozen constants in
  `ml/experiment.py` (the Gold technical feature list, the sign-of-5-session-forward-return
  label, horizon 5, purge 5, embargo 1, decision threshold 0.5, primary gradient-boosting model
  with its fixed hyperparameters, simple logistic-regression model). If the qualified packet
  cannot supply that feature set over the new scope, the run **refuses**; features are not
  substituted without a new preregistration.

## 4. Preregistered methodological constants (fixed before any run)

| Item | Frozen value |
| --- | --- |
| Cost regime | `realistic`: fee `0.001425`, tax `0.003`, round-trip `0.00585` per held name per holding block (`ml/experiment.py:122-127`, unchanged from R02) |
| Baselines | no-skill (train base rate), simple model (logistic regression on the same features), technical rule `momentum_20_session_sign` — all on the matched dates of §3 |
| Uncertainty method | moving-block bootstrap over evaluation sessions; block `5` sessions, `2000` resamples, `95%` percentile interval, bootstrap seed `20261124` |
| Acceptance tolerances | as R02's frozen set: `brier_score 0.0`, `net_return 0.0`, `roc_auc 0.0`, `sharpe_ratio 0.05` |
| Promotion | none: `promoted=false` in every artifact, whatever the criteria say (§6) |

### 4.1 The estimand, named (the lesson from the first experiment)

R02 paired two different estimands: the reported −2.79 pp was a difference of **compounded** net
returns while the reported [−0.42 pp, +0.31 pp] interval belonged to the **mean per-session**
difference — two estimands that cannot bracket each other
([recovery receipt](../finding/20261123-01-r02-recovery-receipt.md) §4). D2 names its estimands
and keeps them apart:

- **Primary estimand:** the arithmetic **mean per-session net-return difference** (primary arm
  minus technical rule) on the matched out-of-sample dates. Its moving-block-bootstrap 95%
  percentile interval is the **only** interval that may be quoted beside it.
- **Secondary quantity (reported, no borrowed interval):** the difference of **compounded** net
  returns per arm. It is reported under its own name and is never bracketed by the per-session
  interval, and the two are never compared to each other's interval.

### 4.2 Promotion criteria (frozen for the record; nothing is promoted)

Carried from R02's frozen criteria (`ml/experiment.py:288-296`) with the economics criterion
restated so its point estimate and its interval are one estimand:

1. **calibration:** primary test Brier is lower than both the no-skill base rate and the
   technical rule on matched dates;
2. **discrimination:** primary test ROC AUC ≥ 0.55 and the block-bootstrap 95% interval lower
   bound is above 0.50;
3. **economics:** the primary arm's mean per-session net-return difference versus the technical
   rule (the primary estimand of §4.1) is > 0 **and** its block-bootstrap 95% interval lower
   bound is above zero — the compounded difference never enters this test and never borrows this
   interval;
4. **risk:** primary test Sharpe exceeds the technical rule by at least the frozen tolerance 0.05;
5. **consistency:** the sign of the primary-minus-technical difference agrees on the validation
   and test folds.

All five are evaluated **for the record only**; passing them changes nothing (§6).

## 5. How it is frozen (existing machinery, before any run)

The freeze uses the shipped R00 machinery — `freeze_protocol` and `require_frozen_protocol` in
`src/equity_lake/backtesting/protocol.py` (protocol.py:327, :548). The manifest content is a pure
function of its inputs, an identical re-freeze reproduces the same bytes, a changed manifest at
the same name is refused, and a run whose assumptions are absent or changed is refused with the
changed fields named — "Freezing a new protocol is an explicit act, never an automatic fallback"
(protocol.py:574). Sequence for a later run, in order, with no step skipped:

1. Confirm G1 (qualified source packet covering the scope) and G2 (scope rule of §3) — otherwise
   stop, D2 stays blocked.
2. Instantiate the scope per §3 and write the prespecification (the §4 constants verbatim plus
   the instantiated scope); its digest is bound into the manifest as `input_digests`, the way
   R02's prespecification is bound (recovery receipt §1).
3. `freeze_protocol(...)` — **before any fit, any prediction or any metric**. The frozen manifest
   is committed (as R01's is) so a reviewer can prove the preregistration predates the results.
4. Run only under `require_frozen_protocol`; a refusal at this gate is a stop, not a tuning
   opportunity. No holdout tuning, no automatic refit, no sweep (R02's `fits=1, refits=0,
   sweeps=0` discipline, recovery receipt §5).
5. Artifacts (protocol, metrics, fold predictions, report) go under `data/research/` as
   auxiliary storage (ADR-0006) with `promoted=false` and the strict-basis provenance recorded;
   nothing is written to `04_platinum/predictions` (ADR-0034 decision 3's rule for research
   artifacts).
6. The report states the outcome whatever it is, with the §4.1 estimand labelling, and records
   censored or refused runs as such — never as successes.

## 6. `promoted=false` in every artifact

Even if all five criteria of §4.2 pass, every artifact carries `promoted=false`. This is not a
promise to be careful: the shipped models enforce it (`R02Spec` refuses `promoted=True`,
`ml/experiment.py:238-239`; the artifact model's validator re-refuses it, `ml/experiment.py:755-757`)
and D2's prespecification inherits the same refusal. The criteria are evaluated for the record
only ("none of the above promotes anything", `ml/experiment.py:295`).

## 7. Production is a separate decision

Production use of anything D2 learns would need **its own ADR amending ADR-0034** plus strict
evidence (residual plan §2.7) — a research run, however clean, is never deployment authority
(ADR-0034 decisions 4–5: no promotion, no deployment, no point-in-time claim beyond what the
basis proves). This plan creates no path from a passing criterion to a live signal.

## 8. What this document is not

Not a run, not a result, not a frozen protocol (the freeze happens at run time, §5), and not an
authorization to fetch data: the qualified source packet is operator input and its acquisition
is not planned here. Status stays **planned, blocked on the qualified data packet** until §5's
step 1 is satisfied; nothing short of a recorded run receipt changes it.

## References

- [Residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md) §2.7 and
  RS-R02-D2 (§5) — the stage this plan executes; §11 owner decisions 2026-11-24 — the approval
  to plan it.
- [R01 preregistration](20261123-01-r01-preregistration.md) — the freeze-before-run precedent
  this plan follows.
- [R02 recovery receipt](../finding/20261123-01-r02-recovery-receipt.md) — the estimand finding
  of §4.1 and the verified R02 baselines D2 repeats.
- [ADR-0032](../decisions/0032-adjusted-read-basis-for-vendor-sourced-slices.md) and
  [ADR-0034](../decisions/0034-vendor-stamped-ml-research-with-disclosure.md) — the coverage-basis
  contract and the research-only admission D2 operates under.
- [ADR-0015](../decisions/0015-point-in-time-adjustment-and-predictor-availability.md) decision 4
  — source clocks and the first observable session; `core/source_clock.py` — the qualification
  machinery of G1.
