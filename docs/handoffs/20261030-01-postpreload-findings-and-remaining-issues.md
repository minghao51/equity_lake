# 20261030-01 — Post-Preload Findings and Remaining Issues

**Title:** Spec the fallout of the T1–T7 pickup: Arrow `string_view` drift, holiday-dated synthetic bars, ML result truthfulness, and the governance edges left open
**Status:** **Implemented (2026-10-31).** Every register item landed: R1/R2/R3/R4/R5/R6/R7/R8/R9/R11 are built and gated, R10 is a proposed ADR awaiting owner acceptance. §9 records the pickup outcome and the traps; §7 states what is left.
**Recorded:** 2026-10-30 (implemented 2026-10-31)
**Baseline commit:** `8c59751` (spec) → `e2c6f42` (all items landed, tree clean)
**Related:** [20261029-01 handoff](20261029-01-demo-preload-and-audit-followups.md) (§8 records the T1–T7 pickup outcome), ADR-0020 (synthetic demo lake), ADR-0014 (label lifecycle), ADR-0015 (point-in-time adjustment), ADR-0016 (canonical identity)

---

## 1. Context

The T1–T7 pickup closed all seven tasks from the previous handoff and, in doing so,
surfaced three things the audit could not see from the outside:

1. A **new defect (F10)** that the first mitigation only patched at one reader.
2. A **data-generation defect (F11)** in `demo seed` that the preload exposed:
   synthetic bars exist on exchange holidays.
3. Two **truthfulness gaps** in the ML surface that a real training run on short
   history made visible (`folds=0 acc=0.000`, and a `forecast` command that
   prints a prediction without persisting one).

The T1–T7 change set is green and `make preload` produced a dense 61-session Gold
run plus 5 persisted predictions through the real ML path. R1 and R4 have since
landed (see §3, §4 and §8); the current gate numbers are at the top of §1. Nothing below is a regression from that work — every
item is either an unfixed part of F10, a defect the pickup documented but did not
repair, or a decision the pickup deferred.

### Current verified state (do not regress)

```text
uv run pytest -n auto        → 1738 passed, 17 skipped, 0 failed
uv run ruff check .          → All checks passed
uv run ruff format --check . → 320 files already formatted
uv run mypy                  → Success: no issues found in 174 source files
uv run equity catalog-generate → no diff
make lint-md                 → exit 0 (R8)
make showcase                → exit 0 in 74s, 9 runs, 3 FindingCards (R9)
GET /predictions             → 100 rows over 20 sessions; all
                               valid | established | current_usable | [] (R5)
```

### Findings register

| ID | Finding | Class | Severity | Status | Effort |
|---|---|---|---|---|---|
| **R1** | Gold/predictions string columns drift to Arrow `string_view`; every Arrow filter pushdown on them raises | storage/reader | **P1** | **Implemented** (read seam + `/predictions` filters + doctor observation); writer-side drift still open | M |
| **R2** | `demo seed` / `bootstrap sample` write bars on exchange holidays (Mon–Fri calendar) | data generation | **P1** | **Implemented** (`59a31d1`) | S–M |
| **R3** | `ml train` reports `status: trained` with `folds=0 acc=0.000`; `forecast --mode predict` prints a prediction that was never persisted | ML truthfulness | P2 | **Implemented** (`dc200e9`) | S |
| **R4** | `/predictions` is hardcoded `not_current_usable`; row-bound forecast provenance/semantics are never persisted | API contract | P2 | **Implemented** (`6df4130`, docs `3d9d779`; [ADR-0021](../decisions/0021-row-bound-forecast-provenance.md) + [20261030-02](../handoffs/20261030-02-row-bound-forecast-provenance-implementation.md)) | M–L |
| **R5** | Only one prediction date exists; historical predictions need per-date as-of refits | ML/surface | P3 | **Implemented** (`97055fc`, D2) — 20 sessions × 5 tickers live | M |
| **R6** | `devtools/preload.py` imports the private `seed_demo._configured_identities` | hygiene | P3 | **Implemented** (`40dcc3d`; acceptance read as scoped to the resolver — `sources.base._empty_frame` etc. predate it) | XS |
| **R7** | Synthetic rows in the canonical lake are indistinguishable from real ingestion (except Gold via coverage source) | provenance/governance | P2 | **Implemented** (`a820737`, D3) | S |
| **R8** | `make lint-md` exits 2 on three pre-existing findings | tooling | P4 | **Implemented** (`899c834`) | XS |
| **R9** | `make showcase` / README claims not re-verified after R2 changes the seeded data | verification | P4 | **Implemented** (`a426ff2`) | XS |
| **R10** | ADR-0021 does not define a `forecast_provenance_version` newer than the reader knows; the reader reports `unknown` (never usable) as a safe interim | decision gap | P4 | **Implemented** — [ADR-0022](../decisions/0022-future-forecast-provenance-envelope.md) proposed (`e2c6f42`) and **accepted** (`02e1298`) | XS |
| **R11** | Reviewer nits left on R4: no single writer→table→API end-to-end proof test (a column-name drift would pass both suites), and one API fixture hardcodes `feature_schema_version: 7` | test hygiene | P4 | **Implemented** (`8e5cf3b`) | XS |
| **R12** | `DeltaTable.to_pyarrow_table()` intermittently aborts the interpreter (`terminate called without an active exception`, exit 134) on deltalake 1.6.0 / pyarrow 21.0.0 — `equity doctor` aborted on roughly a third of runs during parallel work; the R7 observation avoids the path via `to_pyarrow_dataset().count_rows()` | storage/reader | P3 | **Watching** — 0/47 repro attempts at closure; trigger, options and the do-not in [20261031-01 §4.1](20261031-01-next-thread-and-r12-watch.md) | S–M |
| **R13** | Serial-order flake: `tests/unit/test_ml_jobs.py::test_run_prediction_job_clips_and_logs_out_of_bounds_probability` fails in a serial run, passes alone and under `-n auto` | test hygiene | P4 | **Implemented** (`02e1298`) — cause: `cache_logger_on_first_use` froze `equity_lake.ml`'s logger before `capture_logs` ran | XS |

