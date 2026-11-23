# Residual Spec and Implementation Plan — 2026-11-22

**Status: `DRAFT` planning deliverable at `fc589d1` on `main` (tree clean at drafting start).** Closes out the residuals
of the [2026-11-02 implementation spec](20261102-01-portfolio-and-research-implementation-spec.md) §11 with encoded
design decisions, a stage map, and per-stage specifications. Every stage is `planned` or `blocked-input`; none is
implemented or operated. Not an accepted ADR, not publication/delivery authorization, not permission to fetch data,
deploy, delete events, or retrain/promote models. No code, workflow, or data changed while drafting; no existing
workflow was dispatched.

## 1. Verified baseline corrections

Claims earlier records got wrong, re-derived at this baseline:

1. **A broken check ships in the Pages workflow.** `uv run python -m equity_lake.devtools.sync_schedule --check` exits 1 at
   `fc589d1` with `ValueError: Could not find a cron line in the workflow.` (reproduced). The module targets
   `.github/workflows/pages.yml` (`devtools/sync_schedule.py:18`), whose `schedule:` was removed, but the workflow still
   runs the check (`pages.yml:29`). The old spec's "only decision, no code work" claim about H00 is inaccurate.
2. **`pages.yml` builds the wrong surface.** It runs `equity monitor` + `equity dashboard build` (`pages.yml:31-35`) —
   dashboard + health, not the H00 artifact. `.github/workflows/catalog-deploy.yml` separately auto-deploys
   `docs/catalog/dist` on catalog pushes to the same Pages site. The spec §8 one-workflow/one-artifact requirement is
   still unmet.
3. **`total_return` is non-functional end to end.** `storage/lake_reader.py:205-206` raises `ValueError("ADR-0015
   supports split-only adjustment; dividends are excluded")`; `backtesting/data_loader.py:130-136,599` accepts the
   literal and defers to that raise; the CLI rejects `--adjust total_return` (`docs/user-guide/backtesting.md`). The
   mode exists in the surface; no working total-return path and no vendor TR series exist.
4. **R00 measurement evidence is unverified, not "not run".** A bounded search of `docs/` and `data/research/` found no
   planted/null/leakage-control or 5/50/200-instrument scale reports. The measurements spec §7 required may have been
   run; none were located, so the status is `evidence-unverified` until located or reproduced. Shipping freeze/refuse
   controls does not prove them.
5. **R02's reported pairing needs reproduction, not relabeling.** `docs/reports/20261121-r02-result.md` reports paired
   net-return difference −2.79 pp with 95% block-bootstrap interval [−0.42 pp, +0.31 pp]. The reported difference lies
   outside the accompanying interval; it is not yet established that they describe the same estimand. No bug is declared without reproduction; RS-R02-D1 verifies estimand, aggregation, units,
   and interval consistency (§5).
6. **Markdown lint is not recursive.** `make lint-md` runs `uv run pymarkdown -d md013 scan README.md docs`
   (`Makefile:152`) without `-r`. A recursive baseline (`scan -r README.md docs`) exits 1 with 314 lines of preexisting
   findings, captured at `/tmp/residual-recursive-markdown-baseline.log` (ephemeral; RS-Q00 re-captures to durable
   evidence and classifies it). Known findings include MD001 header increments. Broad cleanup is out of this draft's
   scope.

Also verified: `data/research/` holds `h00/`, `r02/`, `protocols/` only; `data/portfolio/` is absent; §11's R20 row is
already closed (2026-11-22) and carries no work here.

## 2. Design decisions (encoded, not questions)

### 2.1 Publishing (RS-H01)

- H00 owns the sole Pages root in the target design. The public artifact is exactly `index.html`, `payload.json`, `sanitization-report.json` from the existing sanitized builder (`dashboard/h00.py`, `equity dashboard h00-build`).
  No dashboard, health report, raw catalog, private or real data in any public route.
- Both existing deployers (`pages.yml` and `catalog-deploy.yml`) become build/test only or are retired; neither may retain `deploy-pages`, Pages-write permissions or an automatic publishing path. One new SHA-pinned manual (`workflow_dispatch`) deploy workflow; one shared Pages `concurrency` group; short-lived artifacts; artifact↔commit binding recorded in a **private CI/deployment receipt** — the public payload carries no digests-as-identifiers or operator metadata (the allowlist forbids them),
  and rollback receipts must not broaden the three-file public allowlist. Keep the last three verified sanitized release bundles for 90 days in access-controlled artifact storage, separately from short-lived CI intermediates. Rollback re-scans the selected bundle; if no clean recoverable bundle exists, withdraw the site rather than rebuild from private inputs.
