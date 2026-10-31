# 20261031-01 — Next Thread Brief and the R12 Watch

**Title:** Pick up from a closed register: what is left, what is deliberately not done,
and the traps that cost time last round
**Status:** **Brief for the next agent.** No code changed by this document beyond the
closure it records. The register it indexes
([20261030-01](20261030-01-postpreload-findings-and-remaining-issues.md)) is fully
implemented; three items remain open and are specified in §4.
**Recorded:** 2026-10-31
**Baseline commit:** `02e1298` (branch `main`, tree clean)
**Related:** [20261030-01 register](20261030-01-postpreload-findings-and-remaining-issues.md)
(§9 has the commit table), [20261030-02](20261030-02-row-bound-forecast-provenance-implementation.md)
(ADR-0021 implementation), ADR-0021 (accepted, immutable), ADR-0022 (accepted,
future envelope), ADR-0020 (synthetic demo lake), ADR-0014 (label lifecycle), ADR-0016
(canonical identity)

---

## 1. Where things stand

The R1–R13 series is closed except the three items in §4. Everything was landed one
commit per stage, each reviewed adversarially before commit, each new rule
mutation-drilled. The last two commits of that work:

| Commit | What it closed |
|---|---|
| `e2c6f42` | ADR-0022 **proposed** (R10) |
| `710aeac` | register closed: statuses, baseline, §9 outcome |
| `02e1298` | R13 fix, R10 **accepted**, and two residuals (stale model summary, notebook output) |

## 2. Verified baseline (do not regress)

Captured at `02e1298`:

```text
uv run pytest                 → 1738 passed, 17 skipped, 0 failed   (serial, 4m06s)
uv run pytest -n auto         → 1738 passed, 17 skipped, 0 failed
uv run ruff check .           → All checks passed
uv run ruff format --check .  → 320 files already formatted
uv run mypy                   → Success: no issues found in 174 source files
uv run equity catalog-generate → no diff
make lint-md                  → exit 0
make showcase                 → exit 0, ~74 s, 9 runs, 3 FindingCards
GET /predictions              → 100 rows / 20 sessions, all valid | established | current_usable
GET /models                   → 35 summaries, all validation_status: verified
```

Serial `pytest` is a first-class gate here, not just `-n auto`: R13 existed only in
the serial order. If a serial run ever fails while `-n auto` passes, suspect
process-global state (structlog config, `sys.modules` stubs, `monkeypatch`ed module
attributes) before suspecting the code under test.

## 3. Decisions taken at closure (do not re-open without new evidence)

1. **R10 — ADR-0022 accepted as proposed.** A future `forecast_provenance_version`
   stays `unknown` + `forecast_provenance_version_is_unsupported_by_this_reader:<n>`,
   visible and non-usable, version printed verbatim. **No new status vocabulary**
   (`valid | historical | invalid | unknown` is unchanged), no backfill. The write
   boundary keeps refusing to emit an envelope it cannot validate — forward
   compatibility is deliberately asymmetric.
2. **R12 — watched, not fixed** (§4.1). It cannot be reproduced on demand, its only
   user-visible symptom is already avoided, and both candidate fixes touch the read
   seam that R1 stabilised.
3. **R13 — fixed at the test**, not by changing production logging. `structlog`'s
   `cache_logger_on_first_use=True` freezes a module logger on first use, so no
   later `structlog.configure` (including `capture_logs`) can rebind it. Tests that
   assert on events from module-level loggers must bind their own proxy.
4. **`--max-model-age-days` stays capped at `DEFAULT_MAX_MODEL_AGE_DAYS` (7).** The
   refit preload therefore plans around ~a week of sessions per boundary and refuses
   a wider plan up front. Lifting the cap changes the persisted freshness contract
   the read API checks — an ADR-level change, not a flag tweak (§4.4).
5. **Writer-side `string_view` elimination stays out of scope.** Every mitigation is
   read-side; the tables still drift on update. A fix is a storage change with its
   own ADR (§4.3).

## 4. Open work, in the order worth doing

### 4.1 R12 — intermittent `to_pyarrow_table()` interpreter abort

**Evidence to start from.** Observed once, during heavy parallel work: `terminate
called without an active exception`, **exit 134** (SIGABRT), on roughly a third of
`equity doctor` runs. Guards do not run on SIGABRT, so nothing is logged. Re-checked
at closure: **0/20** `read_delta("03_gold/features")`, **0/15**
`read_delta("04_platinum/predictions")`, **0/12** `equity doctor`. Intermittent and
load-correlated, not deterministic.

**Environment.** `deltalake 1.6.0`, `pyarrow 21.0.0`, `polars 1.41.2`. The manifest
floors are `deltalake>=0.25` and `pyarrow>=18`, so an upgrade is permitted;
`uv.lock` pins the resolved pair.

**Blast radius (5 call sites).** `storage/delta.py::read_delta` (the seam everything
uses), `ml/feature_loader.py`, `features/labels.py`, `features/engineering.py`,
`backtesting/data_loader.py`. `api/deps.py` and the R7 observation already use
`to_pyarrow_dataset` and are not affected.

**Trigger to act:** a second sighting, or any sighting during a real ingest/backtest
run. When it happens, record the table, the command, the filter set, and whether other
processes were writing Delta at the time.

**Options, if you act:**

- *Seam swap* `to_pyarrow_table(filters=…, columns=…)` →
  `to_pyarrow_dataset().to_table(…)`. **Not a drop-in**: `to_pyarrow_dataset()` takes
  no `columns=`/`filters=`, so this restructures the seam and moves filtering onto the
  pyarrow-dataset path that R1's classify-and-post-filter design exists to avoid.
  Requires: `tests/unit/test_delta_read_filters.py` and
  `tests/unit/test_feature_loader_string_view.py` green, plus the R1 benchmark re-run
  (0.88× pushdown for a 60-session `symbol in` window, 0.98× full history — see
  20261030-01 §4 D4).
