# RS-P01 synthetic ledger rehearsal (2026-11-23)

**Stage:** RS-P01 (ledger rehearsal, `blocked-input`) of the
[residual spec and implementation plan](../plans/20261122-01-residual-spec-and-implementation-plan.md) §2.6, §3, §5.
**Status:** the **synthetic fixture rehearsal PASSED**; the **real-instance rehearsal remains BLOCKED-INPUT**
pending the operator's canonical CSV plus statements/marks. The plan's §4 dependency note makes the synthetic
rehearsal independent of that input; nothing here exercises, invents or claims a real private account.

Recorded 2026-11-23 04:12 UTC in the isolated worktree at `334a58f` (`git rev-parse HEAD` =
`334a58f2774a11a5b892bb1b02ff737e149f5d45`, the merge commit this lane started from). Tools: `uv` 0.9.7, Python 3.12.3, SQLite 3.45.1.

## 1. What this record does and does not claim

Proved here, on committed synthetic fixtures only: a dry run writes no private state; a second identical import
is a no-op; statement reconciliation reproduces agreement and reports a deliberate mismatch; `restore` and
`compact` run on isolated copies first and leave the production copy's bytes and record digest unchanged, with a
restore reproducing the recorded bytes; a fresh v2 ledger needs no migration; a legacy v1 ledger upgrades only on
a legacy copy. One dry-run write-guard mutation was shown to fail its named test and was restored byte for byte.

Not claimed: any real account, broker export, statement, mark or balance; any conclusion about the operator's
book; any migration, compaction or restore **run** against a real ledger (ADR-0031 forbids that without its own
owner instruction). In this checkout `data/portfolio/` does not exist, so "production" below means the
rehearsal's own designated production copy under a temp directory.

## 2. Fixtures (synthetic, committed)

| Path | Shape | Provenance |
| --- | --- | --- |
| `tests/fixtures/portfolio/canonical-ledger.csv` | canonical 18-column import CSV, 4 rows: `OPENING` 10 @ 100 with basis 1000 and cash 1000; `BUY` 5 @ 120 fee 5; `SELL` 4 @ 130 fee 2; `CASH_DIVIDEND` net 11 | written for this rehearsal; reproduces the ADR-0024 decision 6 acceptance hand oracle (cash 924, 11 shares, realised gain 90). Neutral `main` account label, invented `XNAS:PRIVATESYM`, invented amounts |
| `tests/fixtures/portfolio/canonical-ledger-extension.csv` | the same 4 rows plus one `DEPOSIT` of 100 (sequence 5) | synthetic; used only to diverge an isolated drill copy so a restore has something real to roll back |
| `tests/fixtures/portfolio/statement-matched.csv` | canonical 9-column statement: one `CASH` 924.00 row and one `POSITION` 11 row at `2024-01-05T21:00:00Z` | synthetic; states exactly what the fixture ledger replays |
| `tests/fixtures/portfolio/statement-mismatched.csv` | same shape, `CASH` 900.00 and `POSITION` 10 | synthetic; the deliberate disagreement (cash −24.00, quantity −1) |
| `tests/fixtures/portfolio/legacy-v1-ledger.sql` | the shipped v1 table body (seven `event_type` kinds, no `SPLIT`) plus two stored rows and `PRAGMA user_version=1` | synthetic; pinned so the v1→v2 upgrade keeps being exercised on the shape a v1 build left behind. Digest columns are opaque synthetic hex, never a real digest |

Every fixture is under 1 KB. The rehearsal never reads `data/`, the lake, a broker export or the network.

## 3. Rehearsal steps: command, exit code, observed result

All invocations went through Typer's `CliRunner` in-process with the ledger global (`PORTFOLIO_DB_PATH`, read at
call time by `portfolio_db_path`, `src/equity_lake/portfolio/store.py:163`) redirected into a temp directory — the
same seam `tests/unit/test_portfolio_cli.py:139` uses. A bare `uv run equity portfolio …` subprocess would resolve
the repository's `data/portfolio` (`src/equity_lake/core/paths.py:114-115`), which this rehearsal must not touch.

### 3.1 A dry run writes nothing (hashed before and after)

