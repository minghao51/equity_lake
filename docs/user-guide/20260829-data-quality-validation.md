# Data Quality Validation and Drift

The `equity validate` sub-app is the interactive surface of the validation
stack. Pointblank schema contracts are enforced automatically at ingestion
write boundaries (ADR-0007) — a batch that fails its contract does not land.
The CLI commands below are for ad-hoc investigation after the fact: exported
files, copies of lake data, or anything you want to inspect by hand. They
never write to the lake.

All three commands take a path to a single Parquet file or to a directory
(scanned recursively for `*.parquet`, concatenated into one frame).

## `equity validate check`

Runs schema validation, profiling, and built-in quality checks in one pass
and prints a Schema / Profile / Drift result table, followed by any errors
and warnings:

```bash
uv run equity validate check data/lake/01_bronze/market_data/us_equity --type price
uv run equity validate check exports/news.parquet --type news --strict
```

| Flag | Default | Description |
|---|---|---|
| `--type`, `-t` | `price` | Schema family: `price`, `macro`, or `news` |
| `--strict` | off | Fail on warnings |

The command exits `1` when the result is not successful (errors found, or
warnings with `--strict`).

Schemas come from `SCHEMA_REGISTRY` in `src/equity_lake/validation/schemas.py`:

| `--type` | Schema | Enforced checks |
|---|---|---|
| `price` | `PriceDataSchema` | `open`/`high`/`low`/`close` > 0, `volume` >= 0, `high` >= low/open/close, `low` <= open/close, `adj_close` > 0 when present, no duplicate `(ticker, date)` rows |
| `macro` | `MacroDataSchema` | `date`, `indicator`, `value`, `source` all not null |
| `news` | `NewsDataSchema` | `ticker`, `date`, `datetime`, `source`, `headline`, `url` not null; `sentiment_score` between -1 and 1; unique `url` |

Beyond the schema, the pipeline adds its own checks: empty frames, columns
that are entirely null, and required columns with more than 50% nulls all
become errors. An unknown `--type` is a warning, not an error — schema
validation is skipped for that run.

## `equity validate profile`

Profiles a dataset and prints per-column quality metrics (completeness, null
count, mean) plus a row/column summary:

```bash
uv run equity validate profile data/lake/01_bronze/raw_prices --name us_2026_08
```

| Flag | Default | Description |
|---|---|---|
| `--name`, `-n` | required | Profile name |

The name matters: `DataProfiler` persists each profile as JSON under
`data/profiles/<name>.json` (default storage path), so a named profile can be
reused later as a drift baseline.

## `equity validate drift`

Compares a current dataset against a baseline for distribution drift. The
comparison is per column over numeric statistics (mean, stddev, min, max) —
a column drifts when the relative change in any statistic exceeds the
threshold:

```bash
uv run equity validate drift current.parquet baseline.parquet --threshold 0.15
```

| Argument / flag | Default | Description |
|---|---|---|
| `current` | required | Path to current data |
| `baseline` | required | Path to baseline data |
| `--threshold`, `-t` | `0.1` | Relative-change threshold that flags drift |

On drift the command lists the drifting columns with their percent change and
exits `1`; otherwise it prints that no significant drift was detected. Note
the shorthand difference: `-t` is `--type` on `check` but `--threshold` here.

## Reading a `ValidationResult`

`ValidationPipeline.validate` (the engine behind `check`) returns a Pydantic
`ValidationResult`. The fields the CLI renders map to:

| Field | Meaning |
|---|---|
| `success` | No errors were raised (warnings do not fail unless `strict=True`) |
| `schema_valid` | The pointblank schema for the data type passed |
| `profile_valid` | Profiling itself completed without an internal failure |
| `drift_detected` | Drift found against a registered baseline (see below) |
| `errors` | Hard failures: schema violations, empty frame, all-null columns |
| `warnings` | Soft findings: unknown data type, drift columns, profiling issues |
| `metrics` | Nested `quality` (per-column completeness/uniqueness/distribution) and, when a baseline matched, a `drift` sub-dict |

`drift_detected` only turns on when the pipeline has a baseline registered via
`set_baseline(name, df)` in Python. The `check` command does not register one,
so its Drift row is informational — for two-dataset comparisons use
`equity validate drift`, and for programmatic monitoring register baselines
against named profiles.

## Enforced boundaries vs. interactive checks

These two layers are complementary and deliberately separate:

- **Ingestion write boundary** (ADR-0007): schema contracts in
  `validation/pipeline.py` run as part of the write path; a batch that fails
  does not land, so bad data stops at bronze instead of poisoning silver/gold.
- **`equity validate` CLI**: reads Parquet that already exists on disk, with
  no persistence side effects beyond profile JSON under `data/profiles/`.
  Use it to diagnose a rejected batch, inspect an export, or compare two
  snapshots before promoting data.

## Related

- [Pipeline](pipeline.md)
- [Ingestion](ingestion.md)
- [CLI Reference](20260406-cli-reference.md)
- [ADR-0007: pointblank validation at ingestion write boundaries](../decisions/0007-pointblank-at-ingestion-boundaries.md)
