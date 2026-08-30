# Handoff 03 — P0: Storage & validation correctness (storage/, validation/, writers)

Priority: P0. Depends on: 01.
Suggested dispatch: **2 parallel `worker`s** (A: `storage/delta.py` + readers, B:
`validation/` + `ingestion/writers.py` boundary), then one `reviewer`.
This is a **storage + validation change**: change matrix requires writer, reader, health
checks, idempotency tests, and an architecture-docs touch where behavior changes.

## Worker A — merge fallback & read semantics

### A1. `merge_delta` schema-mismatch fallback duplicates keyed rows ✅ verified
`storage/delta.py:131-136`: on exception whose message contains "schema" or "column",
`merge_delta` falls back to `write_delta(mode="append", schema_mode="merge")`. Rows that
failed to merge are **appended on top of existing rows with the same keys** → re-running
signals / silver articles / platinum predictions after schema evolution silently
duplicates keyed data. `tests/unit/test_delta_schema.py:13` currently pins this behavior
as expected — the test encodes the bug and must be rewritten.

Fix direction: on schema mismatch, perform the merge against the evolved schema —
e.g. `DeltaTable.alter_columns` / evolve via a single-row `write_delta(mode="overwrite"`
+ `schema_mode="merge")` seed then re-merge — or overwrite-by-partition when the market
is date-partitioned. Do **not** keep any append path for keyed upserts.
Update `test_delta_schema.py` to assert: (a) schema-mismatch merge preserves key
uniqueness, (b) non-schema errors still raise `DeltaMergeError`.

### A2. `read_delta` swallows all exceptions ✅ verified
`storage/delta.py:150-153` logs and returns an empty frame. Callers can't distinguish
"no data" from "lake broken": `api/deps.py:48` serves HTTP 200 `[]` on a corrupt table;
`signals/history.load_signals` likewise.
Fix: raise (new `DeltaReadError` or reuse existing error type); keep a thin
`read_delta_or_empty` only if a caller genuinely wants the old semantics (grep callers;
expect: api/deps catches and maps to HTTP 503, signals history propagates).

### A3. Cosmetic-but-real (same PR)
- `storage/delta.py:37-49` — parameter `market` is a table path; rename to `table`/`dataset`
  (grep callers; mechanical).
- `storage/s3_sync.py:175` — `process.wait(timeout=600)` kills large syncs; make the
  timeout configurable via Settings (`EQUITY_...` nested model + `.env.example` entry)
  with a sane default.
- `storage/delta.py` migration (`_backup_old_partitions`): with `keep_backup=False`,
  partitions are deleted **before** the rewrite succeeds — reorder to write-then-cleanup
  (🔎 audit-reported; read the function first).

## Worker B — validation boundary: make pointblank real or honest

### B1. Eager pointblank import defeats the optional group ✅ verified
`validation/__init__.py` eagerly imports `.schemas`; `validation/schemas.py:7` does
top-level `import pointblank as pb`; `pointblank` lives only in the optional `validation`
dependency-group (pyproject `validation = ["pointblank>=0.8"]`) and there is **no
`[tool.uv] default-groups`**. Under plain `uv sync`, the lazy imports in
`ingestion/writers.py:78-79` and `cli/commands/admin.py:132,170,206` die with
`ModuleNotFoundError` (importing the submodule executes the package `__init__`).

Fix (pick one, record in the PR description):
1. **Promote pointblank to main dependencies** (recommended: it backs an enforced
   write-boundary contract, and B3 makes it non-optional), or
2. Guard: `schemas.py` imports pointblank lazily inside functions/`__getattr__`, and
   `validation/__init__.py` stops re-exporting schema classes (export only
   `ValidationPipeline` lazily).
Either way add a test that imports `equity_lake.validation.pipeline` with pointblank
blocked (`sys.modules["pointblank"] = None` trick or importlib mock) and asserts a clear
"install the `validation` group" error, mirroring `ml/__init__.py:44-47`.

### B2. `upsert_dataset` orders profiling before the dry-run check ✅ verified
`ingestion/writers.py`: the `validate_quality` block runs `ValidationPipeline.validate`
**before** `if dry_run:`; `DataProfiler.profile()` **always writes**
`data/profiles/<name>.json` (`validation/profiling.py:79-80`), and `storage_path` is
CWD-relative (`:70`). So dry-run + validation persists to disk, in the wrong place.

Fix: move the dry-run short-circuit above validation; change `DataProfiler` default to
`DATA_DIR / "profiles"` (via `core/paths.py`); ensure `profile()` doesn't write when a
`persist=False` flag is passed (validation pipeline should profile in-memory during
ingest; disk writes are for the `equity validate profile` command only).
Test: dry-run with `validate_quality=True` leaves no new files anywhere.

### B3. Decide & enforce: pointblank at the write boundary ✅ verified dormant
`upsert_dataset(validate_quality: bool = False)` and **zero callers pass True** — the
enforced contract is only the column-presence `validate_schema`; silver merges bypass
even that (`bronze_silver.py` `_write_silver_generic` → `merge_delta`).

Decision for the implementer (default recommendation): flip `validate_quality=True` as
the default for ingestion write-boundary calls (price + news types), keep an explicit
opt-out for devtools; add pointblank validation to the silver merge path for the
`sec_extractions`/article tables (they currently skip `validate_schema` too). Add
row-level checks only where cheap (OHLC consistency, duplicate keys) — don't rebuild the
schema registry. If the cost is prohibitive for daily runs, instead make
`validate_quality=True` sampling-based and document it — but get the owner's sign-off in
the PR (this changes an enforced contract; note it in `ARCHITECTURE.md` data-quality
section).

## Acceptance criteria

- Schema-evolution merge preserves key uniqueness (new idempotency test: run merge twice
  with a changed schema, row count stable).
- No append fallback remains for keyed upserts; `test_delta_schema.py` asserts the new
  behavior.
- `uv sync` (no extra groups) + `python -c "import equity_lake.ingestion.writers"` and a
  unit test prove the boundary imports cleanly or errors with the friendly message.
- Dry-run never writes (including profiles).
- Write-boundary validation policy implemented or explicitly documented with owner sign-off.

## Validation

```bash
uv run pytest tests/unit/test_delta_schema.py tests/unit/test_validation.py tests/integration -q
uv run pytest -n auto && uv run ruff check . && uv run mypy
```

## Out of scope

API router changes beyond mapping the new read error to 503 (handoff 09 owns the rest),
monitoring fixes (09).