| Command | Exit | Observed |
| --- | --- | --- |
| `import --file …/canonical-ledger.csv --dry-run` (no ledger yet) | 0 | `4 new, 0 already stored identically`, cash delta `924.00`, quantity delta `XNAS:PRIVATESYM 11`; private tree digest `44136fa3…` → `44136fa3…` (unchanged); no directory, no database, no journal |
| `import --file …/canonical-ledger.csv` (commit) | 0 | `inserted 4, unchanged 0, rejected 0`; production copy `schema v2`, rows 4, `sha256 650d83c0…`, `record_digest 7e36298c…` |
| `import --dry-run` against the populated ledger | 0 | `0 new, 4 already stored identically`; tree digest `e1d094d7…` unchanged |
| `migrate` (report-only default) | 0 | `already at schema v2; nothing to do.`; tree digest `e1d094d7…` unchanged |
| `compact` (report-only default) | 0 | `24576 bytes, 4 row(s)`, `record digest 7e36298c…`, `Report only: VACUUM INTO … Nothing was written.`; tree digest `e1d094d7…` unchanged |
| `restore --from <verified copy>` (report-only default) | 0 | `replaces the current file: 1 event(s) it holds would be left out`, `Report only: nothing was written`; tree unchanged, the only `.bak` present was the candidate itself |

The tree digest covers every path under the private root with its content SHA-256 and mode. No `-journal`,
`-wal` or `-shm` companion appeared in any step, and no `*.bak` was created by a report-only command.

### 3.2 A second identical import is a no-op

| Command | Exit | Observed |
| --- | --- | --- |
| `import --file …/canonical-ledger.csv` (second time) | 0 | `inserted 0, unchanged 4, rejected 0`; rows 4 → 4; `record_digest 7e36298c…` unchanged; replayed book fingerprint unchanged |

The idempotency contract is on the **record** (ADR-0024 decision 5), not on the file bytes. A committed no-op
batch still bumps SQLite's header change counter, so the database's SHA-256 changes while nothing is duplicated:

```text
file sha256 changed: True    size 24576 -> 24576 bytes
differing byte offsets: [27, 95]      # header change counter and version-valid-for: 4 -> 5
PRAGMA integrity_check: ok    user_version: 2
```

`tests/unit/test_portfolio_synthetic_rehearsal.py` therefore asserts the row count, the record digest, the
replayed book, the file mode and that the only changed bytes are those two counters — it does not claim a
byte-identical database after a no-op write path.

### 3.3 Statement reconciliation

| Command | Exit | Observed |
| --- | --- | --- |
| `reconcile --as-of 2024-01-05T21:00:00Z --statement …/statement-matched.csv` | 0 | `cash: statement 924.00, ledger 924.00, delta 0.00, tolerance 0.01, matched`; `quantity XNAS:PRIVATESYM: statement 11, ledger 11, delta 0, tolerance 0.00000001, matched`; `overall: matched` |
| `reconcile --as-of … --statement …/statement-mismatched.csv` | 1 | `cash: statement 900.00, ledger 924.00, delta -24.00, … mismatched`; `quantity …: statement 10, ledger 11, delta -1, … mismatched`; `overall: mismatched`; stderr `the ledger was not adjusted and no balancing event was inserted` |

The deliberate mismatch is reported as a mismatch with its signed delta and the applied tolerance; it is not
absorbed, and neither the ledger bytes nor the record digest changed across either read.

### 3.4 Isolated-copy drills (`restore`, `compact`)

Production baseline before the drills: `sha256 d04de7ba…`, `record_digest 7e36298c…`, private tree
`c76bcd68…`.

| Command | Exit | Observed |
| --- | --- | --- |
| `compact --apply` on an isolated copy | 0 | drill copy `schema v2`, rows 4, `record_digest 7e36298c…` preserved though the file bytes changed (`e70c9965…` vs `d04de7ba…` — compaction preserves the record, not the file, ADR-0031 decision 5); verified pre-compaction backup `portfolio.sqlite3.20261123T041227Z.v2.bak` with `sha256 d04de7ba…` = the production bytes |
| `import` the extension CSV into a second isolated copy | 0 | `inserted 1, unchanged 4, rejected 0`; copy diverges: rows 5, `record_digest bbfb0853…` |
| `restore --from portfolio.sqlite3.rehearsal.v2.bak --apply` on that copy | 0 | `replaces the current file: 1 event(s) it holds would be left out`; restored `sha256 d04de7ba…` (**reproduced the recorded bytes**), `record_digest 7e36298c…` equal to production, replayed book fingerprint equal; the drill kept a verified backup of the diverged file it replaced |

