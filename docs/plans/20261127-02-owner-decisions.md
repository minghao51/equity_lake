# Owner decisions for the remaining operating work

**Status:** Proposed for owner review, 2026-11-27. No proposal here is accepted or authorizes a
run, deployment change, deletion, spend, data access, transfer, ingestion, or model change.
Approve a policy and authorize a concrete operation separately. Supply sensitive inputs through
an approved private channel, not by committing them or pasting credentials into a decision record.
The owner has deferred live notifications. The
[detailed specification](20261127-03-operational-and-research-specification.md) covers the active
planning scope and separates offline work, data input gates and live changes.

## Baseline correction

The [residual plan](20261122-01-residual-spec-and-implementation-plan.md) §11 is a dated
verification ledger, not a current inventory of operations. H00 has since been published and its
rollback path exercised by redeploying the current bundle; see the [handoff](../handoffs/20261127-01-residual-implementation-handoff.md).
The [real-root handoff](../handoffs/20261120-01-complete-remaining-stages.md) records the
landed and widened US yfinance slice in the separate `data/lake_real/`. The demo root is
not evidence that no real data work has occurred. H00 is still manually dispatched, and no live
notification scheduler is authorized. Offline verification is not live evidence for the other items below.

## Policy decisions (proposed ADRs)

| Decision | Options | Recommendation | Input or approval needed |
| --- | --- | --- | --- |
| [ADR-0038: H00 bundle pruning](../decisions/0038-h00-rollback-bundle-retention.md) | Age only; inventory plus exact manual cleanup; automatic last-three cleanup | Keep 90-day expiry; fix receipts on rollback-generated bundles, then inventory read-only. Preserve active and the three most recent distinct verified deployed releases (active counts if eligible). No publisher delete permissions. | Confirm retention floor and any pins or quota need; approve exact IDs separately. |
| [ADR-0039: Live notifications](../decisions/0039-bounded-private-notification-delivery.md) | Paper shadow; one private local pilot; broad rollout | Backlog by owner decision; keep paper shadow unchanged and do not prepare an activation. | Only if reopened: endpoint/channel, host, kill-switch owner, scoped evidence and trial window. |
| [ADR-0040: Encrypted off-host backup](../decisions/0040-encrypted-off-host-ledger-backup.md) | Local only; manual encrypted export and isolated drill; scheduled remote backup | Start with a synthetic isolated drill after selecting a destination and testing key recovery; keep local copies. | Destination, access, key custodians, retained copies, recovery targets and approved spend. |
| [ADR-0041: Backup-file pruning](../decisions/0041-ledger-backup-file-pruning.md) | Inventory only; reviewed exact-file cleanup; timed purge | Keep inventory-only absent disk pressure or a retention requirement. Never delete a required recovery point. | Measured need and exact, reviewed deletion list if reopened. |
| [ADR-0042: In-place SQLite VACUUM](../decisions/0042-in-place-ledger-vacuum.md) | No change; existing copy-based compaction; new in-place path | Defer in-place; use verified `VACUUM INTO` only for a demonstrated need with separate run approval. | Space/replay measurements and a reason copy-based compaction is insufficient. |
| [ADR-0043: Real non-US slices](../decisions/0043-real-root-non-us-admission.md) | Offline only; qualify one market; enable all four | Keep current guard; qualify one venue/source packet and write a concrete ADR-0030 amendment before widening. | Source/license, venue, universe/window, costs, coverage and clock evidence. |
| [ADR-0044: Paid vendor total return](../decisions/0044-paid-vendor-total-return-series.md) | Local derivation; licensed offline comparison; buy/integrate | No purchase now. Reopen for a named gap; compare a licensed sample before committing to an adapter. | Written rights, timing/vintages, methodology, sample and budget. |

## Scoped decisions, not new architecture

### Browser smoke check of H00 staleness

**Options:** Rely on structural tests; run a read-only real-browser check at a controlled clock;
wait 30 days in real time. **Recommendation:** run the browser check. Load the same live artifact
with the browser clock set before, exactly at, and just after `built_at + 30 days`; expect the
stale banner only after the boundary and verify the displayed build date. Record URL, build instant,
browser and outcomes. Test an already-open tab separately as a diagnostic: the current inline
script checks on page execution, with no timer. If automatic transition without reload is desired,
record it as a separate behavior decision, not a passed smoke check. No republish is needed.

### Rehearse onboarding with a real ledger (RS-P01)

**Options:** Stay synthetic; import authorized canonical CSV into an isolated ledger and reconcile;
import directly to the live book. **Recommendation:** isolated rehearsal first, with private CSV,
matching statements and dated marks supplied by the owner. Approve account alias, paths, period,
statement tolerances and handling of exceptions before work. Dry-run first; import only into the
isolated copy, prove repeat import is a no-op, reconcile cash/positions/NAV, and rehearse restore
and compaction there. Verify the original ledger hash (or its absence) is unchanged. Follow the
[ledger guide](../user-guide/20261103-private-portfolio-ledger.md); a live import is a different authorization.

### Prune events on a real ledger under ADR-0036

**Options:** Retain full history; rehearse a candidate cut on an authorized isolated copy; apply
one approved cut to the live book. **Recommendation:** retain full history until a concrete need
exists, then start with report-only and an isolated rehearsal. A separate approval must name the
ledger, account, exact UTC cut, maintenance window and verified recovery copy, and acknowledge loss
of pre-cut event breakdown and reconciliation from the live ledger. Refuse if replay equality,
backup or any other [ADR-0036](../decisions/0036-ledger-event-retention-and-pruning.md) gate fails;
never sidestep a `SPLIT` or sequence-room refusal. No scheduled prune; this is not backup housekeeping.

### Run the second R02 experiment (RS-R02-D2)

**Options:** Hold the current negative/inconclusive result; run the existing strict preregistration
once qualified; change the question in a new preregistration. **Recommendation:** hold until both
[preregistration gates](../research/20261124-01-r02-d2-preregistration-plan.md) pass. Require an
RS-D01 packet with source-issued publication/vintage clocks, expiry and rights over the whole new
scope, plus a disjoint untouched universe and train/validation/test/holdout windows. Freeze before
fitting; keep the declared holdout unread and `promoted=false` even if results are positive. Do
not tune on the first experiment's scope or add text to D2's frozen feature set.

### Evaluate unstructured predictors

**Options:** Stay price-only; test one qualified, auditable SEC feature family; immediately add
news, transcripts, embeddings or LLM scores. **Recommendation:** start with the offline SEC
feasibility gate in the [research proposal](../research/20261127-01-unstructured-predictor-feasibility.md).
It is a separate experiment, not a change to D2. Ask first for a candidate scope, permitted source
snapshot and an explicit budget; report coverage and leakage checks before authorizing any fit.

## Suggested owner sequence

1. Review the read-only browser check, H00 rollback receipt repair and synthetic text-data audit.
   None requires a write to a real ledger; the receipt repair can be verified offline first.
2. If host-loss recovery matters, nominate the backup destination and key custodians. If ledger
   onboarding matters, provide the private rehearsal packet without authorizing live writes.
3. Collect source evidence for one named goal before procurement or fitting. Leave automation,
   deletions, in-place compaction and production activation off until separately justified.
