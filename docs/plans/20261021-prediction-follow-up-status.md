# Prediction Follow-up Status — 2026-10-21

**Status:** I02 and I03 implementation/offline-contract review acceptance is unchanged. I01's authorized exact-file backup and isolated-copy verification completed, and the owner-authorized two-row targeted Delta delete completed in one guarded operation. I01 targeted cleanup is complete; no additional data mutations are planned. Row origin remains unknown, historical Delta files/versions are preserved, and no broader cleanup, vacuum, rollback, or production restore is planned or implied.

## Latest I01 closure — 2026-10-22

- Backup and isolated-copy verification completed with process exit 0; all 16 v14 dependency files (28,026 bytes) and the pinned schema, table ID, version, protocol, and two full-row fingerprints were verified across source, backup, and clone; the source-file SHA matched the preflight. Independent post-result review accepted. The internal report capture is `2026-10-22T14:11:25Z`; no outer UTC start/end was provided, so duration is unknown. Repository-local, same-filesystem snapshot: `data/backups/predictions-v14` (manifest SHA-256 `36a4c402f82a5474c87566584e6208cd79a12f65a3389bc671433883345f2f3e`), providing operation-mistake protection, not disk-failure or full-history recovery. The completion receipt and verification report are retained in the independently Luna-accepted, ignored archive `data/backups/predictions-incident-evidence-20261022/`: 17 byte-verified proof files (53,572 bytes), plus `INDEX.txt` and `SHA256SUMS` (19 files total); all 18 manifest entries passed checksum verification. Their SHA-256 values remain `82c34270ee751a95d76d2aad69139ce64355cd57900a6b1049329cfcf2c03fde` and `abed7a1e817d7a18743124b67ec7d76b93f1970aa07f96f58d54839fe4cbd03e`. The original host-local `/tmp` JSON evidence remains the historical source and was not rewritten. The manifest remains labeled `content_verified_pending_completion`; the separate process-0 receipt and verification report record completion.
- The owner explicitly authorized deleting the two identified rows. One guarded native `Delta.delete` ran and exited 0 at `2026-10-22T16:38:23Z` (recorded start and end are the same second; no duration is claimed). The exact seven-column predicate matched AAPL/MSFT, `2024-01-02`, `up`, `0.8`, `unknown`, `fake`, and `feature_schema_version IS NULL`. Saved result: `/tmp/equity-i01-execution-ZxpZl0/stdout.txt`, SHA-256 `c531b2f6ba015b3224bf798a11f2e821f17f68625643a21568a0ebb579adfee6`; it records `success=true`, `mutation_committed=true`, 2 deleted rows, table version 14→15, and 0 active files after deletion. Table ID and schema SHA-256 remained the pinned `3e7fcae5-61e8-4a03-af3f-363cac0394b0` and `9fe9f7baf4a5c8da59235f5e822bc686c722ded7c1c768941bf553247cc41893`.
- Native fixture checks passed (10), including stale-unrelated and same-partition cases, with `max_commit_retries=0`; independent pre- and post-result reviews accepted. The executed script `/tmp/equity-i01-delete-two-Luna-22/delete_two.py` (SHA-256 `c15fb5ec9c5ae8dba9cac73299feb0551b9c31b4b54325f7dad8a5fe49bceedd`) set `CommitProperties(max_commit_retries=0)` for the single `DeltaTable.delete` call; this is code-verified, not a field exposed by the result JSON. The saved result does not contain protocol, native metric-blob, or audit-detail evidence: do not claim those were independently observed. Code review checked protocol/schema/table ID after the transaction and the Pydantic commit metadata; post-result external proof was scoped to 2 rows, 0 active files, and version 15. No vacuum or physical Parquet deletion was performed; prior Delta files/versions remain. The row origin and actor remain unknown.

