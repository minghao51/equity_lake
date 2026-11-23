# Residual Spec and Implementation Plan — 2026-11-22

**Status: planning document drafted at `2634060` on `main`; stage outcomes recorded in §11 (2026-11-23).** Closes out the residuals
of the [2026-11-02 implementation spec](20261102-01-portfolio-and-research-implementation-spec.md) §11 with encoded
design decisions, a stage map, and per-stage specifications. Every stage has since been implemented locally and its
outcome is recorded in §11 as `verified-local`, `blocked-input`, or `deferred-invariant`. Nothing is `operated`: no
publication, no live delivery, no real-ledger rehearsal, no data acquisition, no model promotion. Still not an
accepted ADR, not publication/delivery authorization, and not permission to fetch data, deploy, delete events, prune
backups, or retrain/promote models.

> **Read §11 first if you are picking this up.** It records, per stage, what was actually implemented, what was
> measured on this host, which mutations were proven, and which prerequisites remain operator-gated. The §3
dispositions below are the drafting-time record, kept for traceability.

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

> **Drafting-time record.** This table states each residual's disposition when the plan was written on 2026-11-22.
> §11 supersedes it with the implemented outcome for every stage; nothing below should be read as current status.

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
prune backups, or retrain/promote models. It records design decisions so implementers do not re-litigate them, and §11
records local implementation outcomes. A `verified-local` stage means its gates, review and mutation proofs passed
locally; it is not an operational claim, and it does not discharge an operator-gated prerequisite. No historical
review count or gate is claimed as current implementation status, and no stage is `operated`.

## 10. Draft review and documentation validation

> Historical record of the drafting review. Implementation outcome and the post-implementation review are in §11.

Independent plan review returned **PASS** after three revisions: remove unrelated offline-stage dependencies,
specify all six leakage-control regressions/mutations, and define positive disposable-checkout proof. This verdict
reviews the plan, not any future implementation. No code suite was run for this documentation-only change.

Explicit lint of all four changed Markdown files passed; 99 local file/anchor links resolved; `git diff --check`
passed. MkDocs built to `/tmp/equity-residual-plan-site` with four existing warnings in ADR-0029 and the Nov07
handoffs, none targeting the new plan. The full recursive Markdown baseline remains non-clean (§1.6); a nonrecursive
repo scan is not substituted for that result.

## 11. Stage outcomes and ledger (2026-11-23)

Every stage below was implemented locally in isolated worktrees and merged into `main` one at a time, each merge
re-verified in the main checkout (targeted tests plus `ruff`, `ruff format --check`, `mypy`, and the recursive
Markdown ratchet). Status vocabulary is §7's. `verified-local` = gates + independent review + mutation proofs
passed locally. **No stage is `operated`.** Nothing here authorizes a publish, a live delivery, a real-ledger
rehearsal, data acquisition, or a model promotion.

Summary of what is genuinely closed and what is not:

- **Closed locally, with measured evidence:** RS-Q00, RS-B01, RS-R02-D1, RS-E01, RS-H01 (infrastructure only), RS-D01
  (guards only), RS-M01..RS-M04 (offline packs), RS-S01 (paper shadow only), RS-P02, RS-R01, and RS-P01's synthetic half.
- **`blocked-input`, requires an operator:** H00 publication (platform reviewer protection evidence + publication
  approval + a route ADR), real-ledger rehearsal (canonical CSV + statements), live notification delivery
  (endpoint/channel + scheduler host + kill-switch owner), off-host backup export/drill (a NEW ADR + destination),
  real non-US market use (a per-market source/licence packet + an ADR amending ADR-0030 decision 5).
- **`deferred-invariant`, each needs its own ADR:** event deletion or pruning, backup-file pruning, in-place `VACUUM`,
  a cash-distribution backtest path, a vendor total-return adapter, R02 promotion/refit.
- **Named residual from the post-implementation review:** the H00 deployment receipt, retained rollback bundles and
  90-day bundle retention are documented in the hosting guide as *not implemented*; ~140 test files still carry
  pre-existing `# type: ignore[import-untyped]` comments that are now redundant (recorded debt, not mass-edited).

Post-implementation review: the independent review of the accumulated diff returned FAIL with four blocking findings
(overstated source-clock integrity claim, MD013 disabled without an auditable record, paper-shadow scan/delivery lock ordering,
and a baseline written without a Pydantic boundary) plus a missing stamp-invariant mutation. All four were fixed and a
real point-in-time gap was closed in the process (evidence published after an `as_of` date can no longer qualify that
read); the fixes and their mutation/gate results are recorded in the RS-R01 status-correction entry below.
The re-review of those fixes returned **PASS**: all four findings resolved, no new issues, and ruff, ruff format,
mypy, `make lint-md` and the four touched test modules all exit 0. Stage entries follow as recorded at merge time.

