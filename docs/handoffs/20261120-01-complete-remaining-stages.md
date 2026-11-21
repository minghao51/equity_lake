# Next Agent — complete the remaining stages: real-root features → R01/R02 → H00 → commit

**Recorded:** 2026-11-20

This document **supersedes** [20261118-01](20261118-01-implement-adr0032.md) as the entry
point. That record is spent — do not extend it. Its status updates are historical and its
§5 trap history is still worth reading.

**Status: everything accepted through ADR-0033 and R00 is implemented; nothing is run and
nothing is committed.** Tip is `ad74703`; the working tree holds 82 uncommitted entries
(57 modified, 25 untracked), nothing staged, nothing committed since the tip, nothing
pushed. No feature run, Gold run, or research run has executed. Six stages remain,
dependency-ordered in §4; each needs its own gate and an independent reviewer verdict.

## 1. Verify state before anything else

```bash
git log --oneline -1                       # ad74703
git status --porcelain | wc -l             # 82 (57 M + 25 ??); git diff --cached --stat empty
# data/catalog.jsonl is expected to be modified in the working tree until the commit stage:
# it carries the accepted ADR-0032 catalog regeneration (coverage_basis/action_coverage_basis).
# A stage must not change it; compare its sha, not its git cleanliness.
uv sync --group dev --group backtesting --group dashboard
uv run pytest -q --junitxml=/tmp/serial.xml        # 2709 collected, 2693 passed, 16 skipped, 0 failed
uv run pytest -n auto -q --junitxml=/tmp/parallel.xml  # must agree exactly
uv run ruff check .                        # clean
uv run ruff format --check .               # clean, 365 files
uv run mypy                                # clean, 191 source files
```

Derive counts from the junitxml (collected − skipped = passed): the `-q` summary line can be
lost from a redirect or a dying xdist worker. `uv sync --group dashboard` alone removes the
backtesting group and changes the skip count (16 → 39) — sync all three groups.

Landed data is not in git; verify it directly (all values verified 2026-11-20):

```bash
uv run python -c "
from pathlib import Path
from equity_lake.storage.delta import read_delta
df = read_delta('01_bronze/market_data/us_equity', lake_dir=Path('data/lake_real').resolve())
print(df.height, df['ticker'].n_unique())"
# expect: 58653 11   (0 duplicate ticker/date pairs, 0 null closes)
sha256sum data/catalog.jsonl    # expect prefix 537bf5fbf8867393 (unchanged all session)
find data/lake -type f | wc -l  # expect 1413 (the demo lake is never written)
ls data/portfolio data/research # expect: both absent
```

Real root also holds 1,015 action rows and 9 weak coverage proofs (`vendor_events_no_clock`).
Per-ticker readable windows (each bounded by its own coverage/action evidence):

| Ticker | Window | Bounded by |
| --- | --- | --- |
| AAPL | 1987-05-11 .. 2026-11-12 | action watermark |
| MSFT | 1987-09-21 .. 2026-08-20 | action watermark (the common end) |
| ADBE | 1987-03-12 .. 2005-05-24 | last action caps it |
| BABA | 2023-12-20 .. 2026-06-11 | vendor list starts there |
| GOOGL | 2014-04-03 .. 2026-09-04 | action watermark |
| GLD | 2004-11-18 .. 2026-11-18 | proven-empty action scope (ADR-0033) |
| NVDA | 2000-06-27 .. 2026-09-10 | action watermark |
| QQQ | 2000-03-20 .. 2026-09-21 | action watermark |
| SPY | 1993-03-19 .. 2026-09-18 | action watermark (ARCX identity) |
| JPM | 2020-01-02 .. 2026-08-20 | coverage start |
| KO | 2020-01-02 .. 2026-08-20 | coverage start |

## 2. What is shipped — do not rebuild

