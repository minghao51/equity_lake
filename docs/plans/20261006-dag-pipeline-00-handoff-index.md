# DAG Pipeline Remediation — Agent Handoff Index

**Date:** 2026-10-06
**Status:** Draft implementation plan; no runtime changes implemented
**Audit baseline:** `522d8fa8a2c23d59d217fab37a2f9a42fb5d7f36`
**Owner:** Repository owner; implementation agents must record their assignments below

## Purpose and entry point

Turn the [DAG pipeline audit](../finding/20261006-dag-pipeline-audit.md) into
small, independently reviewable changes. The priority is trustworthy outputs
and failure contracts, then incremental processing, schema/provenance, and
finally measured optimization. This plan is not a new architecture framework.

Read this index, the assigned wave, `AGENTS.md`, `docs/README.md`, and the
canonical pipeline/data-flow contracts before implementation. Re-read the
current source: audit line numbers are baseline navigation aids, not exact
patch instructions. Older closed handoffs are historical evidence, not proof
that the current contracts hold.

**Approval:** The user requested documentation and handoffs, not authorization
for all policy choices, migrations, network runs, or model retraining. Each
implementer must present its scoped code-change plan for approval. Boundary
changes require an accepted ADR before code. Recommendations below are not
accepted decisions until the owner records approval.

## Dispatch order

| Wave | Handoff | Findings | Depends on | Suggested PR scope |
|---|---|---|---|---|
| W01 | [Immediate DAG and vendor correctness](20261006-dag-pipeline-01-correctness.md) | F01, F02, F13, F15; schema-routing checks from F20 | Approval of validation/date policy | Date joins; connected validation; vendor mapping; finite checks, as separate commits |
| W02 | [Truthful outcomes and coverage](20261006-dag-pipeline-02-outcomes-and-coverage.md) | F06–F10, F25 | W01 for real-output coverage tests | Outcome isolation/reporting, then scope and coverage |
| W03 | [Incremental lifecycle and scoped recovery](20261006-dag-pipeline-03-incremental-contracts.md) | F03–F05, F11, F14 | W02; label/session decisions | Recovery scope/completeness, labels, session policy, action overlap |
| W04 | [Identity and temporal provenance](20261006-dag-pipeline-04-schema-and-provenance.md) | F12, F16, F17 | W03; accepted identity/temporal ADRs | Identity migration separately from enrichment semantics |
| W05 | [Catalog and operational truth](20261006-dag-pipeline-05-catalog-and-monitoring.md) | F18, F19, F23, F24 | W03/W04 final schemas | Catalog contracts; monitoring; explicit drift policy |
| W06 | [Storage safety and bounded processing](20261006-dag-pipeline-06-storage-and-processing.md) | F20–F22 | W02/W04; normally after W05; storage/processing decisions | Delta safeguards separately from bounded LLM batches |
| W07 | [DRY, optimization, and release closure](20261006-dag-pipeline-07-maintainability-and-validation.md) | F26–F28; final closure of all IDs | W01–W06 | Tooling/test selection; targeted DRY; measured performance; end-to-end evidence |

Default execution is sequential: shared files such as `pipeline.py`,
`writers.py`, `features/engineering.py`, and generated catalog output make
uncoordinated parallel workers unsafe. A coordinator may split W01 vendor
mapping from DAG work, or W05 catalog from monitoring, only after assigning
disjoint source/test/doc ownership. Exactly one agent regenerates the catalog
per integrated wave. Reviewers are read-only. Do not commit unless authorized.

W07's test-marker/tooling repairs may be brought forward as a small isolated
preflight PR; optimization must remain last. Do not postpone correctness to
bundle it with cleanup.

## Decisions and stop gates

Record decisions in the wave's progress section and canonical docs; create the
next available ADR number rather than assuming a specific unallocated number.

