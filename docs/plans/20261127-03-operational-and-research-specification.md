# Equity Lake follow-up: operational and research specification

**Status:** Draft for implementation-plan review, 2026-11-27. The owner has put live
notifications on the backlog and requested a detailed plan for the remaining items. This
record does not accept pending ADRs, authorize code changes, approve spend, or authorize a
production operation. See the [owner decision sheet](20261127-02-owner-decisions.md).

## 1. Scope and sequencing

| Stage | Work | Entry condition | Exit evidence |
| --- | --- | --- | --- |
| H1 | Browser smoke test of H00 staleness | Scoped read-only public test | Replayable clock/visibility observations |
| H2 | Fix retained bundles created by rollback | Implementation approval under ADR-0035 | Build and two-hop rollback fixtures pass |
| H3 | Report-only H00 bundle inventory | H2 verified; ADR-0038 or narrowed inventory design approved | Read-only report; no deletion candidates when protection is uncertain |
| L1 | Isolated real-ledger onboarding and recovery rehearsal | Private input packet and copy-only authorization | Reconciliation and recovery receipt; live hash unchanged |
| L2 | Encrypted off-host backup drill | ADR-0040 accepted; named destination, keys and recovery targets | Isolated synthetic recovery proof; separate real-export gate |
| L3 | Evaluate ledger-event prune, backup-file pruning and compaction | Concrete need and corresponding policy/run gates | Report-only results or documented deferral |
| M1 | Qualify one non-US venue and source | Complete source packet for one venue | Decision packet, then one scoped amendment and landing proposal |
| R1 | SEC text feasibility | Synthetic-only audit first; separate approval for real source access | Availability, coverage and leakage report; fit/no-fit recommendation |
| R2 | Strict R02-D2 experiment | Qualified strict price/action evidence and untouched scope | Frozen replayable evaluation, always `promoted=false` |
| R3 | Vendor total-return decision | Named gap, written rights and budget if reopened | No-buy decision, or comparison report and separate adapter proposal |

H1, H2 and the synthetic part of R1 can proceed independently after their respective approval.
Start H3 after H2 proves the retention contract. Collect private L1/L2, M1 and R2 input packets
in parallel; an offline fixture or input checklist is not proof that a packet qualified. L3
is not permission to prune; R3 is not a procurement task. R1 is not an extension of R2. Keep
ADR-0039 in the backlog: no scheduler, endpoint setup, live sends, or delivery implementation
in this plan. The existing paper-shadow behavior stays as it is.

## 2. H00 publication and retention

### H1 — Clock-controlled browser check

- Capture a single live `index.html` response and its build instant, expected fresh-at-build
  state and 30-day threshold. Use a fresh browser context on the same bytes for each check;
  set the clock before the inline script runs. Record URL, response status, artifact digest,
  UTC test instants, browser version and observed banner date/visibility. No deploy or account
  login. If the live artifact is already marked stale at build, use a known fresh synthetic
  copy for the threshold test and report the two results separately.
- For an artifact fresh at build, at `built_at + 30 days − 1 ms` and exactly 30 days the banner
  stays hidden; at `+1 ms` it appears with the artifact's UTC build date. Align the boundary to
  the browser's parsed millisecond clock. Test invalid embedded time in a local fixture; the
  banner should show. Check page errors. Separately advance an already-open tab: the script
  runs at load and has no timer, so an open tab flipping automatically is not part of the
  current behavior. If desired, propose that change separately.

### H2 — Preserve the receipt across successive rollbacks

The retention target is exactly the `h00-release-bundle` Actions artifact from this repository's
sole H00 publisher. It contains the sanitized `index.html`, `payload.json`, and
`sanitization-report.json` plus a deployment `receipt.json`. The payload summarizes the
synthetic demo lake; fixture predictions, when present, contribute counts and schema size only,
not values. This is **not** Gold/Platinum retention, ML model or prediction deletion, lake
maintenance, ledger maintenance, or GitHub Releases cleanup. The `github-pages` artifact and
live site are not pruning targets. The 90-day platform expiry is unchanged.

