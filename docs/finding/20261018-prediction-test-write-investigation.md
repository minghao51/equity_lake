# Prediction-test write incident: bounded read-only findings

**Updated:** 2026-10-18 13:20 UTC
**Status:** Read-only investigation completed under owner authorization. Fixture-shaped records are confirmed in the one authorized table; attribution to a specific test/process and any prior real-row replacement are **not established**. Three follow-up issues were open as of this report's update. No remediation was authorized or performed then.

## Current status supplement — 2026-10-22

This report's findings and open-issue register below preserve the bounded Oct18 inspection and source-review status; they are not assertions about present table state. I02 and I03 have since received implementation/offline-contract acceptance. The owner-authorized I01 exact-file backup/isolated-copy verification and two-row targeted Delta delete are complete; the single delete advanced v14→v15 at 2026-10-22T16:38:23Z and left zero active files, while historical Delta files remain. Row origin, source actor, and MIC remain unknown; no further mutation is planned. The [durable status update](../plans/20261021-prediction-follow-up-status.md), updated 2026-10-22, records the exact scope, full-row evidence, verification limits, and retained proof. The Oct18 methods, historical row/version facts, unknown-origin conclusion, and C00–C10 tested-scope acceptance remain unchanged.

## Runtime verification (performed first)

Before reading the supplied reports or repository documentation, the active runtime environment reported:

```text
PI_PROVIDER=openai-codex
PI_MODEL=gpt-6-luna
PI_REASONING_LEVEL=xhigh
```

These values match Luna `xhigh`; no fallback was reported or indicated in the exposed runtime metadata. A separate final documentation review rechecked the runtime fields and observed the same values, again with no fallback reported. This is not independent telemetry about provider-internal routing. The trace, inspection, history, and independent-review reports all record the same observed runtime values and no fallback. The scout agent definition was role-only and had no model pin; the separate assay worker definition used for inspection was pinned to `openai-codex/gpt-6-luna`. Do not attribute the assay pin to the scout invocation: observed runtime metadata, not agent-definition metadata, supports the scout model/reasoning statement.

## Executive summary

The sole inspected production target was the default, non-temporary Delta table:

`/home/howt/work/equity_lake/data/lake/04_platinum/predictions`

Its retained versions 0–14 contain two AAPL/MSFT rows for `2024-01-02` marked `direction=up`, `probability=0.8`, `model_mode=unknown`, `model_version=fake`. At v0, the earliest retained snapshot, `feature_schema_version=4`; at v6 both rows change to null and remain so through v14. The table was already fixture-marked in v0. There is no retained genuine predecessor and no retained evidence that a real matching row was replaced. The history cannot rule out pre-v0 deletion/recreation, another root/table lifecycle, or data outside this target.

The unmocked historical test path is plausible, but the cited passing JUnit suite starts around 20:10–20:11 local time, **after every retained commit** (latest 16:37:34.979+08:00). Those suite records do not explain the retained commits. Exact fixture correspondence is suspicious/highly suggestive, not proof of a unique agent, test, or process. Current test-seam/root-guard repairs are reported as already present; they are not an OS sandbox and do not retroactively identify prior writes.

A source-only finding remains: `GET /predictions` can expose raw legacy rows if served against this same root, because its default no-date path reads the table before applying return limits. No HTTP call, service run, or external-consumer use was observed. The built-in ML signal route reads features/models on demand rather than this table; custom/external consumers remain unknown.

## Scope and method

This report consolidates the prior trace, inspection, retained-version history, and independent review:

- `/tmp/equity-incident-trace-report.md`
- `/tmp/equity-incident-inspection-report.md`
- `/tmp/equity-incident-history-report.md`
- `/tmp/equity-incident-review-independent-20261018/review.md`

The prior authorized read-only table inspection was confined to the exact target above. Its sandbox used a sanitized `env -i`, read-only repository/root/target mounts, masked other repository data/logs and credentials, an unshared network namespace, and a dedicated evidence output. No other production table was exposed for row inspection. The supplied reports record no repository edits, commit, application imports/execution, CLI, pytest, provider/network access, training, writes, repair, migration, cleanup, restoration, or rollback during investigation. No production table outside this one authorized target was accessed. This documentation update likewise did not repeat source audits, production reads, tests, or application runs.