| Decision | Recommended starting point | Stop condition |
|---|---|---|
| D01 Date and row validation | EOD keys are `pl.Date`; validate one cleaned frame before all indicators; invalid required price input fails rather than silently shrinking scope | Owner has not approved invalid-row handling |
| D02 Scope and coverage | No hidden ten-ticker cap; explicit scope never broadens; report every missing instrument; missing scheduled required coverage fails | **Approved by owner 2026-10-07** (dispatch “approve on D02 … W03 to W07 per your judgement”): cap removed; explicit empty scope rejected/never broadened; enrichment-only requests planned-skip features/ML; missing scheduled required ticker coverage fails the feature stage. Implemented in W02. |
| D03 Label lifecycle | Keep existing Gold target compatibility initially; finalize only label fields after the next observed session, with availability metadata and bounded repair | **Approved by owner 2026-10-08**; recorded in [ADR-0014](../decisions/0014-label-lifecycle-and-availability.md): `label_available_date`, next-session-with-valid-bar definition, label-only key-scoped update, strict as-of exclusion (unknown-availability legacy rows excluded by default), bounded authorized repair (D09), schema-version bump with catalog/model exclusion |
| D04 Market sessions | Closed markets are `not_scheduled`, not failed; never silently substitute an earlier session for an explicit date | **Approved by owner 2026-10-08**: explicit-date semantics with a distinct `not_scheduled` status that does not block open markets; features/ML planned only for scheduled scope; per-market latest-session dates deferred to a separate design; HK/SG union semantics retained and documented |
| D05 Instrument identity | Canonical long market key plus ticker in shared Gold/Platinum identity; ambiguity rejected, not guessed | No accepted schema/storage ADR or migration/reader plan |
| D06 Temporal enrichment | Backward-asof with preceding state; availability/staleness explicit; retain zero defaults only with documented missingness | Release/ingestion cutoff and per-enrichment carry rules are undecided |
| D07 Drift | Explicitly disable unsupported automatic drift at the writer initially; no automatic persisted baseline system | Owner instead requires real baseline persistence without approved design |
| D08 Processing budget | Bounded deterministic batches; distinguish missing table from failed Silver read; no corpus-wide paid retry on read failure | Batch limit/checkpoint/failure policy lacks approval |
| D09 Historical repair | Report affected keys and snapshots first; scoped, authorized repair only; model compatibility/retraining disclosed | Owner has not authorized production data/model changes |

Existing ADR-0011 deliberately defers feature-side price-adjustment opt-in.
Fix vendor event mapping and overlap, but do not silently enable adjusted
features or extend corporate actions to CN/other markets. ADR-0013 is still
Proposed at the audit baseline; do not treat it as accepted or implement the
ML/signals direction change in this remediation without separate approval.

## Non-negotiable implementation constraints

- Preserve the numbered medallion layout, five fixed price markets, top-level
  import boundaries, Polars-first computation, native Typer, and source retries
  through tenacity. No generic scheduler, plugin registry, new domain tree, or
  distributed processing platform.
- Dry-run cannot persist anything, backfill, process LLM work, emit features, or
  perform inference. Test this after every stage-affecting change.
- Use current `Settings` and existing registries; new nested env configuration
  requires the corresponding Pydantic model and `.env.example` entry together.
- Catalog JSONL is generated, never hand-edited. Pipeline/feature changes must
  regenerate it even if the result is unchanged.
- Auxiliary artifacts are outside `data/lake/`; any new durable run/repair
  artifact needs a Pydantic write-boundary model. Do not invent an auxiliary
  catalog dataset for run results or operational findings.
- Mock provider/LLM boundaries and use temporary lake/model paths. Never run
  live ingestion, broad history repair, paid processing, vacuum, or retraining
  as default validation. Use dotenvx when an authorized operation needs secrets.
- Preserve user changes. Do not stage unrelated files, generated site output,
  runtime data, logs, credentials, or caches.

## Verification shared by every wave

First establish the environment and baseline. Package downloads during setup
are distinct from permission to access live financial sources.

