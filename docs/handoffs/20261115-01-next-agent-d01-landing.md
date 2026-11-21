# Next Agent — D01 landing after the second lake root

**Recorded:** 2026-11-15

**Superseded 2026-11-16 by
[20261116-01](20261116-01-next-agent-d01-landing.md)**, which carries the current state (ADR-0031 shipped,
ledger schema v2, suites at 2570/16). This record stays authoritative for the E01-6/P03 history
and for the round-1/round-2 review narrative below.

**Status:** The second lake root (ADR-0030) and the ledger split contract (ADR-0029 Part II)
are **implemented, reviewed (`PASS` at round 3) and owner-accepted (2026-11-15)**;
ADR-0028/0029/0030 are **Accepted**. Nothing is committed and nothing is pushed. The
remaining work is **gated**. This document supersedes
[20261107-05](20261107-05-next-agent-remaining-tasks.md) as the entry point; that record
stays authoritative for the earlier E01-6/P03 history it describes.

## 1. Verify state before anything else

```bash
git log --oneline -6          # last commit is ad74703; all work below is uncommitted
git status --porcelain        # expect the doc/src/test set listed in §2, nothing stray
uv sync --group dev --group backtesting --group dashboard
uv run pytest -q              # 2570 passed, 16 skipped, 0 failed (~9 min)
uv run pytest -n auto -q      # must agree exactly (~11 min)
```

`uv sync --group dashboard` alone **removes** the backtesting group and changes the skip
count (16 → 39). Both suites must agree; if one disagrees, read the log file
(`> /tmp/suite.log 2>&1`) rather than piping through `tail`.

## 2. What is shipped (do not rebuild)

| Work | Where | Notes |
| --- | --- | --- |
| E01-6 stages 1-3 | `backtesting/evidence.py`, `core/price_provenance.py`, `storage/delta.py`, `ingestion/writers.py`, `backtesting/data_loader.py` | protocol v4, nullable row-bound origin envelope, read-seam classification; real rows report `unknown` |
| P03 stages 1-4 + follow-ups | `dashboard/panels.py`, `pages.py`, `streamlit_app.py`, `core/settings.py` | loopback-gated console; populated-ledger no-write proof; tracked per-instrument coverage |
| ADR-0028 | `docs/decisions/0028-*.md` | accepted: us_equity/yfinance, free tier, local-only / no redistribution, `data/lake_real/` |
| D01 probe | `docs/plans/20261114-01-d01-source-probe.md` | 1,260 rows, 252/252 sessions, 7 requests, USD 0.00; **falsified "explicitly unadjusted"** — vendor OHLC/volume are split-back-adjusted. Artifacts in `data/d01/probe-2026-11-14/` (git-ignored; digests in the doc) |
| ADR-0030 | `docs/decisions/0030-*.md`, `core/paths.py`, `ingestion/*`, `monitoring/health.py`, CLI | second root `data/lake_real/`, `resolve_lake_root`, `--lake-dir` on `ingest`/`backfill`/`corporate-actions`/`monitor`, alias + containment + inside-canonical + market-scope guards; 16 tests |
| ADR-0029 Part II | `portfolio/models.py`, `csv_import.py`, `projections.py`, `marks.py`, `cli/commands/portfolio.py` | `SPLIT` = ratio in `quantity`, total basis unchanged, exact-reciprocal replacement that must follow the original in its own account, split-aware oversell check, pre-split mark refused; 20 tests. **Stop condition lifted 2026-11-16:** persistence landed with ADR-0031 (schema v2), so a split is now appended, reloaded and replayed end to end in `tests/unit/test_portfolio_store_migration.py` |
| ADR-0031 (P00-4) | `docs/decisions/0031-*.md`, `portfolio/store.py`, `cli/commands/portfolio.py` | ledger schema **v2** (`SPLIT` in the `event_type` `CHECK`), a v1 ledger refused on read and write with a message naming the upgrade, `verify_ledger_file` (integrity, version, row count, record digest, SHA-256), the verified exact-file backup, `migrate_ledger` (report-only default) and `equity portfolio migrate --apply`, and a constraint rejection reported as a named schema/contract mismatch; 16 store tests |

`git diff --name-only` is large because three stages landed in one turn (P03 follow-ups,
ADR-0030 root, ADR-0029 splits). That is expected, not scope creep — check each file against
the table above.

## 3. Open queue (dependency-ordered)

| # | Task | Gate before starting |
| --- | --- | --- |
| 1 | **Round-3 confirmation** | **done: `PASS` 2026-11-15** (independent reviewer, mutation-proofed) and **owner-accepted 2026-11-15** — spec §10 carries the acceptance. Read it as: the root and the *in-memory* split contract are closed; persisting a split is blocked on row 4 |
| 2 | **D01 landing authorization → ADR-0029 Part I** | **start here**, and the run needs an owner authorization first: one scoped run, `us_equity` only, into `data/lake_real/` (prices + corporate actions + coverage; Gold absent, no source-issued proof). Then Part I: multiply OHLC by the cumulative split factor after each row's date, divide volume, **refuse if split actions are missing**. Not started |
| 3 | **Scoped D01 landing run** | after (2). Bronze prices + corporate actions + coverage under `data/lake_real/`; Gold stays absent (no source-issued proof). Network is authorized only for this bounded run |
| 4 | **P00-4 — implemented (ADR-0031 accepted 2026-11-16)** | schema v2 + verified backup + `portfolio migrate` landed, with the split-persistence defect closed. Carve-out, explicit: **no restore command** (documented as verify-then-atomic-copy) and **no compaction** (ADR decision 5), and **no migration has been run** — no ledger exists in this checkout, so running one against a real ledger needs its own instruction |
| 5 | **R00 → R01 → R02** | R00 harness controls are offline and can start; R01 needs (3) plus a frozen universe/window/holdout/costs |
| 6 | **H00 sanitized public hosting** | deferred; needs a **licensing + privacy** review, not privacy alone (local-only / no redistribution) |

