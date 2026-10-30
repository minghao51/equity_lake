# 20261029-01 — Demo Preload, Showcase Fixes, and Audit Follow-Ups

**Title:** Finish the offline showcase: label finalization, dataset preload, and remaining audit findings
**Status:** **T1–T7 landed 2026-10-30** — see [§8](#8-pickup-outcome-2026-10-30) for the outcome, evidence, decisions taken, and one new finding (F10) found while running the ML path. **Open follow-ups are specified in the [20261030-01 handoff](20261030-01-postpreload-findings-and-remaining-issues.md).**
**Recorded:** 2026-10-29 (updated 2026-10-30)
**Baseline commit:** `f441d57` (branch `main`) + the uncommitted change set described in §2 (committed as `5fe97a3`)
**Related:** [ADR-0020](../decisions/0020-synthetic-demo-lake-as-backtestable-surface.md), ADR-0014 (label lifecycle), ADR-0015 (point-in-time adjustment), ADR-0016 (canonical identity)

---

## 1. Context

A full setup audit (from the user's request: "review and audit the full setup, including the various web functions, test and check all the functionalities") produced seven findings. The three **medium** findings were fixed in this session under ADR-0020; the remaining four low findings are specified below as T3–T7.

The medium findings were all the same class of defect: the documented demo/showcase path produced data no strict consumer could read, and no test covered the seam.

| ID | Finding (pre-fix) | Status |
|---|---|---|
| F1 | `equity validate check` failed on the canonical date-partitioned lake — `_load_parquet` concatenated per-file reads, dropping Hive `date=` columns, so pointblank reported `rows_distinct` failures on valid rows | **Fixed** |
| F2 | README "showcase path" ran `make demo` (writes `data/sample`) then `arena run` (reads `data/lake`) → `empty load`, exit 1 | **Fixed** |
| F3 | `demo seed` wrote `ticker/date/OHLCV` only — no `market/mic/symbol`, and no action coverage, so every consumer failed closed | **Fixed** |
| F4 | Default `equity signal scan` dumps a raw traceback on an empty watchlist | **Fixed (T4)** |
| F5 | Static dashboard exporter interpolates alerts/paths/config into HTML unescaped (injection verified) | **Fixed (T3)** |
| F6 | `tests/unit/test_arena.py` hard-fails 3 tests without the `backtesting` group (others skip) | **Fixed (T5)** |
| F7 | AGENTS.md says `uv run mypy`; bare invocation errors (no `files` in `[tool.mypy]`) | **Fixed (T6)** |

Two more findings were discovered *while implementing* the fixes and are specified as T1/T2/T7.

---

## 2. What landed (current state)

### 2.1 Source changes

| File | Change |
|---|---|
| `src/equity_lake/cli/commands/admin.py` | `_load_parquet` reads a directory as one partitioned dataset (`pd.read_parquet(dir)`) instead of concatenating per-file reads. Fixes F1 for `validate check`, `profile`, and `drift`. |
| `src/equity_lake/devtools/seed_demo.py` | New `_configured_identities()` (config-resolved MIC, fail-closed on unconfigured symbols), `stamp_identity()` (stamps `market/mic/symbol/identity_schema_version` + `identity_provenance=current_ticker_config`), `coverage_proofs()` (one synthetic `ActionCoverageProof` per instrument, `source=synthetic_demo_seed`, schema v2, observable from first seeded session, fresh through the last). Summary now reports `coverage_proofs`. Constants: `US_EQUITY_SOURCE_MARKET`, `COVERAGE_SOURCE`. |
| `src/equity_lake/backtesting/data_loader.py` | `BacktestDataLoader(lake_dir=…)`; new `_market_data_dir()` (registry lookup when no override, `MARKET_DIR_MAP` + `delta_table_path` otherwise); `_read_market(..., lake_dir)`; `_action_inputs_for()` now passes `lake_dir` to `load_corporate_action_inputs` (previously always read the canonical lake). |
| `src/equity_lake/backtesting/arena.py` | `run_arena(..., lake_dir=None)`; empty-load `ValueError` now names the remedy (`equity ingest` or `demo seed` + `--lake`). |
| `src/equity_lake/cli/commands/arena.py` | New `equity arena run --lake <root>` option. |

### 2.2 Tests

| File | Change |
|---|---|
| `tests/unit/test_validation.py` | +2: `test_load_parquet_preserves_hive_date_partition_column`, `test_validate_check_passes_on_partitioned_lake_table` |
| `tests/unit/test_seed_demo.py` | +4 / updated: identity stamping, coverage proofs, unconfigured-symbol fail-closed; fixtures moved from `AAA/BBB/CCC` to configured symbols |
| `tests/unit/test_seed_demo_safety.py` | Fixtures moved to configured symbols (guards unchanged; production-lake refusal still precedes identity resolution) |
| `tests/unit/test_cli_unified.py` | +1: `arena run --help` documents `--lake` |
| `tests/unit/test_make_targets.py` | +1: `showcase` target stays offline (seed + `arena run --lake data/sample`), no `dotenvx`/`ingest`/`setup` steps |
| `tests/unit/test_demo_showcase_seam.py` | **New.** The missing seam test: seed→loader→arena via `lake_dir`, plus actionable empty-load error. `skipif`-guarded on `POLARS_BACKTEST_AVAILABLE`. |

### 2.3 Docs / build

- `docs/decisions/0020-synthetic-demo-lake-as-backtestable-surface.md` (+ index row in `docs/decisions/README.md`)
- `README.md` — showcase block replaced with the working, verified path (`make showcase`), runtime stated honestly instead of "60 seconds"; mermaid updated
- `Makefile` — new `showcase` target (seed `--years 5`, then `arena run --lake data/sample --start-date 2025-01-02 --end-date 2026-06-30`)
- `docs/user-guide/20260406-cli-reference.md` — `arena run --lake`; `demo seed` identity/coverage behavior and config requirement
- `docs/user-guide/20260829-arena-and-findings.md` — coverage prerequisite + offline demo recipe + runtime note
- `notebooks/11-strategy-lab.ipynb` — now actually reads `data/sample` (`DEMO_LAKE`/`DEMO_BRONZE`) and passes `lake_dir=DEMO_LAKE` to `run_arena` (it previously claimed to load the demo lake but read the canonical one)

### 2.4 Verified baseline (do not regress)

```text
uv run pytest -n auto        → 1588 passed, 17 skipped, 0 failed
uv run ruff check .          → All checks passed
uv run ruff format --check . → 309 files already formatted
uv run mypy src              → Success: no issues found in 171 source files
uv run equity catalog-generate → exit 0, no diff in data/catalog.jsonl
make showcase                → exit 0, 9 runs (3 strategies × 3 cost regimes), 3 FindingCards
```

Read-API surfaces after `make showcase` (verified live): `/findings` → 3 cards (`cost-regime`, `strategy-comparison`, `vs-benchmark`), `/backtests` → 9 runs, `/signals?target_date=…` → 3 signals, `/predictions` → empty (see T2).

### 2.5 Local lake state

State *before* the 2026-10-30 pickup; see §8.4 for the current state.

| Path | Contents | Notes |
|---|---|---|
| `data/sample/` | 5y synthetic US prices (identity-stamped) + coverage proofs | The showcase lake; gitignored |
| `data/lake/01_bronze`, `02_silver`, `03_gold` | 3y synthetic (39,150 price rows), coverage proofs, 10 Gold feature rows | Added during the preload probe — **this shadows a real lake** |
| `data/lake/04_platinum/predictions` | Pre-existing, untouched | |
| `data/findings/`, `data/signal_scans/` | Showcase cards/runs, scan reports | Auxiliary artifacts |

Reset the canonical-lake synthetic probe (keeps the pre-existing predictions table):

```bash
rm -rf data/lake/01_bronze data/lake/02_silver data/lake/03_gold data/signal_scans
```

---

## 3. Task specifications

| ID | Task | Effort | Impact | Blocked by |
|---|---|---|---|---|
| **T1** | F8: label finalization errors on sparse Gold history | S | **High** (blocks T2) | — |
| **T2** | Dense feature backfill → labels → predictions preload | M | High (completes the request) | T1 |
| **T3** | F5: escape interpolated values in the static exporter | S | Med (published artifact) | — |
| **T4** | F4: clean error for empty-watchlist signal scan | S | Med (UX/contract) | — |
| **T5** | F6: skip-guard the optional-dependency arena tests | S | Low-Med (CI/dev against bare `uv sync`) | — |
| **T6** | F7: make `uv run mypy` work as documented | XS | Low | — |
| **T7** | `bootstrap sample` writes a non-medallion table layout | S–M | Low-Med (consistency) | decision |

Recommended order: **T6 → T5 → T4 → T3 → T1 → T2**, then T7 once a decision is made. The small, independent fixes first, then the label unblock, then the preload that depends on it.

---

### T1 — F8: `finalize_preceding_labels` errors when the preceding session is absent

**Symptom.** Running the features stage on dates that are not adjacent in Gold logs
`{"event": "label_finalization_failed", "error": "Unsupported or unknown identity schema versions: []"}` and every
`next_day_return` / `label_available_date` stays `null`.

**Reproduction.**

```bash
uv run equity demo seed --lake data/lake --overwrite-production-lake --years 3
uv run equity pipeline --skip-ingestion --skip-ml --date 2026-10-27 --markets us --tickers AAPL,MSFT
uv run equity pipeline --skip-ingestion --skip-ml --date 2026-10-23 --markets us --tickers AAPL,MSFT
uv run python -c "
from equity_lake.storage.delta import read_delta
df = read_delta('03_gold/features'); print(df.select('ticker','date','next_day_return').sort('ticker','date'))"
# every next_day_return is null
```

**Where.**

- `src/equity_lake/features/labels.py:56-62` — `_require_identity_schema()` raises
  `LegacyIdentityUnavailableError(f"Unsupported or unknown identity schema versions: {versions}")`
  when `drop_nulls().unique()` is `[]` (i.e. the frame is empty).
- `src/equity_lake/features/labels.py:272+` — `finalize_preceding_labels()` → `preceding_label_frame()`,
  which reads stored Gold history (reads at ~L228 and ~L254). When the immediately preceding session
  is not in Gold, that read yields an empty frame and the identity check raises.
- `src/equity_lake/features/__init__.py:190-201` — the caller catches `Exception` and logs
  `label_finalization_failed`; the stage still succeeds, so this fails **quietly**.

**Root cause.** "No rows to finalize" (a legitimate pending state) is conflated with "rows with unknown identity" (a genuine contract violation). ADR-0014 requires the latter to fail closed, which is correct — the bug is that an empty frame is treated as the latter.

**Specification.**

1. Distinguish the two states explicitly. An empty preceding-session set must return
   `LabelFinalizationResult(eligible=0, pending=<n>, pending_keys=…)` — a pending no-op — without logging an error.
2. Keep ADR-0014 strictness intact: a **non-empty** frame that lacks `identity_schema_version`, or
   carries a value other than `IDENTITY_SCHEMA_VERSION`, must still raise.
3. Do **not** change the write path, the label columns, or the matched-only update semantics.
4. Keep the "never erase a finalized label" guarantee (ADR-0014 §7) untouched.

**Acceptance criteria.**

- Non-adjacent dates → no `label_finalization_failed` log, `label_finalization` diagnostics show
  `eligible=0` with `pending > 0`.
- Adjacent dates (e.g. `2026-10-26` then `2026-10-27`) → the earlier row is finalized:
  `next_day_return` non-null and `label_available_date == 2026-10-27`.
- A Gold frame with `identity_schema_version = 2` (or nulls) still raises.
- `uv run pytest tests/unit/test_label_lifecycle.py -q` green, plus the new cases.

**Tests to add** (`tests/unit/test_label_lifecycle.py`, which already exists):
`test_finalize_with_missing_preceding_session_is_pending_not_error`,
`test_finalize_still_rejects_unknown_identity_schema`.

**Risk.** Low. The danger is over-relaxing the identity check — guard it with the second test.

---

### T2 — Dense preload: features → labels → predictions

Depends on **T1**. This is the remainder of the user's stated goal ("preload with some datasets to showcase the features"). The full ladder status at handoff:

| # | Surface | Status |
|---|---|---|
| 1 | Bronze prices (identity) | ✅ works |
| 2 | `02_silver/corporate_action_coverage` | ✅ works |
| 3 | Arena → `data/findings/` (cards, metrics) | ✅ works |
| 4 | Gold features | ⚠️ writes, but **labels stay null** (T1) and one date per invocation |
| 5 | Platinum predictions | ❌ blocked (needs labeled rows + a trained model) |
| 6 | Signals → `data/signals`, `data/signal_scans` | ✅ works (3 signals verified) |

**Observed constraints.**

- `equity pipeline --skip-ingestion --skip-ml --date D` computes **one** output date. `--days-back N`
  did **not** produce history (verified: two runs ⇒ two Gold date partitions only), so consecutive
  invocations are required.
- Cost is ~8s per invocation for 5 tickers (measured), so a 250-session backfill is ≈30–35 min.
  Bound it deliberately.

**Specification.**

1. Backfill consecutive business days via `trailing_business_days(...)` (already exported by
   `devtools/seeding.py`), oldest → newest, so each run finalizes its predecessor.
2. Add a bounded, explicit entry point rather than an open-ended loop. Options, in preference order:
   (a) a `Makefile` target `preload` with a `DAYS ?= 60` variable that loops the pipeline;
   (b) a `devtools` helper function (testable) that the CLI/Make target calls.
   Prefer (b) if the loop needs any validation (e.g. refusing `DAYS > 500`), since a bare shell loop
   can't be unit-tested.
3. Document the runtime and the `DAYS` default honestly in the target/help text and in
   `docs/user-guide/20260829-arena-and-findings.md` or a new preload subsection.
4. After labels exist, drive the **real** training/prediction path (check `uv run equity ml --help`
   and `uv run equity forecast --help`) rather than writing prediction rows by hand. Prediction
   writes must go through the existing `04_platinum/predictions` writer so identity and schema
   versions are enforced.
5. Do **not** fabricate predictions, labels, or provenance. If the ML path fails closed on synthetic
   data, report the gate instead of working around it.

**Acceptance criteria.**

- Gold contains a dense run of consecutive sessions with `next_day_return` non-null on all but the
  final session per instrument.
- `curl -s 'http://127.0.0.1:8000/predictions?limit=5'` returns rows whose `api_identity_status` is
  `valid` and `api_schema_status` is `current`.
- Dashboard `datasets` payload shows `features` available with the backfilled row count.
- The preload entry point is idempotent (re-running overwrites, no duplicate keys).

**Note for the implementer.** `equity doctor` and `equity monitor` are the fastest way to see which
surfaces are still empty; both report honestly rather than implying health.

---

### T3 — F5: escape interpolated values in the static dashboard exporter

**Where.** `src/equity_lake/dashboard/exporter.py` — `_render_page()` and the `_render_*` methods build
HTML with raw f-strings; `html.escape` is not imported anywhere in `dashboard/` (verified by grep).

**Verified vulnerability.** Writing `alerts: ['<script>alert("xss")</script>']` into
`logs/health-report.json` and running `equity dashboard build` places the tag unescaped in the
exported `index.html`, which is published to GitHub Pages. Dataset paths, `latest_date`, and the
config JSON (`<pre>`) are the same class of sink.

**Specification.**

1. Escape every interpolated *text* value (alerts, dataset name/path/latest date, error strings,
   titles, subtitle) with `html.escape`.
2. Keep the JSON `<pre>` block escaped too (escaping inside `<pre>` is still correct and prevents
   `</pre>` breakouts).
3. Keep the existing CSS/HTML structure byte-identical apart from the escaped values — do not
   restructure the renderer.
4. Do not add a template engine; a module-local helper is enough.

**Acceptance criteria.**

- Injecting `<script>` via `logs/health-report.json` and via a dataset path yields `&lt;script&gt;` in
  `index.html`, with no literal `<script>alert` substring.
- A test asserts this for both sinks (new `tests/integration/test_dashboard_exporter.py` case; that
  file already exists).
- Existing exporter tests stay green; the rendered pages still contain the same section headings.

**Risk.** Low. Watch for double-escaping of values that are already HTML (there are none today).

---

### T4 — F4: clean error when the private watchlist is empty

**Where.** `src/equity_lake/cli/commands/intelligence.py:203` — `SignalScanner(sc, wl, instruments=instruments)`
is constructed **outside** the `try/except` that converts config problems into
`Invalid scan configuration: …` + `typer.Exit(1)` (that guard covers ~L152-171). With an empty private
watchlist, `SignalScanner.__init__` raises `ValueError("signal scans require at least one canonical
instrument identity")` (`signals/scanner.py:46`), which escapes as a raw rich traceback.

**Specification.**

1. Move the scanner construction into the guarded region, or add a targeted `except (ValueError, RuntimeError)`
   around it — whichever keeps the existing "Invalid scan configuration" contract intact.
2. Message must be actionable and name the fix, e.g.
   `No canonical instruments to scan; add one with 'equity tracking add' or pass --watchlist with --market`.
3. Exit code 1, no traceback, consistent with the other validation paths in that command.

**Acceptance criteria.** `uv run equity signal scan` with an empty watchlist prints one clean error line
and exits 1; no `Traceback` appears in output. Test in `tests/unit/test_signal_scan_safety.py` (exists).

---

### T5 — F6: skip-guard the optional-dependency arena tests

**Where.** `tests/unit/test_arena.py` — three tests require `polars-backtest` and hard-fail without it:
`test_run_arena_matrix_and_benchmark`, `test_write_arena_artifacts_roundtrip`,
`test_card_evidence_refs_point_at_artifacts_write_arena_actually_writes`.

**Why.** AGENTS.md documents the validation sequence as `uv sync` (no `--group backtesting`), so a
fresh clone fails the suite for an environment reason. Every other optional-dependency test in the
repo skips (`tests/unit/test_strategy_weights.py:219`, the lightgbm cases).

**Specification.** Import `POLARS_BACKTEST_AVAILABLE` from `equity_lake.backtesting.engine` and add
`@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")` to exactly
those three tests. Do not mark the whole module — the pure-helper tests (`equal_weight_buyhold`,
`drawdown_series`, card building) must keep running without the group.

**Acceptance criteria.** With `polars-backtest` uninstalled, `uv run pytest tests/unit/test_arena.py`
reports passes + skips and zero failures; with it installed, all tests run.

---

### T6 — F7: make the documented mypy command work

**Where.** `pyproject.toml` `[tool.mypy]` has no `files`/`mypy_path`, so `uv run mypy` exits with
`mypy: error: Missing target module, package, files, or command.` Meanwhile `Makefile:150` and
`.pre-commit-config.yaml` use `mypy src`, and AGENTS.md documents bare `uv run mypy`.

**Specification.** Add `files = ["src"]` to `[tool.mypy]` so bare `uv run mypy` behaves like
`uv run mypy src` (do not weaken `exclude = ["tests/"]`, and keep strictness flags unchanged).
If you prefer the doc change instead, update AGENTS.md **and** the README validation snippet so all
three agree — but pick one, not both.

**Acceptance criteria.** `uv run mypy` (no arguments) reports `Success: no issues found in 171 source files`.

---

### T7 — `equity bootstrap sample` writes a non-medallion layout (needs a decision)

**Observation.** `src/equity_lake/cli/bootstrap.py:213` calls
`write_delta(market_data, market, mode="overwrite", lake_dir=out)`, so the table is named after the
market (`<out>/us_equity`) rather than the canonical `01_bronze/market_data/us_equity`. It also writes
no identity columns and no coverage, so — like `demo seed` before this change — its output is not
consumable by strict readers. It is documented as a sandbox generator, so this may be intentional.

**Decision required (pick one, then implement):**

- **(a) Align it.** Make `bootstrap sample` write the canonical table path and stamp identity +
  coverage by reusing `stamp_identity()` / `coverage_proofs()` from `devtools/seed_demo.py`. This is a
  storage-layout change for an auxiliary lake → per AGENTS.md it likely wants an ADR (or an amendment
  note on ADR-0020).
- **(b) Document the divergence.** Keep the layout, and state explicitly in the CLI reference that
  `bootstrap sample` output is a loose sandbox layout that strict readers reject by design.

Do not silently change the layout without the decision — `docs/user-guide/20260406-cli-reference.md`
documents this command's output directory.

---

## 4. Decisions needed from the owner

1. **Canonical-lake synthetic data** (§2.5): keep it for the demo, or reset with the command in §2.5?
   Keeping it makes `query`/`dashboard`/`monitor` show data but mixes synthetic rows into the canonical
   lake.
2. **Predictions preload (T2)**: run the real training path on synthetic features (honest but
   expected-negative results, possibly slow), or leave `/predictions` empty and document why?
3. **T7**: align `bootstrap sample` with the medallion layout (needs ADR) or document the divergence?
4. **T2 entry point**: `Makefile preload` target vs a `devtools` helper (testable).

---

## 5. Guardrails (unchanged, from AGENTS.md)

- `uv run <command>` only; never bare `python`.
- **Present a plan before modifying code** — the previous agent got explicit approval before the
  Phase-2 work; do the same for T2 and T7.
- **ADR before implementation** for boundary/storage changes (T7 if you choose option (a)).
- Dry-run means no persistence, backfill, LLM processing, feature output, or ML inference.
- Never fabricate provenance. The only synthetic source label sanctioned today is
  `synthetic_demo_seed` for coverage proofs (ADR-0020) and `current_ticker_config` for config-resolved
  price identity (an existing label in `core/instruments.py`'s closed vocabulary — do not invent new
  ones there).
- Polars first; pandas only at external-library boundaries (the `_load_parquet` fix stays pandas because
  `validation/` already sits on that boundary).
- CLI changes need help text, a help-scan test in `tests/unit/test_cli_unified.py`, and a user-guide
  update. Catalog regeneration (`uv run equity catalog-generate`) if any pipeline/feature surface changes.
- Validation before handoff: `uv run pytest -n auto`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy src` (or bare `mypy` after T6).

## 6. Quick reproduction / verification commands

```bash
# Offline showcase (works today)
make showcase
ls data/findings/                                  # 3 cards + 9 run dirs
uv run equity api serve --port 8000 &              # /findings, /backtests, /signals populate
curl -s localhost:8000/findings | head -c 200

# Read-API + dashboard
uv run equity query -q latest_summary
uv run equity dashboard build --output-dir /tmp/dash && python -c "import json;print(json.load(open('/tmp/dash/dashboard-data.json'))['summary'])"

# T1 fix: a non-adjacent date is now a pending no-op, not an error event
uv run equity pipeline --skip-ingestion --skip-ml --date 2026-10-23 --markets us --tickers AAPL,MSFT
# no 'label_finalization_failed' event; adjacent dates still finalize the prior label

# T2 preload + real ML path
make preload DAYS=60
uv run equity ml train --ticker AAPL --start 2026-08-06 --end 2026-10-27
uv run equity pipeline --skip-ingestion --date 2026-10-28 --markets us --tickers AAPL,AMZN,GOOGL,MSFT,NVDA
```

## 7. Definition of done for this handoff series

- T1–T6 landed, each with the tests named above.
- `/predictions` serves at least one row with `api_identity_status: valid` and `api_schema_status: current`
  (or decision 2 is recorded as "leave empty, documented").
- `uv run pytest -n auto` green **from a bare `uv sync`** (no optional groups installed) — this is the T5 gate.
- README, CLI reference, arena guide, and the notebook describe behavior that is actually true when run.

## 8. Pickup outcome (2026-10-30)

All seven tasks landed. Phase 1 (T6 → T5 → T4 → T3 → T1) was committed per task
on top of `5fe97a3`; T7 and T2 follow.

### 8.1 Decisions taken (owner-approved, §4)

1. **Canonical-lake synthetic data: keep, and preload deliberately** through the
   sanctioned paths (`demo seed … --overwrite-production-lake`, the pipeline,
   `make preload`). `/predictions` and the dashboard `features` row count read the
   canonical lake, so the T2 acceptance criteria require it there.
2. **Predictions preload: real path only.** `ml train` for the five tickers, then
   the pipeline ML stage (`run_prediction_job`) persists 5 rows for `2026-10-28`.
   Nothing was hand-written.
3. **T7 = option (b), document the divergence.** `bootstrap sample` keeps its
   sandbox layout (now stated in the command help, the CLI reference, and
   ADR-0020). Aligning it stays a separate storage change needing its own ADR.
4. **T2 entry point = option (b).** `devtools/preload.py` validates the bound and
   drives the real feature stage; `make preload DAYS=…` is the thin wrapper.

### 8.2 What changed per task

| Task | Change | Evidence |
|---|---|---|
| T1 | `labels.py::_stored_keys` returns `None` on an empty date-filtered read (matching the three sibling guards) instead of raising `LegacyIdentityUnavailableError` | `test_finalize_with_missing_preceding_session_is_pending_not_error`, `test_finalize_still_rejects_unknown_identity_schema`; live run on `2026-10-23` logs no failure event, adjacent `2026-10-28` finalizes `2026-10-27` |
| T2 | `devtools/preload.py` (`observed_sessions`, `preload_features`, `main`), `make preload` (DAYS ?= 60, refuses > 500), pipeline user-guide section | `tests/unit/test_preload.py` (13), `test_make_targets.py::test_preload_target_is_bounded_and_offline` |
| T3 | `dashboard/exporter.py` escapes every interpolated value (incl. the four JSON `<pre>` blocks) via a module-local `_escape` | two new cases in `tests/integration/test_dashboard_exporter.py` (alerts + dataset path) |
| T4 | empty resolved instrument list raises inside the existing `Invalid scan configuration: …` guard | `test_empty_watchlist_live_scan_exits_cleanly_without_traceback`; live `equity signal scan` exits 1 with no traceback |
| T5 | `requires_engine` skipif on exactly the three engine-backed arena tests | `uv run --isolated --no-group backtesting pytest …` → 42 passed, 10 skipped, 0 failed |
| T6 | `files = ["src"]` in `[tool.mypy]` | bare `uv run mypy` → 171 files, no issues |
| T7 | help text + CLI reference + ADR-0020 note; help-scan test asserts the wording | `test_bootstrap_sample_help` |

### 8.3 New finding (F10): string_view Gold files break Arrow filter pushdown

**Symptom.** `FeatureLoader.load_features` raised
`ArrowNotImplementedError: Function 'equal' has no kernel matching input types
(string_view, string_view)` on the canonical Gold table, so `equity ml train`
could not read its own data.

**Root cause.** The label-only update merges matched files through delta-rs,
which writes string columns as Arrow `string_view` even when the source arrow
table is `large_string` (reproduced: one `merge_delta` → `large_string`, then one
`update_delta_columns` → `string_view`). PyArrow 21 and 22 have no comparison
kernels for `string_view`, so any pushed-down filter on those columns raises —
`ticker = …`, `isin`, and even a cast-then-compare were all verified to fail.

**Fix (reader-side, minimal).** `ml/feature_loader.py` pushes down only the date
range and applies the symbol scope in Polars, so the date pruning and the exact
identity scope are unchanged. Regression test:
`tests/unit/test_feature_loader_string_view.py` builds the drift deliberately
(merge → label update → assert `string_view` files exist) and asserts the loader
still returns the requested instrument only.

**Open, not fixed:** the storage-level drift itself (delta-rs merge upgrading the
table's string columns to `string_view`) is untouched. Any future reader that
pushes down a string filter on Gold will hit the same pyarrow gap; fixing it at
the writer or via a deltalake upgrade needs its own decision.

### 8.4 Local state after the pickup

| Path | Contents |
|---|---|
| `data/lake/03_gold/features` | 305 rows, 61 sessions (2026-08-05 … 2026-10-28), 5 instruments; every label non-null except the final session per instrument (pending by design) |
| `data/lake/04_platinum/predictions` | 5 rows for 2026-10-28, written by the pipeline ML stage; `/predictions` reports `api_identity_status: valid`, `api_schema_status: current`, `api_usability_status: not_current_usable` (row-bound provenance is not persisted — honest) |
| `data/models` | 5 `xgboost/v1_direction` models trained 2026-08-06 → 2026-10-27 (`folds=0`: too little history for validation folds) |
| `data/lake/01_bronze`, `02_silver` | Unchanged 3y synthetic US prices + coverage proofs |
| `/tmp` only | Dashboard export used for the `features` payload check |

Reset the canonical synthetic preload (keeps models/predictions unless removed):

```bash
rm -rf data/lake/01_bronze data/lake/02_silver data/lake/03_gold
rm -rf data/lake/04_platinum/predictions data/models
```

### 8.5 Verified state

```text
uv run pytest -n auto        → 1608 passed, 17 skipped, 0 failed
uv run ruff check .          → All checks passed
uv run ruff format --check . → 312 files already formatted
uv run mypy                  → Success: no issues found in 172 source files
make preload DAYS=60         → 300 rows, 290→300 labels finalized, 0 errors, ~9.5 min
make preload (re-run, 2 d)   → Gold row count unchanged (idempotent)
```
