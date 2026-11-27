# Residual implementation handoff — pickup (2026-11-27)

**Recorded:** 2026-11-27

**Result: every residual stage of the [2026-11-22 residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md)
is implemented, independently reviewed and pushed — 66 commits since `fc589d1`, with `be0a12f` the last
implementation commit (the handoff and its count correction follow on top; trust `git log`, not this line).
The pending deployment of [run 45980611891](https://github.com/minghao51/equity_lake/actions/runs/45980611891)
has since been approved and deployed — the page is live (§3 bucket 1).**

## Start here

Read in this order: [residual plan §11](../plans/20261122-01-residual-spec-and-implementation-plan.md) →
[ADR-0035](../decisions/0035-h00-sole-public-root.md) / [ADR-0036](../decisions/0036-ledger-event-retention-and-pruning.md) /
[ADR-0037](../decisions/0037-derived-total-return-from-stored-distributions.md) → this handoff.

**First action for a new agent: run the §6 gates, then pick from bucket 1 or bucket 3 of §3.**

## 1. Verified state (2026-11-27)

| Fact | Value |
| --- | --- |
| Branch | `main` = `origin/main` on github.com:minghao51/equity_lake (public); 66 implementation commits since `fc589d1`, last one `be0a12f` |
| Serial suite | green on 3.12 and 3.13 (both with `GITHUB_ACTIONS=true`); serial, ~11-12 min. The old 3157 passed / 16 skipped count predates the §5 fixes and is not re-measured |
| Lint / format / typing | `ruff check`, `ruff format --check`, `uv run mypy` (201 files) all clean |
| Markdown | `make lint-md`: 314 preexisting findings in 35 files, 0 new (reviewed-baseline ratchet) |
| Docs | `uv run mkdocs build` passes with only the four pre-existing warnings |

## 2. What landed (each stage independently reviewed)

| Stage | Delivered | Evidence |
| --- | --- | --- |
| RS-Q00 | recursive Markdown ratchet with a reviewed baseline | [evidence register](../finding/20261123-01-residual-evidence-register.md) |
| RS-B01 | unified `total_return` refusal — one early actionable error at loader/engine/CLI, split-only behaviour unchanged | plan §11 RS-B01 |
| RS-R02-D1 | R02 artifact verification + estimand clarification: the −2.79 pp point estimate is a **compounded** net-return difference while the interval resamples the **mean per-session** difference — two estimands, not a computation error. 275 checks, 0 mismatches | [R02 recovery receipt](../finding/20261123-01-r02-recovery-receipt.md); `uv run python -m equity_lake.devtools.verify_r02_artifacts --lake-dir data/lake_real` |
| RS-P01 | synthetic ledger rehearsal | [P01 rehearsal record](../finding/20261123-01-p01-synthetic-rehearsal.md) |
| RS-P02 | verified backup export + inventory | plan §11 RS-P02 |
| RS-H01 | single manual H00 publisher; private deployment receipt, retained bundles, rollback path | `.github/workflows/h00-publish.yml`, ADR-0035 |
| RS-M01..04 | four non-US market offline packs (cn_ashare, hk_sg_equity, jpx_equity, krx_equity) | `docs/developer/architecture/market-*-feasibility.md` |
| RS-D01 | source-clock qualification + real-root guards | plan §11 RS-D01 |
| RS-S01 | paper-shadow delivery with kill switch and dedup expiry | plan §11 RS-S01 |
| RS-E01 | disposable bootstrap; the publisher now seeds its own lake — `bootstrap --end-date "$FROZEN_LAKE_END_DATE" --keep` with `FROZEN_LAKE_END_DATE: "2026-06-30"` in the workflow, copied to `data/lake/` before the build; rollback runs (`inputs.rollback_run_id`) skip it | `h00-publish.yml`, `src/equity_lake/devtools/bootstrap.py` |
| RS-R01 | R00 controls + scale: null 0/100, planted 46/50, 8/8 mutants killed, 5/50/200 scale measured | [R01 report](../research/20261123-01-r01-report.md) |
| — | realistic mock broker pack for the import boundary | `tests/unit/test_portfolio_mock_pack.py` |

Two new capabilities:

- `equity portfolio prune --before <instant> [--apply] [--backup <path>]` — report-only by default
  (`src/equity_lake/cli/commands/portfolio.py:481`, `src/equity_lake/portfolio/store.py:1516`).
- `storage/total_return.py::derive_total_return` — hand-math oracles in `tests/unit/test_total_return.py`:
  100 → 90 with a 10 distribution = 0% gross total return; a 2:1 split changes no wealth;
  a 4:1 split plus a same-session dividend = +2%.

Decisions recorded:

| ADR | Settles |
| --- | --- |
| [0035](../decisions/0035-h00-sole-public-root.md) | H00 owns the sole public root; one manual SHA-pinned publisher (`h00-publish.yml` holds the only `actions/deploy-pages` step; `pages.yml` is deleted and both legacy deployers are retired); private receipt + retained bundles; withdrawal is manual |
| [0036](../decisions/0036-ledger-event-retention-and-pruning.md) | ledger pruning = checkpoint collapse; operator-invoked; verified-backup precondition; replay-equality proof; pruned-history disclosure |
| [0037](../decisions/0037-derived-total-return-from-stored-distributions.md) | derived total return from stored dividends/splits; research-grade only (every stored action row has a null `source_available_at`); vendor-adjusted series are never stored or mixed |

## 3. What remains

### Bucket 1 — owner/platform: deployment approved and live, one optional check left

1. **The pending deployment was approved and deployed (2026-11-27).**
   [Run 45980611891](https://github.com/minghao51/equity_lake/actions/runs/45980611891) (`h00-publish.yml`,
   dispatched 2026-11-27) was approved, `deploy` succeeded in 12 s, and
   `https://minghao51.github.io/equity_lake/` returns 200. The gate is real and enforced: the `github-pages`
   environment has required reviewers (`minghao51`, `prevent_self_review=false`, `can_admins_bypass=false`)
   with the existing `main`-only branch policy preserved. This was set through the GitHub API, and the API
   response — `protection_rules` now includes `required_reviewers` plus `branch_policy` — is the platform
   evidence. **Awaiting the next dispatch: the footer fix.** The published page's footer claimed it "is not
   a published page" — false for the deployed bytes; fixed in `src/equity_lake/dashboard/h00.py` with a
   regression test in `tests/unit/test_h00_sanitized_page.py`, and the live page will carry the corrected
   footer at the next `h00-publish.yml` dispatch.
2. **Optionally inspect the live page once in a browser** to confirm the stale banner behaves. Staleness is
   proven by structural tests and mutation (`tests/unit/test_h00_sanitized_page.py`), not by a real browser.

### Bucket 2 — backlog / not required now (owner decision 2026-11-24)

- live notification delivery (the paper shadow is done)
- off-host / encrypted backup
- real non-US market slices
- backup-file pruning

### Bucket 3 — planned, blocked on inputs

- **R02-D2 second experiment** — plan at
  [research/20261124-01-r02-d2-preregistration-plan.md](../research/20261124-01-r02-d2-preregistration-plan.md);
  blocked on an RS-D01-qualified data packet: source-issued clocks, coverage/expiry evidence, rights.
- **A paid vendor total-return series** — optional now that derivation exists; options at
  [finding/20261124-01-vendor-total-return-options.md](../finding/20261124-01-vendor-total-return-options.md).

## 4. Named residuals and limits

- Pruning has only ever run on synthetic `tmp_path` ledgers — never a real one. ADR-0036: implementing the
  mechanism authorizes no run against a real ledger; an actual prune needs its own owner instruction.
- A prune refuses when a `SPLIT` sits before the cut and when early sequences exhaust checkpoint room. The
  `SPLIT` refusal surfaces through the replay-equality proof — the OPENING-row checkpoint cannot express a
  split, so `_book_fingerprint`/`_prove_replay_equality` (`src/equity_lake/portfolio/store.py`) refuse and
  nothing changes; there is no separate named early pre-check for it. The sequence-room refusal is named at
  `store.py:1394` ("no deterministic source-sequence room before the earliest retained event").
- The publish receipt and rollback path are tested (`tests/unit/test_h00_release_verification.py`) and were
  exercised for the first time by the real publication of run 45980611891 (build → verify → receipt → bundle
  upload → deploy). No rollback run (`inputs.rollback_run_id`) has ever been dispatched, so the rollback half
  remains tested but unexercised.
- R01's power clears its floor by only 2.6 pp and is synthetic-fixture evidence
  ([R01 report](../research/20261123-01-r01-report.md)).
- 132 test files lost their now-redundant `# type: ignore[import-untyped]` comments (604 occurrences) after `py.typed` was
  added — measured: the removal commit `bfaaa8c` touched 132 files and zero such comments remain (the
  plan §11 "~140 … still carry" line predates that removal).

## 5. Why CI was red — three causes, all fixed (2026-11-27)

A Quality run on `main` was red before the fixes. All three causes were test-infrastructure bugs, not
product regressions, and all three are fixed. Quality run
[46010374519](https://github.com/minghao51/equity_lake/actions/runs/46010374519) on `d1f507d` is the first
green run in this series: all six jobs pass, including both `test` legs (3.12 in 8m3s, 3.13 in 8m44s).

- **A — ANSI escapes inside the option name.** `typer/rich_utils.py` computes
  `FORCE_TERMINAL = True if getenv("GITHUB_ACTIONS") or getenv("FORCE_COLOR") or getenv("PY_COLORS") else None`.
  GitHub Actions sets `GITHUB_ACTIONS=true`, so Rich styles a single option name across spans — the rendered
  text is `ESC[1;36m-ESC[0mESC[1;36m-lake-ESC[0m` where a plain `--lake` was expected — and
  `assert "--flag" in result.stdout` passed locally and failed on CI. Terminal width is NOT the variable:
  `COLUMNS` and Typer's `TERMINAL_WIDTH` change nothing, and the ANSI-stripped output is byte-identical
  between a local run and CI. Fix: `tests/helpers/cli.py` (`strip_ansi`, `help_text`) and every help
  assertion now runs on stripped output. `TERM=dumb` was removed from one `help_env` because a dumb terminal
  pins Rich to 80 columns and truncates long option names with an ellipsis.
- **B — a unit test rebuilt `.venv` under the running suite.**
  `tests/unit/test_make_targets.py::test_reset_lake_dry_run_is_non_destructive_and_lists_the_documented_paths`
  executed `make reset-lake DRY=1` for real. That target runs a bare
  `uv run python -m equity_lake.devtools.reset_lake`, and a bare `uv run` resolves its own interpreter. On
  the 3.13 matrix leg uv therefore deleted `.venv` and rebuilt it with 3.12 mid-session, so every later lazy
  file open and lazy submodule import addressed a path that no longer existed: `vaderSentiment/vader_lexicon.txt`,
  certifi's `cacert.pem` (surfacing inside `httpx` TLS setup via `llm_base.build_chat_client`) and
  `pandas.core.arrays.arrow.extension_types` (via the parquet engine). 36 late-alphabetical tests failed;
  modules already imported kept working, and the 3.12 leg was unaffected because its rebuilt environment
  kept the same `python3.12` directory. Reproduction: the full 3.13 suite fails 38; putting
  `test_make_targets.py` before `test_news_fetcher.py` reproduces with 22; the `DRY=1` test alone is the
  trigger. Fix: `uv` is stubbed on `PATH` for that one call and forwards to `sys.executable` (the sibling
  refusal test already used a `uv` stub).
- **C — a test that could not pass on a clean checkout.**
  `test_reset_lake_errors_out_on_an_unrecognized_dry_value` asserted `path.exists()` for `_RESET_TARGETS`,
  which live under the gitignored `data/` root, so it failed on every fresh checkout. It now compares
  before/after `_fingerprint` values — the real invariant: a refused invocation changes nothing.

Also landed: `quality.yml` gained a `Verify installed dependencies` step (proves the interpreter about to
run the suite has those three files/submodules; it passing while tests report missing files is the signal
that the environment changed *during* the session rather than being installed wrong), and its four `uses:`
refs are pinned to full commit SHAs with `persist-credentials: false`. A `cache-suffix: v2` bump was tried
on the theory of a corrupt uv cache and **reverted** — there was no cache corruption.

**Actionable:** emulate CI with `GITHUB_ACTIONS=true uv run pytest`, and route every new help assertion
through `tests/helpers/cli.py` (`strip_ansi`/`help_text`) — never assert on raw `result.stdout`, and never
try to fix it with a wide `COLUMNS` or `TERM=dumb`.

## 6. Verify from scratch

```bash
uv sync --group dev --group backtesting --group dashboard
uv run pytest                               # serial; ~11-12 min (683 s recorded); not xdist-friendly
GITHUB_ACTIONS=true uv run pytest           # CI emulation (Rich/ANSI terminal mode); also required
uv run ruff check .
uv run ruff format --check .
uv run mypy                                 # 201 files
make lint-md                                # expect "314 preexisting findings in 35 files ... 0 new"
uv run mkdocs build -d /tmp/site            # four pre-existing warnings only
uv run python -m equity_lake.devtools.verify_r02_artifacts --lake-dir data/lake_real   # 275 checks, 0 mismatches
uv run python -m equity_lake.ml.r01_controls --mode all --protocol-root docs --results /tmp/r01.json
```

## 7. Traps (these already cost time)

1. `market_contracts` must read `paths.CANONICAL_PRICE_MARKETS`, never the patchable `paths.PRICE_MARKETS`,
   or single-file test runs raise `KeyError: 'cn_ashare'`.
2. Never add a `# type: ignore[import-untyped]` — the package is typed.
3. Never update the Markdown baseline to hide a finding — fix the document.
4. The ledger's append-only guard allows exactly one `DELETE FROM`, parameterised per event inside the prune
   copy step (`src/equity_lake/portfolio/store.py:1458`).
5. Never prune or back up a real ledger as a test.
6. The publisher seeds its lake with a pinned end date (`2026-06-30`) — never compute it from `date`; that
   breaks reproducibility.
7. Do not switch ingestion to `auto_adjust=True` (raw-at-write-boundary) and never double-count
   vendor-adjusted returns with the ledger's `CASH_DIVIDEND` events.
8. Never run a real `uv` or `make` target from inside the unit suite — a bare `uv run` resolves its own
   interpreter and can rebuild `.venv` under the running process (§5 B). Stub `uv` on `PATH` and forward
   to `sys.executable`.
9. Assert help content on `strip_ansi(...)`/`help_text(...)` output (`tests/helpers/cli.py`) — CI sets
   `GITHUB_ACTIONS` and Rich styles option names across ANSI spans (§5 A). A wide `COLUMNS` or
   `TERMINAL_WIDTH` changes nothing and `TERM=dumb` truncates long option names; neither is a fix.