## RS-B01

Stage RS-B01 / status: verified-local / recorded 2026-11-23
Deliverable and design reference (§2.10, §5): one early actionable `total_return` refusal at loader, engine constructor, engine factory and CLI; `with_price_adjustment` keeps defence in depth; unknown adjust values raise a clear ValueError instead of KeyError; split-only output byte-identical (digest 93fabd19…, unchanged).
Dependencies: RS-Q00 (planned); ADR-0015 unchanged, no new ADR.
Commit identity: 2634060 → ddf86f1 (merged de30b59).
Evidence: /tmp/integrate-b01.log (32 passed); mutation: removing the early resolve_adjustment_method in load() fails test_total_return_is_refused_before_any_read (exit 1), restored with diff -q exit 0 and identical sha256; ruff/format/mypy exit 0 (193 files).
Inputs used: local repo only; no network, no vendor total-return series.
External prerequisite state: none. A future vendor TR series is operator input and needs a new schema/ADR (documented as non-scheduled follow-up).
Rollback: git revert ddf86f1 (no data or migration effects).
Residuals left: cash-dividend backtest path (schema/ADR first, deferred-invariant); `total_return` remains in the public Literals deliberately (removing it would be a breaking surface change); next eligible stage RS-D01 / RS-Q00.

## RS-Q00

Stage RS-Q00 / status: verified-local / recorded 2026-11-23
Deliverable and design reference (§5 RS-Q00, §1, §2): durable evidence register docs/finding/20261123-01-residual-evidence-register.md (each §1 claim CONFIRMED with a reproduction command); genuinely recursive markdown gate (devtools/markdown_lint.py) with a reviewed (file,rule,count) baseline config/markdown-lint-baseline.json (314 findings / 35 files); explicit `make lint-md-baseline` is the only refresh path; changed-file lint in quality.yml retained.
Dependencies: none (base stage). No ADR.
Commit identity: 2634060 -> af4d45c (merged).
Evidence: `make lint-md` exit 0 ("314 preexisting findings in 35 files match baseline; 0 new or expanded", 9s); 11 new tests in tests/unit/test_markdown_lint_gate.py; mutation A removed `-r` -> planted nested violation invisible and the gate wrongly passed (exit 0) -> restored, diff -q identical; mutation B (growth detection) -> wrongly passed -> restored identical; ruff/format/mypy exit 0.
Inputs used: /tmp/residual-recursive-markdown-baseline.log re-derived identically; local repo only.
External prerequisite state: none.
Rollback: revert af4d45c (Makefile returns to the non-recursive scan; four added files removed).
Residuals left: 314 findings frozen as classified debt (not fixed, by design); R00 evidence still unverified -> RS-R01; R02 estimand -> RS-R02-D1.
Plan correction to apply: §1.7 says `data/research/` holds h00/r02/protocols — true in the operator checkout, but a fresh worktree has no `data/research/` at all (runtime data is gitignored). Reword to "in the operator checkout".

## RS-R02-D1

Stage RS-R02-D1 / status: verified-local / recorded 2026-11-23
Deliverable and design reference (§1.5, §2.7, §5): committed read-only verifier `python -m equity_lake.devtools.verify_r02_artifacts --research-dir data/research/r02 [--lake-dir …]` (exit 0 verified / 1 tamper / 2 absent), tracked digest anchor docs/finding/20261123-01-r02-recovery-receipt.md, append-only `## Correction (2026-11-23, append-only)` in docs/reports/20261121-r02-result.md with the original text byte-identical.
Dependencies: independent. ADR-0034 d4/d5 (promoted=false) preserved; no refit.
Commit identity: 2634060 -> f6ccdee (merged).
Evidence: main-checkout reproduction `checks: 275, mismatches: 0` with `--lake-dir data/lake_real`; without a lake root the economic metrics are reported UNVERIFIED (174 checks) rather than assumed — predictions.parquet carries no per-session closes. Tamper tests: tampered baselines -> exit 1; perturbed prediction -> exit 1; mutated prespecification_digest -> exit 1; mutated manifest input_digests -> exit 1; absent archive -> exit 2 SKIPPED with reason. Originals: same six sha256 before/after.
Estimand finding (no defect): point estimate -0.0279030349 (-2.79 pp) is the difference of COMPOUNDED net returns; the recorded interval [-0.0041605634, 0.0030657660] is the percentile interval of the MEAN PER-SESSION difference whose own estimand is -0.0003740027 (-0.0374 pp/session) and lies inside. The economics criterion fails on test_delta > 0 regardless, so 1/5 and promoted=false are unchanged.
Inputs used: operator `data/research/r02/*` + manifest 20261121T130000Z-37fd6727d62f (read-only), ml/experiment.py frozen estimators.
External prerequisite state: satisfied where the archive exists; a fresh clone skips with a reason.
Rollback: revert f6ccdee; archive untouched, appended section additive.
Residuals left: report-generator wording (label compounded vs per-session difference) is a deferred follow-up; RS-R02-D2 still gated on RS-R01 + RS-D01.