**Observed from workflow code:** the rollback path in `.github/workflows/h00-publish.yml`
moves the receipt out of `release/` before verifying the three public files; it then uploads
that same `release/` directory as the next retained bundle without staging the receipt back.
The earlier rehearsal proved redeployment from a build's bundle, not a second rollback sourced
from the first rollback's new bundle. Fix the workflow before depending on that new copy.

- Keep the receipt outside the three-file directory during verification and Pages upload.
  Restore the *original verified receipt bytes* to the retained staging directory only;
  do not regenerate it against the rollback runner's current commit. Verify the final retained
  layout is exactly four regular, permitted entries and verify the public layout still has
  exactly three. Use the existing release verifier in
  `src/equity_lake/devtools/verify_h00_release.py`; do not relax its public allowlist. Keep
  the publisher manual, SHA-pinned, reviewer-gated, and at existing permissions.
- Tests: normal build prepares a four-entry retained artifact; rollback verifies its source,
  then emits a valid retained bundle and a three-entry Pages artifact; a second rollback
  consumes the first rollback's output offline. Missing or changed receipt, mismatched size or
  digest, extra entry or symlink must fail closed before upload. Cover the actual staging command
  path, not only a YAML string. Preserve or compare the original receipt bytes and public hashes
  across the hops. A hosted rehearsal, if separately approved, uses the currently deployed bytes
  and exercises both hops with no deletion; it is not necessary to test real data.

### H3 — Inventory and protection, not a deletion job

- Start with an operator-run report and read-only permissions. Paginate only this repository's
  `h00-publish.yml` runs and exact-name retained artifacts. Join run, deployment result and
  timestamps to artifact ID and expiry. In temporary storage bound and validate archive entries
  before extraction; split the receipt away, use the existing public verifier plus receipt
  validation, and group by the canonical tuple of the three verified public digests. Establish
  origin provenance for a normal build; for a rollback follow its verified source lineage, not the
  rollback workflow's current SHA. An unprovable lineage is unknown, never a pruning candidate.
- Establish active from successful Pages deployment evidence and matching verified byte identity,
  not the newest upload; a rollback may have made older content active. Rank distinct releases by
  most recent successful deployment, with stable tie-breaks. Return active, protected, unclassified
  and candidate IDs, expiry, source/verification status and reasons. Protect the active bytes,
  the three most recent distinct verified deployed releases (active counts if in that set), and
  owner pins. Protect at least one verified non-expired copy of every protected release; do not
  count a copy that will expire before the proposed maintenance window ends.
- No candidates if fewer than three distinct verified deployed releases remain eligible, the active
  mapping is unknown, inventory is incomplete, or a publish/rollback is queued, waiting or running.
  The old rollback copy may be unverified; retain it as unclassified, not as a protection proof.
  Test duplicate content, failed deploy, pending approval, expired and nearly expired files,
  wrong workflow/name, and missing or invalid receipt. Inspect the report without deleting anything.
- **Future separate decision under ADR-0038:** if there is retention pressure, approve exact
  artifact IDs, confirm protected copies and check a no-deploy window immediately before manual
  action. No deletion tool, write permission, automatic purge or post-deploy cleanup in H3.

## 3. Private ledger and recovery

### L1 — Rehearse onboarding and existing recovery mechanisms

- Require account alias, exact canonical CSV and statement periods, marks/as-of dates, original
  ledger path or confirmed absence, allowed workspace, and agreed reconciliation tolerances and
  exception disposition. Use a private isolated directory and copy or fresh v2 database. Verify
  path containment/permissions, prove the workspace cannot alias the live ledger, and record the
  original digest or absence. Do not put raw inputs, balances or file paths in public logs.
- Using the existing [portfolio commands](../user-guide/20261103-private-portfolio-ledger.md),
  validate/report-only first; compare workspace state before and after. Apply only inside the
  approved workspace. Prove repeated import adds zero events, reconcile against statements with
  dated marks, and refuse unexplained mismatches. If a supported legacy migration is needed,
  rehearse it on a separate copy, not the original. Export a verified exact-file backup in a
  no-writer window; restore to another isolated target and verify schema, integrity, row count,
  record digest and replay. Compare file hashes for exact-copy restore, not for `VACUUM INTO`.
  Rehash the original ledger and repeat the absence check if there was none.
