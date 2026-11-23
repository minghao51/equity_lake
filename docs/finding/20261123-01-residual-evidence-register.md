# Residual evidence register — RS-Q00

**Stage:** RS-Q00 (evidence inventory and true recursive lint) of the
[residual spec and implementation plan](../plans/20261122-01-residual-spec-and-implementation-plan.md) §1, §3 and §5.
**Status:** every §1 claim re-derived at this baseline; the recursive lint gate is implemented with a reviewed
baseline ratchet. **No finding was fixed and no rule was disabled** — the 314 preexisting findings are classified
below and frozen as debt.

Recorded 2026-11-23 00:46 UTC on branch `feat/rs-q00-recursive-markdown-lint`, cut from `2634060`
(the plan's own drafting baseline). Tools: `uv` 0.9.7, Python 3.12.3, `pymarkdownlnt` 0.9.35.

## 1. Status summary

| # | Claim (plan §1) | Status | Decisive observation |
| --- | --- | --- | --- |
| 1 | Pages workflow ships a broken cron-sync check | `CONFIRMED` | `sync_schedule --check` exits 1 with `ValueError: Could not find a cron line in the workflow.` |
| 2 | `pages.yml` builds dashboard+health, not the H00 artifact; a second workflow deploys the same Pages site | `CONFIRMED` | no `h00` reference in `pages.yml`; two `deploy-pages` steps in the repository |
| 3 | `total_return` is unusable end to end | `CONFIRMED` | CLI refuses it, the loader maps it, the reader raises |
| 4 | R00 measurement evidence is absent, not "not run" | `CONFIRMED` | no planted/null/leakage-control or scale report in `docs/`, `data/research/`, `data/findings/` |
| 5 | R02's −2.79 pp point estimate lies outside its reported [−0.42, +0.31] pp interval | `CONFIRMED` as a document contrast; the **defect stays undeclared** | point estimate reproduces from the published table, interval does not cover it; estimand/units unverified |
| 6 | Markdown lint is non-recursive | `CONFIRMED` | non-recursive scan exits 0 with 0 findings; recursive scan exits 1 with 314 |
| 7 | `data/research/` holds only `h00/`, `r02/`, `protocols/`; `data/portfolio/` is absent | `CONFIRMED` (operator checkout) | see §3.7 for the fresh-worktree nuance |

`PENDING`: nothing in §1 is left unverified. The R00 *measurements themselves* remain `evidence-unverified` in plan
terms — that is claim 4's finding, not a gap in this register.

## 2. Reproduction environment

- Every command below was run from a clean checkout of this branch (`git status --short` empty before each run).
- `data/research/` and `data/findings/` are ignored runtime data (`data/*` in `.gitignore`): they exist in the
  operator checkout `/home/howt/work/equity_lake` and are **absent** in a fresh worktree. Claims 4 and 7 therefore
  reproduce against the operator checkout, read only; the lint claims reproduce anywhere.
- Commands use `uv run`, per `AGENTS.md`.

## 3. Claim-by-claim verification

### 3.1 Claim 1 — the Pages workflow runs a cron-sync check that cannot pass

Status `CONFIRMED`.

```bash
uv run python -m equity_lake.devtools.sync_schedule --check; echo "exit=$?"
grep -n 'schedule:\|sync_schedule' .github/workflows/pages.yml
```

Observed: exit 1 with `ValueError: Could not find a cron line in the workflow.` The module targets
`.github/workflows/pages.yml` (`sync_schedule.py:18`) and `pages.yml:29` still runs the check, while the workflow's
`on:` block now declares `workflow_dispatch:` only (no `schedule:`). The plan's line references hold.

### 3.2 Claim 2 — the Pages workflow builds the wrong surface, and two workflows deploy

Status `CONFIRMED`.

```bash
grep -n 'h00' .github/workflows/pages.yml || echo "no h00 reference"
grep -rn 'deploy-pages' .github/workflows/
```

Observed: `pages.yml` runs `uv run equity monitor --output-json site/health-report.json || true` (line 32) and
`uv run equity dashboard build --output-dir site` (line 35), and has no `h00` reference. Exactly two
`deploy-pages` steps exist: `pages.yml:54` (SHA-pinned, `# v4`) and `catalog-deploy.yml:51` (`actions/deploy-pages@v5`,
unpinned) — the latter triggers on pushes to `main` touching `data/catalog.jsonl` or `docs/catalog/**` and uploads
`docs/catalog/dist` to the same `github-pages` environment. The one-workflow/one-artifact requirement is unmet.

### 3.3 Claim 3 — `total_return` has no working path

Status `CONFIRMED` at all three seams.

```bash
sed -n '204,206p' src/equity_lake/storage/lake_reader.py
sed -n '130,136p;599p' src/equity_lake/backtesting/data_loader.py
sed -n '47,51p' src/equity_lake/cli/commands/analysis.py
grep -n 'total_return' docs/user-guide/backtesting.md | head -3
```

Observed: `lake_reader.py:205-206` raises `ValueError("ADR-0015 supports split-only adjustment; dividends are
excluded")`; `data_loader.py:130-136` accepts the literal in `AdjustmentMode`/`_ADJUST_METHODS` and line 599 defers
to that raise; the CLI refuses `--adjust total_return` with `raise typer.Exit(1)`; `docs/user-guide/backtesting.md:24`
says cash-dividend adjustment is not implemented. The mode exists only as a surface.

### 3.4 Claim 4 — R00 measurement evidence cannot be located

Status `CONFIRMED`; the plan's `evidence-unverified` posture is the correct one.

```bash
grep -rniE 'planted|null[- ]control|leakage[- ]control|scale (report|evidence|run)|5[/ ]50[/ ]200' docs/ --include=*.md \
  | grep -vE '20261122-01-residual|20261102-01-portfolio'
find data/research data/findings -maxdepth 3 \( -iname '*r00*' -o -iname '*planted*' -o -iname '*scale*' -o -iname '*leak*' \)
```

Observed: exactly two hits, and neither is a report — `docs/handoffs/20261121-01-gap-accounting.md:9` (which
*states* the evidence is unverified) and `docs/developer/architecture/research-protocols.md:24` (which lists the
protocol *knobs* `purge_days`, `embargo_days`). The `find` returned nothing. `data/research/` holds only H00 output,
four protocol manifests and the R02 bundle; `data/findings/` holds older arena artifacts (per-strategy result
directories, `attempts/`, `runs/`) and no R00 control or 5/50/200-instrument scale run. Shipping freeze/refuse
controls in `backtesting/protocol.py` is not measurement evidence.

### 3.5 Claim 5 — R02's interval does not cover its point estimate

Status `CONFIRMED` as a contrast inside one published document; **no defect is declared**. Reproduction of the
estimand belongs to RS-R02-D1.

```bash
sed -n '22,33p' docs/reports/20261121-r02-result.md
```

Observed: the test-fold table reports primary net return −9.99% and technical-rule net return −7.20%, i.e.
−2.79 pp — the reported paired difference reproduces from the table's own arithmetic. The adjacent bullet reports
the same quantity with a 95% block-bootstrap interval of [−0.42 pp, +0.31 pp], which excludes −2.79 pp, and calls
the interval "not evidence of superiority". So the point estimate and the interval cannot both describe the same
estimand as published. The report publishes no bootstrap samples, so this register records the inconsistency and
stops there: estimand, aggregation, units and interval construction stay unverified until RS-R02-D1 reproduces them.

### 3.6 Claim 6 — Markdown lint is not recursive

Status `CONFIRMED`.

```bash
# pre-stage Makefile behaviour (what `make lint-md` ran)
uv run pymarkdown -d md013 scan README.md docs; echo "exit=$?"
# the gate this stage installs
uv run pymarkdown -d md013 scan -r README.md docs; echo "exit=$?"
make lint-md; echo "exit=$?"
```

Observed: the non-recursive scan exits 0 with **zero output lines** — it lints `README.md` and silently skips the
`docs` directory, so it covered almost nothing. The recursive scan exits 1 with **314** finding lines across 35
files. `make lint-md` now exits 0 because the tree matches its reviewed baseline (§6, §7). The fresh reproduction is
byte-identical to the draft's ephemeral capture after prefix normalisation: both normalise to sha256
`cd92e5462ac10a413a738959a6e94b902f2748fae190229c379f12feeb1e558e` (§9).

### 3.7 Claim 7 — research artifacts and the private ledger root

Status `CONFIRMED` against the operator checkout; one wording nuance for fresh checkouts.

```bash
find data/research -maxdepth 2 -mindepth 1 | sort
test -e data/portfolio && echo present || echo absent
```

Observed: `data/research/` contains `h00/` (three-file sanitized bundle), `protocols/` (four manifests) and `r02/`
(`baselines.json`, `metrics.json`, `predictions.parquet`, `protocol.json`, `report.md`, `run.json`) — nothing else.
`data/portfolio/` is absent, so the private ledger has still never been materialised here.

Nuance: in a fresh git worktree or clone, `data/research/` does not exist at all, because `data/*` is ignored and
runtime data is never copied. "Holds only h00/r02/protocols" is therefore a statement about the operator
checkout, not about a fresh clone; both readings agree that no R00 evidence is tracked.

## 4. Recursive lint baseline — classification of the 314 findings

Captured with `uv run pymarkdown -d md013 scan -r README.md docs` (exit 1, 314 lines, 35 files). The machine-readable
form keyed by (file, rule, count) is `config/markdown-lint-baseline.json`; this section is the human classification
the plan requires so the debt is itemised rather than hidden. Nothing here is fixed by RS-Q00.

### 4.1 By rule

| Rule | Name | Count | Nature of the debt |
| --- | --- | ---: | --- |
| MD022 | blanks-around-headings | 155 | heading glued to the text above or below; mechanical to fix |
| MD031 | blanks-around-fences | 55 | code fence glued to a paragraph or list |
| MD040 | fenced-code-language | 35 | fence without an info string |
| MD024 | no-duplicate-heading | 17 | repeated section names inside archive/plan documents |
| MD032 | blanks-around-lists | 13 | list glued to surrounding text |
| MD036 | no-emphasis-as-heading | 10 | bold line used as a heading |
| MD026 | no-trailing-punctuation | 8 | heading ends with a period |
| MD004 | ul-style | 8 | mixed bullet markers inside one list |
| MD029 | ol-prefix | 5 | ordered-list numbering style |
| MD041 | first-line-heading | 3 | file does not open with a top-level heading |
| MD011 | no-reversed-links | 3 | link whose brackets and parentheses are swapped |
| MD034 | no-bare-urls | 1 | URL not in a link or code span |
| MD001 | heading-increment | 1 | heading level jumps by more than one |

### 4.2 By file

`·` means the rule is clean for that file.

| File | Total | MD001 | MD004 | MD011 | MD022 | MD024 | MD026 | MD029 | MD031 | MD032 | MD034 | MD036 | MD040 | MD041 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `docs/reports/equity-data-sources-analysis-2026.md` | 63 | · | · | · | 36 | · | 8 | · | 15 | · | · | 1 | 3 | · |
| `docs/developer/architecture/ARCHITECTURE.md` | 46 | · | · | · | 24 | · | · | · | 13 | · | · | · | 9 | · |
| `docs/archive/CONCERNS.md` | 37 | · | · | · | 33 | · | · | · | 2 | 1 | · | · | · | 1 |
| `docs/archive/20260830-08-p3-ml-backtest-integrity.md` | 14 | · | · | · | 13 | · | · | · | · | 1 | · | · | · | · |
| `docs/plans/20260804-integration-recon.md` | 14 | · | 1 | · | 12 | · | · | · | 1 | · | · | · | · | · |
| `docs/plans/20260623-project-streamlining.md` | 13 | · | · | · | · | · | · | · | 12 | · | · | · | 1 | · |
| `docs/archive/20260830-03-p0-storage-validation-correctness.md` | 10 | · | 3 | · | 6 | · | · | · | · | 1 | · | · | · | · |
| `docs/archive/technical_roadmap.md` | 10 | · | · | · | · | 6 | · | · | · | 1 | · | · | 3 | · |
| `docs/plans/20260711-operational-contracts-implementation-handoff.md` | 10 | · | · | · | · | 9 | · | 1 | · | · | · | · | · | · |
| `docs/archive/20260830-02-p0-ingestion-correctness.md` | 9 | · | · | 1 | 7 | · | · | · | · | 1 | · | · | · | · |
| `docs/plans/20260615-medallion-architecture-migration.md` | 9 | · | · | · | · | · | · | · | 2 | · | · | · | 7 | · |
| `docs/archive/20260830-04-p1-safety-rails.md` | 8 | · | · | · | 7 | · | · | · | · | 1 | · | · | · | · |
| `docs/plans/20260811-phase2c-rag-agent-handoff.md` | 8 | · | · | · | 5 | · | · | · | 1 | 1 | 1 | · | · | · |
| `docs/archive/20260830-09-p4-monitoring-api-catalog.md` | 6 | · | · | · | 5 | · | · | · | · | 1 | · | · | · | · |
| `docs/plans/20260614-unstructured-data-ingestion-design.md` | 6 | · | · | · | · | · | · | · | 1 | · | · | · | 5 | · |
| `docs/plans/20260805-phase2-handoff.md` | 6 | · | 1 | · | 5 | · | · | · | · | · | · | · | · | · |
| `docs/plans/20260806-phase2a-execution-handoff.md` | 6 | · | · | 1 | · | · | · | · | 4 | · | · | · | 1 | · |
| `docs/plans/20260719-project-cleanup.md` | 5 | · | · | · | · | · | · | · | · | · | · | 4 | · | 1 |
| `docs/plans/20261006-dag-pipeline-03-incremental-contracts.md` | 5 | · | 1 | · | · | · | · | · | · | 4 | · | · | · | · |
| `docs/archive/parallel-ingestion.md` | 4 | · | · | · | · | 1 | · | · | · | · | · | · | 2 | 1 |
| `docs/handoffs/20261107-03-e01-6-implementation-pickup.md` | 3 | · | · | · | · | · | · | · | · | · | · | 3 | · | · |
| `docs/archive/20260830-05-p2-market-vocabulary.md` | 2 | · | · | · | 1 | · | · | · | 1 | · | · | · | · | · |
| `docs/archive/20260830-07-p3-dry-consolidation.md` | 2 | · | · | 1 | 1 | · | · | · | · | · | · | · | · | · |
| `docs/decisions/0018-private-watchlist-events-and-delivery.md` | 2 | · | · | · | · | · | · | 2 | · | · | · | · | · | · |
| `docs/developer-guide/project-structure.md` | 2 | · | · | · | · | · | · | · | · | · | · | · | 2 | · |
| `docs/plans/20260804-pre-phase1-hygiene.md` | 2 | · | · | · | · | · | · | · | 2 | · | · | · | · | · |
| `docs/plans/20260821-arch-cleanup-phase3-5-handoff.md` | 2 | · | · | · | · | · | · | · | 1 | · | · | · | 1 | · |
| `docs/reports/20261117-h00-sanitized-demo-lake-page-review.md` | 2 | · | · | · | · | · | · | · | · | · | · | 2 | · | · |
| `docs/user-guide/backtesting.md` | 2 | · | · | · | · | · | · | 2 | · | · | · | · | · | · |
| `docs/developer/architecture/STACK.md` | 1 | · | · | · | · | · | · | · | · | · | · | · | 1 | · |
| `docs/finding/20261016-repository-review-and-equity-alerts.md` | 1 | 1 | · | · | · | · | · | · | · | · | · | · | · | · |
| `docs/handoffs/20260910-01-source-hygiene-tier0.md` | 1 | · | · | · | · | 1 | · | · | · | · | · | · | · | · |
| `docs/handoffs/20261116-01-next-agent-d01-landing.md` | 1 | · | 1 | · | · | · | · | · | · | · | · | · | · | · |
| `docs/plans/20261006-dag-pipeline-01-correctness.md` | 1 | · | 1 | · | · | · | · | · | · | · | · | · | · | · |
| `docs/plans/20261006-dag-pipeline-02-outcomes-and-coverage.md` | 1 | · | · | · | · | · | · | · | · | 1 | · | · | · | · |
| **Total** | **314** | **1** | **8** | **3** | **155** | **17** | **8** | **5** | **55** | **13** | **1** | **10** | **35** | **3** |

`README.md` is clean: all 314 findings live under `docs/`, and 15 of the 35 files carry a single finding each.

## 5. The gate that replaces the non-recursive scan

- `src/equity_lake/devtools/markdown_lint.py` — the whole gate: one recursive scan, one baseline comparison,
  `--check` and `--update-baseline` modes, module CLI in the style of `devtools/sync_schedule.py`.
- `config/markdown-lint-baseline.json` — the reviewed baseline: `file -> rule -> count`, total 314, sorted, with the
  `scan` arguments that produced it.
- `Makefile` — `lint-md` runs the module in `--check` mode; `lint-md-baseline` is the only way to rewrite the
  baseline. Both are in `.PHONY` and in `make help`.

Ratchet semantics, all verified by `tests/unit/test_markdown_lint_gate.py`:

- a finding in a **nested** docs subdirectory that the baseline does not record fails the gate;
- a recorded file that gains one more instance of a recorded rule fails (`rose 1 -> 2`);
- a recorded row whose finding no longer reproduces fails as `STALE`, so fixing documentation without shrinking the
  baseline fails too — broad cleanup cannot happen silently behind a stale list;
- an unchanged tree passes, including a zero-finding tree;
- `--check` never writes the baseline; only the explicit `--update-baseline` (a separate make target, never
  invoked by `lint-md`) may rewrite it;
- no mode argument means exit 2 — the gate can never run as a silent no-op;
- unparsable scanner output and a missing baseline both fail closed.

Changed-file linting is untouched and stays mandatory: `.github/workflows/quality.yml` still runs `pymarkdown`
directly over named Markdown files (asserted by a test), and because the repo gate is now recursive, every
`docs/**` file a change touches is scanned by `make lint-md` as well. The ratchet does not excuse a changed
file: any new finding on it raises its count.

Deliberate limits, stated so they are not mistaken for coverage: MD013 (line length) stays excluded exactly as the
pre-stage command excluded it, and MD033 stays enabled with its narrow `pyproject.toml` element allowlist. The
baseline is keyed by `(file, rule, count)`, not by line, so a finding that merely moves within an unchanged file is
tolerated; a finding that is added is not.

## 6. Mutation and restore record

Serialised, from a `/tmp` copy (`rsync` of the branch excluding `.venv`, `.git`, `data`, caches), with the planted
violation `docs/audits/deep/planted-violation.md` (`## This heading ends with punctuation.`, i.e. one MD026, two
directories below `docs/`). Gate invocation in the copy:
`PYTHONPATH=$MUT/src <branch>/.venv/bin/python -m equity_lake.devtools.markdown_lint --check`.

| Run | Tree state | Exit | Observed |
| --- | --- | ---: | --- |
| 1 | recursion intact, planted file unrecorded | 1 | `NEW   docs/audits/deep/planted-violation.md: MD026 x1 is not in the reviewed baseline` (315 findings, 36 files) |
| 2 | mutation A: `-r` deleted from `SCAN_ARGS` | 1 | planted file invisible (`0 findings in 0 files`); 72 `STALE` rows fail instead — the stale guard, not recursion, caught it |
| 2b | mutation A + baseline refreshed in the copy | 0 | `Wrote 0 findings in 0 files` — the planted finding is now unobserved by construction |
| 2c | mutation A, baseline refreshed | 0 | **gate wrongly passes** with the planted nested violation still present |
| 3 | mutation A restored (`diff -q` clean) | 1 | the same `NEW ... planted-violation.md` finding is caught again |
| 4 | mutation B: body of `growths()` replaced by `return found` (`-r` intact) | 0 | **gate wrongly passes**: reports `315 preexisting findings ... 0 new or expanded`, so the planted nested finding escapes the comparison |
| 5 | mutation B restored (`diff -q` clean) | 1 | the same `NEW ... planted-violation.md` finding is caught again |

Both mutations were single-rule edits in the `/tmp` copy; the branch file was never modified (`git status` showed
the worktree file unmodified during and after the runs). Restore proof in both cases:
`diff -q $BRANCH/src/equity_lake/devtools/markdown_lint.py $MUT/src/equity_lake/devtools/markdown_lint.py` → no
output, then `&& echo identical`.

Run 2's outcome is recorded honestly: deleting recursion alone does **not** produce a green gate, because the tree
no longer reproduces 72 baselined rows and the stale check fails. The wrongly-passing case requires the baseline to
be regenerated in the mutated tree (2b/2c) — which is exactly why regenerating is a separate, reviewed target.

## 7. Corrections, limits and residuals

- No plan claim failed to reproduce. Claim 7 needed one nuance (§3.7): in a fresh worktree `data/research/` is
  absent rather than "only h00/r02/protocols", because runtime data is ignored and never copied.
- Claim 5 is a document-internal inconsistency, not a proven R02 bug. The point estimate −2.79 pp reproduces from
  the published table (−9.99 − (−7.20)); the interval's estimand does not. RS-R02-D1 owns reproduction.
- `docs/developer-guide/devtools.md` enumerates devtools modules and now under-describes the package (it does not
  mention `markdown_lint`). That guide is outside this stage's edit scope, so the omission is recorded here and
  returned as a residual rather than edited in this branch.
- The 314 findings and their classification are debt frozen for later stages: no fix, no rule disabled, no
  `.pymarkdown` config added. Broad cleanup is deliberately not hidden inside RS-Q00.
- Rollback: revert this stage's commit. The Makefile returns to the non-recursive scan and the two new files
  disappear; no data, workflow, or catalog artifact is touched by this stage.

## 8. Digests

| Artifact | sha256 |
| --- | --- |
| normalised 314-line capture (sorted, repo-relative) | `cd92e5462ac10a413a738959a6e94b902f2748fae190229c379f12feeb1e558e` |
| `config/markdown-lint-baseline.json` | `1ff8dfc73a9132e07a631b00dda9511fba15f1bcb6f1754721f6d1d5af098118` |
| `src/equity_lake/devtools/markdown_lint.py` | `6bc816b1888b3d782769548e5686554895f5be8d78817d2de74e163dcc17e8f6` |
| `tests/unit/test_markdown_lint_gate.py` | `d29a4fd5fa766551c6bb7d394280210e671e29f38b99d651dc65588c8b9c88a0` |

The draft's ephemeral capture `/tmp/residual-recursive-markdown-baseline.log` and this stage's fresh capture
normalise to the same digest, so the plan's histogram (MD022 155, MD031 55, MD040 35, MD024 17, MD032 13, MD036 10,
MD026 8, MD004 8, MD029 5, MD041 3, MD011 3, MD034 1, MD001 1) is confirmed rather than inherited.