- Environment protection (required reviewers) must be **actually verified through the platform settings** with evidence recorded in the stage ledger; workflow YAML alone is insufficient proof.
- The stale `sync_schedule --check` step (`pages.yml:29`) is removed from the publish path in the same stage. No schedule is deployed by default anywhere in the target design.
- Public route changes require a new ADR recorded before implementation; no ADR is created or accepted here. The explicit-publication condition (owner decision 3, 2026-11-21) stands: build and preview stay local; nothing publishes during drafting.

### 2.2 Disposable bootstrap (RS-E01)

- Builds run in a disposable checkout: no private mounts, no provider credentials, no ledger secrets; fixed synthetic seed; bounded window. A fixed seed alone is insufficient — `seed_demo()` anchors its window ending yesterday and the CLI has no `--end-date`,
  so reproducible bootstrap requires a narrow optional end-date/as-of parameter threaded through seeder and CLI with help tests. An empty `.env` in CI is not proof a runner holds no secrets: the deploy job is secret-free with least privilege and injects canary secrets proving the sanitization/negative-scan path fires.
- `equity demo seed` writes `data/sample/`, not `data/lake/` (`devtools/seed_demo.py:9`); H00 requires the canonical `data/lake/` plus the builder's synthetic attestation (`provenance=synthetic`, `root=demo_lake_only`).
  `--lake`/`--overwrite-production-lake` are used only to seed an *empty* disposable runner. The current seeder's explicit override permits overwriting; RS-E01 must add a bootstrap preflight that proves the target empty and disposable before invoking it. Never overwrite an operator's existing lake; never `--real`. Unavailable optional datasets stay honestly unavailable (e.g. empty provider seams) — never stubbed with fake data.
- Fresh-clone setup ships three explicit profiles: (a) sample (`data/sample/`, default), (b) canonical synthetic CI (disposable seeded `data/lake/`), (c) real-root restore/re-land profiles, operator-run and explicit, never implicit.

### 2.3 H00 staleness (RS-H01)

- The 30-day staleness rule (`dashboard/h00.py:62`) must survive **elapsed browser time**: the published page shows `stale` once artifact age exceeds 30 days
  even if fresh at build time (client-side age check against a payload timestamp, proven by test). Build-time `stale=false` alone is insufficient.

### 2.4 Archive extraction and upload (RS-H01)

- Before upload, the final archive extraction is checked: exact file list, no symlinks, size bounds, path containment, then a rescan of the extracted tree against the sanitization report. Canary secret/symlink fixtures must fail the check (mutation-proved).

### 2.5 Backup, retention, deletion (RS-P02)

- Backup-file housekeeping and event deletion are different concerns. All ledger events are retained; no automatic deletion, no event pruning — any event deletion needs its own ADR.
  Default: manual local verified backup export (ADR-0031 d3: exact-file copies, manual retention, no scheduled/remote/cloud backup) plus a report-only backup-file inventory; keep pre/post-migration recovery points. Backup-file pruning is a separate housekeeping decision with its own ADR.
- Any **encrypted off-host export or drill** (destination from actual operator input) is outside the current contract because ADR-0031 d3 introduces no remote/cloud backup:
  a NEW ADR is required before that work is implemented or operated, not merely before pruning. Local drills on isolated copies stay in-contract.
- `VACUUM INTO` is the supported compaction form. In-place `VACUUM` (ADR-0031 d5) is a non-goal for lack of measured need — not because writer exclusion is impossible.
  If a measured need arises, d5's conditions apply (verified backup, positive exclusive-writer evidence, 2× free space). Do not turn this allowed optional variant into mandatory backlog.

### 2.6 Real ledger onboarding (RS-P01)

- Inputs are operator-provided canonical CSV plus statements/marks; none exist in this checkout, so nothing is invented.
  Dry-run writes no private state; import is idempotent; reconciliation compares against statements. Restore and compact run on isolated copies first, production ledger hash unchanged. Fresh v2 needs no migration; legacy v1 conversion only on a legacy copy.

### 2.7 R02 interpretation and next experiment (RS-R02)