## RS-P01 (synthetic rehearsal half only)

Stage RS-P01 / status: synthetic rehearsal PASSED; real-instance rehearsal BLOCKED-INPUT / recorded 2026-11-23
Deliverable and design reference (§2.6, §3, §5): rehearsal on committed synthetic fixtures — dry-run with zero private writes (tree hash unchanged), idempotent double import (`inserted 0, unchanged 4`, stable record_digest), statement reconciliation with a deliberate mismatch reported (`delta -24.00`, ledger not adjusted), restore+compact on isolated copies with the production digest unchanged, fresh v2 needs no migration and manufactures no backup, legacy v1 converted only on a legacy copy.
Dependencies: independent of RS-Q00 in execution; ADR-0024 d3-5/10/13, ADR-0031 d2-5/7 unchanged.
Commit identity: 334a58f -> e248d57 (merged).
Evidence: /tmp/integrate-p01.log (12 passed in the main checkout); mutation of `portfolio.py:148` `if dry_run:` -> `and False:` made 2 named tests fail (exit 1), restored with diff -q exit 0 and sha256sum -c OK; ruff/format/mypy 0 (195 files); markdown lint 0.
Inputs used: tests/fixtures/portfolio/{canonical-ledger,canonical-ledger-extension,statement-matched,statement-mismatched}.csv + legacy-v1-ledger.sql — synthetic, committed, invented values. No real ledger exists in this checkout.
External prerequisite state: PENDING — operator canonical CSV + statements/marks (§8). Synthetic rehearsal does not discharge it.
Rollback: revert the single commit (one test module, five fixtures, one record); no production code, CLI, store or data/ path touched.
Residuals: no `portfolio backup`/`backups` command existed at rehearsal time (RS-P02 delivers it); `_write_verified_backup` names per second and never overwrites, so two drills sharing a directory in the same second would refuse the second copy — drills must own their directory. Real-instance rehearsal remains blocked-input.

## RS-P02

Stage RS-P02 / status: verified-local (orchestrator-verified after an interrupted run) / recorded 2026-11-23
Deliverable (§2.5, §5): `equity portfolio backup --output-dir <private-dir> [--apply]` (report-only by default; `--output-dir` required; created 0700 with --apply only; never a shared or public path) and `equity portfolio backups --directory <private-dir>` (read-only inventory; symlinks and foreign files reported and never read). Store helpers `export_verified_backup` / `inventory_backups` plus frozen report models `LedgerBackupReport`, `LedgerBackupCandidate`, `LedgerBackupSkip`, `LedgerBackupInventory`; reused `verify_ledger_file`, `_write_verified_backup`, `_replace_atomically`.
Dependencies: ADR-0031 d3 (manual local verified backup export; no scheduled/remote/cloud backup). No new ADR.
Commit identity: merged from recovered branch (base 2634060).
Evidence: /tmp/integrate-p02.log — test_portfolio_cli, test_portfolio_store_migration, test_portfolio_restore_compaction and test_portfolio_synthetic_rehearsal all pass, plus new tests/unit/test_portfolio_backup.py; `portfolio backup --help` and `portfolio backups --help` inspected; ruff/format/mypy exit 0.
Inputs used: synthetic fixtures and tmp_path only; no operator ledger, no data/ writes.
External prerequisite state: PENDING — the off-host encrypted export/drill needs a NEW ADR + operator destination and is NOT implemented; backup-file pruning and event deletion remain deferred-invariant with their own ADR required.
Rollback: revert the merge (two CLI commands, store helpers, tests, docs); no ledger migration.
Residuals left: retention invariant documented (all events retained, no pruning); in-place VACUUM documented as a non-goal with VACUUM INTO as the supported form; drill evidence is synthetic/isolated-copy only, not an operator-ledger drill.

## RS-H01

