# Residual implementation handoff — pickup (2026-11-27)

**Recorded:** 2026-11-27

**Result: every residual stage of the [2026-11-22 residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md)
is implemented, independently reviewed and pushed — `main` = `origin/main` = `be0a12f`, 66 commits since `fc589d1`.
The only thing between here and a live H00 page is one owner click: approve the pending deployment of
[run 45980611891](https://github.com/minghao51/equity_lake/actions/runs/45980611891).**

## Start here

Read in this order: [residual plan §11](../plans/20261122-01-residual-spec-and-implementation-plan.md) →
[ADR-0035](../decisions/0035-h00-sole-public-root.md) / [ADR-0036](../decisions/0036-ledger-event-retention-and-pruning.md) /
[ADR-0037](../decisions/0037-derived-total-return-from-stored-distributions.md) → this handoff.

**First action for a new agent: run the §6 gates, then pick from bucket 1 or bucket 3 of §3.**

## 1. Verified state (2026-11-27)

| Fact | Value |
| --- | --- |
| Branch | `main` = `origin/main` = `be0a12f` on github.com:minghao51/equity_lake (public); 66 commits since `fc589d1` |
| Serial suite | `uv run pytest` → 3157 passed, 16 skipped, 0 failed (`--junitxml=/tmp/final-junit4.xml`, 683 s) |
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

### Bucket 1 — owner/platform: the only thing between here and a live page

1. **Approve the pending deployment** at
   [run 45980611891](https://github.com/minghao51/equity_lake/actions/runs/45980611891) (`h00-publish.yml`,
   dispatched 2026-11-27). The build job completed successfully in CI — bootstrap seeding, H00 build, release
   verification, private receipt and both artifact uploads all ran — and the deploy job is **waiting** for
   required-reviewer approval on the `github-pages` environment. The gate now exists and is enforced: the
   environment has required reviewers (`minghao51`, `prevent_self_review=false`, `can_admins_bypass=false`)
   with the existing `main`-only branch policy preserved. This was set through the GitHub API, and the API
   response — `protection_rules` now includes `required_reviewers` plus `branch_policy` — is the platform
   evidence.
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
- The publish receipt and rollback path are tested (`tests/unit/test_h00_release_verification.py`) but have
  NOT been exercised by a real publication: the deploy job is still waiting, and no rollback run
  (`inputs.rollback_run_id`) has ever been dispatched.
- R01's power clears its floor by only 2.6 pp and is synthetic-fixture evidence
  ([R01 report](../research/20261123-01-r01-report.md)).
- 132 test files lost their now-redundant `# type: ignore[import-untyped]` comments (604 occurrences) after `py.typed` was
  added — measured: the removal commit `bfaaa8c` touched 132 files and zero such comments remain (the
  plan §11 "~140 … still carry" line predates that removal).

## 5. Known CI issue — read before trusting a red run

The Quality workflow was already failing on `main` before this work. The cause is an assertion-width
fragility in the help-scan tests, not a product regression: typer/rich wraps long option names in CI, so
help assertions like `"--allow-unauthenticated-exposure" in result.output`
(`tests/unit/test_api_cli_safety.py:68`) fail there while passing locally. Suggested fix: normalise
whitespace/ANSI before asserting, or force a wide `COLUMNS` for help invocations. The repository is public,
so GitHub Actions minutes are not a constraint and Quality runs on push. When a run is red, compare against
the pre-existing failure list rather than assuming new breakage.

## 6. Verify from scratch

```bash
uv sync --group dev --group backtesting --group dashboard
uv run pytest                               # serial; ~11-12 min (683 s recorded); not xdist-friendly
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