Order taken: **R1 → R4 → R3 → R6 → R8 → R11 → R2 → R9 → R5 → R7 → R10** — all
landed in that order on 2026-10-31 (see §9).

---

## 2. Task specifications

### R1 — String columns drift to `string_view`, breaking Arrow filter pushdown

**Symptom.** `read_delta`, `DeltaTable.to_pyarrow_table(filters=…)`, or
`Dataset.to_table(filter=…)` raises on any table that has had a label-only update:

```text
ArrowNotImplementedError: Function 'greater_equal' has no kernel matching input types (string_view, string_view)
pyarrow.lib.ArrowNotImplementedError: Function 'equal' has no kernel matching input types (string_view, string_view)
```

**Reproduction.**

```bash
uv run python - <<'EOF'
from datetime import date
import polars as pl, pyarrow.parquet as pq
from equity_lake.storage.delta import read_delta
read_delta("03_gold/features", filters=[("ticker", "=", "AAPL")])   # raises
print({str(pq.read_schema(p).field("ticker").type)
       for p in __import__("pathlib").Path("data/lake/03_gold/features").rglob("*.parquet")})
# {'large_string', 'string_view'}
EOF
```

**Where.**

- `src/equity_lake/storage/delta.py:271-334` — `update_delta_columns` merges through
  delta-rs (`dt.merge(source=df_polars.to_arrow(), …)` at L314). The source table is
  `large_string`; the rewritten target files are `string_view`. Verified minimal
  reproduction: one `merge_delta` → `large_string`, then one `update_delta_columns`
  → `string_view`.
- `src/equity_lake/storage/delta.py:337-363` — `read_delta` passes caller filters
  straight to delta-rs, so any string filter inherits the defect.
- `src/equity_lake/ml/feature_loader.py:57-72` — **current mitigation**: pushes down
  only the date range and applies the symbol scope in Polars. Working, but local.
- `src/equity_lake/features/engineering.py:114` — pushes down `(symbol, "in", tickers)`
  on **Bronze**; it works today only because Bronze files are still `large_string`
  (they have never been merge-rewritten). If Bronze is ever rewritten by
  `update_delta_columns`, this raises too.
- `src/equity_lake/features/labels.py:230-236` (`_stored_keys`) and
  `src/equity_lake/features/labels.py:433` filter dates only — unaffected.

**Root cause.** delta-rs rewrites matched files with Arrow `string_view` string
columns regardless of the source schema, and PyArrow 21 **and** 22 have no
comparison kernels for `string_view` (`=`, `>=`, `isin`, and
`cast(pa.string())`-then-compare all verified to fail). The Delta log still
declares `string`, so a Delta-schema check cannot detect it.

**Decision (owner, 2026-10-30): D4 = A — classify and post-filter, with ordering
operators included.** Implemented as specified below.

**As built.**

1. `read_delta` splits every predicate list with `_split_filters`: predicates on
   columns the Delta log declares as `string`/`large_string` become *residual*,
   everything else stays a delta-rs pushdown. Residual operations are `=`, `==`,
   `!=`, `in`, `not in`, `<`, `<=`, `>`, `>=`, translated to Polars in
   `_residual_expression` (nulls drop, matching Arrow). A missing filter column or
   an unsupported string operator raises `DeltaReadError` with the reason as cause.
2. Projections stay honest: residual columns are added to the read and dropped
   again (`_with_residual_columns`), so `columns=["value"]` with a `ticker` filter
   returns exactly `["value"]`. An all-residual predicate set passes
   `filters=None` to delta-rs, because pyarrow rejects an empty filter list
   ("Malformed filters") — found while implementing.
3. Each read with residual predicates emits one `delta_read_residual_filter`
   debug event naming the columns.
4. **A second reader was broken live and is fixed too:**
   `GET /predictions?ticker=AAPL` returned **HTTP 503** on the canonical table
   because `api/deps.py` pushed identity/ticker equality into the Arrow scanner.
   `deps.list_predictions` now applies those equalities to the decoded rows (the
   date partition filter is still pushed down, and the file/row/byte budgets still
   bound the scan). The contract test that pinned "Arrow row filters before Python
   conversion" was updated to pin the new, documented behaviour.
5. **Drift is visible.** `storage.delta.string_view_columns()` reads up to 50
   newest Parquet footers per table (metadata only) and reports Arrow `string_view`
   string columns; `equity doctor` now includes a `storage_schema` observation per
   watched price table plus `03_gold/features` and `04_platinum/predictions`, with
   states `missing` / `clean` / `drifted` / `unreadable`.

**Measured pruning trade (the question from §4).** Residual filtering costs no
measurable pruning on the real Bronze table (783 day-partitioned files, 50 symbols):
`symbol in […]` + a date range measured best-of-4 as `0.88×` of the pushdown time
for a 60-session window (320.8 ms vs 364.5 ms, 63 rows) and `0.98×` for full history
(613.6 ms vs 624.3 ms, 783 rows). Date pushdown still does the pruning here; the
symbol predicate was only ever row-group pruning inside each day file.