```bash
uv sync --group dev
uv run pytest tests/unit/test_import_boundaries.py tests/unit/test_cli_unified.py -m "not network and not integration"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Until W07 repairs marker selection, inspect selected tests before running them:
`tests/unit/` contains integration/network tests. Do not equate directory with
offline safety. Wave-specific commands list focused suites; run only mocked
integration cases after checking their fixtures.

After code changes, run the wave's focused tests and the broader offline gates:

```bash
uv run pytest -m "not network and not integration"
uv run pytest -n auto -m "not network and not integration and not serial"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run equity catalog-generate
uv run pytest tests/unit/test_catalog_builder.py tests/unit/test_catalog_datasets.py tests/unit/test_catalog_writer.py
uv run --group docs mkdocs build --site-dir /tmp/equity-lake-remediation-site
git diff --check
```

For final release gates see W07, including offline cross-module integration,
serial tests, optional groups, and Python 3.12/3.13. Never relax lint/type or
behavioral assertions to hide the baseline failures.

## Required delivery packet from each implementer

1. Finding IDs addressed; files actually changed; compatibility and policy
   decisions with approval/ADR links.
2. New regression tests and proof they detect the old behavior, where feasible.
3. Exact commands, pass/skip/deselection counts, environment/dependency groups,
   and any unverified external assumptions.
4. Catalog diff or evidence that regeneration is unchanged.
5. Migration/repair needs, dry-run/no-write evidence, and rollback procedure.
6. Remaining findings and the next wave's entry conditions.
7. Update the audit resolution table and this index. Do not mark an ID closed
   merely because code was edited: tests, docs, compatibility, and review count.

## Assignment and progress ledger

| Wave | State | Agent | Commit/PR | Evidence / blocker |
|---|---|---|---|---|
| W01 | **Landed** | pi coding agent | `e44bd5d` (audit/handoff docs) + `2906477` (implementation), local `main`, unpushed | D01 approved via owner dispatch. Three read-only reviews (zai/glm-5.3-flash): 1 critical (emitted-frame row alignment) + warnings — all addressed, see [W01 progress](20261006-dag-pipeline-01-correctness.md#progress). Post-fix gates: 1013 passed / 22 skipped / 3 pre-existing `test_arena.py` failures; lint/format/mypy at documented baseline. Accepted limitations recorded (missing-vs-failed detail → W02; catalog date-dtype wording → W05). W02 dispatched 2026-10-07 after owner D02 approval. |
| W02 | **Landed** | pi coding agent | `8229eda` (implementation + tests), local `main`, unpushed | All W02 findings implemented: truthful outcomes (optional_failures+partial, NO_DATA/DRY_RUN, router causes), equal serial/parallel isolation + per-task correlation context, D02 scope (cap removed, empty scope rejected, enrichment-only skip, scope published), feature coverage with stable reasons + coverage-fails policy, persistence/dry-run/recovery honesty. Regression: 15 load-bearing tests fail on pre-change `src/`. Gates: 1047 passed / 22 skipped / 3 pre-existing `test_arena.py` failures; `-n auto` parity; ruff/format clean; mypy at the 3-error `ml/registry.py` baseline; catalog unchanged (17/52/372); mkdocs 3 pre-existing warnings; `git diff --check` clean. Three read-only reviews (`opencode-go/mimo-v2.6-pro`): 1 critical (exception-path coverage fabrication) + warnings — all fixed; see [W02 progress](20261006-dag-pipeline-02-outcomes-and-coverage.md#progress). Cost disclosure recorded (scope increase to all configured active tickers). |
| W03 | **In progress (PR1 landed; PR2 labels next)** | pi coding agent | `c0f37ee` (PR1 implementation + tests), local `main`, unpushed | PR1 owner-approved 2026-10-08, owner-authorized single commit. Explicit scope through all five price markets (`resolve_explicit_scope`; single-market overrides preserved, multi-market membership from `TickerConfig`, unresolved/ambiguous rejected, `None`/`[]` preserved), CN/HK-SG/JPX/KRX factory forwarding + CN primary/fallback/yfinance same list, consolidated ticker-aware `_read_partition_coverage` (partial partitions fetch only missing tickers; complete scope skips; unspecified incomplete requests fetch the fetcher default), and `BackfillResult` recovery scope/counts with a structured pipeline `recovery` payload. Regression: resolver/factory/CN-seam/real-Delta partial-partition/dry-run/recovery suites (pre-change `git stash` proof: 11 router/backfill/fetchers/orchestrator + 6 integration + 8 backfill + 6 fetchers + 6 router failures on old `src/`; `test_ingestion_types` collection error). Gates (post-review re-run): 1090 passed / 22 skipped / 85 deselected / 3 pre-existing `test_arena` failures; `-n auto` parity; ruff/format clean; mypy 3-error baseline; catalog unchanged (17/52/372); mkdocs 3 pre-existing warnings; `git diff --check` clean. Read-only review (`opencode-go/mimo-v2.6-pro`): **READY WITH WARNINGS — 0 critical, 4 warnings, all addressed** (None-scope fetch default + test; `trading_dates` calendar-day doc; gate counts verified; empty-config fallback preserved); see [W03 progress](20261006-dag-pipeline-03-incremental-contracts.md#progress). Owner-authorized commit 2026-10-08; PR2 labels, PR3 sessions, PR4 action overlap remain |
| W04 | Not started | Unassigned | — | W03, accepted D05/D06 ADRs |
| W05 | Not started | Unassigned | — | W03/W04, D07 approval |
| W06 | Not started | Unassigned | — | W02/W04; normally after W05; D08 approval |
| W07 | Not started | Unassigned | — | Integrated prior waves |

## Copyable dispatch prompt

> Implement only WXX from `docs/plans/20261006-dag-pipeline-XX-<name>.md`.
> Read the index, linked audit, AGENTS.md, canonical contracts, current source,
> and relevant skills first. Verify dependencies and approval gates. Present a
> minimal code-change plan for approval; stop for undecided policy or unaccepted
> boundary ADRs. Add regression tests, change only assigned files, and do not
> run live sources, paid LLM work, production repair, or retraining. Regenerate
> the catalog when required. Return the delivery packet and update the progress
> ledger. No commit or migration without explicit authorization.

Per-unit, copyable prompts (shared preamble, W03 PR1–PR4, W04–W07, and the
read-only reviewer template) live in
[Dispatch Prompts](20261008-dag-pipeline-dispatch-prompts.md).

## Whole-program exit criteria

- All finding IDs are closed with evidence or explicitly deferred by the owner,
  with impact and follow-up recorded; temporal-provider limitations stay honest.
- End-to-end mocked EOD runs prove date/schema compatibility, truthful optional
  degradation, required-source blocking, complete scheduled scope, label
  finalization, scoped recovery, and no dry-run side effects.
- Schema migration and model compatibility are exercised on a copied or
  temporary lake, with no ambiguous identity silently assigned.
- Catalog, monitoring, run results, user guides, and canonical contracts agree
  with executable behavior.
- Offline serial/parallel suites, optional backend tests, lint, format, type,
  catalog, docs, and diff checks pass, or a named baseline waiver is approved.
- Performance claims have before/after measurements and value parity evidence.

## Documentation preparation evidence — 2026-10-06

- Created the audit register and W00–W07 handoffs; linked them from the
  documentation map and MkDocs navigation. No runtime code, catalog output,
  lake data, or model artifacts changed.
- Read-only plan review checked finding ownership, approval gates, file/command
  references, migration safety, and wave dependencies. Clarified W03/W04
  prerequisites for W05 and W01/W06 ownership of F20.
- Markdown lint on the nine new pages passed with repository-standard
  MD013/MD033/MD041 exclusions. Relative page links and every MkDocs navigation
  target resolved.
- `uv run --locked --group docs mkdocs build --site-dir /tmp/equity-lake-dag-plan-site`
  succeeded. Existing warnings remain for the
  README/index conflict and two archived roadmap links; no new-page broken
  links were reported. Build output stayed outside the repository.
- This is documentation validation, not implementation acceptance. Original
  runtime-test counts remain historical audit evidence; all waves are unstarted.