Stage RS-H01 / status: verified-local; publication remains owner/platform-gated / recorded 2026-11-23
Deliverable (§2.1, §2.3, §2.4, §5): a single `.github/workflows/h00-publish.yml` — `workflow_dispatch` only, `permissions: contents: read` at workflow level with `pages: write` + `id-token: write` scoped to the deploy job, shared `concurrency: {group: github-pages, cancel-in-progress: false}`, builds ONLY `equity dashboard h00-build -o dist`, runs `python -m equity_lake.devtools.verify_h00_release --dir dist`, uploads `dist` with `retention-days: 3`, and a single SHA-pinned `actions/deploy-pages`. `pages.yml` retired (deleted); `catalog-deploy.yml` replaced by build-only `catalog-site-build.yml` (no deploy job, no Pages permissions); `sync_schedule.py` and its test retired with no remaining caller.
Dependencies: H00 owner decisions; a route ADR must precede any public-route change (not created here).
Commit identity: merged from a recovered branch (base 2634060).
Evidence: /tmp/integrate-h01.log — test_publish_workflows, test_h00_release_verification, test_h00_sanitized_page and test_markdown_lint_gate all pass; `grep -rn deploy-pages .github/workflows/` shows exactly one real step (h00-publish.yml:83, SHA d6db9016…) plus two explanatory comments; no workflow triggers a publisher on push/schedule; `make lint-md` still 314/35.
verify_h00_release refusals: release path or any entry is a symlink; nested directory; non-regular file; non-plain file name; entry escaping the release directory; size bounds; non-UTF-8; invalid JSON; sanitization report not recording a passing scan, the demo-lake root, synthetic provenance or the allowlist.
Staleness over elapsed viewer time: the stale element is always present and a client-side age check against the payload timestamp plus `stale_threshold_days` flips it, while build-time `is_stale` is retained. Honest limit: proven by structural/JS assertions and mutation, not by a real browser run.
Inputs used: local repo only; no publish, no Pages enablement, no secrets, no network.
External prerequisite state: PENDING — platform environment protection (required reviewers) must be verified in platform settings with recorded evidence; owner publication approval outstanding; rollback bundles and the artifact-to-commit deployment receipt are explicitly documented as NOT implemented yet (docs/user-guide/20260406-dashboard-hosting.md:88-90), and 90-day retention of three verified bundles is not implemented.
Rollback: revert the merge (publish workflow removed, legacy deployers restored); nothing was published, so no withdrawal is needed.
Residuals: deployment receipt and retained rollback bundles unimplemented (documented); publication not performed and not authorized.

## RS-M01 (cn_ashare only)

Stage RS-M01 / status: verified-local (orchestrator-verified after an interrupted run); M02..M04 still open / recorded 2026-11-23
Deliverable (§2.9, §5): `docs/developer/architecture/market-cn-ashare-feasibility.md` (verified seams with file:line, declared gaps, input packets, ADR-0030 d5 as the blocking amendment); `src/equity_lake/core/market_contracts.py` — per-market capabilities with explicit `CapabilityState = Literal["present","unknown"]`, `Capability.input_packet`, and `require_capability` failing closed on `unknown`; fixtures for all four markets under `tests/fixtures/markets/<market>/ohlcv.csv` plus README and `tests/unit/_market_fixture_pack.py`; `tests/unit/test_market_cn_ashare.py`; `corporate_actions.py` now admits only `CORPORATE_ACTION_MARKETS`, a behaviour-preserving literal-to-declaration refactor that keeps us_equity the only supported route and leaves non-US markets declared unsupported with input packets.
Commit identity: merged from a recovered branch (base de30b59).
Evidence: /tmp/integrate-m01.log — test_market_cn_ashare passes; no `data/catalog.jsonl` drift; the corporate_actions.py diff was inspected and does not relax the non-US refusal.
Inputs used: repository evidence and synthetic fixtures only; no network, no data acquisition, no FX.
External prerequisite state: PENDING — a NEW ADR amending ADR-0030 d5 plus a scoped source/licence/input packet before any real-root use; not created here.
Rollback: revert the merge; no data or catalog effect.
Residuals: M02 (hk_sg_equity), M03 (jpx_equity), M04 (krx_equity) outstanding; volume/lot, price-limit, settlement and rights contracts remain `unknown` by design.

## RS-M02