**Acceptance (verified).**

- `read_delta("03_gold/features", filters=[("ticker", "=", "AAPL")])` → 61 rows.
- `read_delta(..., filters=[("symbol", "in", ["AAPL", "MSFT"])])` → 122 rows.
- `read_delta("01_bronze/market_data/us_equity", filters=[("symbol","in",["AAPL"]),
  ("date",">=",…)], …)` → works (previously only by luck of `large_string` files).
- A filter on an absent column raises `DeltaReadError` ("filter column not in table
  schema"); `("ticker", "like", …)` raises `DeltaReadError` with the supported list.
- `GET /predictions?ticker=AAPL` → 200 with only AAPL rows (was 503).
- `equity doctor` reports `03_gold/features` as `drifted` (10 columns) and
  `04_platinum/predictions` as `drifted` (7 columns) on the current local lake.

**Tests added.** `tests/unit/test_delta_read_filters.py` (9 cases: drift
precondition, pushdown/residual split, `=`, sets, ordering, nulls, projection
hiding, absent column, unsupported operator, date-only pushdown);
`test_feature_loader_string_view.py::test_read_delta_filters_the_same_drifted_gold_table`;
`test_api.py::test_filtered_predictions_read_survives_a_merge_rewrite`;
`test_tracking_readiness.py` drift and clean doctor cases.

**Still open (unchanged by R1).** The storage-level drift: delta-rs merge rewrites
keep writing `string_view` string columns. R1 makes every reader correct and the
drift observable; eliminating it at the writer (or via a deltalake upgrade) remains
its own decision.

---

### R2 — Seeded synthetic prices contain bars on exchange holidays

**Symptom.** `data/lake/01_bronze/market_data/us_equity` holds 783 dates
(2023-10-30 … 2026-10-28) of which **31 are not US exchange sessions**, and **0
calendar sessions are missing**. Examples: 2026-09-07 (Labor Day), 2026-07-03
(observed Independence Day), 2026-06-19, 2025-01-09 (national day of mourning).

**Reproduction.**

```bash
uv run python - <<'EOF'
from equity_lake.core.calendar import is_trading_day
from equity_lake.storage.delta import read_delta
dates = sorted(set(read_delta("01_bronze/market_data/us_equity", columns=["date"])["date"].to_list()))
bad = [d for d in dates if not is_trading_day("us_equity", d)]
print(len(dates), len(bad), bad[:5])
EOF
```

**Where.**

- `src/equity_lake/devtools/seed_demo.py:261` — `days = trailing_business_days(int(years * 365))`
  then `synthetic_ohlcv(...)` per symbol; no calendar filter.
- `src/equity_lake/devtools/seeding.py:212-226` — `business_days`/`trailing_business_days`
  are Mon–Fri only, by design (they are window helpers, not calendars).
- `src/equity_lake/cli/bootstrap.py:190` — same weekday-only window, sliced to the last
  *days* entries; same omission.
- `src/equity_lake/core/calendar.py:62` — `is_trading_day(market, d)` already exists and
  is the authoritative answer.

**Root cause.** The seeder uses a weekday approximation instead of the exchange
calendar, so a seeded lake asserts sessions the venue was closed.

**Consequences (why this is P1, not cosmetics).**

- ADR-0014 defines the label horizon as the next **observed** session. With a
  holiday bar present, `2026-09-04` finalizes against `2026-09-07` (a closed day)
  and `2026-09-08`'s predecessor is the phantom bar — the pickup hit exactly this:
  the calendar-derived preload left `2026-09-04` permanently unlabeled until the
  session list was re-derived from Bronze.
- Any calendar-aware consumer (`is_trading_day`, freshness, arena plan dates)
  disagrees with rows that exist in the lake.
- No existing check catches it: `gap_detection` looks for *missing* sessions
  (intersection semantics), and `health` freshness uses `is_trading_day` on the
  clock, not on stored rows.

**Specification.**

1. Filter generated synthetic dates through `is_trading_day(US_EQUITY_SOURCE_MARKET, day)`
   in `devtools/seed_demo.py`, and through the corresponding market's calendar in
   `cli/bootstrap.py` for each market it writes. Keep `trailing_business_days` as
   the window helper (request a wider window, then filter), so no consumer of that
   helper changes.
2. Leave `SUPPORTED`/row-count contracts alone otherwise: same symbols, same
   columns, same provenance stamping, same coverage proofs — only the date set
   shrinks (~4% over three years).
3. Regenerate the sample lake (`make showcase` path is the owner-visible one) and,
   because the canonical lake currently holds synthetic data by decision, note the
   regeneration command in the handoff §3 rather than running it implicitly.
4. Add a one-line note to ADR-0020's consequences: seeded sessions follow the
   exchange calendar; synthetic bars never claim a closed session.

**Acceptance criteria.**

- A freshly seeded sample lake has **zero** dates where `is_trading_day` is false,
  and no calendar session missing inside its window.
- `tests/unit/test_seed_demo.py` asserts both properties on a short window that
  contains a known closure (e.g. 2026-07-03 and 2026-09-07).
- A label-chain regression: seeding a window that straddles a closure and running
  two consecutive sessions finalizes the earlier label with
  `label_available_date` equal to the **next open** session.
- `equity demo seed --dry-run` still reports `days`/`rows` truthfully (report the
  filtered count, not the requested one).

**Tests to add.** `tests/unit/test_seed_demo.py`: `test_seeded_dates_follow_exchange_calendar`,
`test_seeded_window_has_no_missing_sessions`, and the label-chain case above
(`tests/unit/test_label_lifecycle.py` or the seed test — prefer the seed test so it
covers the generator). Update any test that pins an exact row count for a seeded
window.

**Risk.** Medium-low. Row counts drop, so tests and docs that quote "39,150 rows"
or "5y × 50 symbols" must be recomputed; `make showcase` runtime drops slightly.
Nothing depends on the phantom dates except the two behaviours above — both are
the point of the fix.

---

### R3 — ML results claim success without validation, and a print-only prediction

**Symptom A.** After a real 60-session preload, `equity ml train` prints:

```text
  status: trained | model: instrument-v1-…_xgboost_v1_direction_2026-10-27.pkl
  folds=0 acc=0.000 prec=0.000
```

and the saved `*.training_summary.json` carries `validation_fold_count: 0`,
`mean_accuracy: 0.0`, `mean_precision: 0.0`, `mean_recall: 0.0`, `status: trained`.
The zeroes are placeholders, not measurements.

**Symptom B.** `equity forecast --mode predict --ticker AAPL --date 2026-10-28`
prints a full prediction JSON with no `persisted` field, while nothing is written
(the pipeline's ML stage is what persists). The ML-stage results, by contrast, do
carry `persisted` (W02/F25).

**Where.**

- `src/equity_lake/ml/validation.py:141-142` — returns
  `{"folds": 0, "mean_accuracy": 0.0, "mean_precision": 0.0, "mean_recall": 0.0}`
  when no purged fold fits.
- `src/equity_lake/ml/forecasting.py:1537-1555` — `_build_training_summary` defaults
  every metric to `0.0` and hardcodes `status: "trained"`.
- `src/equity_lake/cli/commands/ml.py:251-258` — prints `folds=… acc=… prec=…`
  whenever `status == "trained"`.
- `src/equity_lake/cli/commands/intelligence.py:16-35` — `_format_training_summary`
  does the same (`Mean accuracy: 0.000`).
- `src/equity_lake/cli/commands/intelligence.py:93-96` — `predict` echoes JSON only.

**Root cause.** "Trained" (an artifact exists) and "validated" (purged walk-forward
metrics exist) are conflated in one dict, and missing metrics are rendered as
zeroes rather than withheld.

**Specification.**

1. Add `validation_status` (`"verified"` | `"unavailable"`) and
   `validation_unavailable_reason` to the training summary. When
   `validation_fold_count == 0`, omit the three metric keys (or set them to
   `null`) — never `0.0`.
2. Keep `status: "trained"`: the model artifact is real and usable for the
   showcase. Do not turn this into a training failure.
3. Both formatters print `validation: unavailable (no purged walk-forward fold fits in N labelled rows)`
   instead of a metric line when metrics are absent, and print the metrics only when
   `validation_status == "verified"`.
4. `forecast --mode predict` gains `"persisted": false` in its payload plus one
   stderr line naming the persisting path (`equity pipeline` ML stage). Do **not**
   add a write path to the print command in this task — that is R4/R5 territory.
5. Keep the existing fail-fast in `_tune_hyperparameters` (it already raises on zero
   folds with a clear message) untouched.

**Acceptance criteria.**

- `equity ml train` on the current 60-session history prints no `acc=`;
  the summary JSON contains `validation_status: "unavailable"` and no zero metrics.
- With enough history for at least one purged fold, both formatters print the
  metrics and `validation_status: "verified"`.
- `equity forecast --mode predict` output contains `"persisted": false` and nothing
  is written to `04_platinum/predictions` by that command (assert row count before/after).

**Tests to add.** Unit tests for `_build_training_summary` (zero-fold vs verified)
and `_format_training_summary`; a CLI-output test for `ml train` with a patched
summary; extend `tests/unit/test_cli_unified.py` help/predict coverage. Existing
pins to update: `tests/unit/test_cli_unified.py:524` (folds=2 rendering),
`tests/unit/test_price_forecasting.py:347` (`>= 0`).

**Risk.** Low. Only the CLI and two summary keys change. `grep` confirms no
non-test consumer reads `mean_accuracy` directly.

---

### R4 — `/predictions` is not usable by contract (decided: persist, D1; standard in ADR-0021)

**Status: Implemented.** The row-bound proof is persisted, validated at the write
boundary, and derived by the API, with tests covering the usable, historical,
invalid, unknown, and `v2_meta_label` paths (see the
[implementation plan](20261030-02-row-bound-forecast-provenance-implementation.md)).

**Symptom (before).** Even with 5 real persisted predictions, `GET /predictions`
reports `api_usability_status: "not_current_usable"` with
`row_bound_forecast_provenance_is_not_persisted` and
`prediction_semantics_are_unverified`. Those strings are hardcoded, not derived
from the row.

**Where.** `src/equity_lake/api/deps.py:295-324` (`_annotate_prediction`);
`docs/user-guide/20260811-read-api.md:70` documents the limitation. The stored row
(`04_platinum/predictions`) has `date, direction, feature_schema_version,
identity_schema_version, market, mic, model_mode, model_version, probability,
symbol, ticker` — no provenance or semantics fields.

**Decision (owner, 2026-10-30): persist (option a).** The contract caveat below is
part of the task, not an objection to it.

**Precondition satisfied:** [ADR-0021](../decisions/0021-row-bound-forecast-provenance.md)
(accepted 2026-10-30) defines the supported row-bound forecast proof (E1 artifact
identity, E2 temporal validity, E3 contract compatibility, E4 decision semantics),
the explicit non-provenance list, the per-`model_mode` semantics, and the derivation
rules. Execution is tracked in the
[implementation plan](20261030-02-row-bound-forecast-provenance-implementation.md). The original constraint, for
context — the read-API guide (`docs/user-guide/20260811-read-api.md:70`) states the
standard explicitly: *"A model-version string, current schema number, filename, or
current configuration is not provenance."* So persisting only the payload fields
the writer already has (`model_version`, `threshold_used`, `horizon_days`,
`price_adjustment_version`, …) would be dropping useful metadata, **not** a
supported row-bound forecast proof, and flipping `api_usability_status` on that
basis would silently weaken a documented guarantee. R4 therefore starts with an
ADR that defines what qualifies as a row-bound forecast proof (the analogue of the
ADR-0015 action-coverage proof: source publication time, observable-at, validity
window, and the instrument/date it is bound to) and what "established directional
semantics" means per `model_mode` (note `v2_meta_label` deliberately does **not**
establish direction today).

**Specification.**

1. **ADR first — done.** [ADR-0021](../decisions/0021-row-bound-forecast-provenance.md)
   defines the proof fields and their evidence classes, the explicit
   non-provenance list, the freshness rule, the semantics rule per `model_mode`,
   and the legacy story (rows stay `unknown` and visible, never backfilled).
2. **Persist what the ADR admits.** The inference payload already carries most
   candidates (`model_trained_at`, `model_age_source`, `threshold_used`,
   `threshold_source`, `horizon_days`, `price_adjustment_version`,
   `availability_schema_version`, `feature_schema_compatibility`,
   `adjustment_compatibility`, `candidate_strategy_fingerprint`) — the writer drops
   them at `ml/__init__.py:124` (it persists 11 of the ~20 fields). Extend the row,
   bump a prediction schema version constant, and add the ADR-defined proof fields.
3. **Validate at the write boundary.** Extend `validate_predictions`
   (`ml/__init__.py:29-47`) with the proof's presence/consistency rules so a row
   cannot be written claiming a proof it does not carry.
4. **Derive, do not hardcode.** In `api/deps.py:295-324`, compute
   `api_provenance_status`, `api_prediction_semantics_status` and
   `api_usability_status` from the stored fields; keep the existing reason codes for
   the unknown/invalid cases.
5. **Docs.** Update the read-API guide (the hardcoded sentence must change), the
   pipeline preload section, and the predictions table description; regenerate the
   catalog if the dataset schema is cataloged.

**Acceptance.**

- A freshly written prediction row reports `api_usability_status: current_usable`
  with provenance/semantics reasons empty.
- Removing the proof fields (or writing a row without them) yields `unknown` +
  the existing reason codes — no silent upgrade.
- `v2_meta_label` rows stay `api_prediction_semantics_status: unknown` until the ADR
  defines its semantics.
- Existing rows keep `unknown` and remain visible.

**Tests.** Writer round-trip (payload → table → API annotation), a validator
rejection case for a claimed-but-absent proof, and an API test that both the usable
and the unknown paths are reachable.

**Acceptance.** (a) `/predictions` rows report `api_usability_status` derived from
the row; at least one row is `current_usable`; removing a provenance field flips it
back to `not_current_usable` in a test. (b) docs state the limitation in every
place that advertises `/predictions`, and no doc implies usability.

**Risk.** (a) is the largest item in this document: schema evolution + ADR +
provenance claims that must not be fabricated. If the provenance is not actually
known at write time, (a) is not available — do not invent it.

---

### R5 — Historical predictions need per-date as-of refits (decided: bounded refit preload, D2)

**Symptom.** `04_platinum/predictions` covers exactly one date (2026-10-28) with
models trained on 2026-10-27. Any backfilled prediction date would have to be
produced by a model trained **before** that date; `PriceForecaster` already refuses
future-trained models (`has_future_model` selection, `ml/forecasting.py:1385-1390`).

**Decision (owner, 2026-10-30): bounded refit preload (option b), default ≤ 12
refits.**

**Specification.**

1. New bounded entry point alongside `devtools/preload.py` (same validation style:
   explicit bound, refuse above the maximum, report skipped sessions). It walks
   refit boundaries oldest → newest: train as of the boundary, then run the pipeline
   ML stage for the sessions after it.
2. Reuse the existing refit/availability machinery — `PriceForecaster` already
   selects models with `refit_boundary <= as_of_date` and refuses future-trained
   ones (`ml/forecasting.py:1385-1390`); do not add a second selection rule.
3. Write predictions only through `run_prediction_job` (the pipeline stage). No
   hand-written rows.
4. Report the runtime honestly: one training per boundary (~15 s measured) plus
   inference per session; state the default bound and the cost in the help text and
   the user guide.
5. Order: this lands after R3 (so an unvalidated model says so) and after R4 if the
   persistence schema changes (so backfilled rows are written with the new fields).

**Acceptance.**

- `/predictions` covers the requested window (one row per instrument per session).
- Every row satisfies `model_trained_at <= date`; the API's provenance/usability
  annotations match the model actually used.
- The loop is idempotent (re-running overwrites, no duplicate keys).
- An insufficient window fails with the R3 message (`validation: unavailable …`)
  instead of silently producing unvalidated models, and the run reports which
  boundaries were skipped and why.

**Risk.** (b) multiplies training cost by the number of boundaries; keep the bound
explicit and small (≤ 12 refits by default).

---

### R6 — Promote the shared config→identity resolver

**Where.** `src/equity_lake/devtools/preload.py:47` imports
`devtools/seed_demo.py::_configured_identities` (private). Both devtools modules
now depend on the same fail-closed rule: venue comes from `config/tickers.yaml`,
never from ticker spelling.

**Specification.** Promote it to a public helper (either
`core/instruments.configured_identity(symbol, market)` or
`devtools/seeding.configured_identities(symbols, market)`), keep the
`identity_provenance = current_ticker_config` label and the fail-closed error text,
update both call sites and their tests. No behaviour change.

**Acceptance.** No `_`-prefixed cross-module import remains; `seed_demo` and
`preload` share one implementation; existing seed/preload tests stay green.

**Risk.** XS — pure refactor.

---

### R7 — Synthetic rows in the canonical lake are (mostly) indistinguishable (decided: document + reset, D3)

**Evidence.**

| Table | Marker | Distinguishable from real data? |
|---|---|---|
| `01_bronze/market_data/us_equity` | `identity_provenance = current_ticker_config` | **No** — a real config-resolved ingestion uses the same label |
| `02_silver/corporate_action_coverage` | `source = synthetic_demo_seed` | Yes |
| `03_gold/features` | `action_coverage_source = synthetic_demo_seed` | Indirectly (via the coverage proof) |
| `04_platinum/predictions` | none | **No** |

The pickup's decision was to keep the synthetic canonical lake (T2's acceptance
reads it). Nothing today lets an operator answer "is this lake synthetic?" in one
step, and `README`/dashboard present it as data.

