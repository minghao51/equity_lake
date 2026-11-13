# Next Agent — E01-6 Row-Bound Price-Origin Implementation

**Recorded:** 2026-11-07

**Status:** ADR-0027 is **Accepted policy** and the owner has **approved the bounded
E01-6 plan and decisions D1–D8** below. Implementation has **not started**: no
schema, writer, loader, manifest or catalog change exists, and `data_provenance`
remains `unknown` on every shipped path — which is the correct current output, not
a bug. This pickup is the entry point; the gates in §6 are still binding.

**Checked baseline:** HEAD `f2e34f4` on `main`, tree clean, `HEAD == origin/main`.
Shipped and pushed this session: `104def0` (E01b.1/E01b.2/E01c), `43a6c0a` (P02),
`5d02f0f` (D8 import-header fix), `1bd21a1` + `f2e34f4` (design artifacts),
`b80b73b` (dashboard IA parity guard). Do not redo any of those.

## 1. Read first

1. [ADR-0027](../decisions/0027-row-bound-price-source-provenance.md) in full — the
   frozen policy (meaning/limits, storage, who may assert, read-seam aggregation,
   protocol v4, no-backfill answer, implementation gate).
2. [ADR-0025](../decisions/0025-immutable-research-evidence-and-run-manifests.md)
   decisions 3–4 (evidence and snapshot binding) and
   [ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md) §11
   (privacy) for the boundaries this must not cross.
3. Specification §5.3, §9 (verification contract) and the latest §10 entries.
4. The approved decision record in §2 below, then the code seams in §3.

Design context (not contracts): `assets/svg/e01-6-price-origin-evidence-flow.svg`
draws this plan end to end.

## 2. Approved decisions (owner, 2026-11-07) — implement these, do not re-open

| ID | Decision |
| --- | --- |
| **D1** | First slice is **generator-only**: `devtools/seed_demo.py` asserts `synthetic` end to end. Real adapters keep producing **absent/unknown** — no `real` claim, no source access (D01 stays closed). |
| **D2** | `price_source` grammar: `^(generator\|provider):[a-z0-9][a-z0-9_]{2,31}@[1-9][0-9]{0,3}$`. This slice emits only `generator:synthetic_demo_seed@1`. |
| **D3** | Canonical byte encodings and golden digests, frozen in §4. |
| **D4** | **v4 for all new runs** (`equity-run-manifest/4`), reader support lands **before** new-format writers; v3 evidence is parsed under its own rules and left byte-identical. Run-ID preimage stays scope-only, so v4 intentionally yields new scope IDs for the same scope. |
| **D5** | Nullable `Int16` version + 3 nullable strings; semantic checks reject malformed/partial claims and tolerate valid legacy nulls; slice 1 writes only the demo/`us_equity` route. |
| **D6** | **No retrospective backfill or relabeling.** Unmarked legacy rows/runs stay `unknown` forever. |
| **D7** | Offline test families in full (ADR-0027 §7); no network, provider or LLM call, no seeding run, no real-table mutation. |
| **D8** | Import-header duplicate guard — **already done** in `5d02f0f`; nothing to do. |

## 3. Code seams (verified at pickup)

| Seam | File / anchor | Today |
| --- | --- | --- |
| Price schema | `core/schemas.py::STANDARD_COLUMNS` (line ~48) | canonical identity + OHLCV, **no** price-origin fields |
| Canonical write route | `ingestion/writers.py::upsert_dataset` (~151) | writes values only |
| Direct seeder route | `storage/delta.py::write_delta` (~75) | writes values only |
| Generator branch | `devtools/seed_demo.py::seed_demo` (~225–273) | knows origin (`source = "synthetic"`, `_try_real_fetch` fallback) but only logs it; `write_delta(...)` persists prices without it |
| Read seam | `backtesting/data_loader.py` (snapshot names `price_marks:<market>`, `action_coverage:<market>`, `source` in `lake:delta`/`lake:parquet`) | records `(name, sha256, source, version)` as plain mappings; no origin |
| Manifest | `backtesting/evidence.py` (`RUN_PROTOCOL` line 117, `RunManifest` ~597, `InputSnapshot` ~412, `_provenance_needs_evidence` validator) | v3; `data_provenance` defaults `unknown` and only requires `input_snapshots` |
| Manifest writers | `cli/commands/arena.py` (~625), `backtesting/report.py` (~831) | pass `data_provenance="unknown"` literally |
| Readers | `backtesting/evidence_view.py` | read runs/attempts; no v4 awareness |
| Validation | `validation/pipeline.py` | price checks; no envelope semantics |
| DuckDB mirror | `storage/duckdb.py` (`_EMPTY_VIEW_COLUMN_TYPES` ~35) | would cast a new version column as VARCHAR unless `SMALLINT` is added |
| Source projections | 5 modules under `sources/` referencing `STANDARD_COLUMNS` | must yield **nulls** for the new columns, not drop them |
| Catalog | `catalog/datasets.py` + `uv run equity catalog-generate` | must stay byte-identical unless schema metadata is genuinely cataloged — prove it |