Stage RS-M02 / status: verified-local (offline pack) / recorded 2026-11-23
Deliverable (§2.9, §5): docs/developer/architecture/market-hk-sg-equity-feasibility.md; tests/unit/test_market_hk_sg_equity.py; one MARKET_CONTRACTS row; mkdocs nav. Verified seams cited at paths.py:71,157,170,224; types.py:41,62,93; router.py:61-73,389; sources/hk_sg.py:55; calendar.py:27,44-45,62-72,75-94,116,141-156,159; tickers.yaml:650-651,789-790; instruments.py:40,42,62,264; catalog/datasets.py:221-228.
Dependencies: RS-D01 guards; ADR-0030 d5 unchanged, no ADR created. Commit: 6543f7f -> a0b91fa (merged).
Evidence: 36 market tests pass in the main checkout (/tmp/integrate-m02.log); mutation of corporate_actions -> _US_CORPORATE_ACTIONS made 2 tests fail (exit 1), restored with diff -q 0 then pytest 0; fixture digest 8571b7aa…; ruff/format/mypy 0 (196 files); catalog byte-identical (537bf5fb…, 18 datasets).
HK/SG no-conflation proof: two venues (XHKG/XSES), two currencies (HKD/SGD) never converted; test_hk_and_sg_are_never_conflated uses 2024-06-10 (XHKG closed, XSES open) and 2024-06-17 (inverse) to show classify_market_sessions/is_trading_day return True (union) while trading_days_between excludes them (intersection); market_timezone returns the HK zone and is documented as not an SG clock.
External prerequisite state: PENDING — operator market source/licence packet plus a NEW ADR amending ADR-0030 d5 before any real-root use.
Residuals: actions, volume/lot, price-limit, settlement and rights all `unknown` with input packets; next eligible RS-M03.

## RS-M03

Stage RS-M03 / status: verified-local (offline pack) / recorded 2026-11-23
Deliverable: market-jpx-equity-feasibility.md; test_market_jpx_equity.py; one MARKET_CONTRACTS row; nav. Resolves the caveat: paths.py:158 ("JPX",) is a calendar identifier while identity is XTKS (instruments.py:44-46,63), bridged only at calendar.py:46; one calendar so intersection == union, pinned to the exact 06:00 UTC TSE close.
Dependencies: RS-M02. Commit: a0b91fa -> 12052d4 (merged).
Evidence: mutation of _MIC_TO_CALENDAR["XTKS"] -> "XHKG" failed the tests (exit 1), restored diff -q 0 then pytest 0; fixture digest e6691d00….
External prerequisite state: PENDING — same operator packet + ADR-0030 d5 amendment.
Residuals: the five contracts remain unknown; next eligible RS-M04.

## RS-M04

Stage RS-M04 / status: verified-local (offline pack) / recorded 2026-11-23
Deliverable: market-krx-equity-feasibility.md; test_market_krx_equity.py; one MARKET_CONTRACTS row; nav. Seams: paths.py:73,159; types.py:64; router.py:391,162-175; sources/krx.py:19-20,75,80 (FinanceDataReader, not yfinance); calendar.py:47; tickers.yaml:975-976,1133; catalog/datasets.py:237-244.
Dependencies: RS-M03. Commit: 12052d4 -> abfc7f3 (merged).
Evidence: mutation of PRICE_MARKET_CONFIG_KEYS["krx_equity"] -> ("jpx",) failed the tests (exit 1), restored diff -q 0 then pytest 0; fixture digest 477a75d9….
External prerequisite state: PENDING — same operator packet + ADR-0030 d5 amendment.
Residuals: five contracts unknown plus a provider venue-label gap; all five markets are now declared, with us_equity the only market holding a `present` corporate-action capability; next eligible stage is the operator input packet + ADR-0030 d5 amendment.

## RS-D01