**Decision (owner, 2026-10-30): document + reset (option b). No schema change.**

**Specification.**

1. `equity doctor` gains one read-only observation that answers "is the canonical
   lake synthetic?" from the markers that already exist (`02_silver` coverage
   `source = synthetic_demo_seed`, Gold `action_coverage_source`), reporting counts
   per table and the earliest/latest affected date. It must say "unknown" rather
   than "real" when no marker is present.
2. A `make reset-lake` target (and a documented equivalent command) removes the
   synthetic artifacts deterministically: `data/lake/01_bronze`,
   `data/lake/02_silver`, `data/lake/03_gold`, `04_platinum/predictions`, and
   `data/models`, with a `DRY=1` listing mode and a confirmation guard that refuses
   when the lake looks real (i.e. the doctor observation reports real/unknown
   provenance).
3. README/demo docs state that the local lake may hold synthetic rows, that Bronze
   rows are **not** distinguishable (only `current_ticker_config`, the same label a
   real config-resolved ingestion writes), and that the reset target is the
   supported way back to an empty lake before real ingestion.
4. Do not invent a third provenance label; R7(b) is visibility, not provenance.

**Acceptance.**

- `equity doctor` reports the synthetic marker counts and refuses to call an
  unmarked lake real.
- `make reset-lake DRY=1` lists exactly the paths above and writes nothing;
  `make reset-lake` removes them and the following `equity doctor` reports an empty
  lake.