| Work | Where | State |
| --- | --- | --- |
| ADR-0028/0029/0030/0031 (real slice, raw-price basis, second root, ledger migration) | `ingestion/`, `storage/`, `portfolio/`, `core/paths.py` | accepted, implemented, reviewed |
| ADR-0032 (opt-in weak basis) | `core/availability.py`, `storage/lake_reader.py:176` (`with_price_adjustment(coverage_basis=...)`, default `source_clock`), `features/engineering.py:183` + `features/__init__.py:50` (feature-path opt-in) | implemented 2026-11-19: weak `vendor_events_no_clock` proof, basis stamps carried in data, schema-version bumps 3/5/8 (incl. `ACTION_COVERAGE_SCHEMA_VERSION` 2→3), 32 tests incl. landed-slice read-only verification, 8 mutation proofs |
| R00 harness controls | `backtesting/protocol.py` (446 lines) | implemented: `ResearchProtocol`/`ResourceBudget` freeze universe/window/holdout/costs/metrics/tolerances/budget (`max_runtime_seconds`, `max_symbols`, `max_sessions`); `freeze_protocol` writes a dated, byte-reproducible manifest under `protocols_root` and refuses overwrite; `require_frozen_protocol` + `changed_fields` refuse an absent or changed assumption. Public API in `__all__` (line 430). 23 tests (`tests/unit/test_research_protocol.py`), 4 mutation proofs |
| ADR-0033 (proven-empty action scopes) | `ingestion/`, action store | implemented: a successful empty vendor fetch may write a proven-empty proof; 6 mutation proofs. GLD is the landed instance |
| ARCX venue support, `equity widen` | `config/tickers.yaml`, `cli/commands/data.py` | implemented: one request per ticker, clamped to coverage; BABA/QQQ/SPY/GLD configured |
| Real-root slice | `data/lake_real/` | 11 tickers / 58,653 price rows / 0 duplicates / 0 null closes; 1,015 action rows; 9 weak coverage proofs |
| Demo root + catalog | `data/lake/`, `data/catalog.jsonl` | 1,413 files, sha `537bf5fbf8867393…` — unchanged; never write here |
| H00 review | `docs/reports/20261117-h00-sanitized-demo-lake-page-review.md` | drafted: sanitization contract §4 (PROPOSED), publish gate §5, blockers B1-B10, owner questions §7. Nothing built, nothing published |

## 3. The known gap: the feature runner

The feature path is canonical-root-only today. `FeatureEngineer._setup_views()`
(`src/equity_lake/features/engineering.py:84-92`) reads `LAKE_DIR` only, and
`cli/commands/pipeline.py` carries no `--lake-dir` (grep for `lake_dir` there finds
nothing). Because the action store now holds 11 tickers, a subset read must pass
per-instrument `action_inputs` (the mapping at `features/engineering.py:198`, consumed at
`:322-327`); without it the ticker-only event identity resolver refuses out-of-scope events
with an unverified-identity error (the message observed this session was
`corporate action identity is unverified`). Stage 1 closes exactly this gap.

## 4. Remaining stages, dependency order, each gated

**Stage 1 — real-root feature runner (implementation).** Thread a lake root and
`coverage_basis` through `FeatureEngineer` / `generate_features` / `run_feature_job` and add
a CLI entry point (CLI change matrix applies: help text, CLI help-scan test, user guide).
Accept per-instrument `action_inputs`; default stays strict. Proofs required: a dry run that
writes nothing; a refusal when strict basis is demanded against weak-only evidence; a
refusal when the watermark/window does not reach the requested window — one mutation proof
per new refusal. **No run writes until tests and the dry run pass.**
Gate: `uv run pytest -q --junitxml=…` and `uv run pytest -n auto -q --junitxml=…` green
(2,709+ collected, 16 skipped), `uv run ruff check . && uv run ruff format --check . &&
uv run mypy` clean, plus the three proofs attached to the stage record.

**Stage 2 — first real-root Gold run (authorized universe).** Needs an explicit owner
instruction naming tickers, window and basis (ADR-0030 d10: the Gold table may only appear
from an explicitly opted-in run with its own authorization). Choose one coherent window
inside the intersection of the named tickers — the recorded candidate is the five-ticker
window AAPL/MSFT/NVDA/JPM/KO, 2020-01-02..2026-08-20, basis `vendor_events_no_clock`.
Stage features; verify the weak stamps travel in the rows; verify `data/lake/` (1,413 files)
and `data/catalog.jsonl` (`537bf5fb…`) are untouched. Real writes to `data/lake_real/` only.
Gate: reviewer `PASS`; report rows per feature date, tickers covered, stamp values, and the
untouched-demo evidence.