Stage RS-D01 / status: verified-local guards; real qualification BLOCKED-INPUT / recorded 2026-11-23
Deliverable (§2.8, §5): `src/equity_lake/core/source_clock.py` — frozen `SourceClockRecord` (extra="forbid") with record_id = sha256 of its own content, source, market, series ("*" = whole market), evidence_kind, evidence_digest/evidence_uri (at least one required), source_available_at, coverage_start/end, expires_on, rights_reference, recorded_at, supersedes, record_schema_version; append-only store at `DATA_DIR/source_clock_qualification/records.jsonl` (auxiliary, not cataloged; DATA_DIR read at call time); `qualify_source_clock(...)` and `require_qualified_source_clock` (raises `SourceClockUnavailable`); `core/paths.refuse_cross_root_symlink` called from `storage/delta.delta_table_path` (single resolution seam); opt-in clock gate on `FeatureLoader.load_features(source_clock_source=…, source_clock_records=…)` with defaults unchanged.
Dependencies: ADR-0015 d4, ADR-0030 d2-4/d10, ADR-0032 d6/d10, ADR-0034, ADR-0006, ADR-0019. No ADR created or changed.
Commit identity: 6543f7f -> ede3f9d, 006fe95 (merged).
Evidence: /tmp/integrate-d01.log — test_source_clock_qualification, test_pit_invariants, test_lake_root_selection and test_feature_loader_coverage_basis pass in the main checkout; mutation (a) removing the strict as-of filter failed test_future_dated_rows_cannot_enter_an_earlier_as_of_read (exit 1), restored diff -q 0; mutation (b) removing the refuse_cross_root_symlink call failed 3 canary tests (exit 1), restored all pass; fixture digest 7fec1283…; catalog unchanged 537bf5fb…; ruff/format/mypy 0; markdown ratchet 0.
Why an ingest clock cannot be proof: `evidence_kind` is closed to `source_publication|source_vintage`; `ingest_time` exists only so the refusal is explicit and testable (constructing raises). `source_available_at` may not postdate `recorded_at`; `expires_on >= coverage_end`; a tampered row fails its content digest and is treated as unverifiable.
Gate fail-closed cases: no record, unverifiable record, all superseded, more than one active record (ambiguity), outside the coverage window, expired — each returns `unknown` plus the input packet; there is no weak-basis fallback.
Root guards: canary `lake_real/01_bronze -> lake/01_bronze` and the reverse are refused on read, the canonical default root is guarded, root escapes are refused, a legitimate explicit `--lake-dir` keeps working, and the guard is inert for paths outside any named lake root (no I/O).
External prerequisite state: PENDING — source-issued publication/vintage clocks, coverage/expiry evidence and rights, plus any ADR amending ADR-0030 d5 before a real-root extension. No source is qualified and no live consumer is unblocked.
Rollback: revert the two commits; no data, catalog, ADR or ingestion default changed; delete the auxiliary directory to discard records.
Residuals: PIT on `vendor_events_no_clock` rows remains impossible and is tested as a refusal, never relabelled.

## RS-S01 (paper-shadow half only)

Stage RS-S01 / status: verified-local shadow; live activation BLOCKED-INPUT / recorded 2026-11-23
Deliverable (§2.11, §5): `equity tracking shadow` with `--as-of`, `--rounds` (1–30), `--scan/--no-scan`, `--require-strict-coverage`, `--watchlist`, `--ticker-config`, `--config`, `--lake-dir`, `--data-dir`, plus `equity tracking deliver --remove-event ID`. Local `PaperSink` receipt recorder (no HTTP sender is ever constructed). Kill switch = file marker `data/tracking/DELIVERY_DISABLED` (fails closed if uninspectable). Expiry = the event's own stored session at most 5 venue sessions behind the latest completed session -> `expired`/`expired_out_of_window`; removal marks `pending`->`expired`/`removed_by_operator` and reports `unsend_possible: false`. No ledger migration and no new column.
Dependencies: ADR-0018 outbox contract (unchanged). No ADR created.
Commit identity: 6543f7f -> f4329eb, 5ed4352, 16fa929 (merged).
Evidence: /tmp/integrate-s01.log — test_private_tracking_events, test_tracking_cli and test_tracking_readiness pass in the main checkout (38/38 in the lane, 86 across touched modules); mutation disabling expiry failed test_paper_shadow_expires_a_stale_session_instead_of_burst_delivering (exit 1), restored diff -q 0 with identical sha256; ruff/format/mypy 0 (196 files); markdown ratchet 0.
Guard order: readiness (watchlist_unreadable / history_corrupt) -> strict-coverage gate -> kill switch -> bounded scan -> readiness re-checked -> per round, under `_delivery_lock(blocking=False)` (overlap refused, never queued), kill switch and quiet hours re-checked -> per event expiry -> revalidation -> at most 3 attempts.
Strict coverage: a `vendor_events_no_clock` (weak) basis is refused by the live-scan readiness path while the paper shadow still runs on synthetic data, proven with a fixture.
Disclosure preserved: `NOT_EXACTLY_ONCE_DISCLOSURE` appears in the module, `format_schedule_plan`, the report (`not_exactly_once`) and the docs; dedup keys are never described as exactly-once and duplicate receipts are recorded in `duplicate_event_ids`.
Inputs used: synthetic fixtures and tmp_path only; no network, no real data, no secrets.
External prerequisite state: PENDING — operator endpoint/channel, scheduler host and kill-switch owner (§8 item 6); scheduler activation stays owner-gated; intended shape is an external local cron/systemd job, never a public Pages job or a secret-bearing GitHub job.
Rollback: revert the three commits; no ledger migration, no data effect.
Residuals: live delivery not enabled or tested; an earlier partial branch's `deliver_pending`-inside-`flock` design was discarded as a self-deadlock.

## RS-E01