- The reset target refuses (exit non-zero) when the lake reports real provenance.

**Tests.** Doctor observation unit test on a temporary lake with and without the
markers; a `test_make_targets.py` contract test that `DRY=1` is non-destructive and
that the path list matches the documented set.

**Risk.** S. The target is destructive, so the confirmation guard and the dry-run
mode are part of the acceptance, not optional.

---

### R8 — `make lint-md` is not green

**Symptom.** `make lint-md` exits 2:

```text
README.md:10:1: MD001 Heading levels should only increment by one level at a time
README.md:40:1: MD033 Inline HTML [Element: details]
docs/20260615-audit-findings-and-remaining-tasks.md:41:1: MD032 Lists should be surrounded by blank lines
```

All three predate the T1–T7 work (verified against `f441d57`).

**Specification.** Fix MD032 and MD001 in content (blank line before the list;
correct the heading level). For MD033 keep the collapsible `<details>` block and
add a documented pymarkdown config (`permit` `details`/`summary` for README) rather
than rewriting the README structure — the config must also be honoured by
`make lint-md` (`uv run pymarkdown -d md013 scan README.md docs`).

**Acceptance.** `make lint-md` exits 0; the README still renders its collapsible
section; no other doc file loses lint coverage.

**Risk.** XS.

---

