# ADR-0025: Immutable Research Evidence and Run Manifests

**Status:** Proposed (2026-11-03). Policy content is owner-approved for
drafting; the record itself is **not accepted**. Acceptance is a prerequisite
for E01 implementation, and acceptance alone does not authorize publishing,
exporting, or migrating any existing evidence.

**Recorded:** 2026-11-03

**Deciders:** Repository owner

**Related:** ADR-0006 (auxiliary data paths and Pydantic write boundaries),
ADR-0017 (bounded additive evolution and replay), ADR-0020 (synthetic demo lake
as a backtestable surface), ADR-0021 (row-bound forecast provenance), ADR-0022
(future forecast-provenance envelope versions), ADR-0023 (writer-side
`string_view` drift), ADR-0024 (private portfolio ledger and book accounting),
[P00–P03 / E00–E01 specification](../plans/20261102-01-portfolio-and-research-implementation-spec.md)
§4 (E00–E01 reproducible research evidence) and §9 (shared verification and
completion contract).

## Context

A research run is currently a directory of files, not a record.
`write_arena_artifacts` writes each completed pair to
`<base>/<strategy>__<cost_regime>/` and the benchmark curve to
`<base>/benchmark__equity.parquet` (`backtesting/report.py:462`, `:466`), and
`_run_slug` (`:92`) derives that directory name from the pair alone. A second
invocation of the same strategy and cost regime therefore overwrites the first
run's artifacts in place, and nothing in the directory states which invocation
produced it.

The persisted curves carry no dates. `write_backtest_report` writes
`equity.parquet` and `drawdown.parquet` with a synthetic positional column
(`{"t": range(equity.len()), "equity": equity}`, `report.py:72`; drawdown at
`:73`). The backend report does carry real dates next to `creturn`, but
`_extract_equity_curve` (`backtesting/engine.py:454-474`) reduces the frame to
the scaled value column, so the timestamps are dropped before a
`BacktestResult` exists — `equity_curve` is a bare `pl.Series`
(`backtesting/result.py:18`, `:29`). Nothing downstream can recover session
dates, gaps, or an irregular calendar from a stored curve.

Cards point at paths rather than runs. `write_finding_card` writes
`<base>/<id>.json` (`findings/writer.py:67-69`) for the three stable ids
`strategy-comparison` (`report.py:253`), `cost-regime` (`:371`) and
`vs-benchmark` (`:427`), and their `evidence_refs` are relative directory names
such as `"<strategy>__realistic/"` and `"benchmark__equity.parquet"`
(`report.py:259`, `:282`, `:433`). Because those paths are rewritten by the next
run of the same pair, a card that survives an earlier invocation can point at a
different run's curve with no way to tell.

Attempt accounting exists only at the invocation edge. `ArenaOutcome` carries
`requested` and `failed`, and each `ArenaRun` carries
`evaluation_status: evaluated | no_trades | ineligible`
(`backtesting/arena.py:196-219`). The CLI refuses to publish when zero requested
pairs completed (`cli/commands/arena.py:129-180`), and the cards persist counts
of requested/completed/failed/non-evaluable runs in `scope`
(`report.py:182-190`). Per-pair outcome, attempt time, and failure detail are not
persisted per run, so once the terminal output is gone nothing distinguishes
"the last attempt failed" from "the last attempt succeeded".

The readers are honest but identity-free. `list_backtests` globs
`FINDINGS_DIR.glob("*/metrics.json")`, sorts by directory name, and silently
skips unreadable files (`api/deps.py:725-735`); `load_finding_cards` globs
`<base>/*.json` and skips invalid cards with a warning
(`findings/writer.py:85-100`). Neither has a notion of run identity, success,
recency, or last-success age. There is no run manifest, no run ID, and no
`latest` projection anywhere in the tree today.

E01 (spec §4) changes the auxiliary layout under `data/findings/` and the
readers over it, which is exactly the case spec §4 and ADR-0009 gate behind an
accepted record. `data/findings/` is already an auxiliary owner
(`core/paths.py:107`, ADR-0006); this record keeps run evidence there rather
than introducing cataloged run tables or a second research store.

## Decision