Stage RS-E01 / status: verified-local (orchestrator-verified after relaunch) / recorded 2026-11-23
Deliverable (§2.2, §5): `seed_demo(end_date=…)` pins the synthetic window; `equity demo seed --end-date YYYY-MM-DD` (ISO parse, `BadParameter` on garbage); new `src/equity_lake/devtools/bootstrap.py` with `bootstrap_disposable_lake()`, `BootstrapRefused`/`BootstrapError`, `unavailable_datasets()`, and `python -m equity_lake.devtools.bootstrap --end-date … [--keep] [--seed] [--years] [--tickers]`; three documented fresh-clone profiles in docs/getting-started/quickstart.md (sample -> data/sample, canonical synthetic CI -> disposable seeded data/lake, real-root restore/re-land operator-run and explicit); CLI reference updated.
Dependencies: ADR-0020/0027 generator envelope, ADR-0030 real root explicit-only. No new ADR.
Commit identity: 79c26f5 -> a67e777, 7f7e5dc, af97c87, 37e1628 (merged).
Evidence: /tmp/integrate-e01.log — test_disposable_bootstrap and test_seed_demo pass in the main checkout (70 passed in the lane); `equity demo seed --help` shows `--end-date` and the bootstrap entry point exposes `--keep/--seed/--years/--tickers`; ruff/format/mypy 0; `make lint-md` 0 with 0 new findings.
Disposable guards, each proven: (a) default `mkdtemp` under the OS temp root, disposed unless `--keep`; (b) `git archive HEAD` members proven equal to `git ls-tree`, with links, `.env` and untracked `data/*` refused; (c) destination and the child's `PROJECT_ROOT`/`LAKE_DIR` both resolve beneath the allocation; (d) the child reports its imported module and a foreign (operator) provenance is refused; (e) symlinked destination aliases refused; (f) a reused/non-empty target refuses; (g) an operator lake — or containment either way — is refused regardless of `overwrite_production_lake`, with the sentinel hashed before/after (unchanged).
Reproducibility: same seed + `end_date` under simulated wall-clock days 2026-12-01 and 2027-06-01 yields equal `price_batch_digest`, equal sorted frames and one identical `price_source_sha256`; a control without `end_date` shows the days shift and the digests differ.
Canary / unavailable: a path canary and a value canary make `build_sanitized_page` raise `H00BuildError("sanitization scan failed closed (classes: paths, secret_shapes)")` with nothing written, while a clean synthetic lake builds exactly the three allowlisted files; a temporary `.env` canary is caught by the `secrets` class; optional provider and gold/platinum routes report `available: false` with reasons and create nothing.
Mutation: removing the destination guard made the operator-lake sentinel test fail (exit 1, `BootstrapError` instead of the preflight `BootstrapRefused`); restored from /tmp with `diff -q` exit 0 and the test green.
Inputs used: tracked source at HEAD only, plus the synthetic generator; no network, no provider credentials, no real data.
External prerequisite state: none for the offline bootstrap; real-root use still needs the explicit operator path and must never be implicit.
Rollback: revert the four commits; no lake, catalog or ADR touched.
Residuals: CI workflow wiring is RS-H01's; `equity demo seed --end-date` unified help coverage is confirmed by the auto-discovering help scan.

## RS-R01