**Blast radius note:** `STANDARD_COLUMNS` is not writer-local. It drives the DuckDB
`equity_all` view DDL and five source projections; that is why D4 mandates
reader-first and why the columns must be additive-nullable.

## 4. Frozen encodings (D3) — implement exactly, pin with golden fixtures

Canonical rules: UTF-8, no BOM, no trailing newline, `"\n"` separators, lines sorted
lexicographically; JSON via `sort_keys=True, separators=(",",":")`,
`ensure_ascii=True`; decimals as canonical plain text (never float/exponent);
enums lowercase. Never include a path, hostname, timestamp, environment value,
credential, URL/token or account data.

1. **Price-batch digest** — lines `market|MIC|symbol|date|open|high|low|close|volume|adj_close`:
   `us_equity|XNAS|SYNT|2024-01-03|100|101|99|100.5|1000|100.5` and
   `us_equity|XNAS|SYNT|2024-01-04|100.5|102|100|101.5|1200|101.5`
   ⇒ `price_batch_sha256 = ba8ca8a731c8a6f2bebc8055d32b79635a233a3f5f5365de50358a19bb736283`
2. **Generator evidence preimage** (JSON, canonical) ⇒ `price_source_sha256 = 1f1a431537a128473a40551224a93fb435231a57d56eb99567603ff83b804959`:
   `{"generator":"synthetic_demo_seed","generator_revision":"1","identities":["us_equity|XNAS|SYNT"],"parameters":{"days":2,"profile":"demo_v1","seed":7},"price_batch_sha256":"ba8ca8a7…736283","schema":"price-source-evidence/1","sessions":{"end":"2024-01-04","start":"2024-01-03"}}`
3. **Summary envelope projection** — lines `market|MIC|symbol|date|version|provenance|source|source_sha256`, `-` for null
   ⇒ `envelope_sha256 = 341fc14bb53082ee902654272b7f2d1433d386510526544565b44921d53e5d9d`
4. **`PriceOriginSummary` v1** (one per price snapshot, inside the v4 manifest):
   `{schema_version: 1, snapshot{name, source, version, sha256}, consumed_rows, counts{synthetic, real, unknown, unknown_reasons{}}, sources[], envelope_sha256}`.
   Consistency rules: `synthetic + real + unknown == consumed_rows`; when
   `unknown > 0`, `sum(unknown_reasons.values()) == unknown` and the reason keys are
   the closed set (`missing_envelope`, `unsupported_version`, `incomplete_binding`,
   `untracked_input`, `empty_scope`); `unknown == 0` ⇒ no reasons;
   `consumed_rows == 0` ⇒ aggregate `unknown` regardless of counts.
5. **Aggregate** (validator re-derives; never trusts the claim):
   all synthetic + complete binding ⇒ `synthetic`; all real ⇒ `real`; both ⇒ `mixed`
   (aggregate only, never a row kind); any missing/null/unknown/unsupported/
   incomplete/untracked/empty ⇒ `unknown`. A non-`unknown` claim without complete,
   snapshot-bound summaries is **refused**, not downgraded.

## 5. Bounded file plan (three sequential commits)