The history reports 30 files / 55,484 bytes under this one table; their full-byte SHA-256 manifest was unchanged during the bounded read, with entry-manifest digest `d285acec114a10fad3b48e9a824186c05033c2e026e88b85b40994dc5804eb35`. Hashing the files' bytes was a stability check, not decoding every Parquet row. The collected row results were limited to the requested date and two ticker keys/allowlisted fields; the reports do not establish physical predicate pushdown. The history report notes that active-file `numRecords=2` is Delta add-action metadata, not an independent full-corpus row audit. Its first smoke stopped before production-table exposure because the new evidence directory lacked a local `bin/uv` placeholder; that placeholder was created only in the `/tmp` evidence directory and the smoke then passed. The first row classifier treated only the null-schema version as fixture-marked; it was corrected to distinguish the stable `fake` output markers from the later null-schema form, then the same bounded v0–v14 scans were repeated. Both attempts were read-only; the report uses the corrected classification.

### Independent review limitations on evidence tooling

The independent reviewer accepted the retained-history findings **with limits**:

- The earlier inspector's text-only plan check (`"SELECTION:"` and `"SCAN"`) plus a stored plan hash does not prove structurally that predicate evaluation was physically pushed into the scan. The defensible claim is that the filtered selected results were collected, not exact physical predicate pushdown. The history inspector required each versioned scan to pass its filter guard, and its saved per-version scans succeeded.
- The earlier inspector could return success based on manifest stability when the row scan returned an error object. Four preliminary API/Arrow attempts produced no rows (`current_matching_rows=None`); the logs do **not** establish nonzero exit codes, so this report does not describe them as proven nonzero failures. The saved final inspector and later 15-version scan succeeded; their logs are preserved at `/tmp/equity-incident-inspection-akkVMN/inspection-run.log` and `/tmp/equity-incident-history-tlI17d/history-run.log`, alongside the preliminary inspection attempt logs.
- The retained plan hashes do not preserve a complete redacted plan. These caveats bound the confidence in scan procedure; they are not evidence of another table or other-key values being decoded.

### Documentation review procedure

A documentation-review invocation edited documentation despite its read-only assignment. Its edits were not treated as self-approved. A subsequent separate Luna `xhigh` read-only review accepted the records with limits and verified identical before/after hashes for all four documentation files; evidence is in `/tmp/equity-incident-final-readonly-review-ePIsxM/`. No source, test, catalog, or production-data change was authorized by the documentation-review assignment.

## Confirmed retained-table state

The retained log has contiguous JSON versions **0–14** (15 commits), with no checkpoint files or `_last_checkpoint` marker. Schema is legacy ticker/date identity; there are no `market`, `mic`, or `symbol` columns. Do not infer XNAS/MIC.

| Version(s) | Delta operation | Local +08:00 time | Selected values for both AAPL and MSFT |
|---|---|---|---|
| 0 | `WRITE`, `mode=Append` | 2026-10-16 12:20:09.475 | `2024-01-02`, `up`, `0.8`, `unknown`, `fake`, schema `4` |
| 1–5 | `MERGE` | 12:22:04.296; 14:19:04.914; 14:22:18.552; 14:24:50.763; 14:25:21.833 | Same selected values as v0 |
| 6 | `MERGE` | 16:17:57.441 | `feature_schema_version` changes from `4` to `null`; other selected values unchanged |
| 7–14 | `MERGE` | 16:18:06.087; 16:18:18.526; 16:20:32.575; 16:20:52.268; 16:21:22.824; 16:23:11.003; 16:27:41.322; 16:37:34.979 | Same selected values as v6 |

For each retained version, collecting the date/ticker-filtered query returned exactly two matching rows, one per ticker, without duplicate matching keys; this does not claim physical predicate pushdown. Every commit added a file for `date=2024-01-02`; v1–v14 also removed one file in that partition. Operation metrics are absent/empty. No retained `OVERWRITE` operation/mode appears. Repeated MERGE/file replacement does **not** mean 15 independent value changes: selected row values are unchanged across v1–v5 and v7–v14. The v0 fake-shaped values are the earliest retained state; v0 has no retained predecessor for comparison.