**Status 2026-11-20 — Stages 1, 2, 2a and 3 are done and reviewed; 4 and 5 are
contractually gated.** Stage 1 (runner) PASS; Stage 2 (1,260 Gold rows, weak stamps) PASS;
Stage 2a (backtest root/basis, 14 tests, 9 mutations) PASS; Stage 3 (R01) PASS — frozen
protocol `e53872973370…`, pre-holdout baseline +22.79% / Sharpe 1.49 / -8.22% / 229 trades,
manifest bound. **R02 is blocked by ADR-0032 decisions 4 and 10** (ML loaders must refuse
vendor-stamped rows; no point-in-time claim on that basis) and needs a new accepted decision
plus a real-root ML reader. **H00 needs the review's §7 answers.** The working tree is
committed in stage groups; nothing pushed.

**Stage 2a — backtest weak-basis integration (discovered at the Stage 2 review).** The
backtest path is still canonical-root/strict-only: `backtesting/data_loader.py` accepts a
`lake_dir`, but `backtesting/engine.py:253-260` instantiates the loader with defaults and
exposes no root or basis, and its point-in-time path does not accept
`vendor_events_no_clock`. Add `lake_dir` and `coverage_basis` to the backtest entry points
(default canonical / `source_clock`), thread the four loader/engine call sites, expose the
CLI flags, and prove: strict on weak evidence refuses, weak on strict evidence refuses,
one root per run, and a weak-basis backtest on the 2025 Gold window produces the same
numbers as the adjusted read. Gate: full serial + `-n auto`, reviewer `PASS`. Stage 3 is
blocked until this lands.

**Stage 3 — R01: freeze the protocol, obtain scope approval, then run the baseline.**
Freeze a `ResearchProtocol` via R00 (`freeze_protocol`) covering universe, window, holdout,
costs, metrics, tolerances and resource budget. **Before any backtest runs, the owner must
approve the run's scope and policy in the spec's approval ledger** (§10) — the freeze is not
an authorization. Then run the simple-strategy baseline and bind an E01 run manifest to the
frozen digest (`require_frozen_protocol` refuses drift). Spec §7 R01 rules bind: explicit
survivorship/selection limits, untouched holdout, no strategy selection on the holdout,
disclosed fee/lag asymmetry.
Gate: owner approval recorded; the frozen manifest exists and is byte-reproducible; the run
manifest carries the protocol digest; reviewer `PASS`.

**Stage 4 — R02: one incremental ML experiment.** Under the frozen protocol only: one
target/policy against **a technical-rule baseline as well as** no-skill and simple-model
baselines on matched OOS dates, persisted fold predictions with calibration/discrimination
metrics and the same economic metrics as R01. State the **prespecified uncertainty**
(dependence-aware resampling or an interval, declared before the run) and the
**deployment/promotion criteria in advance — and that this stage promotes nothing**. No
automated refit, no holdout re-tuning; negative or inconclusive is a valid outcome.
Gate: reviewer `PASS` on the evidence, whatever the sign.

**Stage 5 — H00: sanitized demo-lake page (gated).** Only after the owner answers the
review's §7 open questions (all eight: metrics disclaimers, predictions display, hosting
visibility, producer/retention, composition, wording, operator-run status, catalog text).
Close blockers B1-B10 under the review's §4 allowlist contract (which becomes binding on
adoption); build offline; publish requires explicit approval per the review's §5 gate.
Gate: the §4 absence checks pass as checks (not promises), the canonical dashboard output is
unchanged, reviewer `PASS`, and separate explicit publish approval.

**Stage 6 — commit the working tree.** Local `main` only, no push, grouped by stage with
repo-style messages (`feat(scope): …` / `docs(scope): …`); report the commit ids and
`git status` afterwards. Do not touch `data/`. This stage is one owner decision — run it
only on instruction.

## 5. Subagent execution protocol

- **`doc-writer`** — prose artifacts only: this handoff class, ADR/spec/README updates,
  release notes. Never code, never data.
- **`worker`** — code and data stages, **one stage at a time**, each with the stage's
  explicit gate command in its prompt. Workers read the stage's files before editing, use
  `uv run` exclusively, keep changes minimal, and return a short structured summary
  (files changed, tests run, gate result, risks) — never a transcript.
- **`reviewer`** — a formal `PASS` / `NEEDS-REVISION` / `BLOCKED` verdict after each stage,
  verified by running the gate, with `file:line` evidence for each finding. Never record a
  verdict that was not actually returned.
- **Main agent** — stay brief: dispatch, verify the returned artifact against the gate,
  never paste large outputs into context, keep the status to one screen.