- Acceptance: reconciliation matches agreed tolerances or records a named failure; recovery
  candidate passes existing checks; the live ledger and lake are unchanged. A write to the
  live ledger requires a distinct scope and owner authorization.

### L2 — Test off-host recovery before handling real data

- Gate on [ADR-0040](../decisions/0040-encrypted-off-host-ledger-backup.md) and approved
  destination, access and jurisdiction, key custody and recovery, retention, cost cap, recovery
  point objective and recovery time objective. Choose either off-site removable media or private
  object storage, not both for the first pilot. Choose an established authenticated encryption
  tool; keep keys separate from the ciphertext and keep plaintext out of the destination and CI.
- Start with a known synthetic ledger. Export/verify a local backup, encrypt before transfer,
  record protected integrity evidence, then retrieve/decrypt on an isolated workspace. Verify
  the restored hash, schema, row count and record digest; time the restore and record backup age.
  Exercise separately held recovery credentials; corrupt ciphertext and missing/wrong key must
  refuse. Report failed exports as unverified, keep the local copy, and never overwrite the live
  target. A verified local copy is not evidence that off-host recovery works.
- Acceptance: recovery proof, agreed targets met or explicitly failed, and no remote plaintext.
  Before any real export, obtain separate approval of the data scope and re-run the same gates.
  Ongoing scheduling or remote retention/deletion is not part of the pilot.

### L3 — Keep three maintenance decisions separate

| Decision | Report-only or isolated work | Gate before any live change |
| --- | --- | --- |
| Event prune | Under ADR-0036, name the candidate UTC cut and measure replay/storage need. Preview against an approved copy; verify backup, checkpoint equality, retained history, no-op repeat and disclosure of lost pre-cut breakdown. Do not bypass `SPLIT` or sequence-room refusals. | Approve ledger, account, cut, exclusive/no-writer window and verified recovery copy; acknowledge loss of pre-cut reconciliation from the current book. |
| Backup-file prune | Use the existing read-only inventory. Show protected version, migration, manifest and owner-pinned backups; compare potential space savings. No cleanup command in this stage. | Accept ADR-0041 and separately approve exact contained paths and digests. Recheck verified surviving recovery copies; refuse unknown or changed entries and aliases. |
| Compaction | Measure size, reclaimable pages and replay time. If warranted, trial existing `VACUUM INTO` on a copy; verify schema version, integrity, row count and semantic digest. This is SQLite, not Delta VACUUM. | A real copy-based run needs its own approval. Keep in-place off the implementation queue under ADR-0042; reopen only if a measured need survives the copy-based trial. |

## 4. Data admission and research

### M1 — Qualify one non-US venue and propose a scoped landing

- Reuse RS-M01–M04 fixture packs. Compare candidate packets by use case, rights/retention,
  source tier and cost, exact MIC and symbols, timeframe/calendar, currency and volume unit,
  missing sessions, raw versus adjusted OHLC, split/dividend evidence, source timing/vintage and
  expiry. HK and SG need distinct venue evidence even under `hk_sg_equity`. No provider calls
  or costs during packet design. No clockless claim upgraded to strict coverage.
- If one packet qualifies, draft and approve the concrete ADR-0030 decision 5 amendment
  naming that market, MIC, source, date range, tables and approved basis. Scope any authorized
  probe to the named request envelope; writes need separate authorization. Apply the source
  change matrix only if the approved path changes routing, config, schema or source contracts;
  real tables remain uncataloged. Prove validation, identity, action/volume semantics, exact venue
  calendars, health scope, alias/containment and idempotent repeat on fixtures before landing.
- Acceptance for a later landing: scope and coverage report, gaps/refusals, expected Delta
  changes under explicit `--lake-dir` only, no change to demo root/catalog, and no redistribution.
  Stop if no packet qualifies; do not expand by enabling all four guards.

