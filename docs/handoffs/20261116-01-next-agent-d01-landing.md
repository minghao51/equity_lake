# Next Agent — D01 landing after the ledger v2 and the second lake root

**Recorded:** 2026-11-16

**Status:** **Superseded 2026-11-17** by [20261117-01](20261117-01-post-d01-landing.md) — its §3 queue rows 1-3 (landing authorization, ADR-0029 Part I, the landing run) are spent.

ADR-0028/0029/0030/0031 are **Accepted**. Everything they authorize has shipped and
survived three review rounds on the root/split stages plus a dedicated P00-4 stage: the second
lake root, the ledger `SPLIT` contract **including persistence**, and the ledger schema v2 with
its verified backup and explicit upgrade. Nothing is committed and nothing is pushed. Everything
left is **gated** on an owner decision.

This document **supersedes** [20261115-01](20261115-01-next-agent-d01-landing.md) as the entry
point. That record stays authoritative for the E01-6/P03 history and for the round-1 and round-2
review narrative it captures; it also carries the pitfalls that produced those findings.

## 1. Verify state before anything else

```bash
git log --oneline -6          # last commit is ad74703; all work below is uncommitted
git status --porcelain        # expect 46 entries: 36 modified + 10 untracked (this file included)
uv sync --group dev --group backtesting --group dashboard
uv run pytest -q              # 2570 passed, 16 skipped, 0 failed (~9 min)
uv run pytest -n auto -q      # must agree exactly (~13 min)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # 189 files
```

`uv sync --group dashboard` alone **removes** the backtesting group and changes the skip count
(16 → 39). Both suites must agree; if one disagrees, write the log to a file
(`> /tmp/suite.log 2>&1`) instead of piping through `tail`, or use `--junitxml`.

## 2. What is shipped (do not rebuild)

| Work | Where | Notes |
| --- | --- | --- |
| E01-6 stages 1-3 | `backtesting/evidence.py`, `core/price_provenance.py`, `storage/delta.py`, `ingestion/writers.py`, `backtesting/data_loader.py` | protocol v4, nullable row-bound origin envelope, read-seam classification; real rows report `unknown` |
| P03 stages 1-4 + follow-ups | `dashboard/panels.py`, `pages.py`, `streamlit_app.py`, `core/settings.py` | loopback-gated console; populated-ledger no-write proof; tracked per-instrument coverage |
| ADR-0028 | `docs/decisions/0028-*.md` | accepted: `us_equity`/yfinance, free tier, local-only / no redistribution, `data/lake_real/` |
| D01 probe | `docs/plans/20261114-01-d01-source-probe.md` | 1,260 rows, 252/252 sessions, 7 requests, USD 0.00; **falsified "explicitly unadjusted"** — vendor OHLC/volume are split-back-adjusted. Artifacts in `data/d01/probe-2026-11-14/` (git-ignored; digests in the doc) |
| ADR-0030 | `docs/decisions/0030-*.md`, `core/paths.py`, `ingestion/*`, `monitoring/health.py`, CLI | second root `data/lake_real/`; `resolve_lake_root` refuses an escape, `data/` itself, a symlink/`..` alias **and** a root inside the canonical lake; `--lake-dir` on `ingest`/`backfill`/`corporate-actions`/`monitor`; the market guard scopes on **price markets only** and refuses enrichment identifiers (`macro`, `us_news`, …) cleanly. 16 tests |
| ADR-0029 Part II (ledger) | `portfolio/models.py`, `csv_import.py`, `projections.py`, `marks.py`, `cli/commands/portfolio.py` | `SPLIT` = ratio in `quantity`, total basis unchanged, exact-reciprocal replacement that must follow the original **in its own account**, split-aware oversell check, pre-split mark refused. 20 tests |
| ADR-0031 (P00-4) | `docs/decisions/0031-*.md`, `portfolio/store.py`, `cli/commands/portfolio.py` | ledger schema **v2** (`SPLIT` in the `event_type` `CHECK`); a v1 ledger is refused on read and write naming `equity portfolio migrate`; `verify_ledger_file` (integrity, version, row count, record digest, SHA-256); verified exact-file backup; `migrate_ledger` (report-only default) + `equity portfolio migrate --apply`; constraint rejection reported as a schema/contract mismatch. 17 tests |
| Catalog / Settings / deps | — | unchanged: no schema constant, no DAG node, no catalog row, no `Settings` key, no dependency. Never hand-edit `data/catalog.jsonl` |

Explicit stop conditions carried by the shipped work:

- **No real row has been written.** `data/lake_real/` does not exist; `data/portfolio/` does not
  exist. No ingest, no migration and no landing has been run against real data.
- **No restore command and no compaction.** ADR-0031 decisions 4 and 5 are policy only; restore
  is a documented manual procedure (verify, then an atomic copy).
- **No source-issued action proof**, so Gold stays absent in the real root.

## 3. Open queue (dependency-ordered; each item gated)

