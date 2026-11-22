# Gap accounting — what actually remains (2026-11-21)

**Recorded:** 2026-11-21
**Supersedes:** [20261120-01-complete-remaining-stages](20261120-01-complete-remaining-stages.md)
as the entry point. That record closed its own six stages; this one accounts for what is
left afterwards and corrects the documentation that still pointed at spent work.

**Result: one open item, and it is a decision, not work — H00 publication needs an explicit
owner approval. No implementation stage is open, nothing is running, nothing is queued.**

## 1. Verified state (re-derived, not copied)

```bash
git status -sb                          # clean; main...origin/main at 23514f8
uv run equity portfolio --help          # import/migrate/restore/compact/positions/summary/reconcile
find data/lake -type f | wc -l          # 1413 (demo root, untouched)
find data/lake_real -type f | wc -l     # 65699 (real root)
sha256sum data/catalog.jsonl            # 537bf5fbf8867393... (unchanged)
ls data/portfolio                       # absent
```

| Fact | Value |
| --- | --- |
| Branch | `main` = `origin/main` = `23514f8`; nothing staged, nothing unpushed |
| Demo lake | 1,413 files, catalog `537bf5fb…` — never written by real-root work |
| Real root | 11 tickers, 58,653 price rows, 1,015 action rows, 9 weak coverage proofs |
| Research artifacts | `data/research/h00/`, `protocols/`, `r02/` present (git-ignored) |
| Private ledger | `data/portfolio/` absent — P00-4's runtime paths are unexercised |

## 2. The one open gap

**H00 publication.** The page is built, reviewed `PASS`, and adopts the eight owner answers
in [20261121-h00-owner-decisions](../reports/20261121-h00-owner-decisions.md). Decision 3
makes publication a **separate explicit approval**; `.github/workflows/pages.yml` is
`workflow_dispatch:` only, so nothing can deploy without a person dispatching it. Building
and previewing are local loopback operations
([guide](../user-guide/20261121-h00-sanitized-demo-lake-page.md)).

Anything else that looked like a gap is not one:

| Looked open | Reality |
| --- | --- |
| P00-4 restore / compaction | **Shipped 2026-11-17** — `restore_ledger`, `compact_ledger`, the `equity portfolio restore` and `equity portfolio compact` commands, 18 tests. The older migration record's "not implemented" note was stale |
| Missing `EQUITY_CONSOLE__PRIVATE_DIR`, `EQUITY_MONITORING__*` in `.env` | Optional by contract: `core/settings.py` defaults them (2 / 5.0). Unset `private_dir` renders the private panels `unavailable` and attempts no private read |
| Empty provider secrets (`AWS_*`, `STOCKTWITS_*`, `REDDIT_*`, `FRED`, `FINNHUB`, `DEEPSEEK`, `WANDB`) | By design — no provider or spend authorization exists. Those commands are simply unavailable locally |
| E01 "partial" | Closed: E01c plus E01-6's persisted row-bound origin envelope (ADR-0027). Rows without evidence still report `unknown`, which is correct |
| D01, R00, R01, R02, P02, P03 | All closed and reviewed; R02 was negative and promoted nothing |

Deliberately not open, so no agent should start them: model refits, scheduled ingestion,
real-ledger migration/restore/compaction runs, any point-in-time claim on vendor-stamped
rows, and promotion of the R02 experiment.

## 3. Environment deltas, per checkout (not defects)

| Delta | Consequence | Handling |
| --- | --- | --- |
| `.gitignore:23` is `data/*` | A fresh clone has **no lake**, no artifacts, no ledger | Re-land per machine; the demo root comes from `equity demo seed`, the real root from the ADR-0028/0030 `equity widen`/`backfill` path |
| Gate dependency groups | Syncing `--group dashboard` alone drops the backtesting group and changes skip counts 16 → 39 | `uv sync --group dev --group backtesting --group dashboard` before any gate |
| `data/portfolio/` absent | Private ledger paths are mechanism-only here | First real ledger is the honest exercise of import → migrate → restore → compact |
| Secrets unset | Provider, S3, macro, news and LLM commands unavailable | Fill `.env` only under a recorded authorization |

## 4. Documentation corrected in this pass

| Document | Was | Now |
| --- | --- | --- |
| `plans/20261102-01-…-spec.md` header | "whole E01 still needs E01-6; P02 is next" | Every stage closed except H00 publication |
| Same, §2 queue | 2026-11-06 statuses (E01-6 open, D01/R01/R02 gated, H00 deferred) | Current statuses for all nine rows, plus a dated status paragraph |
| Same, §2 §10 | "the provenance upgrade needs a persisted price-source marker that does not exist yet" | E01-6 delivered the marker (ADR-0027) |
| Same, §10 P00-4 migration record | "restore and compaction are NOT implemented" | Dated pointer to the 2026-11-17 restore/compaction record; runtime residual kept |
| Same, §10 | — | New dated entry *Repository gap accounting — 2026-11-21* |
| `docs/README.md` | Nov-07 continuation and a Start Here bullet pointing at the spent E01-6 pickup | New 2026-11-21 current-status section, Nov-07 marked historical, Start Here repointed |
| `getting-started/quickstart.md` | Install only | Fresh-clone deltas: combined sync, git-ignored `data/`, optional-key semantics |

No ADR was needed: nothing architectural, stored or public-API changed. No code, schema,
catalog or data file was touched, so the code gates are unaffected by this record.

## 5. Next entry gate

- **To publish H00:** an explicit instruction naming it as publication approval. Then
  dispatch `pages.yml` manually, confirm the artifact still passes its own negative scan,
  and record the run in spec §10.
- **To do anything else:** nothing is waiting. A new product stage needs its own bounded
  authorization under the spec's §1 before any code moves.