**Housekeeping:** The verified recovery backup remains the read-only v14 snapshot at `data/backups/predictions-v14`; it protects against operation mistakes, not disaster recovery or full-history loss. The durable evidence archive above contains byte-verified copies; the original `/tmp` artifacts, including deletion stdout (`c531b2f6ba015b3224bf798a11f2e821f17f68625643a21568a0ebb579adfee6`) and script (`c15fb5ec9c5ae8dba9cac73299feb0551b9c31b4b54325f7dad8a5fe49bceedd`), remain unchanged historical sources. The only removed item was obsolete synthetic test root `/tmp/equity-i01-exact-backup-restore-prep-20261022-Luna-01-corrected-test`; this owner-approved minimal cleanup preceded independent allowlist review. All other `/tmp` material, unreviewed historical-failure evidence, and private-authority material were retained; review was bounded to a 100-item inventory, not a global cleanup. The already-recorded delete at `2026-10-22T16:38:23Z` (v14→v15) was not rerun.

## Scope and current state

This update follows the [2026-10-20 handoff](20261020-prediction-incident-follow-up-handoff.md) and the [2026-10-18 incident report](../finding/20261018-prediction-test-write-investigation.md). The handoff's I02/I03 `Pending` statuses and its I01 preparation status are historical snapshots, superseded only by the statuses recorded here. The Oct18 table facts, methods, limitations, and C00–C10 acceptance history remain as written; none is a claim about the table's present state. See also the [findings register](../finding/20261016-repository-review-and-equity-alerts.md).

| Follow-up | Current status | Scope and limits |
| --- | --- | --- |
| I02 — raw predictions API | **Accepted implementation/offline-contract review.** | Per-row status and bounded historical reads only; no production API request, consumer-use finding, or readiness claim. |
| I03 — Delta-maintenance containment | **Accepted implementation/offline-contract review.** ADR-0019 was accepted before implementation. | CLI-only lake-relative containment; no vacuum, compaction, or migration was run. Shared storage behavior is unchanged. |
| I01 — fixture-row follow-up | **Targeted cleanup complete.** | The authorized v14 backup/isolated-copy verification and one guarded two-row delete are complete. The delete advanced the table from v14 to v15 and left 0 active files; historical Delta files/versions remain. Origin remains unknown; no further mutation is planned. |

This is a status record, not additional authorization. The required runtime was Luna `xhigh`; the exposed environment values (`PI_PROVIDER=openai-codex`, `PI_MODEL=gpt-6-luna`, `PI_REASONING_LEVEL=xhigh`) are not independent provider-routing telemetry or proof of provider-internal routing/authentication. No fallback indicator was exposed; its absence is not provider-routing proof.

## I02 — bounded raw predictions API

I02 is accepted for its **implementation and offline contracts**, not for production operation. The API preserves historical rows while annotating each row with identity, provenance, schema, value-validity, prediction-semantics, and usability status. Without supported row-bound forecast provenance and verified directional semantics, returned rows are explicitly `not_current_usable`, including structurally canonical rows. No venue or MIC is inferred from a ticker.

The default window is 31 inclusive UTC calendar dates; an explicit date selects exactly that historical date. Market/MIC/symbol filters are validated and applied with date partition selection before Python row conversion, using one pinned Delta snapshot. Explicit selected-file-count, selected-file-byte, processed-row, and decoded-byte budgets fail closed; the `limit` bounds returned rows, not the scan coverage. These are source/test contracts, not proof of a production endpoint call or use.

A separate Luna `xhigh` review accepted the implementation after the stored-MIC normalization issue was corrected: noncanonical stored MIC values are marked invalid, not relabeled, and exact canonical venue filtering remains coherent. Offline evidence:

- Focused API/safety tests: **72 passed**; `/tmp/equity-i02-focused-20261020.{log,xml}`.
- Serial suite: **1,553 passed**; `/tmp/equity-i02-serial-20261020.{log,xml}`.
- Parallel suite: **1,553 passed**; `/tmp/equity-i02-parallel-20261020.{log,xml}`.
- Ruff, format, source mypy, and non-strict docs build passed in the preserved I02 evidence. Relevant logs include `/tmp/equity-i02-ruff-cache-20261020.log`, `/tmp/equity-i02-format-20261020.log`, `/tmp/equity-i02-mypy-src-20261020.log`, and `/tmp/equity-i02-docs-nonstrict-20261020.log`. The strict docs build did **not** pass: it stopped on five pre-existing documentation warnings (`/tmp/equity-i02-docs-20261020.log`).

These test/static results and the separate Luna review are historical acceptance evidence; they were not rerun for this status note and do not establish production readiness.

## I03 — CLI-only Delta-maintenance containment