- *Dependency upgrade* `deltalake`/`pyarrow`. The honest fix if it is a finalizer or
  thread-shutdown bug, but it changes Arrow `string_view` behaviour, which is R1's
  open writer-side item. Its own thread, its own full gate run.
- *Detect only*: a subprocess smoke probe (`uv run python -c 'read_delta(...)'` in a
  loop) in CI or a health check turns a silent kill into a signal. Low value unless it
  recurs.

**Do not** "fix" it by swallowing exceptions: a SIGABRT cannot be caught, and a broad
`except` around the read seam would hide the real Delta errors `DeltaReadError` exists
to surface.

### 4.2 Nothing else is owed

The register's remaining rows are closed. Do not invent work from the "out of scope"
list in 20261030-01 §3 without a decision.

### 4.3 Writer-side `string_view` (storage change, ADR first)

Rows in `03_gold`/`04_platinum` drift to Arrow `string_view` after a label-only update,
so every Arrow predicate on those columns fails; R1 mitigated at the read seam and made
the drift visible in `equity doctor` (`storage_schema`). Eliminating the drift means
changing the writer (a deltalake/pyarrow upgrade or a custom writer) — a storage
boundary change. Write the ADR first (options, migration of existing files, what it
does to R1's residual-filter seam and its measured cost), then implement.

### 4.4 Freshness policy ceiling (decision, then code)

`PriceForecaster` caps `max_model_age_days` at `DEFAULT_MAX_MODEL_AGE_DAYS` (7), and
`predict()` persists that bound per row (`model_max_age_days`), which is what the read
API uses for `api_usability_status`. Consequences: one refit boundary covers about a
week of sessions, and a historical backfill needs one boundary per week. Widening it is
an ADR decision about how stale a model may be and still count as `current_usable` —
then a forecaster change and a preload default change, with the API derivation tests
updated.

## 5. Current lake state (what "the data" means right now)

```text
data/lake/03_gold/features      → 305 rows, 61 sessions (2026-08-06 … 2026-10-28), 5 tickers
data/lake/04_platinum/predictions → 100 rows, 20 sessions (2026-10-01 … 2026-10-28),
                                   5 tickers, every row valid | established | current_usable
data/models                     → 35 artifacts (7 refit dates × 5 tickers),
                                   every summary validation_status: verified
data/sample                     → seeded by `make showcase`; data/findings holds 3 cards
```

The canonical lake is **synthetic** by decision (ADR-0020). `equity doctor` reports that
under `synthetic_provenance`; `make reset-lake DRY=1` lists exactly what a reset would
delete, and `make reset-lake` refuses any lake it cannot prove synthetic (`unknown` for
a lake holding rows without the `synthetic_demo_seed` marker — never `real`).

Reproduce the prediction window:

```bash
uv run python -m equity_lake.devtools.refit_preload \
  --days 20 --refits 6 --train-window 20 --test-window 5 --embargo-window 1
```

## 6. Traps confirmed the hard way

- `read_delta` must pass `filters=None` (not `[]`) to delta-rs; pyarrow rejects an empty
  filter list with "Malformed filters".
- `is_trading_day` has **union** semantics ("some venue trades"). Seeding a multi-venue
  market must use `trading_days_between` (intersection) or `.HK`/`.SI` bars land on the
  other venue's holidays.
- `validate=False` and "no purged fold fits" are different claims. Both are
  `validation_status: unavailable`; only the second may blame the folds.
- Anything destructive must decide "empty" over **every** dataset it would delete,
  discovered generically (any directory with a `_delta_log`), plus any non-Delta data
  file. A hand-enumerated subset read as `empty` and would have wiped a populated lake.
- A model older than `DEFAULT_MAX_MODEL_AGE_DAYS` (7 calendar days) is expired at
  inference, so one refit boundary covers about a week of sessions. `refit_preload`
  refuses such a plan before training rather than half-writing.
- `structlog`'s `cache_logger_on_first_use=True` freezes a module logger's processors on
  first use; `structlog.testing.capture_logs()` cannot rebind it. Bind a fresh proxy in
  the test.
- Never restore a mutated file with `git checkout`/`git stash` while other work is
  uncommitted: copy to `/tmp`, mutate, restore from the copy, prove it with `diff -q`.
- Run only one `uv run pytest` at a time. Two concurrent runs made a 5-minute suite take
  21 minutes and made a sub-second test report 44 s, which is easy to misread as a
  performance regression.

## 7. If you want the same working pattern

1. Dispatch an implementation worker per dependency stage with the **frozen contract
   pasted into the brief**, and tell it not to commit; the orchestrator reviews the
   tree and commits.
2. Run an adversarial `reviewer` pass on the dirty tree **before** committing, one per
   stage. Last round it caught four real defects that green suites hid: a vacuous "no
   write" assertion, a fabricated "validation not requested" reason, a union-semantics
   calendar filter that seeded venue holidays, and a destructive reset that called a
   populated lake `empty`.
3. Mutation-drill every new rule: remove the rule, run the targeted tests, confirm a
   **named** test fails, restore from `/tmp`, `diff -q` to prove the restore.
4. Keep the full gate run (`pytest` serial **and** `-n auto`, ruff, `ruff format
   --check`, mypy, `equity catalog-generate`, `make lint-md`) as the last step.
5. Record the outcome in the register and in a handoff like this one, including the
   numbers you actually measured.