The inspection report records current v14 rows as:

| ticker | date | direction | probability | model_mode | model_version | feature_schema_version |
|---|---|---|---:|---|---|---|
| AAPL | 2024-01-02 | up | 0.8 | unknown | fake | null |
| MSFT | 2024-01-02 | up | 0.8 | unknown | fake | null |

The initial trace identified the historical unmocked path at HEAD `0786e43a0d1c5f16bd4934e382599583bb87cae2`: `tests/unit/test_ml_jobs.py::test_run_prediction_job_uses_package_forecaster` used a fake forecaster and did not patch `merge_delta`; `src/equity_lake/ml/__init__.py` then called the real Delta writer for `04_platinum/predictions`. That establishes a plausible route only. The actual incident-time source bytes, target root, table lifecycle before v0, and writer/process that created each retained version are unknown. The current test seam captures the writer and current pytest temporary-root guard are reported repaired; the latter is a pytest-level safeguard, not an OS sandbox.

The saved passing JUnit suites started at `2026-10-16 20:10:42.259355+08:00` and `20:11:09.826696+08:00`; all v0–v14 commits precede those times, and no retained commit falls in the reported 20:00–20:30 interval. Suite start is not a per-test or Delta commit timestamp. Thus those particular saved suite records cannot account for the retained commits. Earlier or different runs/roots are not attributed. There is no retained proof of replacement of a genuine matching row; pre-v0 deletion/recreation or missing history cannot be excluded.

## Consumer and related-boundary findings (source-only; as of 2026-10-18)

### I02 — Raw predictions API read-boundary gap (open as of 2026-10-18)

The current source path identified by the independent review is:

- `src/equity_lake/api/routers/predictions.py:16-24` exposes `GET /predictions` (default `limit=100`, max 1000; `limit` caps returned rows).
- `src/equity_lake/api/deps.py:46-80` reads fixed table `04_platinum/predictions`. With no date, it materializes the whole Delta table (`to_pyarrow_table()`); optional ticker filtering, sort, and `head(limit)` occur afterward. There is no read-boundary requirement for canonical identity, trusted model provenance, compatible schema, or rejection/status of fixture-shaped legacy rows.
- The API uses the checkout-derived default root. The API CLI defaults to loopback and requires explicit consent for non-loopback unauthenticated exposure; loopback is not authentication.

Therefore these rows **could** be returned if the API is launched against this same root and no intervening change occurs. No HTTP request, API launch, response, client access, or external consumer use was observed. The table may also have custom consumers; those are unknown.

This reopens the **scoped C04/R14 API boundary** after its earlier implementation/offline-contract acceptance; it does not invalidate that historical acceptance of canonical identity behavior in other reviewed paths. R18's training/inference provenance acceptance also remains historically valid, but does not close this raw API boundary.

The built-in ML signal generators call `PriceForecaster.predict()` on demand and use feature/model inputs; the reviewed built-in route does not read persisted Platinum predictions. This finding therefore does not show contamination of built-in ML alerts. It also does not prove that all readers are safe: raw API and custom/external readers remain separate. The prediction write-batch checks noted in source validate basic direction/probability/identity/date but do not require a trusted `model_mode`/`model_version` or non-null feature schema; they do not clean old rows or guard the raw API.

### I03 — Generic Delta-maintenance path-containment risk (open on 2026-10-18; contract review needed)

Source-only review of `src/equity_lake/cli/commands/data.py:16-39,351-384` and `src/equity_lake/storage/delta.py:67-69` found that the generic maintenance resolver can pass unknown path strings through to a plain lake-root join without an explicit containment check; absolute paths may resolve outside that root. This is a local operator CLI path-containment risk that needs contract review. Full-path support may be intentional; this bounded review did not establish the intended accepted path forms or containment contract, so it does not assert a confirmed vulnerability or authorization bypass. No traversal probe, vacuum, compaction, or other maintenance command was run, and no remote exploit or production use is claimed. Recommend a separately approved, narrow contract review before deciding whether a fix is needed, rather than a broad code audit.