[ADR-0019](../decisions/0019-cli-delta-maintenance-lake-containment.md) records the accepted policy before implementation. It preserves short/canonical market identifiers, mapped dataset IDs, named `features` and `predictions` routes, full lake-relative medallion paths, and safe unknown relative paths, including noncatalog tables. It rejects empty identifiers, absolute paths, `..` components, and symlinks resolving outside the configured lake; in-lake symlinks are normalized to their effective route. The complete input list is validated before any of the three maintenance calls. The shared storage join/root behavior is unchanged, and no auxiliary-artifact CLI route or catalog-only restriction was added.

A separate Luna review accepted the implementation/offline contracts. Temporary-filesystem tests cover these contracts and mock all maintenance calls. No actual maintenance operation was performed. Preserved offline evidence:

- Focused tests: **234 passed**; `/tmp/equity-i03-focused-final3.{log,xml}`.
- Serial suite: **1,578 passed**; `/tmp/equity-i03-serial-final3.{log,xml}`.
- Parallel suite: **1,578 passed**; `/tmp/equity-i03-parallel-final3.{log,xml}`.
- Ruff, formatting, source mypy, LSP, non-strict docs build, and scoped diff checks passed in the accepted offline work. Logs include `/tmp/equity-i03-ruff-final2.log`, `/tmp/equity-i03-format-final2.log`, `/tmp/equity-i03-mypy-final2.log`, `/tmp/equity-i03-docs-final2.log`, and `/tmp/equity-i03-diffcheck-final.log`. Strict docs still stopped on five pre-existing warnings at `/tmp/equity-i03-docs-strict-final2.log`; do not describe the docs build as warning-free.

These results are offline contract evidence only. ADR-0019 explicitly does not demonstrate an exploit, production use, or production maintenance.

## I01 — accepted current-only inspection, diagnosis, and limits

### Point-in-time inspection result

The separately authorized inspection covered only the current Delta snapshot at `/home/howt/work/equity_lake/data/lake/04_platinum/predictions`, date `2024-01-02`, tickers AAPL and MSFT. One guarded read ran from `2026-10-21T12:47:00Z` to `12:47:01Z`, with accepted clearance, exit status 0, a complete schema-3 report, `error: null`, process and gate status 0, and final stage `after_report_write`. The saved status also records `report_state=present_validated`. A status-record counter field is null/unknown by design; use only the complete typed report for counts.

The report records version 14 before and after, table ID `3e7fcae5-61e8-4a03-af3f-363cac0394b0` before and after, and schema SHA-256 `9fe9f7baf4a5c8da59235f5e822bc686c722ded7c1c768941bf553247cc41893`. The observed schema has these seven nullable fields in this order:

| Field | Arrow type | Nullable |
| --- | --- | --- |
| `ticker` | `string` | yes |
| `date` | `date32[day]` | yes |
| `direction` | `string` | yes |
| `probability` | `double` | yes |
| `model_mode` | `string` | yes |
| `model_version` | `string` | yes |
| `feature_schema_version` | `int64` | yes |

The complete matching rows at that snapshot were:

| ticker | date (`date32[day]`, epoch days) | direction | probability (`double`, exact hex) | model_mode | model_version | feature_schema_version (`int64`) | full-row fingerprint SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AAPL | 2024-01-02 (`19724`) | `up` | `0.8` (`0x1.999999999999ap-1`) | `unknown` | `fake` | `null` | `50492900a10c3a2883282ca906668df69e8e64a23e3a9cdf01e8541b7a7bb59c` |
| MSFT | 2024-01-02 (`19724`) | `up` | `0.8` (`0x1.999999999999ap-1`) | `unknown` | `fake` | `null` | `396b69e016dba40d3377a304955f407f3830fae8010fbee555b6b982043cd276` |

Fingerprints cover the full typed rows, including all seven schema-ordered fields, type tags, nulls, date epoch value, and exact float hex representation, using the report contract's compact, sorted-key UTF-8 JSON and SHA-256. A separate review recomputed the saved report's schema and row fingerprints and reports matching results; that is not an independent live reread or proof of provider-internal routing/authentication.