| # | Task | Gate before starting |
| --- | --- | --- |
| 1 | **Owner authorization for the D01 landing** | none technical — this is the owner's decision, and it is the only thing blocking new data work. Fix the scope in writing first: `us_equity` only, an explicit ticker list, an explicit date window, target `data/lake_real/`, and "Gold absent" |
| 2 | **ADR-0029 Part I — reconstruct raw prices at ingest** | after (1), and it must land **before** the run: multiply OHLC by the cumulative split factor after each row's date, divide volume, keep `adj_close` as a cross-check, **refuse the ingest when split actions are missing**, leave origin `unknown`, no schema/catalog/reader change |
| 3 | **Scoped D01 landing run** | after (2): bronze prices + corporate actions + coverage under `data/lake_real/`, then `equity monitor --lake-dir data/lake_real`. Network is authorized only for this bounded run |
| 4 | **P00-4 remainder — restore command + compaction** | ADR-0031 decisions 4/5; **not authorized**. Needs its own instruction, and a migration/restore run needs one on top of that |
| 5 | **R00 → R01 → R02** | R00's harness controls are offline and can start now; R01 needs (3) plus a frozen universe/window/holdout/costs |
| 6 | **H00 sanitized public hosting** | deferred; needs a **licensing + privacy** review, not privacy alone (local-only / no redistribution) |

## 4. Traps that cost time here

- **A mutation run can leave the tree mutated.** A timeout once left `return None  # MUTATION` in
  `cli/_app.py`. Back up to `/tmp`, restore in a `finally`, then `grep -rn MUTATION src/` and
  `diff -q` the restored file. Treat any timeout as "tree may be dirty", not "test failed".
- **A mutation that removes a CLI guard can trigger a real network fetch.** Keep every falsifier
  offline with an invalid `--date not-a-date`, `--dry-run`, or a market the guard refuses first.
- **Target the right falsifier.** Several tests call a callee directly (the coverage read, the
  merge) and therefore cannot catch a *caller* losing an argument. A surviving mutation may just
  be aimed at the wrong test.
- **A model-level suite does not cover the storage boundary.** The split contract passed 20 tests
  and three review rounds while `store.py`'s v1 `CHECK` still rejected `SPLIT`, because no test
  appended through `LedgerStore`. `tests/unit/test_portfolio_store_migration.py` now covers that
  seam; keep the habit for any event kind, column or constraint change.
- **A schema `CHECK` is the schema.** `CREATE TABLE IF NOT EXISTS` never alters an existing
  table, so editing the constant fixes only new databases: bump the version, rebuild explicitly,
  and refuse the old version by name.
- **`tests/` is excluded from the project mypy config** (`files = ["src"]`). A single-file
  `mypy tests/...` emits import/unused-ignore noise; bare `uv run mypy` is the authoritative gate,
  and the repo convention for test imports is an inline `# type: ignore[import-untyped]` where the
  checker asks for one.
- **Advisories go stale after edits.** Two verified false positives cost time here: an
  `unreachable-except` note on the `except sqlite3.IntegrityError` branch (reachable — proven at
  runtime) and a semgrep "raw SQL" hit on a literal `PRAGMA user_version=2`. Re-probe before
  "fixing" a finding.
- **Reviewer per stage, formal verdict** (`PASS` / `NEEDS-REVISION` / `BLOCKED`), and never record
  a verdict you did not receive. An independent reviewer that wrote none of the code is what
  turned two `NEEDS-REVISION` rounds into a real `PASS`.
- **Never run `equity portfolio migrate --apply` or any ingest without an explicit instruction.**
  Report-only is the default for a reason; there is no ledger in this checkout to migrate.
- **No real row may enter `data/lake/`.** The demo lake's coverage proof is `synthetic_demo_seed`
  + `complete` and would validate real rows; that is why the second root exists.
- **The catalog is generated.** `uv run equity catalog-generate`; byte identity only matters when
  a schema or DAG constant changed.

## 5. Paste-ready prompts

**D01 landing authorization (the owner's decision — send this as its own message):**

```text
D01 landing authorization. Scope: us_equity only, tickers <LIST>, date window <START>..<END>,
target data/lake_real/, Gold absent. Authorize: (a) ADR-0029 Part I implementation first,
(b) one scoped landing run afterwards, (c) network use for that run only. Prove before writing:
a dry run that writes nothing, the corporate actions covering every split in the window, and a
refusal when an action is missing. Report afterwards: rows per session, ticker coverage,
split seams checked, and confirmation that data/lake/ is untouched and data/catalog.jsonl is
byte-identical.
```

**ADR-0029 Part I (after that authorization):**

```text
Implement ADR-0029 Part I only: reconstruct raw prices at ingest for us_equity into
data/lake_real. Multiply OHLC by the cumulative split factor after each row's date, divide
volume, refuse the ingest when split actions are missing, keep adj_close as a cross-check,
leave origin at unknown, no schema/catalog/reader change. Prove: no split seam remains,
re-adjusting reproduces the vendor relationship, re-ingest is byte-identical.
```

**R00 (offline, can start without any landing):**

```text
Implement R00 harness controls only, offline. Freeze universe, window, holdout and cost
assumptions in a dated record; refuse a run whose assumptions are absent or changed; emit a
run manifest with digests. No model fit, no ingestion, no network. Prove: a changed assumption
refuses the run, and an unchanged one reproduces identical manifests.
```

**P00-4 remainder (only with its own instruction):**

```text
Implement ADR-0031 decisions 4 and 5 only: a restore command (verify candidate, back up the
current file, atomic replace, re-verify) and compaction (VACUUM INTO preferred, post-conditions
checked). Report-only by default; never run `--apply` against a real ledger without an explicit
instruction; prove each refusal and the no-write default by test.
```

## 6. Definition of done for any future stage

Relevant tests pass in **both** serial and parallel runs; the `AGENTS.md` change matrix is
satisfied (schema → constants, validators, catalog, reader compatibility, migration note);
public behaviour is documented (user guide for a CLI change); the catalog is regenerated only if
a schema/DAG constant changed; every new rule has a mutation proof; the stage has an independent
reviewer verdict; and the owner has accepted it. Acceptance of a policy ADR is **not**
authorization to run it.