## 6. Prompt pack (copy-paste)

**Runner implementation (worker):**

```text
Implement the real-root feature runner only. Scope: thread a lake root and `coverage_basis`
through FeatureEngineer / generate_features / run_feature_job, add a CLI entry point
(help text + CLI help-scan test + user guide per the change matrix), and accept
per-instrument action_inputs (features/engineering.py:198 shape). Default stays strict;
no clock synthesis; origin stays unknown. Do not run against data/lake_real/ except the
read-only checks named below; do not touch data/lake/ or data/catalog.jsonl. Proofs:
(1) a dry run that writes nothing, (2) strict-basis refusal against weak-only evidence,
(3) watermark/window refusal, each with one mutation proof (one rule at a time, restore
the original, confirm the named test fails). Gate: `uv run pytest -q --junitxml=/tmp/g.xml`
and `uv run pytest -n auto -q --junitxml=/tmp/gp.xml` green, `uv run ruff check . &&
uv run ruff format --check . && uv run mypy` clean. Return: files, tests, gate result, risks.
```

**Feature run — execution (worker, after explicit owner authorization):**

```text
Real-root Gold feature run. Scope: data/lake_real/ only, tickers <LIST>, window
<START>..<END>, basis vendor_events_no_clock, per-instrument action inputs. Real writes
only under data/lake_real/. Do not touch data/lake/ or data/catalog.jsonl; do not run
without the owner instruction naming tickers/window/basis. Proofs: dry run first (writes
nothing), then the run; report rows per feature date, tickers covered, weak-stamp values,
and `find data/lake -type f | wc -l` = 1413 plus `sha256sum data/catalog.jsonl` prefix
537bf5fbf8867393 after the run. Gate: full suite serial + `-n auto` green via --junitxml.
Return: files, rows, stamps, gate result, risks.
```

**Feature run — review (reviewer):**

```text
Review the real-root Gold run just executed. Verify by running: re-derive rows per feature
date and stamp values from data/lake_real/, confirm data/lake/ (1413 files) and
data/catalog.jsonl (sha prefix 537bf5fbf8867393) are untouched, confirm the dry-run and
refusal proofs exist and fail when their rule is mutated. Verdict PASS / NEEDS-REVISION /
BLOCKED with file:line evidence. Do not edit anything.
```

**R01 protocol freeze (worker):**

```text
Freeze the R01 ResearchProtocol via R00 only. Scope: backtesting/protocol.py public API
(freeze_protocol, require_frozen_protocol, changed_fields); assumptions = universe, window,
holdout, costs, metrics, tolerances, resource budget, per spec section 7 R01. Do not run
any backtest, do not fit anything, no network, no lake write. Proofs: freezing the same
assumptions twice reproduces identical bytes; a changed assumption is refused by
require_frozen_protocol. Gate: full suite serial + `-n auto` green via --junitxml.
Return: manifest path (name only), changed files, gate result, risks.
```

**R01 run (worker):**

```text
Run the R01 baseline under the frozen protocol, and only after the owner's approval of
this scope/policy is recorded in the spec §10 ledger. Scope: simple deterministic strategies
vs the corrected benchmark, matched scope, disclosed fee/lag asymmetry; bind an E01 run
manifest to the frozen protocol digest. Do not select a strategy on the holdout; do not
alter the frozen assumptions; no promotion. Proofs: the approval entry exists; the run
manifest carries the protocol digest; an assumption change refuses the run; report net
returns, drawdown, Sharpe convention, turnover, sample size, unavailable coverage. Writes
under data/research/ only.
Gate: full suite serial + `-n auto` green via --junitxml. Return: summary, files, gate
result, risks.
```

**R02 (worker):**

```text
One incremental ML experiment under the frozen protocol. Scope: one target/policy vs a
technical-rule baseline, no-skill and simple-model baselines on matched OOS dates; persist
fold predictions/probabilities/outcomes, Brier/calibration and the same economic
metrics/costs as R01. Prespecify the uncertainty method and the promotion criteria, and
state that nothing is promoted here. No automated refit, no holdout re-tuning; a negative
result is a valid outcome. Proofs: per-refit maturity/purging evidence; signal availability,
entry and outcome alignment stated explicitly; baseline comparison includes the technical
rule. Writes under data/research/ and data/<name>/ only.
Gate: full suite serial + `-n auto` green via --junitxml. Return: metrics summary, files,
gate result, risks.
```