### R9 — Re-verify the owner-visible path after R2

**Specification.** After R2 lands, run `make showcase` end to end and update every
place that quotes its output or runtime (`README`, arena guide, ADR-0020
consequences, notebook). Confirm 9 runs / 3 FindingCards, and re-check the
`/findings`, `/backtests`, `/signals`, `/predictions` read-API surfaces listed in
the previous handoff §2.4.

**Acceptance.** `make showcase` exit 0 with the documented counts; README runtime
statement still true; recorded in the follow-up handoff.

**Risk.** XS. Blocked by R2.

---

## 3. Out of scope (recorded so it is not re-litigated)

- Writer-side elimination of `string_view` (deltalake/pyarrow upgrade or custom
  writer). R1 mitigates at the read seam and makes the drift visible; a writer fix
  is a storage change with its own decision.
- Broad schema-evolution/DDL hardening (still W06 from an earlier register).
- Real-vendor corporate-action proofs (ADR-0015 gate) — unchanged.
- Removing synthetic data from the canonical lake — decision 1 of the previous
  handoff keeps it; R7 covers visibility, not removal.

## 4. Decisions

**Taken 2026-10-30:**

1. **D1 (R4) — persist.** Row-bound forecast provenance and semantics are persisted
   and `api_usability_status` is derived from the row. Precondition: an ADR first,
   because the read-API guide's current standard excludes model metadata alone from
   counting as provenance (see R4).
