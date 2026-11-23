# RS-R01 preregistration — R00 controls and scale evidence

**Status: FROZEN before any control or scale measurement** (residual stage RS-R01; residual plan
§1.4, §2.12, §5). This document is the human-readable half of the freeze; the machine-readable
half is
[`20261123-01-r01-preregistration.json`](20261123-01-r01-preregistration.json) and the frozen
manifest itself is
[`protocols/20261123T135029Z-42d13c802634.manifest.json`](protocols/20261123T135029Z-42d13c802634.manifest.json).

The stage's own rule: the plan's numbers were *chosen* (§2.12), not offered as questions, so the
only thing this freeze may do is record them verbatim and make them inescapable for the run.

## Frozen identity

| Item | Value |
| --- | --- |
| Experiment | `R01-controls-scale-2026` |
| Protocol digest (SHA-256, assumptions only) | `42d13c802634bb4f4bc127db5991d2fe85ad5f7ee8ebf64461b49c4817e2a481` |
| Preregistration digest (SHA-256 of the prespecification) | `fb20a96cfdeff0090303a69995d4d968b4fae64a3a1002cecdbed540b99d33e4` |
| Frozen at (UTC) | `2026-11-23T13:50:29+00:00` |
| Code identity at freeze | commit `0a3802eaaa9955c75d48889c3fa82d6b537765c0`, clean tree |
| Manifest bytes sha256 | recorded in the JSON above; the manifest is committed byte-for-byte |

The manifest lives in two places by design. The run writes it to `data/research/protocols/`
(auxiliary storage, ADR-0006, gitignored — absent in a fresh clone), and the *same bytes* are
committed at `docs/research/protocols/` so a reviewer can prove the preregistration predates the
results. `require_frozen_r01(protocol_root=Path("docs"))` binds the committed copy, and a fresh
clone can therefore re-verify the digest without the operator checkout:

```bash
uv run python -c "
from pathlib import Path
from equity_lake.ml.r01_controls import require_frozen_r01
frozen, protocol, spec = require_frozen_r01(protocol_root=Path('docs'))
print(frozen.protocol_digest, spec.digest())"
```

## What is frozen (residual plan §2.12, verbatim defaults)

| Frozen item | Value |
| --- | --- |
| Nominal one-sided alpha | `0.05` |
| Null seeds (100) | `1729…1828` on the five-instrument control fixture |
| Planted-control seeds (50) | `11729…11778` on the same fixture |
| Pass rule | one-sided 95% binomial **upper** bound on the null false-positive rate `<= 10%` **and** one-sided 95% binomial **lower** bound on planted-signal power `>= 80%`, with all six leakage controls passing |
| Individual null seeds | never required to lose; a null false positive is allowed |
| Statistic | `mean_per_session_net_return` |
| Estimand (named, one only) | the arithmetic mean of the per-session net returns of the long/flat policy over the evaluation sessions — **not** the compounded net return, which is recorded separately and never compared to this interval |
| Dependence-aware resampling | moving-block bootstrap over sessions, block = 5 sessions, 2000 resamples, bootstrap seed = the predeclared control seed itself, one-sided 5% quantile as the lower bound; a seed "detects" only when that bound clears zero |
| Planted effect size | `+1.0%/session` drift on sessions whose trailing 20-session momentum is positive (nothing on negative-momentum sessions); frozen round-trip cost `58.5 bp` per five-session block (`≈11.7 bp/session`) is charged in full |
| Scale sizes | `5`, `50`, `200` instruments over the three-year design `2022-01-03 … 2024-12-31` (782 Mon–Fri sessions), warmup `25` sessions, minimum universe `5` instruments |
| Per-size ceiling | `1800 s` (30 min) |
| Aggregate ceiling | `7200 s` (2 h) for controls **plus** scale |
| Peak RSS ceiling | `min(8 GiB, 50% of host RAM)`; abort if RSS cannot be monitored |
| I/O counters | measured from `/proc/self/io`, or reported `available: false` **with a reason** — never invented zeros |
| Promotion | none: `promoted = false` in every artifact |

The freeze names the controls it will measure, so the ledger cannot lose one later:

1. `append_after_cutoff_invariance`
2. `delayed_publication_invariance`
3. `foreign_identity_proof_refusal`
4. `pending_labels_excluded`
5. `missing_or_expired_coverage_refusal`
6. `execution_lag_and_label_purge`

Each has one named regression in `tests/unit/test_r01_controls.py`, and
`python -m equity_lake.ml.r01_controls --mode mutations` removes one rule at a time, requires the
named regression to fail, and restores the original bytes from a `/tmp` copy verified with
`diff -q`.

## Disclosures made at freeze time (nothing hides behind the digest)

- **Fixture design calculation, before the freeze.** The planted drift strength was fixed by a
  design calculation on fixture seeds `1001–1005`, which are *outside* both predeclared sets: at
  `1.0%/session` all five detected the plant (mean per-session net returns `+0.23%` to `+0.65%`),
  which is pre-freeze evidence that the harness is not blind. No predeclared seed informed the
  frozen strength.
- **One superseded observation, disclosed.** While developing the harness, at the superseded
  `0.4%/session` plant strength, predeclared planted seeds `11729` and `11730` were inspected.
  Those observations are recorded here rather than hidden, and both seeds stay in the frozen set
  unchanged; the frozen power result therefore includes them honestly.
- **Scale fixture provenance.** No shipped ticker pool holds 200 instruments (`us_equity` 44 +
  `cn_ashare` 36 + `hk_sg_equity` 25 = 105), so the scale fixtures are generated by
  `equity_lake.devtools.seeding.synthetic_ohlcv` with the `MARKET_CONFIGS` profile: shipped pools
  in order for the first 105 instruments, then deterministic `SCALE####` ids. That is a
  computational fixture, not market data, and computational scale is not economic capacity.
- **Control results are harness evidence.** They are never evidence about markets, never a
  promotion, and never a point-in-time claim (spec §7; ADR-0034).

## What would make this stage unverified

A missing or failing leakage control leaves RS-R01 `unverified` — no seed count and no scale point
can compensate for it. A seed set that cannot complete inside the frozen ceilings is recorded
`censored` with the exact reason and the measured per-seed cost, and a censored set may not be
read as a passing power claim. A scale point over a ceiling refuses; the budget is never widened.