**H00 build (worker, gated — only after owner answers the review's §7):**

```text
Build the H00 sanitized demo-lake page per docs/reports/20261117-h00-sanitized-demo-lake-page-review.md,
closing B1-B10 under the section 4 allowlist: no absolute paths, no settings dump, no raw
alert text, no pipeline_results content, no timestamps beyond the build stamp, no digests,
no ledger/watchlist reads, no .env in scope. Build offline; preview on loopback only;
publish requires explicit approval and must not run from this prompt. Proofs: the §4
absence scan passes as checks (not promises), the canonical dashboard output is unchanged,
and no real-slice row can reach the artifact. Gate: full suite serial + `-n auto` green via
--junitxml. Return: files, scan result, gate result, risks.
```

**Documentation update (doc-writer):**

```text
Update prose only: docs/README.md "Current continuation", the ADR index if needed, and the
user guide sections touched by the stages just completed. No code, no config, no data.
Cross-reference the authoritative records instead of restating numbers; mark this handoff's
spent stages in place. Gate: links resolve (checked by reading), status vocabulary
unchanged. Return: files and sections touched.
```

**Commit (worker, on explicit instruction only):**

```text
Commit the working tree on local main only, no push. Group by stage (feature runner, Gold
run, R01/R02, H00, docs) with messages in the repo's existing style
(feat(scope): ... / docs(scope): ...). Do not touch data/; do not commit private material
(ledger, real-slice rows, absolute paths, .env values, digests-as-identifiers). Report the
commit ids and `git status` afterwards. Gate: `git status --porcelain` empty apart from
untracked data artifacts.
```

## 7. Traps that cost time here

- **`equity backfill` is per trading day (~1 s/request).** AAPL's per-day attempt was killed
  at 40 min after 4,040 rows. Use `equity widen` for ranges (one request per ticker).
- **A single read needs one coherent window.** Each ticker's readable window is bounded by
  its coverage/action evidence (ADBE ends 2005, BABA starts 2023-12-20; see §1's table).
  The min watermark rule caps anything shared — the common end is 2026-08-20 (MSFT).
- **A visited empty vendor list is not evidence.** Only a successful fetch may produce a
  proven-empty proof (ADR-0033); a failed fetch must never write one.
- **yfinance supplies no MIC.** Identity comes from the exact current `TickerConfig`;
  `equity corporate-actions` must be constructed with it or no weak proof is issued.
- **Subset reads must scope action inputs per instrument** (§3). The ticker-only event
  identity resolver refuses out-of-scope events; do not widen the resolver to make a run
  pass.
- **Never synthesize a clock.** Origin stays `unknown` (ADR-0027); strict stays the default;
  point-in-time consumers refuse weak rows.
- **Never run `--apply` against a real ledger, a wide ingest, or a feature run without an
  explicit instruction.** Report-only and dry-run are the defaults.
- **`-q` summary lines can be lost from redirects.** Use `--junitxml` and derive counts
  (collected − skipped = passed).
- **Mutations: one rule at a time, restore the original, confirm the named test fails.** A
  mutation proof without the failing test name is not a proof.
- **Never publish or commit private material**: the ledger, real-slice rows, absolute paths,
  `.env` values, digests-as-identifiers (the H00 review's B1-B10).

## 8. Definition of done for any stage

Relevant tests pass in serial **and** `-n auto`; the `AGENTS.md` change matrix is satisfied;
public behaviour is documented (user guide for CLI, `ARCHITECTURE.md` for structure);
`data/catalog.jsonl` regenerated only if a schema/DAG constant changed
(`uv run equity catalog-generate`, never by hand); a mutation proof for every new refusal;
an independent reviewer verdict actually received; owner acceptance recorded.

## 9. Known minor items

- **Heterogeneous windows** (§1 table): one coherent read window is a deliberate constraint,
  not a defect; per-ticker runs are possible but each needs its own action inputs.
- **`mkdocs.yml`** now carries ADR-0032/0033 but the intermediate ADR list is still partial;
  cosmetic, unresolved by request.
- **R00's protocol module has no CLI by design** — it is an import-level API; do not add a
  command for it without an ADR or owner instruction.
- **The commit (stage 6) is one owner decision**, not a standing instruction.
