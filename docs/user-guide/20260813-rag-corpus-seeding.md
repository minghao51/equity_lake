# RAG corpus seeding (Phase 2C)

Runbook to (re)generate the silver corpus that the Phase 2C RAG agent retrieves
over: SEC filing extractions (`02_silver/sec_extractions`) and earnings-call
transcript articles (`02_silver/processed_articles`). A full transcript **bronze
base** is loaded once and expanded per ticker; silver enrichment is scoped per
run to bound DeepSeek cost.

| Table | Layer | Producer | Keys / cost |
|---|---|---|---|
| `01_bronze/raw_articles` | bronze base | HF seeder + `equity sec` | SEC needs `SEC_USER_AGENT`; HF is keyless; **no LLM** |
| `02_silver/sec_extractions` | silver | `equity sec --process` | `DEEPSEEK_API_KEY` (LLM) |
| `02_silver/processed_articles` | silver | HF seeder (scoped) | `DEEPSEEK_API_KEY` (LLM) |

All writes are idempotent (deterministic `article_id` → Delta upsert), so every
step is safe to re-run. No corpus data is committed — `data/*` and `*.parquet`
are gitignored; only the regen scripts live in the repo.

## Prerequisites

Credentials are read from the environment. Required keys: `SEC_USER_AGENT`
(SEC fair-access), `DEEPSEEK_API_KEY` (silver enrichment). The HuggingFace
transcript dataset (`kurry/sp500_earnings_transcripts`, ~33k calls, 2005–2025)
is public and keyless.

The repo's `.env` contains values with spaces/special characters that break
plain shell sourcing. Load it robustly once per shell (or prefix each command
with `dotenvx run --` if dotenvx is installed):

```bash
eval "$(uv run python -c "from dotenv import dotenv_values; import shlex; \
  [print(f'export {k}={shlex.quote(str(v))}') for k,v in dotenv_values('.env').items() if v]")"
```

## Step 1 — SEC extractions (`sec_extractions`)

Fetches 10-K/10-Q Risk-Factors sections from EDGAR → bronze, then enriches to
silver via DeepSeek in one command.

```bash
uv run equity sec --tickers AAPL,MSFT,GOOGL --lookback 180 --process
```

- Bronze lands at `01_bronze/raw_articles` (`source_type="sec_filing"`); silver
  at `02_silver/sec_extractions`.
- Re-runs are a no-op: already-extracted `article_id`s are skipped (no LLM call).
- **Known gap:** the section extractor's Item 1A regex does not match every
  issuer's HTML (e.g. MSFT currently yields 0 sections). Affected issuers simply
  contribute no rows; expand `--tickers` to widen coverage.

## Step 2 — Earnings transcripts (`processed_articles`)

Loads the **full** HF dataset as an expandable bronze base, then enriches a
**ticker-scoped** subset to silver. The seeder is a devtools script, not a
cataloged source — no schema/catalog/CLI changes.

```bash
# Full bronze base (keyless, ~1.8 GB download cached at data/.cache/) + scoped silver:
uv run python -m equity_lake.devtools.seed_transcripts --tickers AAPL,MSFT,GOOGL
```

Flags:

- `--tickers AAPL,MSFT,...` — tickers for the **silver** enrichment (default
  `AAPL,MSFT,GOOGL`). The bronze base is always the full dataset.
- `--skip-bronze` — skip re-merging the bronze base (use after the first run).
- `--skip-silver` — load only the bronze base (no DeepSeek spend).
- `--force-download` — re-fetch the HF parquet.
- `--dry-run` — preview the bronze row count and the scoped silver enrichment
  with no lake writes or LLM tokens; a cold cache still downloads the source
  parquet.

Exit code: 0 only when every requested step (bronze/silver) succeeds; a
failed merge or an empty silver scope exits 1.

Why silver is scoped from the in-memory HF frame (not by re-reading bronze): the
transcript ticker is not a bronze column (it lives in `source_metadata`, mirroring
SEC rows), so the production processor cannot pre-filter by ticker before the
LLM. The seeder therefore filters the HF frame directly, keeping the DeepSeek cost
bounded to the selected tickers (each body is truncated to 2000 chars, batched 15
per call).

## Expanding to more tickers

The bronze base holds all ~685 tickers, so expanding silver is a scoped re-run:

```bash
uv run python -m equity_lake.devtools.seed_transcripts \
  --skip-bronze --tickers AAPL,MSFT,GOOGL,NVDA,META,AMZN
```

`--skip-bronze` avoids re-merging the 33k-row base; the silver merge upserts on
`(article_id, ticker)`, so previously enriched transcripts are not duplicated.

> **Caveat — do not use the production pipeline for the HF base.** Once the full
> transcript base is in bronze, `equity pipeline --markets us_earnings_transcripts`
> would attempt to enrich *all* ~33k transcripts (there is no pre-LLM ticker
> scope). Enrich the HF base via the seeder script only, until a pre-LLM ticker
> scope is added to the unstructured processor.

## Verification

```bash
uv run python -c "
import duckdb
from equity_lake.storage.lake_reader import duckdb_scan_for
from equity_lake.core.paths import BRONZE_RAW_ARTICLES_DIR, SILVER_PROCESSED_ARTICLES_DIR, SILVER_SEC_EXTRACTIONS_DIR
con = duckdb.connect(':memory:'); con.execute('INSTALL delta; LOAD delta;')
for name, p in [('bronze raw_articles', BRONZE_RAW_ARTICLES_DIR),
                ('silver sec_extractions', SILVER_SEC_EXTRACTIONS_DIR),
                ('silver processed_articles', SILVER_PROCESSED_ARTICLES_DIR)]:
    print(name, '->', con.execute(f'SELECT count(*) FROM {duckdb_scan_for(p)}').fetchone()[0], 'rows')
"
```

## Related

- Design & decisions: [`docs/plans/20260811-phase2c-rag-agent-handoff.md`](../plans/20260811-phase2c-rag-agent-handoff.md)
- Ingestion operation: [Ingestion](ingestion.md)
- API keys: [`docs/20260406-api-keys.md`](../20260406-api-keys.md)
