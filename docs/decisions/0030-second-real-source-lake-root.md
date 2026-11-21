# ADR-0030 — A second, real-source lake root: `data/lake_real/`, never mixed with the demo root

**Status:** Accepted (2026-11-15)

**Recorded:** 2026-11-14

**Accepted:** 2026-11-15 by the owner ("proceed with the name, proceed with the
implementation"). Decision 5's layer list was amended at acceptance to include the
corporate-action tables that ADR-0029's raw-price reconstruction consumes; the CLI guard
enforces the *market*, not the layer. Round 2 (2026-11-15) tightened decision 3 at the
implementation seam: a root that resolves *inside* the canonical lake (`data/lake/01_bronze`)
is refused as well as one that aliases it, because it would otherwise write
`01_bronze/01_bronze/…` under `data/lake/`, which decision 10 forbids. Implementation record:
spec §10. The implementation was **accepted 2026-11-15** after reviewer round 3 returned
`PASS`; that acceptance covers the root, its guard and its tests, not a landing run
(decision 10).

**Implementation note 2026-11-17 — reporting scope.** Decision 5's "the `us_equity` slice
only" is now the single definition `REAL_ROOT_PRICE_MARKETS` in `core/paths.py`, read by both
the `--lake-dir` scope guard and the health monitor (`price_markets_for_root`). Monitoring a
real root therefore judges `us_equity` alone instead of erroring on four markets the root may
not hold, and the checks that cannot be evaluated there (Feature Store, Unstructured
Freshness) report `not_applicable` with a reason rather than `PASS` — an absent price table is
still a failure, and the canonical root's verdicts are unchanged. `equity data ingest` also
threads the current `TickerConfig` (as `backfill` already did), because the vendor supplies no
MIC and every `us_equity` row previously failed `LegacyIdentityUnavailableError` before the
write; a ticker absent from the config still fails closed. Record: spec §10.

**Amended 2026-11-18 by [ADR-0032](0032-adjusted-read-basis-for-vendor-sourced-slices.md)
(Accepted, not yet implemented).** Decision 5's consequence "Gold stays absent in the real root
because no source-issued action proof exists" no longer states a permanent limit: under
ADR-0032 a Gold table may exist there when it is stamped with the weaker
`vendor_events_no_clock` coverage basis and produced by an explicitly opted-in run. The
containment, per-root isolation and "not cataloged" rules of this record are untouched, and
nothing is implemented yet.

**Owner decision recorded 2026-11-14:** real-source rows are stored under `data/` but in a
different folder from the synthetic demo lake, with the same medallion layout. This record
turns that instruction into a storage contract. It is required before implementation because
it changes the enforced layout in `AGENTS.md` ("cataloged tables live under `data/lake/`
only"; the numbered medallion layout is canonical).

## Context

[ADR-0028](0028-d01-real-source-slice.md) decision 6 leaves the mixed-lake question open:
the canonical lake holds synthetic demo rows (39,150 price rows, 50 symbols) and a
`synthetic_demo_seed` action-coverage proof that is `complete` and valid through
2026-10-28. Validating real rows against that proof is exactly the failure D00 and
ADR-0025 guard against, and merging real rows into those tables would make them
indistinguishable at the row level (the tables predate E01-6 and carry no origin columns).

The repository already has the seam needed to avoid this: `write_delta` and `merge_delta`
accept `lake_dir` (`storage/delta.py`), `delta_table_path(table, lake_dir)` is a plain join,
and several readers already take `lake_dir=` (`arena --lake`, `tracking --lake-dir`,
`build_tracking_readiness(lake_dir=…)`). What is missing is a *contract* for a second root
and threading the option through the write path (`upsert_dataset` and the `ingest` /
`backfill` commands do not expose it).

## Decision

1. **Two roots, one layout.** The canonical root stays `data/lake/` and its constants are
   unchanged. The real slice lives at **`data/lake_real/`** with the identical numbered
   medallion subpaths (`01_bronze/market_data/<market>`, `02_silver/…`, `03_gold/…`,
   `04_platinum/…`). `LAKE_DIR` remains the default for every existing caller; nothing
   changes for the demo lake. The name is fixed by this record: renaming it after the first
   write would be a migration.
2. **Explicit selection, never discovery.** A root is used only when passed explicitly
   (`--lake-dir` on the write and read commands, or the existing `lake_dir=` seam). There
   is no default switch, no environment-driven mode, no "both roots" run, and **no fallback
   to the canonical root** when a requested root is absent. A read of an absent root keeps
   the existing absent/empty semantics; a write creates only what it writes. No command
   copies, promotes or mirrors data between roots.
3. **Containment.** A supplied root must resolve under `PROJECT_ROOT/data/`, must not be a
   symlink escape, and must not silently alias the other slice. Validation lives at the CLI
   seam (the ADR-0019 containment precedent), not inside `delta_table_path`, which stays a
   dumb join so no implicit redirection can hide in storage.
4. **The real root is not cataloged.** `data/catalog.jsonl` continues to describe
   `data/lake/` only; `catalog-generate` and its byte-identity gate are unchanged. Rationale:
   the real slice is gated research evidence, not a serving table, and a second catalog
   would change the catalog row contract for a need that is not yet measured. Consequences,
   stated rather than implied: catalog-driven surfaces (dashboard `MARKET_DATASETS`,
   catalog readers) do not see the real root, and **promotion to the canonical root is a
   separate, explicit operation** with its own record — never a copy performed by ingestion.
5. **Layer scope of the D01 landing.** The real root carries the `us_equity` slice only:
   `01_bronze/market_data/us_equity`, `01_bronze/corporate_actions/us_equity`,
   `02_silver/corporate_actions/us_equity` and
   `02_silver/corporate_action_coverage/us_equity`. The action tables are included because
   ADR-0029's raw-price reconstruction consumes the split events. `03_gold/features` and
   `04_platinum/` stay absent there because no source-issued action proof exists; the
   existing fail-closed feature gate is the enforcement, not a new switch. No demo row and
   no demo coverage proof is ever copied into either root. The CLI refuses a non-canonical
   root for any market other than `us_equity`; it constrains the market, not the layer.
6. **Health, readiness and the console are per-root.** `equity monitor` and the readiness
   readers take the root explicitly; there is no aggregate over both roots (a union is
   precisely the misleading roll-up the console refuses). The P03 console and the static
   dashboard keep reading the canonical root; pointing them at the real root is a later,
   separate decision, although `build_tracking_readiness(lake_dir=…)` already permits it.
7. **Evidence rules are unchanged.** Real rows remain `unknown` origin (ADR-0027); the
   raw-price reconstruction and its refusal rules come from ADR-0029 Part I; split actions
   follow ADR-0011. This record adds no provenance claim.
8. **No migration, move or delete.** The demo lake is untouched, nothing is relocated, and
   `data/*` is already git-ignored, so neither root can be committed by default. Backup and
   retention for the real root are out of scope (P00-4-adjacent) and are not implied here.
9. **Change matrix when implemented** (storage row): `upsert_dataset(…, lake_dir=…)`,
   `run_daily_ingestion(…, lake_dir=…)`, `--lake-dir` on `equity ingest` and
   `equity backfill`, root threading for the health/readiness writes and checks,
   tests (writer and reader isolation, no cross-root resolution, absent-root refusal,
   dry-run no-write), the user guide and `ARCHITECTURE.md`, and one clarifying sentence in
   `AGENTS.md` pointing here. No lake schema change, no catalog row, no new Settings key,
   no dependency.
10. **Stop condition.** Acceptance authorizes implementing the second root, not ingesting.
    The scoped D01 landing run still needs its own authorization, Gold remains blocked, and
    no real row may enter `data/lake/` under any circumstance.

## Consequences

- The demo lake keeps working unchanged, including the P03 console and the showcase, while
  a real slice can be built beside it without sharing tables, proofs or health reports.
- Two roots mean two of everything operational: health, freshness, backup and (later)
  retention. That duplication is accepted as the price of not mixing evidence, and each
  root's status must be reported separately rather than summed.
- The catalog stays single-rooted, so the real slice is invisible to catalog-driven
  surfaces until promotion. That is deliberate; it also means a reader cannot discover the
  real root by accident.
- `data/lake_real/` is a sibling of `data/lake/`, so the ADR-0020 `--lake` seam and the
  existing `lake_dir=` parameters remain the only way in — no new abstraction is introduced.
- If real-source research later becomes the primary surface, promotion (and catalog
  inclusion) needs its own record; this one deliberately does not pre-approve it.