After both drills the production copy was still `sha256 d04de7ba…`, `record_digest 7e36298c…`, rows 4, private
tree digest `c76bcd68…` (unchanged), with no `.bak` and no journal beside it. The drills wrote only inside their
own copies.

### 3.5 Schema paths

| Command | Exit | Observed |
| --- | --- | --- |
| `migrate` on the fresh v2 ledger (report-only) | 0 | `already at schema v2; nothing to do.` |
| `migrate --apply` on the same fresh v2 ledger | 0 | same message; **no backup was created** and the tree was unchanged |
| `migrate` on a *legacy v1 copy* (report-only) | 0 | `Report only: portfolio.sqlite3 is schema v1; v2 is available.`, rows 2, `record digest af1c8f1f…`, `Report only: nothing was written (no backup, no directory, no journal).`; no `.bak` |
| `migrate --apply` on the legacy v1 copy | 0 | `Upgraded …: schema v1 -> v2, 2 row(s) unchanged`; `verified backup: portfolio.sqlite3.20261123T041228Z.v1.bak (sha256 a0a6740b…)` = the v1 original's bytes; `record digest unchanged`; `integrity_check` and `foreign_key_check` passed |
| v1 original after the upgrade (no command run on it) | — | `sha256 a0a6740b…` unchanged, still `schema v1`, no `.bak` beside it: the conversion happened **only on the copy** |

A no-op `--apply` is not a backup source: `migrate_ledger` returns `already_current` before any write
(`src/equity_lake/portfolio/store.py:713-722`), so forcing a migration on a current ledger to obtain a copy
produces nothing. A verified export of a current ledger is RS-P02's `portfolio backup` command and is not
available in this build (see §6).

## 4. Digest demonstration: "production hash unchanged"

| Quantity | Before the drills | After `compact` on a copy | After `restore` on a copy |
| --- | --- | --- | --- |
| production `sha256` | `d04de7ba…` | `d04de7ba…` | `d04de7ba…` |
| production `record_digest` | `7e36298c…` | `7e36298c…` | `7e36298c…` |
| production rows | 4 | 4 | 4 |
| production private tree digest | `c76bcd68…` | `c76bcd68…` | `c76bcd68…` |
| `*.bak` beside production | none | none | none |

Two digest families are used, and they behave differently: the **record digest** hashes only
`(account, external_id, content_digest)` and is stable across runs and machines
(`7e36298c…` for this fixture, `af1c8f1f…` for the legacy v1 rows); the **file SHA-256** includes the wall-clock
`recorded_at` written by each append (`src/equity_lake/portfolio/store.py:297`), so it is per-run and is quoted
here only from the run at 04:12:27 UTC. The replayed book fingerprint (cash, positions, basis, realised gain,
fees, income at `2024-01-05T21:00:00Z`) is likewise stable and is what the restore drill compares.

## 5. One-rule mutation and restoration

Mutated one rule in `src/equity_lake/cli/commands/portfolio.py:148` — the dry-run write guard
(`if dry_run:` → `if dry_run and False:`) — so a `--dry-run` falls through to `commit_events` and writes.

| Step | Result |
| --- | --- |
| pristine file copy to `/tmp/portfolio-cli-pristine.py` | `sha256 8a33a5e4d66d83a5e6c1c9300e26e7253dde47f9b3d27c746b68f34a15348a35` |
| `pytest -k dry_run` with the mutant in place | **exit 1: 2 failed** — `test_dry_run_import_creates_no_private_state` and `test_dry_run_import_on_a_stored_ledger_leaves_the_tree_byte_identical`; the mutant printed `Imported … inserted 0, unchanged 4` and created the ledger the guard was supposed to prevent |
| restore from the `/tmp` copy | `diff -q /tmp/portfolio-cli-pristine.py src/equity_lake/cli/commands/portfolio.py` → exit 0; `sha256sum -c` → `OK`; `git status` clean for that file |
| `pytest -k dry_run` after restoration | **exit 0: 2 passed** |

The mutation touched only that one line in this worktree and was restored before anything was run again; the
repository's real ledger was not involved at any point.

## 6. Observations and residuals (no defects found)

- **No defect was reproduced** in `import`, `migrate`, `restore`, `compact` or `reconcile` during this rehearsal.
  Every refusal and every report-only mode behaved as ADR-0024 and ADR-0031 describe.