## 4. Traps that cost time here

- **A model-level suite does not cover the storage boundary.** The split contract passed 20
  tests and three review rounds while `store.py`'s SQLite `CHECK` still rejected `SPLIT`: the
  tests exercised validate/replay/marks, never `LedgerStore.append`. That gap is now closed by
  `tests/unit/test_portfolio_store_migration.py`; keep the habit when an event kind, column or
  constraint changes — prove it through a real append against a temporary ledger, not only
  through the in-memory replay. A constraint is schema, so widening it is a migration.
- **A schema `CHECK` is the schema.** `CREATE TABLE IF NOT EXISTS` never alters an existing
  table, so editing the constant fixes only new databases. Bump the version, rebuild the table
  explicitly, and refuse the old version by name (`equity portfolio migrate`).
- **A mutation run can leave the tree mutated.** A timeout killed the script mid-mutation and
  left `return None  # MUTATION` in `cli/_app.py`. Always back up to `/tmp`, restore in a
  `finally`, then `grep -rn MUTATION src/` and `diff -q` the restored file. Treat any timeout
  as "tree may be dirty", not "test failed".
- **A mutation that removes a CLI guard can trigger a real network fetch.** The `--lake-dir`
  test originally fell through to a live `equity ingest` when its rule was removed and hung
  for 30 minutes. Pass an invalid `--date` so the falsifier stays offline.
- **Target the right falsifier.** The merge-isolation test calls `merge_delta` directly, so it
  does **not** guard `upsert_dataset`'s threading; `test_upsert_dataset_threads_the_root_and_never_defaults_it`
  does. A mutation that "survives" may just be aimed at the wrong test.
- **`tests/` is excluded from the project mypy config** (`files = ["src"]`). A single-file
  `mypy tests/...` emits `import-untyped` noise; the repo convention is an inline
  `# type: ignore[import-untyped]`. The authoritative gate is bare `uv run mypy`.
- **Lens/pyright advisories go stale after edits.** A `No parameter named "lake_dir"` error
  survived several turns after `health.py` gained the parameter; runtime `inspect.signature`
  and bare mypy were already clean. Re-probe before "fixing" a stale finding.
- **Reviewer per stage, formal verdict** (`PASS` / `NEEDS-REVISION` / `BLOCKED`). Round 1
  found real defects in both stages; do not skip a round because the diff looks small. Never
  record a verdict you did not receive.
- **Catalog byte identity only matters when a schema constant changed.** No catalog change
  here; do not hand-edit `data/catalog.jsonl` ever.
- **No real row may enter `data/lake/`.** The demo lake's coverage proof is
  `synthetic_demo_seed` + `complete` and would validate real rows; that is why the second root
  exists.

## 5. Paste-ready prompts

**Round-3 confirmation (spent 2026-11-15 — verdict `PASS` for both stages; kept for provenance):**

```text
Adversarial round 3 on the round-2 fix delta only (paths.resolve_lake_root inside-canonical
refusal, cli/_app.py _require_real_root_scope price-market scoping, portfolio/models.py
split_reversal_issues per-account keying, and the four new wiring tests). Verify each rule is
mutation-proofed and that the docs match: a root inside data/lake/ is refused, the enrichment
identifiers macro/us_news are refused cleanly on a real root instead of raising ValueError,
the scope guard fails closed from ingest/backfill/corporate-actions, the orchestrator's
skipped_existing decision reads the selected root, and a reversal's verdict does not depend
on CSV row order. First line: PASS / NEEDS-REVISION / BLOCKED.
```

**ADR-0029 Part I (after a landing authorization):**

```text
Implement ADR-0029 Part I only: reconstruct raw prices at ingest for us_equity into
data/lake_real. Multiply OHLC by the cumulative split factor after each row's date, divide
volume, refuse the ingest when split actions are missing, keep adj_close as a cross-check,
leave origin at unknown, no schema/catalog/reader change. Prove: no split seam remains,
re-adjusting reproduces the vendor relationship, re-ingest is byte-identical.
```

**P00-4:**

```text
Draft an ADR only. P00-4: backup/restore, migration and compaction policy for the private
SQLite ledger, including verification and failure modes. No implementation, no migration run.
```

## 6. Definition of done for any future stage

Relevant tests pass in **both** serial and parallel runs; the `AGENTS.md` change matrix is
satisfied; public behaviour is documented; the catalog is regenerated only if a schema/DAG
constant changed; every new rule has a mutation proof; the stage has a reviewer verdict; and
the owner has accepted it. Acceptance of a policy ADR is **not** authorization to implement it.