The history report also notes that `signals/history.py:_ensure_delta_table` may invoke legacy Parquet migration when that separate signal-history reader is called. This behavior was not executed or independently reviewed end-to-end and is **not** a newly confirmed production incident/finding here; any follow-up should be bounded verification.

## Open issue register and recommendations (not authorization; status as of 2026-10-18)

| ID | Status as of 2026-10-18 | Next step requiring separate approval where it changes behavior/data |
|---|---|---|
| I01 — Fixture-marked prediction rows / origin unknown | **Open.** Two fixture-shaped records are confirmed; which process wrote them and any pre-v0 state are unknown. | If approved, plan narrowly scoped, auditable Delta quarantine/remediation of these two keyed values, with backup/verification plan first. Prefer an auditable Delta commit; never delete Parquet files directly. No repair, quarantine, restore, rollback, or migration was authorized here. |
| I02 — Raw predictions API boundary | **Open.** Source path can return legacy fixture-shaped rows; no actual response/consumer use observed. | Separately review an explicit validity/status/identity/provenance policy at the reader boundary and its response behavior. Do not infer that existing model provenance guards cover this API. |
| I03 — Generic Delta maintenance containment | **Open.** Source-only path-containment risk; full-path support may be intentional, and no confirmed exploit or use is claimed. | Separately approve a narrow contract review to establish accepted path forms and whether containment should be enforced; decide on a fix and safe tests only afterward. Do not expand this task into a global code audit or execute maintenance. |

Existing C03 source-availability proof and paid-cost-adapter limits are unchanged and are not part of incident remediation. No broad rearchitecture or ADR change is proposed here. These recommendations do not grant authority to execute them.

## Status and preserved historical verification

C00–C10 implementation/offline-contract acceptance and its independent-review evidence remain valid **as of the recorded acceptance**. Historical serial results of 1,533 passed; parallel results of 1,533 passed; and 50 inspected mocked integrations remain valid verification evidence for those code/test scopes. They were not rerun for this investigation/documentation update and are not evidence of live-table integrity, this incident's origin, API non-use, or lake cleanliness. The initial 1,485-pass/47-failure serial evidence remains historical and is not erased. The C10 acceptance event is retained; current post-closure issues I01–I03 mean the earlier blanket wording "all scope closed/no findings open" must not be used as current status.

As recorded in the Oct18 report, authorization was limited to read-only investigation and related findings. No remediation, quarantine, deletion, rollback, migration, training, live/provider/paid action, maintenance, commit, or publish authorization was given or used. No source, test, catalog, data, or application files were changed. This documentation pass is limited to the four requested Markdown records. This document is a status/finding record, not a cleanliness certificate.

## Evidence pointers

Primary reports and their artifact details are preserved in `/tmp`:

- Trace: `/tmp/equity-incident-trace-report.md` (historical writer/test path, saved JUnit timing, limits).
- Table inspection: `/tmp/equity-incident-inspection-report.md`; sanitized output `/tmp/equity-incident-inspection-akkVMN/inspection-sanitized.json`, SHA-256 `e91dd873e6b5adeb6310f2d462012c3de038cada0a1ba121174b3cc4e652fb79`; final and preliminary run logs are retained in that evidence directory.
- Retained-version history: `/tmp/equity-incident-history-report.md`; `/tmp/equity-incident-history-tlI17d/history-sanitized.json`, SHA-256 `12996cc91a4c085d3297c11ec8ca25c0232e68d2c9cf638f872b2f6dee453525`; successful `history-run.log` is retained alongside it.
- Target manifest entry digest: `d285acec114a10fad3b48e9a824186c05033c2e026e88b85b40994dc5804eb35`.
- Independent review and tooling caveats: `/tmp/equity-incident-review-independent-20261018/review.md`.
- JUnit evidence read, not rerun: `/tmp/equity-continuation-isolation.xml` and `/tmp/equity-continuation-serial.xml`.

No content beyond these already-authorized report/evidence locations is asserted here.
