# Research Protocols (R00) and R01 measured controls

R00 freezes *what a research run may assume* before any feature, model or backtest
executes. It is offline by construction: it does not ingest, fetch, fit, or write a
lake table. Its record is auxiliary storage (ADR-0006) under
`data/research/protocols/`, written through the Pydantic models in
`src/equity_lake/backtesting/protocol.py`.

R00 is a harness control, not evidence about markets. Synthetic control results are
harness evidence only (spec §7), and computational scale is not economic capacity.

## The frozen field set

One `ResearchProtocol` states, canonically:

| Assumption | Field |
| --- | --- |
| Experiment identity | `experiment` |
| Universe | `universe` (canonical display keys or bare tickers; the basis is derived by parsing) |
| Window | `start_date`, `end_date` |
| Untouched holdout | `holdout_start`, `holdout_end` (must lie inside the window) |
| Execution clock and price | `entry_clock`, `entry_price` (closed vocabulary) |
| Label | `label_horizon_days`, `label_maturity_days` |
| Leakage controls | `purge_days`, `embargo_days` |
| Costs | `costs` (regime name plus the fees and taxes actually applied) |
| Metrics and baselines | `metrics`, `baselines` |
| Acceptance tolerances | `acceptance_tolerances` |
| Synthetic controls | `generator`, `seeds` (predeclared, never chosen after seeing results) |
| Resource budget | `resource_budget` (`max_runtime_seconds`, `max_symbols`, `max_sessions`) |

Set-like fields are sorted and de-duplicated, so the same assumptions in a different
order produce the same identity. The protocol digest is the SHA-256 of the canonical
payload in `PROTOCOL_FIELD_ORDER`; code and time are never part of it.

## Freeze, refuse, emit

`freeze_protocol(protocol, recorded_at=..., root=...)` writes one dated manifest,
`<YYYYMMDD>T<HHMMSS>Z-<digest[:12]>.manifest.json`. The manifest embeds the canonical
protocol, its digest, the code identity (commit plus dirty-diff digest), a digest of
non-secret configuration, and any frozen input digests the caller supplies. Secrets
are stripped before hashing (ADR-0025's rule), so a credential is never hashed or
stored. Manifest bytes are a pure function of the inputs: freezing unchanged
assumptions with the same `recorded_at` reproduces identical bytes, and a different
manifest at the same name is refused rather than overwritten.

`require_frozen_protocol(protocol, root=...)` is the gate in front of a run:

- no manifest for the experiment → `ProtocolError: assumptions are absent`;
- manifests exist but none carries the candidate digest → `ProtocolError:
  assumptions are changed`, naming the differing fields;
- a matching manifest → returned, so R01/R02 can bind their E01 run manifests to
  `record.protocol_digest`.

There is no permissive mode and no automatic re-freeze. Changing an assumption means
explicitly freezing a new dated protocol — a decision, not a fallback.

`load_frozen_protocols(root)` refuses to return a manifest whose digest no longer
matches its embedded protocol, so a tampered or corrupt record can never be read as
evidence.

## R01 — measured controls and scale (delivery status)

R00 shipped *freeze/refuse* controls; R01 (residual plan §2.12, §5) turns them into *measured*
controls under the same freeze machinery. Delivery status: **`verified`** for the frozen
measurement set on its host — six of six leakage controls pass with killed mutants, the
predeclared 100 null and 50 planted seeds completed inside the frozen ceiling, and the 5/50/200
scale points ran under every ceiling with parity intact. Evidence, gate exit codes and the ledger
entry live in [`docs/research/20261123-01-r01-report.md`](../../research/20261123-01-r01-report.md),
the preregistration in
[`docs/research/20261123-01-r01-preregistration.md`](../../research/20261123-01-r01-preregistration.md)
and the machine-readable results in
[`docs/research/20261123-01-r01-results.json`](../../research/20261123-01-r01-results.json).

The frozen manifest for the stage is committed byte-for-byte at
`docs/research/protocols/20261123T135029Z-42d13c802634.manifest.json` (protocol `42d13c80…`,
prespecification `fb20a96c…`, frozen `2026-11-23T13:50:29Z`), because the run-side location
`data/research/protocols/` is auxiliary storage (ADR-0006) and is absent in a fresh clone. Binding
the committed copy is `require_frozen_r01(protocol_root=Path("docs"))`; nothing else can mint a
freeze.

### The controls/scale procedure

1. **Preregister, then freeze.** `r01_spec()` holds the predeclared seed sets (`1729…1828`,
   `11729…11778`), the planted effect size, the statistic and its one estimand, the
   dependence-aware resampling rule, the pass/fail bounds, the scale sizes and the single-host
   ceilings. Its digest is bound into the frozen protocol as `input_digests.r01_preregistration`,
   so a changed prespecification refuses rather than silently reinterpreting a run.
2. **One estimand.** The decision statistic is the **mean per-session net return**, never the
   compounded net return (R02's archive showed they are different estimands); the compounded figure
   is recorded beside it, labelled, and is never compared to the mean's interval.
3. **Run the six leakage controls**, each with a fixed fixture, an expected result and one named
   regression: append-after-cutoff invariance, delayed publication, another instrument's proof,
   pending labels, missing/expired coverage, execution lag and horizon (`ml/r01_controls.py`).
4. **Run the planted positive control and both null variants** over the predeclared seeds, then
   apply the frozen one-sided Clopper-Pearson pair: null false-positive-rate upper bound `<= 10%`
   and planted-power lower bound `>= 80%`. Individual null false positives are allowed; no seed is
   required to lose.
5. **Measure scale** with `python -m equity_lake.ml.r01_scale` — 5/50/200 instruments over the
   three-year design, per-point wall time, peak RSS, I/O counters, decision counts and parity, each
   point isolated in its own child process so its peak RSS belongs to that point alone.
6. **Prove each refusal with a one-rule mutation** (`--mode mutations`): one rule removed at a time,
   the named regression must fail, and the original bytes are restored from a `/tmp` copy verified
   with `diff -q`.

### What R01 refuses to claim

- A missing or failing leakage control leaves the stage `unverified`; no seed count and no scale
  point compensates for it.
- A seed set or scale point that cannot complete inside the frozen ceilings is recorded `censored`
  with the exact reason and the measured per-seed cost, and a censored set may never be read as a
  passing power or scale claim. The budget is never widened: an exceeded ceiling refuses.
- Unavailable I/O counters carry a **reason**, never an invented zero, and an unmonitorable peak RSS
  aborts the run.
- Control results are synthetic-fixture harness evidence, not evidence about markets, not a
  promotion, not a point-in-time claim; computational scale is not economic capacity (spec §7).