2. **D2 (R5) — bounded refit preload.** Per-date as-of refits, default ≤ 12
   boundaries, oldest → newest, through the pipeline ML stage only.
3. **D3 (R7) — document + reset.** No new provenance label; `equity doctor` reports
   the synthetic markers and `make reset-lake` (with `DRY=1`) is the supported way
   back to an empty lake.

4. **D4 (R1) — filter seam: classify-and-post-filter, ordering operators
   included.** String predicates are applied in Polars; date/numeric predicates stay
   a delta-rs pushdown. Measured on Bronze: `0.88×` the pushdown time for a 60-session
   `symbol in` window and `0.98×` for full history (no pruning penalty).

**Still open:** none. R10 (future proof-envelope version) is a documentation gap, not
a decision this handoff can settle.

## 5. Guardrails

- `uv run <command>` only; never bare `python`.
- Present a plan for approval before modifying code (R1 and R2 touch shared seams).
- ADR before implementation for any schema/contract change: R4(a) and R7(a) require
  one; R2 requires an ADR-0020 amendment note, not a new ADR.
- Never fabricate provenance, labels, predictions, or metrics — R3 exists precisely
  because zeroes were standing in for measurements.
- The only sanctioned synthetic labels remain `synthetic_demo_seed` (coverage proofs)
  and `current_ticker_config` (config-resolved price identity); do not add a third
  without an ADR.
- Polars first; pandas only at external-library boundaries.
- CLI changes need help text, a help-scan test in `tests/unit/test_cli_unified.py`,
  and a user-guide update. Catalog regeneration only if a pipeline/feature surface
  changes (`uv run equity catalog-generate`).
- Validation before handoff: `uv run pytest -n auto`, `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy`, `make lint-md`.

## 6. Reproduction and verification commands

```bash
# R1 — the drift and the pushdown failure
uv run python -c "
from equity_lake.storage.delta import read_delta
print(read_delta('03_gold/features', filters=[('ticker','=','AAPL')]).height)"
uv run python -c "
import pathlib, pyarrow.parquet as pq
print({str(pq.read_schema(p).field('ticker').type) for p in pathlib.Path('data/lake/03_gold/features').rglob('*.parquet')})"

# R2 — holiday-dated bars
uv run python -c "
from equity_lake.core.calendar import is_trading_day
from equity_lake.storage.delta import read_delta
d=sorted(set(read_delta('01_bronze/market_data/us_equity', columns=['date'])['date'].to_list()))
print(len(d), len([x for x in d if not is_trading_day('us_equity', x)]))"

# R3 — zero-fold success and the print-only prediction
uv run equity ml train --ticker AAPL --start 2026-08-06 --end 2026-10-27 | tail -2
uv run equity forecast --mode predict --ticker AAPL --date 2026-10-28 | grep -c persisted || echo "no persisted field"

# R8 — markdown lint
make lint-md; echo "exit=$?"

# Baseline (must stay green)
uv run pytest -n auto && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## 7. Definition of done for this handoff series

- **Done:** R1–R11 and R13 — implemented, tested, and recorded with commit pointers in
  the register above. R12 is deliberately *watched*, not fixed (trigger and options in
  [20261031-01 §4.1](20261031-01-next-thread-and-r12-watch.md)).
- **Done to the letter:** `make lint-md` exits 0; every doc, README claim, notebook
  output, and CLI line that this series touched states behaviour that is true when run
  (R3 and R9 were the two that did not).
- **Residuals, all closed at `02e1298`:** the five pre-R3 `*.training_summary.json`
  files carrying `mean_accuracy: 0.0` were replaced by re-deriving that boundary
  through the refit preload (35/35 summaries now carry `validation_status`;
  `GET /models` is homogeneous), and `notebooks/06-ml-prediction.ipynb`'s stale
  `folds: 0 / mean_accuracy: 0.0` output is cleared.
- Each closing change updates this register, records the new lake state, and records
  the verified baseline numbers.

## 8. Next thread: start here

**Start with [20261031-01](20261031-01-next-thread-and-r12-watch.md)** — it is the
current brief: verified baseline, the closure decisions, the three items that remain
(R12 watch, writer-side `string_view`, the freshness-policy ceiling), the lake state,
and the traps.

**Do not re-litigate.** ADR-0021 and ADR-0022 are accepted; R1's read seam, R4's proof
columns, R3's "no unmeasured metric is printed", R5's write-through-the-pipeline rule,
R7's "never report an unmarked lake as real or empty", and R10's "no new status
vocabulary" are contract. A change to any of them needs a new ADR.

**What worked (reuse it):**

1. One implementation worker per dependency stage, frozen contract pasted into the
   brief, worker told not to commit — the orchestrator reviews the tree and commits.
2. An adversarial `reviewer` pass on the dirty tree **before** committing, one per
   stage. This pickup it caught four real defects that green suites hid: a vacuous
   "no write" assertion, a fabricated "validation not requested" reason, a
   union-semantics calendar filter that seeded venue holidays, and a destructive reset
   that called a populated lake `empty`.
3. Mutation-drill every new rule: remove the rule, run the targeted tests, confirm a
   *named* test fails, restore from a `/tmp` copy (not `git checkout`, which would
   discard other uncommitted work), and `diff -q` to prove the restore.
4. Full gates last, never first — and run them **one at a time**. Serial `pytest` is a
   real gate: R13 existed only in the serial order, and two concurrent runs once made a
   5-minute suite take 21 minutes and a sub-second test report 44 s.

## 9. Pickup outcome (2026-10-31)

Landed in this order, one commit per stage, each reviewed with a mutation-drilled
rule before commit:

| Task | Commit | What changed |
|---|---|---|
| R3 | `dc200e9` | training summaries carry `validation_status`; metric keys are absent, never `0.0`; both formatters print the reason; `forecast --mode predict` reports `persisted: false` |
| R6 | `40dcc3d` | `configured_identities` promoted to `devtools/seeding.py`; one implementation, identical fail-closed text |
| R8 | `899c834` | MD001/MD032 fixed in content, narrow documented MD033 permit, `make lint-md` green |
| R11 | `8e5cf3b` | writer → real Delta → `/predictions` end-to-end proof test; `FEATURE_SCHEMA_VERSION` replaces the hardcoded `7` |
| R2 | `59a31d1` | seeded sessions follow the exchange calendar (`is_trading_day` for the single-venue seed, `trading_days_between` for the multi-venue sample); 5y us_equity 1305 weekdays → 1255 sessions, 0 phantom |
| R7 | `a820737` | `equity doctor` reports `synthetic_provenance`; `make reset-lake` (DRY=1) removes exactly five paths and refuses any lake it cannot prove synthetic |
| R9 | `a426ff2` | `make showcase` re-verified (74 s, 9 runs, 3 cards) and the strategy-lab notebook re-executed against the refreshed lake |
| R5 | `97055fc` | bounded as-of refit preload (`make refit-preload`, ≤12 boundaries) writing through `run_prediction_job`; plan-time age refusal, truthful coverage, idempotent re-runs |
| R10 | `e2c6f42` | proposed ADR-0022 codifying future-envelope reading and the v2 procedure |

**New lake state after this pickup** (canonical `data/lake`):

```text
03_gold/features     → 305 rows, 61 sessions (2026-08-06 … 2026-10-28), 5 tickers
04_platinum/predictions → 100 rows, 20 sessions (2026-10-01 … 2026-10-28), 5 tickers,
                          every row valid | established | current_usable
