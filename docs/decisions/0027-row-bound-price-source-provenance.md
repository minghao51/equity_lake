# ADR-0027: Row-Bound Price-Source Provenance

**Status:** Accepted (2026-11-07). The owner accepted this policy alongside
ADR-0026. E01-6 implementation still needs its own approved bounded plan and the
encoding/compatibility gates below. Shipped research provenance remains
`unknown` until an approved implementation supplies and validates this evidence.

**Recorded:** 2026-11-07

**Deciders:** Repository owner

**Related:** [ADR-0017](0017-bounded-additive-evolution-and-replay.md),
[ADR-0020](0020-synthetic-demo-lake-as-backtestable-surface.md),
[ADR-0021](0021-row-bound-forecast-provenance.md),
[ADR-0022](0022-future-forecast-provenance-envelope.md),
[ADR-0025](0025-immutable-research-evidence-and-run-manifests.md),
[E01-6 pickup](../handoffs/20261107-01-portfolio-projections-and-open-items.md#6-e01-6-open-item),
[separate P02 policy](0026-portfolio-projection-precision-marks-and-reconciliation.md).

## Context

E01c binds canonical universe identity and input snapshots at the read seam.
The actual code uses `equity-run-manifest/3`; Delta snapshots name the consumed
version and non-Delta reads hash the scanned files. These establish which inputs
were read, not where their price values originated.

`core/schemas.py::STANDARD_COLUMNS` includes identity but no price-origin fields.
`devtools/seed_demo.py::seed_demo` knows whether its fetch or generator branch
produced a frame, but returns that fact in a summary rather than persisting it
on price rows. Its action coverage `source=synthetic_demo_seed` describes action
coverage, not the price source. The sample path and `identity_provenance` are
also not price-origin evidence. Therefore the current `unknown` manifests are
correct, even when input snapshots exist.

A single lake can hold legacy, real and synthetic rows; a table/path-wide flag
would mislabel mixed scopes and replacements. E01-6 needs an explicit storage,
writer, reader and legacy policy before the provenance value can change.

This record extends ADR-0025 Decisions 3 and 4 for price-origin evidence.
It does not amend the forecast envelopes in ADR-0021/0022 or infer forecast
usability from a price marker. Accepted ADR text and historical evidence remain
unchanged. Acceptance establishes policy only, not implementation or source access.

## Decision

### 1. Meaning and limits

Persist a versioned origin assertion on each price row, bound to its exact
`(market, MIC, symbol, date)` and OHLCV payload. Row kinds are
`synthetic | real | unknown`; `mixed` is an aggregate, never a row kind.

- `synthetic`: an identified deterministic generator produced these values.
- `real`: an identified price-source adapter obtained these observations from
  its declared external source, not a synthetic fallback.
- `unknown`: supported evidence is absent or the producer cannot establish origin.

This is locally recorded lineage, not vendor authentication, accuracy, exchange
licensing, retention permission, adjustment correctness, source publication time,
PIT availability, strategy skill or operational readiness. A hash proves a
binding to bytes, not that a provider or a human told the truth.

Under this record, the manifest's `data_provenance` classification describes
**price origin** across the consumed price inputs. It does not certify the
origin/quality of action, feature, prediction or other input families. Display
that limitation wherever the classification is presented. Existing action
coverage, adjustment and availability gates stay independent and fail closed.
Expanding this label to other input families needs a further explicit contract.

### 2. Storage choice: nullable fields on existing Bronze price rows

Use native nullable additive columns on the existing market-price Delta tables,
not a table property, auxiliary sidecar, new dataset or source lookup by path:

| Column | Type | Version-1 meaning |
| --- | --- | --- |
| `price_provenance_version` | Nullable Int16 | Supported envelope version, initially 1; null means legacy/no envelope |
| `price_provenance` | Nullable String | `synthetic`, `real` or `unknown` |
| `price_source` | Nullable String | Non-secret, namespaced producer/source identifier with revision, e.g. a generator or provider-adapter revision |
| `price_source_sha256` | Nullable String | Lowercase SHA-256 of the exact non-secret source price batch or generator evidence described below |

For a supported non-unknown claim all four fields are required. A version-1
`unknown` row carries null source/hash; the legacy state has all four fields
absent or null. Partial envelopes, invalid enums/types/hashes and contradictory
known-version fields are rejected at the write boundary. No fallback label is
inferred from an incomplete envelope. Source strings contain no account data,
credential, URL query token, request header or machine-specific absolute path.

A provider adapter hashes the exact non-secret response price data before
normalization, in a documented deterministic encoding, excluding transport
secrets. A generator hashes its deterministic input manifest: generator revision,
seed, tuning parameters, exact identities/session scope and generated price-batch
digest. The implementation plan must freeze these byte encodings and source IDs
with golden fixtures before code; a generic configuration hash or source name
alone is insufficient. These definitions are implementation entry gates, not claims that
an evidence encoder already exists.

Write the origin envelope and associated price values in the same Delta row
operation. Never preserve an earlier row's marker when replacing its values from
another source, nor fill a new row's missing marker from an existing target row.
Absent evidence remains null/unknown. A merge retry refreshes state without
reconstructing source evidence from the refreshed table or current configuration.

Keep the medallion layout, date partitioning and canonical identity/date keys.
Implementations must follow ADR-0017 additive evolution/OCC, schema constants,
pointblank ingestion validation (including semantic envelope checks), catalog
metadata/regeneration, reader compatibility and migration notes. Nullable legacy
fields must not be rejected by generic all-null/high-null quality checks; the
known-version semantic checks still reject malformed claims. No quality bypass
may allow a false supported provenance claim.

### 3. Who may assert a marker

The component that actually obtains or generates the price batch supplies the
envelope before standardization/persistence:

- The offline demo generator can assert `synthetic` for the rows it generated.
- A source adapter may assert `real` only with its actual response evidence and
  an explicitly documented source identity; unchanged adapters without that seam
  continue producing unknown origin until separately implemented and reviewed.
- A real-fetch request that falls back to synthetic data is `synthetic`, not
  `real`. Partial batches preserve each row's actual branch; no invocation-wide
  requested mode supplies origin to unrelated rows.
- Saved fixture inputs may assert only their recorded evidence. Tests can model
  real-origin envelopes using mocked responses; passing such tests does not
  authorize live source access or turn invented fixture prices into market data.
- Storage, readers, CLI flags, ticker configuration, action coverage, feature
  code, LLMs and research writers may validate/carry evidence but never invent it.

Audit both the canonical `ingestion/writers.py::upsert_dataset` route and direct
`write_delta` seeder routes. Standardization/projection must retain the envelope;
copying a price frame to another root cannot change its origin. A newly approved
writer rejects unsupported envelope versions rather than emitting unvalidated
claims. Dry-run does not fetch/generate, persist a marker or evolve a table.

### 4. Read-seam binding and complete-scope aggregation

Capture marker fields from the same pinned Delta table instance/version or exact
non-Delta file set that produced the consumed prices. Aggregate before a column
projection/cleanup can discard them. Do not re-open a table or inspect current
versions after computation. For non-Delta inputs, freeze/hash the actual scanned
bytes or detect changes and refuse the claim; a concurrent file replacement must
not produce a known origin bound to different bytes.

Classify every consumed price row, including warm-up/history and all markets,
not only trades or terminal marks. If a shared loaded frame supports several
runs, a conservative classification of the complete shared input is permitted
and must disclose that scope; do not claim a narrower per-run subset without
capturing its actual lineage. Reset evidence between loader invocations.

| Consumed price evidence | Aggregate |
| --- | --- |
| Nonempty; all rows supported synthetic; complete snapshot binding | `synthetic` |
| Nonempty; all rows supported real; complete snapshot binding | `real` |
| Nonempty; both supported synthetic and real; complete snapshot binding | `mixed` |
| Any missing/null/explicit unknown/unsupported envelope, incomplete binding, untracked in-memory input or empty scope | `unknown` |

Unknown dominates mixed: known real plus known synthetic plus one unproved row
is `unknown`, with the known counts still observable. A consistent unsupported
future envelope remains readable as unknown with its stored version and reason
visible; it is not interpreted using version-1 rules. A malformed or contradictory
supported envelope is an input validation failure: do not publish a successful
run by dropping the row or downgrading the defect to a healthy unknown result.
Readers do not rewrite any price rows to implement these outcomes.

Retain snapshot names/source/version/digests and record an explicit version-1
price-origin summary per price snapshot: snapshot reference, consumed row count,
counts by origin/unknown reason, source identifiers and a SHA-256 of the sorted
canonical identity/date/envelope projection. Its encoding is another golden-fixture
entry gate. The counts must cover the consumed scope exactly. A manifest validator
re-derives the aggregate from these summaries and refuses a claimed non-unknown
value without complete snapshot-bound evidence. Snapshots alone remain insufficient.
The summary is an auditable local assertion, not authentication or a substitute
for retaining the named source snapshot.

### 5. Evidence protocol and consumer compatibility

Use a new run protocol `equity-run-manifest/4` for the strengthened derivation
and price-origin summaries. Reader support must land before new-format writers;
retain explicit parsing/validation of existing protocol-v3 evidence under its own
rules. If compatibility cannot be preserved, stop and resolve it before writing
new evidence. No old manifest acquires a summary, classification or protocol bump.

The run-ID preimage remains scope-only: the protocol change changes new scope
IDs; price-origin summaries, input snapshots, source digests and code identity
remain manifest evidence, not extra scope-hash inputs. Two same-scope runs with
different input versions still coexist and expose that difference in manifests.
No legacy-path writer is restored. Attempts, cards, latest-success/last-attempt,
API response shapes and display-only evidence references keep their frozen
contracts. W&B and public exports remain out of scope.

This record does not change `FORECAST_PROVENANCE_VERSION`, forecast row statuses,
Gold availability/adjustment proofs or the v3 universe-basis rule. P02 book marks
have their own private `synthetic|unknown` flag and cannot satisfy this envelope.

### 6. Existing lakes, runs and backfill answer

**No retrospective backfill or relabeling.** Existing rows without a complete
supported marker stay unknown indefinitely, including existing demo/sample rows.
Do not infer origin from their path, identity, action coverage source, filenames,
current provider configuration, similar values or a prior terminal summary.

Adding nullable columns during a future authorized write is not permission to
populate old rows. Existing pinned Delta versions keep their original evidence.
A later separately authorized source fetch or isolated synthetic generation may
write new evidenced observations; it must not edit old snapshots/runs or silently
replace the owner's lake to make provenance look complete. Any scope, overwrite,
re-ingestion, schema operation against real stores, migration or retained-source
remediation requires separate authorization and its own reviewed plan.

Historical finalized manifests retain their stored provenance, including unknown;
readers never upgrade them using a newer lake snapshot. Legacy artifacts stay
read-only. D01 licensing, retention, target-root and source-call gates remain
closed. Neither acceptance nor tests for this ADR authorize any live source use.

### 7. Implementation gate and acceptance matrix

The policy gate is satisfied. Before implementation: approve a bounded
synthetic-fixture file plan, freeze source/summary encoding and IDs, and verify
run-reader compatibility and write-boundary coverage. No source call, seeding
command, catalog regeneration or real table mutation follows from acceptance.

Required offline tests for the later implementation:

- Generator and mocked provider round-trips; synthetic fallback cannot inherit a
  requested real label; distinct branches/identities/dates keep their own markers.
- Additive nullable evolution, old-schema reads, merge value/marker atomicity,
  null replacement rather than stale-marker preservation, retries and idempotency.
- Absent/partial/malformed/future-version envelopes; supported/unsupported writer
  refusal and reader behavior; generic quality checks tolerate valid legacy nulls.
- All-synthetic/all-real/mixed/unknown-dominates, empty scope, warm-up inclusion,
  multi-market and shared-frame scope, filtered-out rows and loader reuse.
- Pinned Delta reads and a table advancing after read; non-Delta same-size content
  changes/concurrent replacement; projection retains marker; in-memory input without
  a read seam stays unknown. No version/hash is collected post-computation.
- Manifest non-unknown claims require complete summaries, snapshot binding and
  consistent counts; summaries have golden digests; no-marker runs stay unknown.
- Existing v3 run/attempt/card/latest readers remain compatible, new v4 scope IDs
  are intentional, legacy paths stay frozen and old manifests remain byte-identical.
- Network/LLM calls are forbidden in the fixture suite; no-write dry-run, no
  private values/secrets in evidence, docs and catalog consistency are tested.

Use the existing one-writer/separate-reviewer protocol, formal review verdict,
coordinator mutation/restore proofs, serialized tests and spec §9 final gates.
E01-6 is open until implemented and independently accepted, even after a policy
ADR is accepted. P02 can proceed on its own accepted policy; this record does not
restart or rebuild B00, F01–F04, P01 or E01a/b/c.

## Consequences

Newly evidenced price rows could support an honest synthetic/real/mixed run
classification without pretending that identity, snapshot integrity or action
coverage proves source origin. Legacy data can remain unknown permanently.

Costs include nullable schema/catalog changes, provider/generator evidence seams,
quality-check compatibility, preserving fields through readers and a new run
protocol with explicit old-reader support. Provenance remains self-asserted;
source licensing, exact correctness and publication/PIT proof are separate.

Rejected alternatives: path/table flags; borrowing action coverage `source`;
inferring from ticker metadata or requested CLI mode; detached mutable sidecars;
post-hoc current-version lookups; synthetic/real majority voting over unknown
rows; and relabeling old lakes or immutable runs to improve a showcase.

No commit, provider/LLM access, private account, real ingestion, paid source use,
refit/training, migration/backfill/reset, deployment, W&B change or freshness
widening is authorized. The P02 plan approval does not authorize E01-6 code.

## References

- `src/equity_lake/core/schemas.py::STANDARD_COLUMNS` — current OHLCV/identity
  surface without a price-origin marker.
- `src/equity_lake/devtools/seed_demo.py::seed_demo`, `stamp_identity` and
  `coverage_proofs` — actual fetch/generator branch, direct price writer and
  separate action provenance. These were inspected as source only; no seeder or
  fetch was executed.
- `src/equity_lake/ingestion/writers.py::upsert_dataset` and
  `src/equity_lake/validation/pipeline.py` — price write/quality boundaries and
  current generic null checks.
- `src/equity_lake/backtesting/data_loader.py::BacktestDataLoader._read_market`
  — read-seam snapshots and current final column projection.
- `src/equity_lake/backtesting/evidence.py::InputSnapshot`, `RunManifest` and
  `src/equity_lake/backtesting/report.py::_finalise_arena_run_evidence` —
  current snapshot contract and deliberate unknown provenance.
