# Research Protocols (R00)

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

## What R00 does not do

R00 does not run a backtest, mint a backtest run manifest, produce a feature or
write anything under `data/lake/` or `data/lake_real/`. Binding an E01 manifest
(ADR-0025) and running features remain the gated R01/R02 stages; a Gold table in the
real root still requires its own scoped authorization (ADR-0030 decision 10).