- The run is archived as `promoted=false`, negative/inconclusive (1/5 criteria). Do not retune the touched holdout, do not promote a weak-basis model, do not automatically refit.
  A new experiment needs a frozen protocol on a new untouched scope, strict-vs-weak separation, preregistered matched dates, costs, baselines, block-bootstrap criteria. Production would require a separate ADR overriding ADR-0034's restrictions plus strict evidence. No positive alpha is promised.

### 2.8 Point-in-time and real-root invariants (RS-D01)

- PIT on `vendor_events_no_clock` rows remains impossible; stamps and invariants preserved, never relabeled. Qualifying an alternative source means source-issued publication/vintage evidence, never an ingest-time fake clock; new evidence is appended, old rows never restamped.
  Tests: as-of invariance, future-data invariance, revisions. The real root is never cataloged or mixed with the demo root; guard tests use canary symlinks and containment; explicit `--lake-dir` stays.

### 2.9 Markets (RS-M01..RS-M04)

- Four non-US markets, strictly one at a time: `cn_ashare`, `hk_sg_equity`, `jpx_equity`, `krx_equity`. Offline feasibility and golden fixture packs first.
  Per market: directory maps, config, router, schema, calendar, currency, action, volume, rights contracts. ADR-0030 decision 5 restricts the real root to `us_equity`; a NEW ADR must amend it before any extension.
  No network call or spend without an actual scoped source/license/input packet. No implicit HK/SG conflation, no FX, no multi-market portfolio support.

### 2.10 Dividends and total return (RS-B01)

- Cash-dividend adjustment is not equivalent to the `total_return` mode. RS-B01 delivers a consistent, actionable refusal of `total_return` at loader/engine/CLI (one early error, not a per-session mask), preserving split-only behavior exactly.
  A future vendor total-return series would be a NEW schema/ADR adapter path — nothing here implies a current vendor TR path exists.
  Portfolio `CASH_DIVIDEND` book-income events already exist; do not reimplement them. A future cash-distribution backtest path needs a new decision covering fee/tax/reinvestment/currency, ex-date/payment/publication semantics and hand-math oracles. For example, 100 → 90 plus a 10 cash distribution is zero gross total return, not −10% or +10%; a 2:1 split creates no wealth. Never count both vendor-adjusted returns and cash distributions. Strict reads keep failing on weak or unproven coverage.

### 2.11 Live notifications (RS-S01)

- Build on the existing seams: outbox (`signals/tracking.py:1432`), quiet hours (`in_quiet_hours`, `tracking.py:1590`; 21:00-08:00 America/New_York at :1855), delivery revalidation (`tracking.py:294,1171,1650`), `enabled`/`live_delivery_enabled` gates (`tracking.py:1706-1726,1858`).
  Sequence: local fake sink + paper shadow, then one bounded private endpoint plus an **external local scheduler** — never public Pages, never a secret-bearing GitHub job.
  Requirements: DST-safe exact venue calendars; readiness checked before scan then deliver; no overlapping scheduler runs; bounded retries and missed-session handling; kill switch; delivery dedup keys with expiry, remove support, quiet-hours enforcement.
  ADR-0018's outbox contract is **not exactly-once**: unknown-receipt/duplicate-delivery risk stays disclosed, never claimed solved by dedup keys.
  Live rule scans run only on strict source coverage — weak research rows (`vendor_events_no_clock`) are insufficient for live; local paper shadow may use synthetic data. The endpoint/channel is missing operator input; no delivery during drafting. Rule-only signals need no ML promotion.

### 2.12 R00 evidence posture (RS-R01)

- Spec §7 required planted/null/leakage controls and measured 5/50/200 instruments over a three-year design; those measurements are `evidence-unverified` (§1.4) until located or reproduced.
  Draft defaults selected here: nominal one-sided alpha 0.05; 100 null seeds 1729–1828 and 50 planted-control seeds 11729–11778 on the five-instrument control fixture; pass only when the one-sided 95% binomial upper bound on null false-positive rate is ≤10% and lower bound on planted-signal power is ≥80%. Preregister the effect size, statistic and dependence-aware resampling rule before observing results; never require every null seed to lose. Freeze these defaults before running, without selecting seeds after results.
  Single-host sequential ceilings: 30 min per scale size, 2 h aggregate for controls and scale, RSS `min(8 GiB, 50% host RAM)`. Abort if RSS cannot be monitored; unavailable I/O counters carry a reason, not invented zeros. Over-budget or incomplete seed/scale sets remain censored and cannot establish a passing power or scale claim. No benchmark executed during drafting.