### 1. Ownership: run evidence stays an auxiliary artifact

Run evidence lives under `DATA_DIR / "findings"` (`FINDINGS_DIR`,
`core/paths.py:107`) and keeps the existing auxiliary contract: Pydantic v2
models are the schema contract at the write boundary (ADR-0006), and run
evidence is **not** cataloged, **not** pointblank-validated, and **not** a
parallel research platform. No lake tables, no `data/lake/` paths, no catalog
rows are added for runs.

### 2. Immutable per-run layout and finalization

Each invocation writes one run directory:

```text
data/findings/runs/<UTC-timestamp>Z-<short-hash>/
```

containing that run's validated artifacts — equity and drawdown with real dates,
metrics, trades, cards, and the manifest of Decision 3. A run directory is
**finalized** only after its artifacts validate and its checksums exist. A
failed or partial write must not create a finalized run: partial output is
either invisible to readers or explicitly marked failed, never indistinguishable
from a complete run. Collisions are resolved by minting a new run ID, never by
overwriting an existing run directory; two identical-scope invocations must
coexist (spec §4 acceptance).

### 3. Run identity and manifest

Every run carries a unique run ID and a manifest that records at least:

- the run ID, and aware UTC timestamps for attempt start and finalization;
- protocol/version identifiers for the evidence format;
- the exact canonical universe and the exact date window, as requested;
- strategy and model parameters actually used;
- actual fees and taxes applied;
- execution timing assumptions;
- the seed;
- the code commit plus a digest of the dirty diff, if any;
- a digest of the non-secret configuration actually in force;
- data provenance, one of `synthetic | real | mixed | unknown`.

Missing source evidence stays `unknown`. Bronze identity alone does not prove
real data: the presence of a `01_bronze` path is not evidence that the marks are
real, so a run that cannot name its inputs is `unknown`, not `real`. Secrets are
never hashed or copied into a manifest: no `.env`/dotenvx material, API key, or
credential value is read, digested, or stored.

### 4. Snapshot binding

Source snapshots and hashes must identify **what was actually read** at read
time. Collecting current Delta table versions after computation is explicitly
not snapshot binding — a post-hoc version lookup describes the table as it is
now, not the snapshot the computation consumed. Non-Delta fixtures (parquet/CSV
inputs, synthetic lakes, saved prediction frames) need immutable input hashes
taken at read time, so a fixture-driven run is reproducible in the same sense as
a Delta-driven one.

### 5. Failure visibility

Every requested pair is recorded as `completed`, `failed`, or `ineligible`,
together with measured-metric availability (which metrics were actually
measured, as distinct from missing or non-finite) and the failure detail. The
**last attempt** is kept distinct from the **last successful comparable run**, so
a failed attempt cannot leave a misleading healthy "latest", and the age of the
last success stays visible on the same surface as the failed attempt.

### 6. Dated evidence

The backend's actual equity timestamps are persisted alongside values and
drawdown, and out-of-sample identity, date, label, prediction, and fold rows are
persisted where the run produces them. A calendar range is never reconstructed
from a `Series` length. Legacy undated curves remain undated and are labeled
historical — never silently repaired, reindexed, or given invented session
dates.

### 7. API compatibility

`BacktestResult.equity_curve` stays a Polars `Series`; dated evidence is added
through an explicitly tested companion field or view rather than by changing the
existing attribute's type. The default shapes of the current readers stay
unchanged unless a separately approved API change is documented and tested:

- `/findings` returns `list[FindingCard]` and `/findings/{card_id}` a single card
  (`api/routers/findings.py:17-29`);
- `/backtests` returns the stored `metrics.json` payload plus `slug`
  (`api/deps.py:728-734`);
- the notebook path loads cards through `load_finding_cards`
  (`notebooks/11-strategy-lab.ipynb`);
- the W&B projection logs a table of `id/axis/claim/verdict/conclusion/metrics`
  (`ml/registry.py:81-94`);
- static/local readers keep reading the same auxiliary tree.

### 8. Latest view and legacy paths