The scan selected one same-date Parquet file (1,933 bytes; selected-file metadata stable during this read), read one raw batch with two scanned rows and 108 decoded bytes, and returned two matches: AAPL 1, MSFT 1; duplicate requested-key rows 0, distinct legacy identity keys 2, duplicate identity rows/groups 0. The bounded native path applies date-partition selection, then Arrow `compute.is_in` membership before Python row materialization; all raw batches, rows, and decoded bytes are counted against caps before filtering. It does **not** claim physical ticker predicate pushdown. The same-date Parquet decode was the bounded scan scope. This is only the observed point-in-time result: no full-table row scan, history survey, global corpus comparison, or global production-byte hash was performed. Identity remains `legacy_ticker_only_identity_unknown` on `(ticker, date)`; do not infer market/MIC, assign an actor, or claim these exact values prove which test/process wrote them. Origin and any original live-row replacement remain unknown.

Saved accepted evidence:

- Report: `/tmp/equity-incident-i01-native-diagnosis-20261021-Luna-integrated-final/out/inspection.json` — SHA-256 `a721711b0d857b6af965373884e6524caca753697f3d1fdae483a8f1c0bcd588`.
- Execution log: same directory `out/inspection.log` — SHA-256 `9125a432693632dbf89c7c51b5e6ee98e58574b5f45e331ee6bb479ff8817c3e`.
- Status: same directory `out/inspection-status.json` — SHA-256 `0c4cdec1de64c5dc698d9594f669f27cadbc8a79fa8826648216d78d4b313957`.
- Execution bundle: `/tmp/equity-incident-i01-execution-20261021-luna-01a15e7b-a819-70d1-aae1-727c2c086f1a/`; `host-invocation.txt` records the actual outer argv and one-second interval, `exit_status=0`, and `child-artifact-hashes.sha256` records the child report/log/status/command hashes. The outer invocation was `/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/bash /tmp/equity-incident-i01-native-diagnosis-20261021-Luna-integrated-final/scripts/sandbox-wrapper.sh inspect --luna-clearance-file /tmp/equity-incident-i01-native-diagnosis-20261021-Luna-integrated-final/out/luna-clearance.json`.

### Earlier failed attempts and offline harness diagnosis

The earlier Oct21 attempts remain historical evidence, not the current result: preparation gates caught cwd/LF and clearance-gate issues before exposure; a subsequent read-only exposure at `/tmp/equity-incident-i01-exec-w3fdKXES/inspection.json` produced only an incomplete `unsupported_schema` preflight (null version, absent schema, cleared counters); then `/tmp/equity-i01-live-retry-20261021-Cw7ZaO/{stdout.log,stderr.log,exit.status}` recorded an accepted clearance and exit 139 with no complete report. Cleared counters were not zero-match evidence. The prior exit 139 is consistent with a native crash, but its signal source, failing stage/component, and root cause remain **unknown**. It was not independently established as the same cause as any earlier failure. The successful read is an operationally successful later attempt, not proof that the prior failure's cause was identified.

Offline work used synthetic fixtures and the `/tmp/equity-incident-i01-native-diagnosis-20261021-Luna-integrated-final/` harness only; it changed no repository inspection/runtime code. Synthetic history diagnostics reproduced a baseline native abort/near-3-GiB virtual-address-space pressure and an Arrow 21 ticker-predicate compatibility failure (`greater_equal(string_view, string_view)` unavailable). Setting `MALLOC_ARENA_MAX=1` reduced virtual allocation without raising the 3-GiB `RLIMIT_AS` cap. A date-partition-native scan plus capped native Arrow-batch `compute.is_in` before Python row conversion avoided the unsupported predicate path; every raw batch/row/decoded-byte count is budgeted before membership filtering, and untargeted keys are not Python-materialized. This does not claim physical Parquet ticker pushdown. Six tuned historical fixture reads (three v4 and three null-schema) passed the strict full-report gate. Harness evidence also records 24 report-tamper negatives, 16 clearance-tamper negatives, four prefilter-budget negatives, a native-failure simulation, and pinned log/argv/status checks. Native OS thread counts can exceed a worker setting of 1; `RLIMIT_AS` limits virtual address space, not RSS. These synthetic diagnostics do not establish the original live exit-139 cause.

### Successful read-only backup inventory — 2026-10-21