Stage RS-R01 / status: verified-local (frozen measurement set, this host) / recorded 2026-11-23
Deliverable (§1.4, §2.12, §5): `src/equity_lake/ml/r01_controls.py` (prespec + freeze, six leakage controls, planted/null runner, exact Clopper-Pearson bounds, censoring/status, mutation runner, CLI `python -m equity_lake.ml.r01_controls --mode freeze|controls|scale|mutations|all`) and `src/equity_lake/ml/r01_scale.py` (5/50/200 harness with ceilings, RSS, I/O, parity, refusals); durable preregistration `docs/research/20261123-01-r01-preregistration.{md,json}` and manifest `docs/research/protocols/20261123T135029Z-42d13c802634.manifest.json`; results `docs/research/20261123-01-r01-results.json` and report `docs/research/20261123-01-r01-report.md`; 31 named regressions in `tests/unit/test_r01_controls.py` and `tests/unit/test_r01_scale.py`.
Dependencies: RS-Q00, RS-E01. ADR-0034 unchanged; no promotion.
Commit identity: 6543f7f -> 0a3802e, 73ce538, f74f9ce (freeze, before any run), b506d83, 353647d (merged).
Frozen before the run: protocol digest `42d13c802634bb4f4bc127db5991d2fe85ad5f7ee8ebf64461b49c4817e2a481`, prespecification digest `fb20a96cfdeff0090303a69995d4d968b4fae64a3a1002cecdbed540b99d33e4`, recorded_at 2026-11-23T13:50:29Z. Seeds 1729–1828 (100 null) and 11729–11778 (50 planted), one-sided alpha 0.05, FPR upper bound <=10%, power lower bound >=80%, statistic `mean_per_session_net_return` with the estimand named and explicitly NOT the compounded return, moving-block bootstrap (block 5, 2000 resamples, seed = the control seed), sizes 5/50/200, ceilings 1800 s per size, 7200 s aggregate, RSS <= min(8 GiB, 50% host RAM).
Evidence (reproduced by the orchestrator in the main checkout): `--mode controls` -> null 0/100 false positives, one-sided upper bound 0.029513 <= 0.10; planted 46/50 detected, lower bound 0.826209 >= 0.80; six leakage controls passed; sets complete, not censored. `--mode all` -> `verification_status: verified` with no reasons, exit 0. Scale: 5 = 0.025 s / 342 MB / 3785 rows / 914 decisions / min universe 5; 50 = 0.074 s / 390 MB / 37832 / 15504 / 49; 200 = 0.243 s / 519 MB / 151263 / 45562 / 198; aggregate 8.72 s of 7200 s; I/O measured (`read_bytes`/`write_bytes` genuinely 0, `rchar` 241040) with a tested path for unavailable counters; parity digests equal and vectorised economics == the R02 reference path (diff 0.0). All eight mutants killed (exit 1) and restored.
Inputs used: synthetic generator only (`devtools/seeding.synthetic_ohlcv`, `devtools/test_data`); no network, no real data, no R02 artifacts modified.
External prerequisite state: runtime capacity for the frozen defaults was available on this host; the measurement is synthetic-fixture evidence, not a market claim, and the report says so.
Rollback: revert the stage commits; no data, catalog, ADR or R02 artifact touched.
Residuals (named in the report): synthetic fixture only; model-based decisions are not required to agree across sizes; planted strength was set by a pre-freeze design calculation on non-predeclared seeds 1001–1005; a 0.4%/session inspection of planted seeds 11729–11730 during harness development is disclosed in the preregistration and those seeds stayed in the frozen set unchanged; power clears its floor by only 2.6 pp.
Note: the lane found and fixed a real fixture defect — `TEST_DATA_TICKERS["cn_ashare"]` listed `601988` twice, which corrupted 200-instrument economics until the pool was de-duplicated.

## RS-R01 status correction after independent review (2026-11-23)

The independent review of the accumulated diff returned FAIL with four blocking findings against merged stages (two against RS-D01's and RS-S01's work, one against RS-Q00's gate, one against the module's Pydantic boundary). All four were fixed in the main checkout, plus a real PIT gap the review's mutation request exposed:
1. `core/source_clock.py` — the integrity claim is narrowed everywhere (module docstring and `docs/developer/architecture/pipeline-contracts.md`): the digest is a corruption check, NOT a signature, and the store is not tamper-proof against a writer who recomputes it. No signature/key path was added (that needs its own decision record).
2. `devtools/markdown_lint.py` — the MD013 exclusion is now RECORDED and auditable: the baseline artifact is a frozen Pydantic model with `disabled_rules` entries (rule, justification, measured legacy cost) and fails closed when the recorded set does not match the gate's. Measured cost of closing it: 5250 MD013 findings. Counts unchanged at 314 findings / 35 files.
3. `signals/tracking.py` — the paper-shadow scan moved INSIDE the non-blocking delivery lock (readiness -> scan -> deliver), so two local runs cannot overlap in the scan phase; new regression `test_the_scan_cannot_overlap_another_shadow_run` proves a competing run never reaches the scan, and the readiness-recheck test now asserts the plan's order (scan skipped on refusal).
4. `devtools/markdown_lint.py` — baseline write/load go through `LintBaseline` (extra="forbid", positive counts, total must equal the recorded findings); two new tests cover a mismatched disable set and a missing disable set.
5. New PIT invariant in `core/source_clock.py`: evidence the source had not published by the `as_of` date can no longer qualify that read (look-ahead refusal). Mutation proof: removing the check made `test_clock_evidence_published_after_the_as_of_date_never_qualifies_it` fail (exit 1); restored with `diff -q` exit 0 and identical sha256, test green.
Also fixed during this pass: `src/equity_lake/py.typed` added (the package is mypy-strict clean over 200 files, but every isolated per-file analysis reported first-party imports as untyped); repo-convention `# pyright: ignore[reportMissingImports]` added to the two optional `wandb` imports in `ml/registry.py`. Pre-existing stale `# type: ignore[import-untyped]` comments remain in ~140 test files (611 occurrences) and are recorded as debt rather than mass-edited here.
Gates after the fixes: `uv run ruff check .` 0, `uv run ruff format --check .` 0, `uv run mypy` 0 (200 files), `make lint-md` 0 (314/35), and the touched test modules green.