data/models          → 35 artifacts (7 refit dates × 5 tickers)
data/sample          → re-seeded by make showcase; data/findings holds 3 cards
```

Reproduce the prediction window:

```bash
uv run python -m equity_lake.devtools.refit_preload \
  --days 20 --refits 6 --train-window 20 --test-window 5 --embargo-window 1
```

**Traps confirmed the hard way (keep these):**

- `read_delta` must pass `filters=None` (not `[]`) to delta-rs.
- `is_trading_day` has **union** semantics: for a multi-venue market it says "some
  venue trades", so seeding `hk_sg_equity` needs `trading_days_between` (intersection)
  or `.HK`/`.SI` bars land on the other venue's holidays.
- `validate=False` and "no fold fits" are different claims; both are `unavailable`,
  but only the second may blame the folds.
- Anything destructive must decide "empty" over **every** dataset it would delete,
  discovered generically — a hand-enumerated subset read as `empty` and wiped a lake.
- A model older than `DEFAULT_MAX_MODEL_AGE_DAYS` (7 calendar days) is expired at
  inference, so one refit boundary covers roughly a week of sessions; the preload
  refuses the plan up front instead of half-writing.
- `make reset-lake` refuses a `lake_dir` split, a symlinked target, an unrecognised
  `DRY` value, and any lake that is not provably synthetic.

### Closure (2026-10-31, `02e1298`)

Three loose ends from §9 were settled after the table above:

- **R10** — ADR-0022 **accepted** as proposed (`unknown` + reason token, no new status
  vocabulary) with the asymmetric reader/writer rule and the v2 procedure.
- **R13** — fixed at the test: `cache_logger_on_first_use=True` freezes
  `equity_lake.ml`'s module logger on first use, so `capture_logs()` cannot rebind it
  after an earlier serial test ran `setup_structured_logging()` for real. Serial
  `pytest` went from 1 failed / 1737 passed to **1738 passed, 0 failed**.
- **Residuals** — the five pre-R3 summaries were replaced by re-deriving the
  2026-10-27 boundary through `refit_preload --days 2 --refits 1` (35/35 summaries now
  carry `validation_status`, the five 10-27 rows were rewritten so row and artifact
  shas stay bound, 100 rows / 20 sessions unchanged), and notebook 06's stale
  validation output is cleared.

**R12 is now a watch item**, not a task: 0/47 reproduction attempts at closure
(0/20 gold reads, 0/15 prediction reads, 0/12 `equity doctor` runs), user-visible
symptom already avoided, and both candidate fixes touch the read seam R1 stabilised.
The trigger to act, the options and the "do not" are in
[20261031-01 §4.1](20261031-01-next-thread-and-r12-watch.md).

Final verified baseline at `02e1298`:

```text
uv run pytest                 → 1738 passed, 17 skipped, 0 failed   (serial)
uv run pytest -n auto         → 1738 passed, 17 skipped, 0 failed
uv run ruff check .           → All checks passed
uv run ruff format --check .  → 320 files already formatted
uv run mypy                   → Success: no issues found in 174 source files
uv run equity catalog-generate → no diff
make lint-md                  → exit 0
GET /predictions              → 100 rows / 20 sessions, all current_usable
GET /models                   → 35 summaries, all validation_status: verified
```