- A no-op re-import changes the database file's SHA-256 (SQLite header change counter, offsets 27 and 95) while
  leaving the record identical (§3.2). This is an implementation detail of the writable commit path, not a
  contradiction of ADR-0024 decision 5, but any future "file hash unchanged" claim across a write path needs the
  record digest instead.
- Backup names are second-granular and never overwritten (`_write_verified_backup`,
  `src/equity_lake/portfolio/store.py:593-598`): two backup-producing mechanisms in the *same* directory within
  one second would refuse the second copy. Not observed as a failure here — each drill used its own directory —
  but it is the reason every drill in this rehearsal owns a directory.
- **Missing command, reported not added:** there is no `portfolio backup` (verified export) or `portfolio backups`
  (inventory) command in this build; a current v2 ledger therefore has no in-contract way to produce a verified
  copy, and a no-op migration is *not* a substitute (§3.5). Both are RS-P02's deliverable; this lane may not edit
  the CLI command module.
- The real-instance rehearsal — operator canonical CSV plus statements and marks, real balances, real refusals —
  is untouched and remains `blocked-input` (plan §8 inputs).

## 7. Reproduction

```bash
# The durable artefact: 12 tests over the five rehearsal steps, on the committed fixtures only.
uv run pytest tests/unit/test_portfolio_synthetic_rehearsal.py -v

# The narrative run behind §3-§4 (in-process CLI, ledger redirected into /tmp; the repository's
# data/portfolio is never created). Digests other than the record digests differ per run.
uv run pytest tests/unit/test_portfolio_synthetic_rehearsal.py -q --junitxml=/tmp/rehearsal-junit.xml
```

Gates for this change (all from the worktree, logs in `/tmp`): `uv sync --group dev --group backtesting
--group dashboard` exit 0; `pytest tests/unit/test_portfolio_synthetic_rehearsal.py` 12 passed; `ruff check .`
exit 0; `ruff format --check .` exit 0; `uv run mypy` exit 0 (195 source files); `make lint-md` equivalent
(`python -m equity_lake.devtools.markdown_lint --check`) exit 0. The three pre-existing portfolio test modules
also still pass (108 passed) — this change adds files only and edits no command, store or existing test module.

## 8. Lane input for the residual plan ledger

Filled from `/tmp/residual-lane-inputs/ledger-format.txt`. The plan itself is not edited by this lane.

```text
Stage RS-P01 / status: synthetic rehearsal PASSED, real-instance rehearsal BLOCKED-INPUT / recorded 2026-11-23 04:12 UTC:
Deliverable and design reference (residual plan §2.6, §3, §5): dry-run with zero private writes (tree hash), idempotent
  double-import, statement reconciliation, restore + compact on isolated copies with the production digest unchanged,
  fresh v2 = no migration, legacy v1 converted only on a legacy copy — all on committed synthetic fixtures.
Dependencies (stage IDs) and accepted-ADR evidence: independent of RS-Q00 in execution (§4); ADR-0024 decisions 3-5/10/13,
  ADR-0031 decisions 2-5 and 7.
Commit identity (stage start -> reviewed final diff): 334a58f2774a11a5b892bb1b02ff737e149f5d45 -> this worktree commit.
Evidence: /tmp/rehearsal-tests.log (12 passed), /tmp/rehearsal-junit.xml, /tmp/rs-p01-rehearsal-evidence.log,
  /tmp/mutation-mutant.log (mutant: 2 failed), /tmp/mutation-restored.log (restored: 2 passed);
  test module tests/unit/test_portfolio_synthetic_rehearsal.py; this record.
Inputs used (and provenance): tests/fixtures/portfolio/{canonical-ledger,canonical-ledger-extension,
  statement-matched,statement-mismatched}.csv and legacy-v1-ledger.sql — all synthetic, committed, invented values.
External prerequisite state: pending — operator canonical CSV plus statements/marks (plan §8). No real ledger exists in
  this checkout, so no real-instance claim is made.
Rollback / withdrawal procedure: revert the single commit (new test module, five fixtures, this record); it touches no
  production code, no CLI command, no store module and no data/ path, so nothing but evidence is withdrawn.
Residuals left and next eligible stage: a verified backup export/inventory command (RS-P02); the real-instance rehearsal
  stays blocked-input until the operator inputs arrive.
```

**Status line:** rehearsal **PASSED on synthetic fixtures**; the real-instance rehearsal remains **BLOCKED-INPUT**
pending the operator's canonical CSV and statements/marks.