## 3. Residual → stage map

| Residual | Source | Stage | Disposition at drafting |
| --- | --- | --- | --- |
| H00 publication; no publish path exists | §11 row 1 | RS-H01 | `planned`; owner approval + platform verification |
| pages.yml stale sync check + dashboard artifact | §1.1–1.2 | RS-H01 | `planned` (defect reproduced) |
| catalog-deploy auto-deploy collision | §1.2 | RS-H01 | `planned` |
| H00 staleness over elapsed time | §2.3 | RS-H01 | `planned` |
| Archive extraction checks / canaries | §2.4 | RS-H01 | `planned` |
| Real private ledger never exercised | §11 row 2 | RS-P01 | `blocked-input` (no operator ledger) |
| Backup inventory + local recovery drill | §11 row 3 (retention) | RS-P02 | `planned` |
| Off-host encrypted export/drill | §2.5 | — | `blocked-input` + NEW ADR required first |
| Event deletion / pruning | §11 row 3 (deletion) | — | `deferred-invariant`; own ADR required |
| Backup-file pruning (housekeeping) | §2.5 | — | `deferred-invariant`; own ADR required |
| In-place `VACUUM` form | §11 row 4 | RS-P02 (reference) | `deferred-invariant`; non-goal, no measured need |
| R02 promotion / refit | §11 row 5 | — | `deferred-invariant`; no automatic refit |
| R02 archival/reproducibility (incl. §1.5 check) | §1.5 | RS-R02-D1 | `planned`; no refit |
| New ML experiment | §2.7 | RS-R02-D2 | `planned`; gated on RS-R01 + RS-D01 |
| R00 controls/scale measurements | §1.4 | RS-Q00 + RS-R01 | `evidence-unverified` (locate, then reproduce) |
| PIT on vendor-stamped rows | §11 row 6 | RS-D01 guards | `deferred-invariant` (permanent contract) |
| Real root never cataloged/mixed | §11 row 7 | RS-D01 guards | `deferred-invariant` (permanent contract) |
| Non-US real slices | §11 row 8 | RS-M01..RS-M04 | `blocked-input`; one market at a time |
| Cash-dividend adjustment | §11 row 9 | RS-B01 | `planned` (refusal consistency) |
| Cash-ledger dividends | §2.10 | — | `deferred-invariant`; schema/ADR first |
| Live delivery + scheduler (R12) | §11 row 10 | RS-S01 | `blocked-input` (endpoint/channel) |
| Outbox not-exactly-once risk (ADR-0018) | §2.11 | RS-S01 | `deferred-invariant`; disclosed, never claimed solved |
| Live scans on strict source coverage only | §2.11 | RS-S01 | `planned`; weak rows insufficient; paper may use synthetic |
| Doc lint recursion + baseline findings | §1.6 | RS-Q00 | `planned` (classify, not blanket-fix) |
| Disposable bootstrap / fresh-clone profiles | §2.2 | RS-E01 | `planned` |

## 4. Dependency graph and priority

```text
RS-Q00 ─► RS-E01 ─► RS-H01 ─► publish (owner-gated, external prerequisites)
RS-Q00 ─► RS-P01 (real rehearsal blocked-input; synthetic fixture rehearsal independent)
RS-Q00 ─► RS-P02 (local backup inventory/drill needs no demo lake)
RS-Q00 ─► RS-R02-D1 (independent of every other stage)
RS-Q00 + RS-E01 ─► RS-R01
RS-D01 ─► RS-M01 ─► RS-M02 ─► RS-M03 ─► RS-M04 (strictly sequential)
RS-Q00 ─► RS-B01 refusal fix (no external source prerequisite)
RS-D01 qualified-source evidence ─► any later total-return adapter
RS-R01 + RS-D01 ─► RS-R02-D2
RS-S01 shadow ─► independent of RS-R01/R02; live step blocked-input
```

Priority: RS-Q00 → RS-E01 first; then RS-H01 (unblocks the only §11 owner-gated item), RS-P02, RS-R01, RS-R02-D1 as
hosts allow; RS-B01's refusal fix can run after RS-Q00 without waiting for source access. RS-D01's offline guards precede market implementation; actual qualified-source evidence is required before dependent data runs. RS-R02-D2 follows controls + qualified data; RS-S01 shadow can run offline, while live activation waits for its strict-data and endpoint inputs.