**Commit 1 — reader-first (no behaviour change; writer stays v3)**
- `backtesting/evidence.py` (M): `RUN_PROTOCOL_V4`, `PriceOriginSummary` (+counts), optional `price_origin_summaries` on `RunManifest`, validators implementing §4.4–4.5 and refusing summaries on a v3 manifest; v3 path byte-identical.
- `backtesting/evidence_view.py` (M): read v3 and v4; additive fields only.
- `tests/unit/test_evidence_protocol_v4.py` (A): v3 byte-identical reads, v4 round-trip, re-derivation, `unknown` dominates, malformed/future-envelope handling.

**Commit 2 — storage + write boundary**
- `core/schemas.py`, `ingestion/writers.py`, `storage/delta.py`, `devtools/seed_demo.py`, `validation/pipeline.py`, `storage/duckdb.py`, the 5 `sources/*` projections, `catalog/datasets.py` (+ regenerate, prove byte identity).
- `tests/unit/test_price_provenance_envelope.py` (A) + extend `test_seed_demo.py`, `test_ingestion_writers.py`, `test_delta_schema.py`, `test_a3_delta_reads.py`: round-trip, atomic row+marker, null-replacement, retries, malformed/partial/future refusal, legacy-null tolerance, dry-run writes nothing.

**Commit 3 — read-seam aggregation + manifest wiring**
- `backtesting/data_loader.py` (M): capture marker fields from the **same pinned table instance/version**; aggregate **before** projection; reset between loads.
- `cli/commands/arena.py`, `backtesting/report.py` (M): derived aggregate instead of the hardcoded `unknown`; emit v4.
- Extend `test_evidence_snapshots.py`, `test_arena.py`, `test_report_backtest.py`: pinned-version binding, table advancing after read, shared-frame scope disclosure, filtered rows, loader reuse, empty scope ⇒ `unknown`.

**Docs:** `docs/user-guide/backtesting.md`, `ARCHITECTURE.md`, spec §10 outcome, `docs/README.md` status block. No new ADR (ADR-0027 covers it); no Settings key; no dependency.

## 6. Gates that remain closed

No retrospective backfill or relabeling (D6); no `real` claims or provider/source
access (D01: licensing, retention, target root, spend); no change to
`FORECAST_PROVENANCE_VERSION`, forecast envelopes, Gold availability/adjustment
proofs or the v3 universe-basis rule; P02 book marks (`synthetic|unknown`) can never
satisfy this envelope; legacy artefacts stay read-only; no seeding run, no
`data/` mutation, no LLM/provider call; P03 remains unauthorized.

## 7. Process (same protocol as P01/P02/E01)

One bounded writing worker at a time with explicit allowed paths; a **separate**
adversarial reviewer per commit (formal first-line verdict `PASS` /
`NEEDS-REVISION` / `BLOCKED`), coordinator mutation/restore proofs from `/tmp`
backups (`diff -q` identical, never from HEAD), serialized tests, external
checkpoints. Then the full gates: focused → serial → parallel → ruff check/format →
strict mypy → catalog regeneration (byte identity, isolated path) → `make lint-md`
→ `git diff --check`, plus an LSP probe on changed files. Record actual counts;
never quote JUnit totals as passes.

## 8. Paste into the next agent

```text
Pick up docs/handoffs/20261107-03-e01-6-implementation-pickup.md and implement E01-6
under accepted ADR-0027 using the already-approved decisions D1–D8 (§2) and the
frozen encodings + golden digests (§4). Do not re-open the decisions.

Verify git status first (HEAD f2e34f4, tree clean); preserve everything shipped.
Do NOT implement beyond the bounded file plan (§5): three sequential commits,
reader-first, and stop for owner review after each commit lands.

Slice scope: generator-only synthetic claims (D1); v4 for all new runs with
reader-first compatibility (D4); nullable Int16 + 3 nullable string columns (D5);
no backfill or relabeling (D6); offline tests only (D7). D8 is already done.

Hard refusals: no real-provider claims or source access (D01 closed); no change to
FORECAST_PROVENANCE_VERSION, forecast envelopes or Gold proofs; P02 book marks cannot
satisfy the envelope; no `data/` writes, seeding runs, provider/LLM calls, catalog
hand-edits or runtime lake mutation; P03 gated.

Use one writer, a separate adversarial reviewer per commit with a formal verdict,
coordinator mutation/restore proofs, serialized tests, and the full gate set. Report
actual counts and stop for owner acceptance; no commit without explicit permission.
```