### R1 — SEC text feasibility, then a separate ablation proposal

- Start with a synthetic availability audit against the
  [research proposal](../research/20261127-01-unstructured-predictor-feasibility.md): one
  10-Q risk-factor section, accession identity, issuer-to-instrument mapping and explicit
  cutoff, with as-of invariance under amendments and future docs. Pin before-close, after-close,
  early close, holiday, duplicate, equal-clock conflict, missing section/clock and delayed
  extraction cases. Date-only/midnight fallbacks are not verified first-public clocks. No source
  fetch, LLM processing or fit in this audit.
- For an authorized data review, require a rights-permitted immutable excerpt or snapshot,
  source-issued publication/dissemination evidence and compatible strict price/action clocks.
  Record coverage, effective sample and issuer/session concentration. Freeze minimum coverage,
  power/effect and budget criteria before looking at returns. Return an explicit stop if clocks,
  rights, independent observations or price basis fail.
- If feasible, submit a frozen run proposal: technical baseline, plus filing metadata, plus text
  novelty/uncertainty. Compare text vs metadata on matched instrument-sessions, with fold-local
  transforms, purge/embargo, paired Brier improvement and blocked uncertainty, and common cost
  assumptions. Keep future vintages and any holdout out of fitting. No external text transfer,
  default model change, lake publish or promotion. News, transcripts and embeddings stay later
  alternatives requiring their own evidence, not automatic follow-ups.

### R2 — Prepare evidence for R02-D2 without relabeling or refitting

- Preserve the [existing D2 preregistration](../research/20261124-01-r02-d2-preregistration-plan.md).
  Check G1 against RS-D01 qualification (source-issued clock/vintage, coverage, expiry, rights
  and record ID), and G2 for an untouched universe and disjoint windows with matched sessions.
  Weak vendor stamps, fetch time and a synthetic PASS are not substitutes. Do not borrow the
  prior experiment's declared holdout or touch the new holdout during evaluation.
- Before any fit, freeze snapshots and digests, features, label availability, purging/embargo,
  baselines, costs and matched evaluation dates. Execute only after run approval. Review evidence
  and recompute from retained outputs; return negative/inconclusive if indicated. Always retain
  `promoted=false`, with no live model, inference, signals or production dataset changes.

### R3 — Keep paid total return opt-in and separate

Preserve the research-only local derivation in ADR-0037 and the uniform reader/backtest refusal.
Document the unmet use, if any, before contacting a provider. If reopened, require approved
license and cost cap, vintage/clock and coverage evidence, and explicit distribution, currency
and revision semantics. Compare matched-period returns using a licensed offline sample and split
and dividend oracles. No integration, `auto_adjust` switch, double counting or ledger-mark use.
A procurement trial and an adapter are separate decisions under ADR-0044.

## 5. Review, evidence and approval packet

For each stage record owner, exact scope and exclusions, approved inputs (or missing inputs),
start/stop conditions, before/after evidence, verification result and next approval. Use
`blocked-input`, `planned`, `verified-offline`, or `operated` for the named scope; a fixture passing
never makes a real run operated. Keep sensitive logs and receipts private. Operations that could
change, delete or transfer real data need explicit approval over the specific target, a rollback or
refusal plan, and tested protections. Pending ADRs must be accepted before their boundary changes.

For later implementation, show the relevant change-matrix entries, targeted tests for every
refusal, and independent review of workflow, persistence and source-boundary changes. Follow
`AGENTS.md` validation with `uv`: serial and parallel suites as applicable, Ruff, format, mypy and
any relevant doc/catalog checks. Preserve the complete suite logs. For this documentation draft,
run the Markdown ratchet, build MkDocs and verify links; no pipeline or network test is needed.

**Suggested first implementation approval:** H1 browser check, H2 receipt-preservation repair and
fixtures, and R1 synthetic availability audit. In parallel, provide the private L1/L2 packets or
select one source packet to qualify. No deletion, private-data operation, provider spend or model
fit is part of that first approval.