## 5. Stage specifications

Per stage: deliverable, verified files/seams (present at `fc589d1`), tests + one-rule refusal mutation,
acceptance/external prerequisites. Statuses `planned`/`blocked-input`; design detail is in §2 and cited, not restated.

### RS-Q00 — Evidence inventory and true recursive lint

- **Deliverable:** durable evidence register (each §3 claim `CONFIRMED`/`PENDING`/`STALE`) with reproduction commands;
  classification of the 314-line recursive markdown baseline (re-captured from
  `/tmp/residual-recursive-markdown-baseline.log`) by rule and file. Make the lint gate genuinely recursive with a reviewed baseline ratchet for existing findings (separate archive inventory): fail on any new or expanded finding, never blanket-disable rules. Changed-file lint stays mandatory; broad cleanup is not hidden inside this stage. Search for the unlocated R00 measurement reports.
- **Files/seams:** `Makefile:150-152`, `docs/`, `data/research/`, `data/findings/`.
- **Tests/mutation:** planted nested-file violation fails the gate; remove recursion → escapes → fail; restore +
  `diff -q`.
- **Acceptance/external:** reviewer PASS on classification + gate change; none.

### RS-E01 — Disposable bootstrap

- **Deliverable:** §2.2 profiles and disposable bootstrap — canonical synthetic `data/lake/` for H00/CI, fixed seed plus
  the narrow optional end-date/as-of parameter threaded into `seed_demo()` and CLI with help tests (seeder otherwise
  anchors to yesterday); canary-secret proof the negative scan fires; "unavailable stays unavailable" fixtures.
- **Files/seams:** `src/equity_lake/devtools/seed_demo.py:228-271`, `src/equity_lake/cli/commands/demo.py`, `dashboard/h00.py` containment/attestation,
  `.gitignore:23` (`data/*`).
- **Disposable proof:** the bootstrap creates and owns a fresh OS temporary checkout from tracked source only, excluding ignored runtime data and `.env`; it does not accept an arbitrary operator lake as its destination. Before seeding, resolve the checkout and `core.paths.LAKE_DIR`, prove both lie beneath that newly allocated directory (including module/import provenance), reject symlink aliases and require the target lake to be empty. A `CI=true` flag or marker file alone is not proof. These guards belong to the new bootstrap, not a claim about the existing explicit-overwrite seeder.
- **Tests/mutation:** supplying a nonempty operator lake or a symlink to it refuses even with the existing explicit overwrite option; the original lake's file list and hashes remain unchanged. A reused/nonempty temporary target and an import resolving to the operator checkout also refuse. Same seed + end-date on two wall-clock days yields identical logical-data digests; help tests cover the new option. Remove the destination guard → the operator-lake sentinel test fails; restore. Separately, an injected canary in proposed public bytes must make the scan fail.
- **Acceptance/external:** reviewer PASS; none.

### RS-H01 — Publish infrastructure

- **Deliverable:** §2.1/§2.3/§2.4 design — one SHA-pinned manual deploy workflow publishing only the three H00 files;
  both legacy deployment paths retired; a workflow test asserts exactly one `deploy-pages` step across the repository and no push/schedule-triggered publisher; shared Pages concurrency; private deployment receipt (artifact↔commit binding,
  rollback records); short-lived artifacts; tested rollback/withdrawal; stale-over-elapsed-time behavior; extraction
  checks; required-reviewer protection verified via platform with recorded evidence; `pages.yml:29` removed.
- **Files/seams:** `.github/workflows/pages.yml`, `.github/workflows/catalog-deploy.yml`,
  `devtools/sync_schedule.py:18`, `dashboard/h00.py`.
- **Tests/mutation:** artifact contains exactly the three allowlisted files (nine-class negative scan reused); stale
  fixture with old payload timestamp flips the banner; symlink/oversize/path-traversal fixtures refused; one-rule
  mutation of the extraction allowlist lets a canary through → fail; restore + `diff -q`.
- **Acceptance/external:** reviewer PASS; dry-run build only. Platform settings evidence + owner publication approval +
  route ADR for any route change; nothing publishes without those.

### RS-P01 — Ledger rehearsal (`blocked-input`)

- **Deliverable:** §2.6 rehearsal on operator-provided canonical CSV/statements — dry-run with zero private writes
  (proved by hash), idempotent double-import, statement reconciliation, restore + compact on isolated copies first with
  production hash unchanged; fresh v2 = no migration; legacy v1 only on a legacy copy.