A "latest" projection points at the newest **successful** run, not the newest
directory. Legacy `<strategy>__<regime>/` directories, `<id>.json` cards, and
`benchmark__equity.parquet` become a read-only compatibility view over the
evidence that already exists. An old card must never point silently at a
different run's newly overwritten curve: legacy references resolve inside the
legacy view, and new cards reference run IDs.

### 9. Retention and migration

There is no automatic migration, deletion, or rewrite of existing findings, lake
artifacts, or model artifacts. Historical runs whose inputs cannot be
established never gain a fabricated manifest and never gain a `real` label.
Corrupt or incomplete runs must not look successful: they are reported as
failed, incomplete, or unknown, and are never surfaced as the latest successful
run.

### 10. Privacy

No private information appears in public or W&B projections. That requirement is
normative; the *mechanism* below is not. A sanitized allowlist should govern
exported projections — fields absent from the allowlist are dropped rather than
serialized and hoped to be harmless — and the W&B table's explicit column list
(`ml/registry.py:81-94`) is the shape such an allowlist must keep. Whether the
W&B projection is in E01 scope at all, and the exact allowlist contents, stay
open (E01-3 / E01-5).

## Open decisions

E01 must freeze these before implementation. They are deliberately not decided
here.

| ID | Open item | Blocks |
| --- | --- | --- |
| E01-1 | The exact run-ID hash inputs: which manifest fields feed `<short-hash>`, their canonicalization and serialization order, and whether the hash covers scope only or scope plus code identity. | The layout, collision handling, and the "two identical-scope invocations coexist" acceptance check. |
| E01-2 | Which readers keep their legacy shapes, and the exact projection from runs back into them (which run backs a legacy slug, and what a legacy card's `evidence_refs` resolve to). | Reader compatibility tests for `/findings`, `/backtests`, the notebook path, and static/local readers. |
| E01-3 | Whether the W&B projection is in scope for E01 at all, or stays out of scope until its own change. | The privacy allowlist tests and any change to the comparison path. |
| E01-4 | The snapshot-hash mechanism for non-Delta fixtures: file-content hash versus immutable fixture-directory hash, and where the read-time seam computes it. | `data_provenance` classification for fixture-driven runs and the synthetic-versus-real distinction. |
| E01-5 | The export allowlist contents and where it is enforced (one seam versus per-reader), if W&B or any public projection is in scope. | Privacy tests for exported projections. |

## Compatibility / migration

No historical artifact is rewritten. Existing `<strategy>__<regime>/`
directories, `<id>.json` cards, `benchmark__equity.parquet`, and
`data/findings/<slug>/metrics.json` stay where they are and are read through the
legacy read-only view (Decision 8). Nothing is moved, renamed, backfilled, or
retroactively given a manifest; undated legacy curves stay undated and are
labeled historical (Decision 6); a legacy card keeps its `evidence_refs`
verbatim. This record schedules no migration — if one is ever wanted it needs
its own approved change with its own tests.

## Consequences

**What `backtests` gains.** Runs become addressable: a run ID, a manifest, dated
curves, per-pair attempt status, and a last-success age that a failed attempt
cannot erase. Readers can tell "no evidence" from "failed evidence" from
"evidence from an unknown source" without parsing terminal output.

**What `findings` gains.** Cards bind to an immutable run instead of a mutable
path, so a card cannot silently inherit a later run's curve. The three existing
card ids and their verdict semantics are unchanged.

**What stays legacy and read-only.** Every artifact written before E01, the
`<strategy>__<regime>/` layout, the flat `<id>.json` card paths, and the
`benchmark__equity.parquet` benchmark artifact. They remain readable; they gain
no manifest, no run ID, and no provenance upgrade.

**Costs and residual limits.** Checksums prove integrity, not truth: a manifest
can describe a wrong computation honestly. Provenance is self-asserted, so a run
that cannot name its inputs stays `unknown` indefinitely, and Bronze identity is
explicitly insufficient evidence of real data. Snapshot binding is only as good
as the read-time seam, so any reader that does not hash at read time produces
`unknown` provenance by construction. Two layouts must be supported while the
legacy view lives, which costs reader branching and test surface.

### Rejected alternatives

- **Keep overwriting the stable `<strategy>__<regime>/` paths.** Rejected: it is
  the root cause — evidence that cannot be addressed cannot be compared, and a
  card can point at a curve it never measured (Context).
- **Catalog runs as lake tables under `data/lake/`.** Rejected: run evidence is
  auxiliary, single-owner, and unvalidated by pointblank (Decision 1, ADR-0006);
  cataloging invites treating research output as a shared contract dataset.
- **One global `latest.json` without last-success semantics.** Rejected: a single
  pointer updated by every attempt makes a failed attempt look like the latest
  evidence and hides the age of the last real success (Decision 5).
- **Derive dates from `Series` length.** Rejected: it invents a contiguous
  calendar, which is wrong for every irregular window, gap, or holiday
  (Decision 6).
- **Hash current Delta table versions after computation.** Rejected: a post-hoc
  version names the table as it is now, not the snapshot that was read
  (Decision 4).
- **Auto-migrate old runs into the new layout.** Rejected: it would fabricate
  manifests and provenance for evidence that never had them (Decision 9).
- **Add a parallel research store or platform.** Rejected: `data/findings/`
  already owns this artifact class; a second store duplicates readers and
  privacy surface (Decision 1).

## References

- [P00–P03 / E00–E01 specification](../plans/20261102-01-portfolio-and-research-implementation-spec.md)
  §4 — the ADR gate and the acceptance list this record encodes; §9 — the
  verification and completion contract E01 must satisfy.
- [ADR-0006: Auxiliary Data Paths with Pydantic Write Boundaries](0006-auxiliary-data-paths.md)
  — the ownership and write-boundary contract run evidence stays under.
- [ADR-0017: Bounded Additive Evolution and Replay](0017-bounded-additive-evolution-and-replay.md)
  — additive evolution, which is why no historical artifact is rewritten here.
- [ADR-0020: Synthetic Demo Lake as a Backtestable Surface](0020-synthetic-demo-lake-as-backtestable-surface.md)
  — why `synthetic` must remain a first-class provenance value, distinct from
  `real`.
- [ADR-0021: Row-Bound Forecast Provenance](0021-row-bound-forecast-provenance.md)
  and [ADR-0022: Future Forecast-Provenance Envelope Versions](0022-future-forecast-provenance-envelope.md)
  — the provenance-envelope precedent for "missing evidence stays `unknown`" and
  "never upgrade evidence by inference".
- [ADR-0023: Writer-Side `string_view` Drift](0023-writer-side-string-view-drift.md)
  — the read-seam discipline the snapshot binding in Decision 4 depends on.
- [ADR-0024: Private Portfolio Ledger and Book Accounting](0024-private-portfolio-ledger-and-accounting.md)
  — the sibling E00/P00 gate; same "no fabricated provenance" posture.
- `src/equity_lake/core/paths.py:107` — `FINDINGS_DIR = DATA_DIR / "findings"`,
  the auxiliary owner for run evidence.
- `src/equity_lake/backtesting/report.py` — `write_backtest_report` (`:57`, dated
  evidence gap at `:72-73`), `_run_slug` (`:92`), `build_finding_cards` scope
  counts (`:182-190`), card ids and `evidence_refs` (`:253`, `:259`, `:282`,
  `:371`, `:427`, `:433`), `write_arena_artifacts` (`:442-466`).
- `src/equity_lake/backtesting/engine.py:454-474` — `_extract_equity_curve`
  drops the backend's date column.
- `src/equity_lake/backtesting/result.py:18`, `:29` — `equity_curve: pl.Series`,
  the interface Decision 7 keeps.
- `src/equity_lake/backtesting/arena.py:196-219` — `ArenaRun.evaluation_status`,
  `ArenaOutcome.requested` / `failed`.
- `src/equity_lake/findings/writer.py:67-69`, `:85-100` — card path
  `<base>/<id>.json` and the best-effort `load_finding_cards`.
- `src/equity_lake/api/deps.py:725-735` — `list_backtests` glob, name sort, and
  silent skip.
- `src/equity_lake/cli/commands/arena.py:129-180`, `:312` — the zero-completion
  refusal and the single-run write to `<strategy>__<cost_regime>`.
- `src/equity_lake/ml/registry.py:81-94` — the W&B table column list that the
  Decision 10 allowlist must keep.