## 6. Execution log — first batch (2026-11-27)

**H1 — browser staleness check: passed.** Live artifact `https://minghao51.github.io/equity_lake/`,
HTTP 200, 5398 bytes, sha256 `0a07f7a8…ba0d`; `built_at=2026-11-27T14:00:45.206088+00:00`,
threshold 30 days, boundary `2026-12-27T14:00:45.206Z`. Loaded those exact bytes from disk over
`file://` in system Chrome (Playwright 1.65.1), with the clock installed and **paused** before the
inline script ran (an unpaused fake clock advances and moves a 1 ms boundary). Results: 1 ms before
the boundary and exactly at it → banner hidden; 1 ms after → banner visible reading "stale — rebuild
due — last build 2026-11-27 (threshold 30 days)"; real time → hidden; an unparseable
`data-built-at` → visible (fail-open). No console or page errors in any case. Diagnostic: an
already-open tab stays hidden after 40 simulated days, confirming the check runs at load with no
timer — if automatic flipping is wanted, that is a separate behaviour decision, not a defect. This
is one observed run, not a repeatable test; turning it into a Playwright test is a follow-up.

**H2 — rollback receipt preservation: fixed and rehearsed.** The defect was as specified (§2): the
rollback path verified with the receipt moved out and then let the shared upload step re-retain a
three-entry bundle. A rollback-only step now restores the receipt verbatim and fail-closed guards
the four-entry layout (exact four names, all regular files) before the Pages staging and the retained
upload. Tests pin both hops through the real verifier CLI, the Pages directory staying three-entry,
and the step order. Independent reviewer verdict: ship, no blocking findings, no nits. Rehearsed
again here against a **real retained bundle** (run 46011953819, receipt sha256 `c75e6528…0407`,
bound to commit `e8ee001…`): both hops verified `3 allowlisted files, 8567 bytes`, each left
`release/` holding exactly the four names and `dist/` exactly the three public ones, and the receipt
bytes were identical across both hops and to the original. Because rollback verification binds the
receipt's own file digests and not the runner's commit, a bundle whose recorded commit is no longer
reachable still redeploys its original bytes — that provenance is preserved rather than rewritten.

**R1 — text availability audit: tool landed, no source touched.**
`src/equity_lake/devtools/audit_text_availability.py` (run with `python -m`, read-only, offline)
audits one event table against a decision-session grid and reports only counts, dates,
identifiers and reasons. It refuses a missing, naive, date-only or unparseable clock, an
`evidence_kind` outside `SOURCE_ISSUED_CLOCK_KINDS`, a missing section or document identity, an
unestablished instrument or observable session, an equal-clock group whose copies disagree, and a
group where any copy lacks a `content_digest` (deduplication is evidence-backed, never assumed).
Observable sessions come from `core/availability.source_observable_session`, so the venue,
close and holiday logic is the shared one rather than a copy. Midnight-shaped clocks are warned,
never forgiven: admission still needs an aware clock plus source-issued evidence, so a midnight
timestamp alone establishes nothing. The remaining invariant measures three falsifiable
properties (observable session is a trading session for that issuer, never precedes the clock's
UTC date, and never inverts under clock order) — it replaces an earlier "as-of sets only grow"
assertion that the snapshot construction made true by definition, and a test breaks the shared
seam to prove the check fails. `future_safety_check` is documented and printed as what it is: a
guard on the synthetic events' mapping, not proof that leakage is impossible. Exit codes are
`0` clean, `1` any named refusal, `2` nothing to audit. 34 tests cover the nine named cases
(before/after close, early close, holiday, weekend, duplicate, equal-clock conflict, missing
clock, missing section, delayed extraction) plus revisions, the invariants and value-freedom.
An independent review returned fix-then-ship; the three blocking findings — digest-blind
deduplication, the tautological check, and a devtools-guide sentence that overstated the midnight
handling — are fixed. The tool has **not** seen any real or realistic text, so it establishes no
coverage claim for any source; that needs an approved packet under Phase 0 step 2.