- **Files/seams:** `equity portfolio import|migrate|restore|compact|reconcile` CLI (ADR-0024, ADR-0031 contracts).
- **Tests/mutation:** mutation of the dry-run write guard produces a write → fail; restore.
- **Acceptance/external:** reviewer PASS; operator ledger inputs (§8).

### RS-P02 — Backup recovery and preservation

- **Deliverable:** §2.5 posture — manual local verified backup export + report-only backup-file inventory (in-contract
  per ADR-0031 d3); pre/post-migration recovery points; recovery drill on isolated local copies; documented invariant
  that all events are retained. Off-host export/drill specified but gated on a NEW ADR.
- **Files/seams:** `src/equity_lake/portfolio/store.py` verified-copy helpers and `src/equity_lake/cli/commands/portfolio.py`. Add `equity portfolio backup --output-dir <private-dir> [--apply]` (report-only by default) and `equity portfolio backups --directory <private-dir>` (read-only inventory). Reuse `restore`/`compact` for drills; never force a no-op migration just to obtain a backup. Update CLI help-scan tests and the private-ledger guide.
- **Tests/mutation:** report-only creates no directory/file/journal; real backup is verified and mode-restricted; busy/journal/corrupt-file refusals precede mutation; inventory never follows symlinks or deletes candidates. A tampered backup fails verification; restore to an isolated empty target reproduces the recorded bytes and replay fingerprint. Mutate the verification gate → corrupted candidate must be caught; restore.
- **Acceptance/external:** reviewer PASS + one successful local drill; off-host variant additionally needs its ADR +
  destination input. In-place `VACUUM`: documented non-goal — no implementation task.

### RS-R01 — R00 controls and scale evidence

- **Deliverable:** the §7 measurement set — planted-signal positive control; null controls (noise/permuted labels,
  predeclared seeds, dependence-aware resampling); leakage controls (append-future invariance, delayed observations,
  wrong identity, pending labels, missing coverage, execution lag); 5/50/200 instruments over the three-year design
  with warmup and minimum-universe disclosure; wall time, peak RSS, I/O, decision counts, output parity. Preregistered
  protocol (§2.12) frozen before running; censored runs recorded.
