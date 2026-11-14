# Next Agent — remaining tasks after E01-6 and P03

**Recorded:** 2026-11-07

**Status:** E01-6 (row-bound price origin) and P03 (private console) are **implemented and
reviewed**. Check `git log --oneline origin/main..HEAD` for what is committed and
`git status --porcelain` for anything still in the working tree; nothing is pushed
(`origin/main` is `231d603`). Everything below is **gated** and
needs the owner's authorization before work starts.

## 1. Verify state before anything else

```bash
git log --oneline -8          # E01-6 stages 1-3, P03 stages 1-4 on top of 231d603
git status --porcelain        # must be empty
uv sync --group dev --group backtesting --group dashboard
```

`uv sync --group dashboard` alone **removes** the backtesting group; use the three-group
form above or your skip count silently jumps (16 → 39). `uv run pytest` (serial) and
`uv run pytest -n auto` should both report the same counts; the last recorded boundary was
**2514 passed, 0 failed, 16 skipped**.

## 2. What is shipped (do not rebuild)

| Work | Where | Notes |
| --- | --- | --- |
| E01-6 stage 1 | `backtesting/evidence.py`, `core/schemas.py` | protocol `equity-run-manifest/4` reader support, `PriceOriginSummary` + derivation, v3 preserved |
| E01-6 stage 2 | `core/price_provenance.py`, `storage/delta.py`, `ingestion/writers.py`, `devtools/seed_demo.py` | nullable envelope, both write routes, generator-only `synthetic`, catalog regenerated |
| E01-6 stage 3 | `backtesting/data_loader.py`, `arena.py`, `report.py`, `cli/commands/arena.py` | read-seam classification, derived `data_provenance`, v4 writers |
| P03 stages 1-4 | `dashboard/panels.py`, `dashboard/pages.py`, `dashboard/streamlit_app.py`, `core/settings.py` | loopback-gated Portfolio / Data Readiness / Research Runs / Signals, one configured private path |

Read `docs/README.md` → this file → `docs/plans/20261107-04-p03-private-console-plan.md` →
ADR-0027 → ADR-0018 → spec §5.4/§9/§10 before choosing work.

## 3. The remaining queue (each item is gated)

| # | Task | Gate before starting |
| --- | --- | --- |
| 1 | **D01** licensing / retention / source access / spend / target root | owner must name the source, the spend ceiling and the target root; **no network call** until then |
| 2 | **P00-2** replacement split / quantity-adjustment semantics | an approved **ADR first** (import, replay, basis, valuation-block interaction, ADR-0024 §9) |
| 3 | **P00-4** backup / restore / migration / compaction of the private ledger | an approved **ADR first** (verification, failure modes, no migration run until authorized) |
| 4 | **R00 → R01 → R02** research controls → real baseline → ML hypothesis | blocked behind E01 (done) **and** D01 |
| 5 | **H00** sanitized public hosting | deferred; needs a privacy review of any proposed export |
| 6 | **P03 follow-ups** (small, no new gate needed if scoped to these) | a populated-ledger render no-write proof; a per-instrument coverage view for Data Readiness |

Recommended order is dependency-driven, not preference: P03 follow-ups and the two ADRs
(P00-2/P00-4) are independent; D01 unblocks R00–R02; H00 comes last.

## 4. Traps that cost time in the last session

- **One writer + a separate adversarial reviewer per stage**, formal first-line verdict
  (`PASS` / `NEEDS-REVISION` / `BLOCKED`). Reviewers found real defects in every stage;
  do not skip the round because the diff "looks small".
- **Mutation proofs**: for each rule, remove the rule, watch its named test fail, restore
  from a `/tmp` copy and prove `diff -q` identical. Tests that pass with the rule removed
  are false confidence (two were found this way and had to be strengthened).
- **`tests/` is excluded from the project mypy config** (`files = ["src"]`). A single-file
  `mypy tests/...` produces config-inconsistent noise; the authoritative gate is bare
  `uv run mypy`.
- **Catalog byte identity is the wrong expectation when a schema constant changes**:
  `catalog/datasets.py` derives the price description and column list from
  `STANDARD_COLUMNS`, so regenerate and commit the JSONL; never hand-edit it.
- **Reader-first**: land a reader before a writer for any protocol or schema change
  (that ordering is why E01-6 did not break stored evidence).
- **Privacy is testable**: the console's no-write and configured-path properties are
  asserted, not promised. Keep it that way.

## 5. Paste-ready prompts

**D01 (only with explicit authorization):**

```text
D01 gate: propose (a) the licensing/retention evidence needed for a scoped US raw-price
slice, (b) the exact source, spend ceiling and target root, and (c) a no-write feasibility
probe that never relaxes the adjusted/PIT proof. No network call until I approve the
specific source and spend.
```

**P00-2 / P00-4 (each an ADR first):**

```text
Draft an ADR only. P00-2: replacement split/quantity-adjustment semantics (import, replay,
basis, valuation-block interaction) consistent with ADR-0024 §9 and the shipped
`valuation_blocks` behaviour. P00-4: backup/restore, migration and compaction policy for
the private SQLite ledger, including verification and failure modes. No implementation,
no migration run.
```

**Push (owner only):**

```bash
git log --oneline origin/main..HEAD   # review the four+ commits first
git push origin main
```

## 6. Definition of done for any future stage

Relevant tests pass; the change matrix in `AGENTS.md` is satisfied (source/schema/DAG/
storage/CLI/pipeline rows); public behaviour is documented (user guide for CLI/UI
changes, `ARCHITECTURE.md` for structure); the catalog is regenerated when the schema or
DAG changes; any boundary change has an accepted ADR; and the owner has accepted the
stage. Acceptance of a policy ADR is **not** authorization to implement it.
