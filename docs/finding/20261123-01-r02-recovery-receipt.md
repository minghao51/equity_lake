# R02 archive recovery receipt (2026-11-23)

**Stage:** RS-R02-D1 — archival verification and offline recomputation. **No refit, no retune, no promotion, no
artifact rewrite.** The archive under `data/research/r02/` is gitignored (`.gitignore`: `data/*`), so this tracked
file is the *anchor*: the recorded sha256 of every archived file plus the two digest bindings. Verification reads
this table; it never regenerates the archive.

Why a receipt: the R02 run (`docs/reports/20261121-r02-result.md`, `promoted=false`) had no recorded digest of its own
emitted files, so "unchanged since the run" was unverifiable. It is verifiable from this file forward — the six
digests below were taken once, before any verification work, from the operator's live archive, and are reproduced by
every rerun of the command in §2.

## 1. Digest anchor

Recorded 2026-11-23 (UTC) from the live archive `data/research/r02/`, before this stage touched anything.

| Artifact | sha256 | Bytes |
| --- | --- | --- |
| `protocol.json` | `56c1c502f8e64bb9ff87a9877b5ce66ab0af649207371bbe3a2a6db100087f59` | 4765 |
| `metrics.json` | `5deb90fd49af2e43bb62a0453cef846143df8938f8fed306151d2140f35c3c0e` | 38049 |
| `predictions.parquet` | `13544b950bbc2a33e7a8872af5f7cfe639c8bfe06138d3b4d71c27cc9db20467` | 20565 |
| `baselines.json` | `c2e5435f61bab2aed4cbea6a8f52313b6543b32f07a70c0a7c8a7d95b11909b2` | 6404 |
| `run.json` | `3ffc600144ff095a4914c7a1217ba24ac7c86924e16b95886e01099c24aa16f6` | 2301 |
| `report.md` | `95b9eca89762edb90bdf3bf8a5b6a201f3e39e42d6ee9afc1fcbb3bc0f71f432` | 7108 |

Digest bindings (the protocol↔prespecification pair, recorded inside every artifact and in the R00 manifest):

| Binding | sha256 |
| --- | --- |
| `protocol_digest` | `37fd6727d62f927d28d2f5e312f57d173eb3ac07155f01ccd2152993540e5c21` |
| `prespecification_digest` | `ae74513b2ada97711e31477709352edec5ee75635dae36ee526e7796ab980b69` |

The manifest is `data/research/protocols/20261121T130000Z-37fd6727d62f.manifest.json`
(`recorded_at` 2026-11-21T13:00:00Z, `input_digests.r02_prespecification` = `ae74513b…`).

## 2. Verification and recomputation command

`prediction input` — `predictions.parquet`: 575 rows × 17 columns (validation 285, test 290), 5 instruments, test
decision dates 2024-07-02..2024-09-23, no holdout row. `baselines.json` — matched-date comparison for
`validation`/`test`. Neither file is written by anything below.

```bash
# 1. Recover a read-only copy of the archive (the live root is gitignored; copy, never verify in place).
mkdir -p /tmp/r02-recovery && cp -a data/research /tmp/r02-recovery/

# 2. Verify digests + bindings and recompute every metric offline. Read-only; exit 0 = verified,
#    1 = tamper/mismatch, 2 = nothing to verify. --lake-dir supplies per-session closes so the
#    economic metrics and every bootstrap interval are recomputed too (no model is fitted).
uv run python -m equity_lake.devtools.verify_r02_artifacts \
  --research-dir /tmp/r02-recovery/research/r02 \
  --anchor docs/finding/20261123-01-r02-recovery-receipt.md \
  --lake-dir data/lake_real
```

Observed on 2026-11-23: 275 checks, **0 mismatches**, exit 0. The six digests equal §1 exactly, before and after.

## 3. Recomputed vs reported (test fold, 2024-07-01..2024-09-30)

Recomputed with `ml/experiment.py`'s own estimators: `_discrimination`, `_economic_summary`,
`_strategy_returns`, `_bootstrap_economics`, `_bootstrap_discrimination`, `_bootstrap_paired_difference`,
`_promotion_verdict` (block 5 sessions, 2000 resamples, seed 20261121, 95% percentile) — from
`predictions.parquet` plus the frozen Gold closes. Values are exact to 1e-9; the report's own 4-dp rendering is
reproduced cell by cell.

| Arm | Brier | ROC AUC | Net return | Sharpe | Max DD |
| --- | --- | --- | --- | --- | --- |
| Primary | 0.3835 (rec. 0.383511) | 0.4269 (0.426928) | −9.99% (−0.099942) | −1.16 (−1.159692) | −17.52% (−0.175209) |
| Simple | 0.4710 (0.471028) | 0.4574 (0.457350) | −7.77% (−0.077663) | −1.09 (−1.094587) | −14.88% (−0.148817) |
| Technical rule | 0.5034 (0.503448) | 0.4837 (0.483746) | −7.20% (−0.072039) | −1.56 (−1.557100) | −12.62% (−0.126165) |
| No-skill | 0.2643 (0.264349) | 0.5000 (0.500000) | −4.88% (−0.048808) | −0.93 (−0.929618) | −12.14% (−0.121442) |