- **Files/seams:** `backtesting/protocol.py` freeze/refuse controls (R00's shipped part); `data/research/protocols/`
  manifests.
- **Leakage checks:** (1) append data after cutoff: identity/date-keyed features, weights, predictions and decisions on/before cutoff remain equal; (2) delay publication: the observation is unavailable before its observable session and cannot affect earlier outputs; (3) substitute another instrument's proof: explicit identity refusal before fit/write; (4) pending labels: no training/evaluation row whose maturity exceeds cutoff enters the sample; (5) missing/expired coverage: strict processing refuses or records unavailable, never falls back to raw marks; (6) execution lag/horizon: a hand-computed two-session fixture enters only on the frozen executable bar and purges label intervals crossing the fit cutoff.
- **Tests/mutation:** each leakage control has a named regression, fixed fixture, expected result, recorded observed result and one-rule mutation of its own invariant; every mutant must fail its named regression, then be restored before the next. Planted/null controls meet §2.12's frozen criteria (individual null false positives are allowed). Separate freeze/ceiling refusal mutations prove unfrozen or over-budget runs cannot proceed.
- **Acceptance/external:** reviewer PASS on the frozen protocol, all six leakage-control records plus mutation/restoration evidence, complete statistical control results and measured scale reports; a missing or failing leakage check leaves this stage unverified. Numeric defaults are chosen in §2.12 rather than left as an approval question. Record resource feasibility before execution; a censored point remains incomplete evidence, not success. Not executed during drafting.

### RS-D01 — Strict source qualification and root guard

- **Deliverable:** §2.8 guards — source-issued publication/vintage qualification procedure (no ingest-time clocks);
  append-only evidence records; as-of/future invariance and revision tests; real-root guard tests with canary symlinks +
  containment; `--lake-dir` preserved; strict refuses weak; mixed bases refuse.
- **Files/seams:** `storage/lake_reader.py`, `ml/feature_loader.py`, `resolve_lake_root`/`--lake-dir` guards,
  ADR-0032 d10 / ADR-0034.
- **Tests/mutation:** canary symlink under the real root refuses cataloging/mixing; remove a stamp invariant check →
  future-dated fixture passes → fail; restore.
- **Acceptance/external:** offline guard and qualification-fixture work needs no external input. Actual source qualification is separately `blocked-input` until source-issued clocks, vintage/expiry evidence and rights are supplied; passing synthetic guards alone cannot unblock a live consumer or claim qualified data.

### RS-M01..RS-M04 — Markets, one at a time

- **Deliverable (per `cn_ashare`, `hk_sg_equity`, `jpx_equity`, `krx_equity`):** offline feasibility pack + golden
  fixtures, then the §2.9 contract set (directory maps, config, router, schema, calendar, currency, action, volume,
  rights). HK/SG never conflated; no FX; no multi-market portfolio support.
- **Files/seams:** market config/router/schema modules; repository calendar.
- **Tests/mutation:** golden fixtures round-trip; calendar boundary tests; one-rule mutation of an action/volume
  contract → fixture fails; restore.
- **Acceptance/external:** reviewer PASS per market; NEW ADR amending ADR-0030 decision 5 + scoped
  source/license/input packet (§8) before any network use or spend.

### RS-B01 — Total-return compatibility

- **Deliverable:** §2.10 refusal — consistent actionable error for `total_return` at loader/engine/CLI (single early
  error naming ADR-0015 and the absent vendor series), split-only byte-identical; documented adapter plan for a future
  vendor TR series (NEW schema/ADR; hand-math oracles; never double-count dividends) as non-scheduled follow-up.
- **Files/seams:** `storage/lake_reader.py:30-33,205-206`, `backtesting/data_loader.py:130-136,599`,
  `docs/user-guide/backtesting.md`.
- **Tests/mutation:** every entry path (CLI, Python loader, engine) raises the same actionable error before per-session
  work; mutation reverts one entry to silent pass-through → fail; restore. Split-only golden results unchanged.
- **Acceptance/external:** reviewer PASS; none (a vendor series would be operator input, §8).

### RS-R02 — Archival (D1) and new experiment (D2)

- **D1 (independent; no refit):** verify `data/research/r02/` artifacts against their digests (protocol `37fd6727…`,
  prespec `ae74513b…`), protocol↔manifest binding, and offline recovery (recompute every reported metric from
  `predictions.parquet` + `baselines.json`). Explicitly verify estimand, aggregation, units, and bootstrap-interval
  consistency of the −2.79 pp / [−0.42 pp, +0.31 pp] pairing (§1.5): clarify or recompute any discrepancy from the
  immutable artifacts **without refitting**, appending a correction trail (never rewriting the report in place).
- **D2 (gated on RS-R01 + RS-D01):** one experiment per §2.7 — frozen protocol, new untouched scope, strict-vs-weak
  separation, preregistered matched dates/costs/baselines/block-bootstrap criteria; no automatic refit; `promoted=false` in every research artifact even if evaluation criteria pass. Production is not this stage and would need a separate ADR plus strict evidence.
- **Files/seams:** `data/research/r02/*`, `data/research/protocols/`, `ml/feature_loader.py`, `backtesting/protocol.py`.
- **Tests/mutation:** digest tamper → load refuses; perturbed artifact → discrepancy flagged by the recompute check;
  one-rule mutation of the binding check → fail; restore.
- **Acceptance/external:** D1 reviewer PASS on a verified recovery copy and recomputation receipt; originals remain byte-identical and corrections are append-only. Missing original artifact/snapshot dependencies leave reproduction `blocked-input`, not regenerated by refitting. D2 reviewer PASS with a negative outcome acceptable; the qualified data packet and frozen untouched scope must exist first.

### RS-S01 — Paper shadow to bounded live

- **Deliverable:** §2.11 sequence — local fake sink + paper-shadow loop over the existing outbox; readiness checked
  before scan then deliver; DST-safe exact venue calendars; overlap-refusing external local scheduler; bounded retries +
  missed-session handling; kill switch; delivery dedup keys with expiry/remove, quiet-hours honored, ADR-0018's
  not-exactly-once disclosure preserved (unknown receipt / duplicates); live rule scans only on strict source coverage
  (paper shadow may use synthetic). Rule-only signals throughout — no ML promotion.
- **Files/seams:** `signals/tracking.py:255,1432,1590,1706-1726,1855-1858`, `api/routers/tracking.py`,
  `cli/commands/tracking.py`.
- **Tests/mutation:** quiet-hours fixture delivers nothing; duplicate/unknown-receipt behavior demonstrated and
  disclosed (not silently "exactly-once"); weak-basis fixture refused by live-scan readiness; kill switch stops
  delivery; overlap lock refuses a second run; one-rule mutation of expiry → stale event delivers → fail; restore.
- **Acceptance/external:** shadow results recorded; reviewer PASS; endpoint/channel + scheduler host (§8) before any
  live delivery. No delivery during drafting.

## 6. Shared gate (every stage, after implementation, before acceptance)

1. `uv sync --group dev --group backtesting --group dashboard` (add `--group docs` for docs changes).
2. Serial, then parallel pytest with redirected logs and JUnit XML (`--junitxml=…`), run sequentially, never
   overlapping an agent's own pytest.
3. `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
4. `lens_diagnostics source=lsp scope=paths` on changed code (an empty session cache is not proof).
5. `uv run equity catalog-generate` only for source/schema/pipeline changes; never edit JSONL by hand.
6. CLI help surface + user docs for behavior changes; architecture/data-flow docs when structure changes.
7. Documentation: explicit changed-file markdown lint (the repo `make lint-md` scan is not recursive — §1.6; the
   preexisting baseline, incl. MD001 findings, is RS-Q00's to classify, not stage failures), file + anchor checks for
   every referenced path, MkDocs build to a temporary output directory. State lint-coverage limitations honestly in the
   stage record.
8. Independent reviewer PASS on the dirty stage diff; prove each distinct new refusal with a one-rule mutation, serialized and restored from a `/tmp` copy with `diff -q`. Re-review fixes that change behavior; run final gates after restoration. Never `git checkout`/stash/reset another agent's or the owner's work.

## 7. Stage ledger template

Status vocabulary: `planned` / `implementing` / `verified` / `operated` / `blocked-input` / `deferred-invariant`.
`verified` = gates + review + mutation passed; `operated` additionally requires a real authorized run (a publish, a
drill, a live delivery).

```text
Stage RS-__ / status / recorded UTC:
Deliverable and design reference (this document §__):
Dependencies (stage IDs) and accepted-ADR evidence:
Commit identity (stage start → reviewed final diff):
Evidence: gate logs + JUnit XML paths, mutation/restore record, digests:
Inputs used (and provenance):
External prerequisite state (satisfied / pending, named operator input):
Rollback / withdrawal procedure:
Residuals left and next eligible stage:
```

## 8. Operator inputs required

1. Repository Pages access and the publication authorization record required by the existing human release gate. The sole-H00-root layout is already chosen in §2.1; a route ADR must codify that design before implementation.
2. Platform Pages environment-protection settings evidence (required reviewers).
3. Real ledger: canonical CSV + statements/marks (RS-P01).
4. Off-host backup/drill destination + acceptance of the NEW ADR that must precede it (RS-P02).
5. Per-market source/license/input packet + ADR-0030 amendment acceptance (RS-M01..04).
6. Live endpoint/channel + scheduler host + kill-switch owner (RS-S01 live step).
7. Runtime hardware capacity and measurement availability for the already chosen RS-R01 protocol defaults (§2.12); insufficient capacity yields censored evidence, not silent budget expansion.
8. Optional: vendor total-return series decision (opens the RS-B01 follow-up schema/ADR).

## 9. What this document is not

Not an accepted ADR, not a publication or delivery authorization, not permission to fetch data, deploy, delete events,
prune backups, or retrain/promote models. It records design decisions so implementers do not re-litigate them; it
records no implementation and no implementation outcome. All stage statuses are `planned`, `blocked-input`, or `deferred-invariant`;
no historical review count or gate is claimed as current implementation status.

## 10. Draft review and documentation validation

Independent plan review returned **PASS** after three revisions: remove unrelated offline-stage dependencies,
specify all six leakage-control regressions/mutations, and define positive disposable-checkout proof. This verdict
reviews the plan, not any future implementation. No code suite was run for this documentation-only change.

Explicit lint of all four changed Markdown files passed; 99 local file/anchor links resolved; `git diff --check`
passed. MkDocs built to `/tmp/equity-residual-plan-site` with four existing warnings in ADR-0029 and the Nov07
handoffs, none targeting the new plan. The full recursive Markdown baseline remains non-clean (§1.6); a nonrecursive
repo scan is not substituted for that result.