One separately authorized guarded metadata inventory ran from `2026-10-21T16:57:12Z` to `16:57:13Z` against the exact predictions table. The saved report `/tmp/equity-i01-backup-inventory-prep-20261021-Luna-01a15f06-2a68-722f-8a6e-d899b2f6fa36-corrected/inventory-evidence/backup-inventory.json` has SHA-256 `b5d20a8d8366a58ed07fde6357ae16b9be9405d6e33860e013858b8622c15fa6`; its status file has SHA-256 `712bcfe3a5268b75ec19b002b896d9fbbed7389f21857fd463ed2fdff4ece137`. Both record a complete validated report and exit status 0. The report binds table ID `3e7fcae5-61e8-4a03-af3f-363cac0394b0`, version `14`, schema SHA-256 `9fe9f7baf4a5c8da59235f5e822bc686c722ded7c1c768941bf553247cc41893`, and protocol reader 1 / writer 2. The saved post-result review accepted the inventory report, signature, and gates without reading production rows.

The current-snapshot dependency inventory lists 15 contiguous commit logs (`v0`–`v14`, 26,093 bytes) and one active Parquet file, `date=2024-01-02/part-00000-5071f6a6-957b-4fbf-93a3-a15dfa1a45ce-c000.snappy.parquet` (1,933 bytes): 16 files and 28,026 metadata-reported bytes total. There is no `_last_checkpoint` pointer or checkpoint dependency. These are path/size metadata and metadata fingerprints, not content hashes. The inventory read no Parquet payloads or rows and performed no history survey, copy, restore, or write. The count of two AAPL/MSFT rows comes only from the earlier inspection, not this inventory. This identifies a minimum current-v14 recovery set; it does not prove a successful restore, historical-file availability, time-travel recovery, or destination suitability. The approved backup comprises all 16 inventoried files—the 15 required commit logs and the one Parquet file active at v14. It excludes data files removed by v14 and is not a full historical backup; restoration of earlier versions is not promised.

### Historical initial mountpoint stop — superseded (2026-10-22)

The following records the initial blocked state only. It is superseded by the completed backup/isolated-copy verification and targeted delete in the closure above. Its mountpoint lookup and stop decision remain valid for that earlier point in time; they are not the current I01 status.

The owner explicitly approved: “Approve the exact-file backup and isolated restore verification.” This approval covers the exact-file backup and a full-current-v14, two-row restore validation on an isolated copy only: verify that the complete copy contains exactly the two expected typed rows and their full-row fingerprints, and matches the pinned table ID, schema, version, and protocol. It authorizes no production-row read, history survey, production restore, quarantine, delete, rollback, or other remediation. This supersedes the earlier pending-owner-approval language for that scope only.

One independently cleared read-only mountpoint lookup ran once. Exact argv:

```text
/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/findmnt --nocanonicalize --mountpoint /mnt/equity-backups --noheadings --output TARGET,FSTYPE
```

It ran from `2026-10-22T02:22:22.593971988Z` to `2026-10-22T02:22:22.599259378Z`, exited `1`, and had empty stdout and stderr. This means the exact mountpoint was not found in the current namespace; it is not evidence that storage is globally unavailable. The saved evidence checksum manifest `/tmp/equity-mount-availability.ZBreJi/sha256sums.txt` has SHA-256 `70114cfa05c664d7acadd0d0b46b4eacacceb2166be6331f522c6a11c568021d`.

**Stop state:** no backup, copy, restore, source-file SHA-256, parent backup record/signature, or actual production target/destination operation occurred. The earlier authorized row inspection and metadata inventory are historical point-in-time reads; they do not establish that the source is unchanged everywhere. The temporary package and synthetic passes are not a live backup. No fallback, alternate-location probing, filesystem-capacity probing, directory creation, mounting, or backup-stage production-table access occurred; no harness repair or test was done.

**Next owner action:** make the already configured `/mnt/equity-backups` backup volume available in the current namespace, or explicitly approve another storage path. Mounting, filesystem administration, or changing the destination is not automatic and is not covered by the current approval.