Also reproduced exactly: all 16 block-bootstrap discrimination intervals (Brier and ROC AUC, 4 arms × 2 folds), all
24 economic intervals, the validation fold (+9.23% primary, −14.03% technical), the matched-date comparison deltas
(`primary_minus_technical_net_return` = −0.027903034916643032, `primary_minus_simple_model_net_return` =
−0.022278556059746824, Sharpe delta 0.397408, both Brier deltas), `folds_evidence` row/session/date counts, the
no-skill probability = the recorded train base rate, and the promotion verdict: exactly one criterion met (`risk`),
`promoted=false`, `would_promote_if_authorized=false`. **No mismatch was found in any number.**

## 4. Estimand finding: the −2.79 pp / [−0.42 pp, +0.31 pp] pairing

`docs/reports/20261121-r02-result.md` states the paired net-return difference is **−2.79 pp** with a 95%
block-bootstrap interval **[−0.42 pp, +0.31 pp]**. The point estimate lies outside its own interval. This is a
**documentation/aggregation mismatch between two different estimands, not a computation error, and not a bug** —
verified by recomputing both quantities from the immutable artifacts:

| Quantity | Estimand | Value (test fold) | Where it comes from |
| --- | --- | --- | --- |
| −2.79 pp | difference of **compounded** net returns per arm (`cumprod(1+r)` minus one) | −0.0279030349 (−2.79 pp) | `baselines.json` `primary_minus_technical_net_return`; identical to −0.09994159932738922 − (−0.07203856441074619) |
| −0.0374 pp | **mean per-session** net-return difference (matched dates) | −0.0003740027 (−0.0374 pp per session) | `mean_daily_return` primary − technical, from `_strategy_returns` |
| [−0.4161 pp, +0.3066 pp] | 95% interval **for the mean per-session difference** | [−0.0041605634, +0.0030657660] | `_bootstrap_paired_difference` → `difference[positions].mean(axis=1)` |

`_bootstrap_paired_difference` resamples the *mean of per-session differences*, not the difference of compounded
returns, so its interval cannot bracket the compounded point estimate by construction. The per-session value
(−0.000374) is inside the recorded interval, as it must be — the interval is correct for the quantity it resamples.
Read together, the pair means: *the compounded net-return difference is −2.79 pp (no interval recorded for it), while
the mean per-session difference is −0.04 pp per session with a 95% interval of [−0.42, +0.31] pp that includes zero.*

Consequence for the frozen verdict: **none.** The `economics` criterion requires `test_delta > 0` *and* the interval's
lower bound above zero (`_promotion_verdict`); the compounded delta is negative, so the criterion fails on its first
condition regardless of the interval. The 1-of-5 result and `promoted=false` stand.

Where the same pairing appears (all append-only, none rewritten): `data/research/r02/report.md` (the row
`| net return difference (primary - technical) | -0.0279 | [-0.0042, 0.0031] |`), the `economics` detail string in
`metrics.json`/`report.md`, and `docs/reports/20261121-r02-result.md`. A future artifact should label the two
quantities separately (compounded difference vs mean per-session difference); that is a report-generator wording fix
for a gated stage, deliberately **not** made here. The correction trail is appended in
`docs/reports/20261121-r02-result.md` §"Correction (2026-11-23, append-only)".

## 5. What is verified and what is not

Verified (from the archive alone, or archive + frozen Gold closes):

- File integrity against §1, and the archive holds exactly those six files.
- `protocol_digest`/`prespecification_digest` re-derived from the frozen code (`r02_protocol().digest()`,
  `r02_spec().digest()`) — they still equal `37fd6727…`/`ae74513b…` at this commit, so the code still freezes the
  design the run used; every artifact and every prediction row carries both; `protocol.json` embeds the frozen
  protocol payload and prespecification; the R00 manifest self-validates, is byte-canonical, and binds
  `input_digests.r02_prespecification`.
- Every metric, interval, count and the promotion verdict listed in §3, reproduced by the frozen estimators with no
  model fitting.

Not verified / out of scope:

- The upstream Gold rows themselves (`.gitignore`: `data/*`): the archive proves *what the run computed from the rows
  present*, not that those vendor rows are complete or point-in-time. ADR-0032/0034's weak basis
  (`vendor_events_no_clock`) stands unchanged; nothing here upgrades it.
- The run's wall-clock and `run.runtime.wall_time_seconds` (11.35 s) are recorded, not reproducible.
- `data/lake_real` is required for the economic recomputation. Without it (e.g. a fresh clone, CI) the module still
  verifies digests, bindings and all discrimination metrics, and prints the economic metrics as **UNVERIFIED with
  that reason** rather than passing them silently.
- No refit was performed and no metric was recomputed from a re-fit; `run.json` still records `fits=1, refits=0,
  sweeps=0, holdout_tuning=false`, and the holdout (2024-10-01..12-31) remains unread (`rows_read=0`).

## 6. Stage evidence

- Verification module: `src/equity_lake/devtools/verify_r02_artifacts.py` (read-only; never opens a file for writing).
- Tests: `tests/unit/test_r02_artifact_recovery.py` — tamper, perturbed predictions, mutated binding, absent archive.
- Before/after proof: the six sha256 values in §1 are identical before and after this stage (`sha256sum
  data/research/r02/*`), and `git status` shows no change under `data/` (it is gitignored).
