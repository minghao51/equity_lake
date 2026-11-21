# H00: The Sanitized Demo-Lake Page (build and preview only)

H00 is a **separate, offline artifact**: one static page composed from the synthetic
demonstration lake at `data/lake/`. It is not the dashboard (`equity dashboard build`)
and it is not published by this stage. The contract it implements is the H00 review
([§4-§6](../reports/20261117-h00-sanitized-demo-lake-page-review.md)) as adopted on
2026-11-21 ([owner decisions](../reports/20261121-h00-owner-decisions.md)).

> **Publishing is not implemented.** Building and previewing are local operations on a
> loopback address. Publication (and therefore any public operator identity) requires a
> separate, explicit owner approval; nothing in this page's build path deploys, uploads,
> pushes or schedules anything.

## Build

```bash
uv run equity dashboard h00-build
```

The build writes one composed artifact to `data/research/h00/`:

| File | What it holds |
| --- | --- |
| `index.html` | The page: synthetic-data banner first, then the allowlisted inventory |
| `payload.json` | The same allowlisted payload, machine-readable (`"provenance": "synthetic"`) |
| `sanitization-report.json` | The negative-scan result, inside the artifact and subject to the same scan |

`--output-dir/-o` writes somewhere else (useful for review copies). The build takes no
lake-root option: it reads the canonical synthetic root and refuses every other root, so
`data/lake_real/`, `data/d01/`, the private portfolio directory, watchlists/tracking,
`.env`, `logs/` and the pipeline-results files cannot be selected as inputs.

## What the page may contain

Fields are named in one place (`equity_lake/dashboard/h00.py`) and serialized from there,
so a field that is not named does not exist in the artifact:

- page chrome and static prose written by the builder;
- the synthetic-data banner, the demo notice and the adopted performance disclaimer;
- demo dataset inventory: lake-relative name, availability, rows, symbols, latest date —
  **no** dataset path, no settings, no alert text, no run metadata;
- the corporate-action coverage-proof summary (`source`, `coverage_status`, window,
  `source_valid_until`) and the verified provenance labels
  (`synthetic_demo_seed`, `current_ticker_config`);
- schema/feature inventory: column names, column count and schema versions;
- aggregate synthetic metrics only (owner decision 1). Fixture predictions are a row
  count and a column count — no row, no model version, no probability value;
- one page build timestamp and the builder version, nothing else time-stamped.

Dropped by scope, not by filtering: settings dumps, absolute paths, health/update status
(owner decision 7), digests, per-symbol figures, prediction rows, vendor or provider
attribution text (owner decision 8), and any real/live-market claim.

## Why the build can be trusted

The build composes every file **in memory**, then runs a negative scan over the composed
bytes before writing. It fails closed — nothing is written — if any of these appears:

absolute paths, real-slice fingerprints (`lake_real`, probe artifacts, the verified real
row canaries), ledger/watchlist names, every value of the local `.env` (compared in
memory, never logged), any 64-hex digest, raw alert or run-metadata text, vendor strings
(`yfinance`, `akshare`, `efinance`, `JPX`, `KRX`), and the forbidden claims of review §6.

Before any number is published, each contributing demo table is attested: price rows must
carry `identity_provenance = current_ticker_config` and, when the ADR-0027 envelope is
present, `price_provenance = synthetic`; the coverage proof and the feature rows must
carry `source`/`action_coverage_source = synthetic_demo_seed`. An unlabelled or
vendor-labelled table aborts the build instead of being summarized.

## Preview

```bash
uv run equity dashboard h00-preview          # http://127.0.0.1:8765/
uv run equity dashboard h00-preview --port 9000
```

The preview reuses the private-console loopback gate
(`dashboard.streamlit_app.is_loopback_bind_address`): a non-loopback host is refused
before a socket is opened, and there is no public-server variant. Open
`http://127.0.0.1:8765/index.html` and read `sanitization-report.json` next to it.

## Staleness

A page is stale when its build instant is more than **30 days** old (owner decision 4).
A stale page shows the stale banner — `stale — rebuild due` — plus its last build date,
instead of presenting its numbers as fresh. Rebuild with `equity dashboard h00-build`;
`equity dashboard h00-preview` warns on start when the artifact on disk is stale.

## Scope and non-claims

- The build never writes outside `data/research/h00/`. The canonical dashboard export,
  `data/lake/`, `data/lake_real/` and `data/catalog.jsonl` are untouched.
- The page describes a synthetic demonstration surface (ADR-0020). It is not market data,
  it is not a performance claim about any real strategy, and it is not investment advice.
- Building proves the artifact is clean; it does not authorize publication, a Pages
  deployment, a schedule, or any availability claim.