Before any actual backup exposure, the full harness had **not cleared review**. Before one state-bound backup attempt, first correct the known gaps and obtain a fresh Luna review plus fresh source/code-baseline evidence and parent attestation: the restore path produced by the corrected template still mismatches; the repository-status baseline mismatches; bounded package enumeration still uses unbounded `os.listdir`; the actual shared-production-egress wrapper rehearsal is missing; and the accounting failure branch was not exercised after true success. The 35/39 synthetic passes do not clear the full harness. Once eligible, retain the pinned v14 ID/schema/inventory checks and abort any drift without refresh or retry. No new quarantine approval is requested.

### Historical I01 remediation proposal — superseded; no further action planned

The proposal below records the earlier decision state and is superseded for the two target rows by the owner-authorized backup/verification and completed delete described above. Do not treat its old pending-approval language or numbered steps as current instructions. The delete does not authorize broader remediation: quarantine, production restore, rollback, migration, vacuum, or further writes remain outside this completed scope.

1. Define the two exact targets by the full typed rows and fingerprints above, not ticker/date alone. At execution time, require table ID `3e7fcae5-61e8-4a03-af3f-363cac0394b0`, version `14`, schema SHA-256 `9fe9f7baf4a5c8da59235f5e822bc686c722ded7c1c768941bf553247cc41893`, and exactly one null-safe full-row match for each approved fingerprint. Any table-ID/version/schema/count/fingerprint drift means abort; do not refresh the snapshot and retry. The observed version 14/table ID above are a point-in-time baseline, not a claim about present state.
2. The separately authorized, guarded metadata inventory is complete (see above); its 16-file current-snapshot dependency set is descriptive metadata, not a backup or proof of restore. The owner later authorized the exact-file backup and full-current-v14, two-row validation on an isolated restore copy only; that earlier blocked state was superseded by the completed backup and isolated-copy verification recorded in the latest closure above. A full-table comparison is authorized only on that isolated copy; no production-row read or other full-table comparison is authorized. Do not infer future live state from this inventory or call its observed byte count a table-size estimate.
3. Validate the proposed Delta delete API's behavior on temporary fixtures first; deletion semantics are not yet established. Define a typed Pydantic quarantine/audit artifact and its write boundary before producing it. Only after separate approval, conditionally delete the exact approved full-row matches with null-safe predicates and state-bound checks.
4. Treat the two target rows as non-atomic: specify idempotent state and stop on any OCC/state-version conflict, with no refresh/retry. Any conflict-sensitive rollback is a separate approval and plan.

No blanket cleanup, direct Parquet deletion, vacuum, table reset, guessed canonical identity, retraining, or migration was performed or is authorized/planned. The completed scoped delete above authorizes no further cleanup.

## Historical boundaries

C00–C10 implementation/offline-contract acceptance remains accepted for its recorded scope. It is not production operational readiness and does not answer I01. The Oct18 incident report remains the source for its historical inspection methods, evidence limits, and row/version facts. No prior suite was rerun for this status note. This document records status and constraints; it is not self-approval or a new policy/authorization. The `/tmp` evidence paths above are host-local and may not exist elsewhere.

## Repository review and validation — 2026-10-23

- Reviewed local history: `f010fb6ed2268ca5709a9281654cc32ef6a36aeb` (eight accepted ADRs; 8 files), `bca1029afd97d0d619738f2a5cb88ec53f1c902a` (integrated code/tests; 169 files), and `d8249df5b706cfb544488bc2bd828ee0b2d18f2a` (guide/incident closure; 23 files). These are existing local commits, not newly pushed work.
- Saved offline code-validation evidence in `/tmp/equity-worktree-validation-Luna-02/hostsource`: serial and parallel suites each **1,586 passed**; Ruff, formatting, configured `mypy src` (170 source files), and temporary DAG/catalog comparison passed. These historical code gates were not rerun for this docs-only update; no code changed after them.
- Separate, non-configured `mypy --strict src` reported **49 errors across 23 files** (46 previously known; 3 additional strict-only annotation findings at `ml/validation.py:71,165` and `signals/scanner.py:60`). It did not pass and is not the configured mypy gate.
- The initial offline MkDocs strict build after the exclusion/link corrections passed at **2026-10-23T10:52:11Z** with **0 warnings**. Material's MkDocs 2.0 notice and omitted-navigation/excluded-file notices were informational. Final-state docs validation is captured separately; this documentation update makes no code-test or production-readiness claim.
- Unrelated P2 tracking and CLI/config follow-ups remain separate from this docs-only update.
