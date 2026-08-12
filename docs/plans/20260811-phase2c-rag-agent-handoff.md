# Phase 2C (RAG agent) — current-task handoff for pickup

**Date:** 2026-08-11 · **Workstream:** 2C (of Phase 2) · **Status:** 2A + 2B
complete, committed, gate green; 2C scoped, **all provider/dim decisions
LOCKED, all keys present**; only the **silver corpus is unseeded** before real
2C execution.
**Read first:** `AGENTS.md`, then
[`20260810-phase2a-review-handoff.md`](./20260810-phase2a-review-handoff.md),
then this doc. (`20260805-phase2-handoff.md` §5 is the original 2B/2C on-ramp.)

> **Cold-start note.** Phases 2A (ML rigor) and 2B (read API) are DONE and
> verified. This is the pickup doc for **2C — the RAG agent over the lake**.
> **No 2C code is written yet.** Decisions are locked in §2; the only external
> prerequisite left is seeding the silver corpus (§4). **Start there.**

---

## 1. Current state (verified 2026-08-11)

**Working tree clean; HEAD `da7ccbd`.** Recent `main` (newest first):

| Hash | What |
|---|---|
| `da7ccbd` | docs: this handoff (decisions confirmed, corpus plan) |
| `23fe5d5` | docs: Phase 2C handoff (initial) + `.env.example` `OPENROUTER_API_KEY` placeholder |
| `bb0f0e3` | feat(api): 2B `equity api serve` + Dockerfile `api` stage + docs |
| `3597433` | feat(api): 2B read routers — signals/models/predictions/backtests |
| `f4334e1` | feat(api): 2B read API foundation — FastAPI app factory + findings router |
| `9ce78c4` | refactor(ml): 2A P2 polish — shared scoring, no double-write, lgbm tune |
| `62c4722` | fix(ml): macro-join dtype + W&B report deps; publish 2A findings |
| `d60f52b` | feat(ml): 2A P1 close-out — ticker scope, LGBM SHAP test, ML guide |

**Gate green:** `uv run ruff check .` (239 files) · `uv run ruff format --check .`
· `uv run mypy src` (151 files) · `uv run pytest tests/unit -n auto` (EXIT 0;
only the pre-existing EOD-data skip). **2B verified live** (uvicorn over the
real lake): `/health`→ok · `/findings`→6 cards · `/models`→2 models ·
`/backtests`→9 runs · `/findings/{id}`→200 · missing→404.

### Done — do not reopen
- **2A (ML rigor):** P1 + P2 shipped; 6 FindingCards in `data/findings/`; public
  W&B **https://wandb.ai/howt51/equity_lake** (2 runs + 2 Reports); cross-ticker
  breadth study under `data/findings/ml/<TICKER>/` + `…/CROSS_TICKER_SUMMARY.md`
  (headline: `enrichment-ablation` robustly negative **5/6 tickers**).
- **2B (read API):** `src/equity_lake/api/` — `main.create_app()` (lazy
  FastAPI), routers `{health, findings, signals, models, predictions,
  backtests}`; `fastapi`/`uvicorn` core deps; `equity api serve`; Dockerfile
  `api` stage; boundary `LAYER_BOUNDARIES["api"]={"cli","pipeline"}`.

### Left to do — 2C (RAG agent)
`src/equity_lake/agent/{rag,index,tools,eval}.py` — embed silver articles into a
sqlite-vec index, retrieve, answer over DeepSeek **with citations**, and pass a
non-negotiable accuracy + citation-rate eval. **Not started.**

---

## 2. Locked decisions (do not relitigate)

| Decision | Value | Why |
|---|---|---|
| Chat provider | **DeepSeek** — `DEEPSEEK_API_KEY`, `base_url=https://api.deepseek.com`, model `deepseek-v4-flash` | Already wired at `ingestion/llm_base.py:59-63`; reuse. |
| Embedding provider | **OpenRouter** `qwen/qwen3-embedding-8b` — `OPENROUTER_API_KEY`, `base_url=https://openrouter.ai/api/v1` | User choice; OpenAI-compatible embeddings; native dim 4096, MRL-truncatable 32–4096; 32k context. |
| Embedding dim | **`EMBEDDING_DIM = 1024`** | MRL-truncated, lean index; locks the sqlite-vec column width. |
| Sequencing | **Proceed** (not wait) | `OPENROUTER_API_KEY` is set. |

**Critical:** the `openai` PyPI package is the **SDK**, not OpenAI-the-company —
point it at DeepSeek/OpenRouter via `base_url`. **Never add `OPENAI_API_KEY`**
(it is correctly absent everywhere). DeepSeek chat already uses
`response_format={"type":"json_object"}` (`llm_base.py` `process_batch`).
Verify-at-impl caveat: confirm with one probe call that OpenRouter forwards the
`dimensions` param; if not, fall back to native 4096 and update `EMBEDDING_DIM`.

---

## 3. The 2C task list (do in order)

1. **Seed the corpus** (§4) — silver `processed_articles` + `sec_extractions` are
   empty. *(Spends DeepSeek tokens + EDGAR/Finnhub fetches.)*
2. **Probe the embedding endpoint** — one real
   `client.embeddings.create(model="qwen/qwen3-embedding-8b", input="…",
   dimensions=1024)`; confirm OpenRouter honors `dimensions`; lock the vec
   column width (§2 caveat).
3. **B8 client factories** (`ingestion/llm_base.py`, §5a): `build_chat_client()`
   (DeepSeek) + `build_embedding_client()` (OpenRouter); refactor
   `BaseLLMBatchProcessor` to use `build_chat_client()` (byte-equivalent).
4. **Deps + boundary** (§5b): `sqlite-vec` `agent` group (lazy-import + mypy
   override); `agent/` package + `LAYER_BOUNDARIES["agent"]`.
5. **`agent/index.py`** — chunk the seeded silver frames, embed via
   `build_embedding_client()`, store in sqlite-vec. **Design the chunker against
   the real silver schema** (the one open design item).
6. **`agent/rag.py`** — embed query → KNN → context → DeepSeek answer **with
   citations**; **refuse-with-citation** when no chunk clears the similarity
   threshold.
7. **`agent/eval.py`** — the **non-negotiable** eval (§6): accuracy ≥ target AND
   citation-rate ≥ threshold. Must be green before any UI/API exposure.
8. *(Optional)* expose via a 2B `/rag/ask` router or the dashboard — **only
   after eval green**.

> Cost guardrail: steps 1, 5, 6, 7 spend real tokens. Scope-limit first runs
> (3 tickers / 180-day lookback).

---

## 4. Corpus-seeding plan (task 1)

Both corpus tables are produced by **DeepSeek bronze→silver enrichment** of
`bronze/raw_articles`. All keys present (`DEEPSEEK_API_KEY`, `SEC_USER_AGENT`,
`FINNHUB_API_KEY`).

| Corpus table | Producer | Command |
|---|---|---|
| `sec_extractions` | `sec_processor.process_sec_bronze_to_silver` | `equity sec --tickers AAPL,MSFT,GOOGL --lookback 180 --process` (fetch EDGAR→bronze **and** enrich→silver in one command) |
| `processed_articles` | `bronze_silver.process_bronze_to_silver` | `equity transcripts --tickers AAPL,MSFT,GOOGL` (→bronze), then `equity pipeline --markets us_earnings_transcripts` (gate at `pipeline.py:122`) |

```bash
# dotenvx is NOT installed in this repo — load .env directly:
set -a; . ./.env; set +a
uv run equity sec --tickers AAPL,MSFT,GOOGL --lookback 180 --process
uv run equity transcripts --tickers AAPL,MSFT,GOOGL
uv run equity pipeline --markets us_earnings_transcripts
```
**Verify:** `ls data/lake/02_silver/{sec_extractions,processed_articles}/`
(parquet present) + DuckDB row counts. Then inspect the silver schema to design
the chunker (task 5). Expand scope (more tickers / longer lookback) once the
shape is confirmed.

*(Note: `equity news` writes `us_news` sentiment — a separate table, useful as
auxiliary context, not the corpus.)*

---

## 5. Build details (file:line)

### 5a. B8 factories — `src/equity_lake/ingestion/llm_base.py`
- Extract `AsyncOpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
  base_url="https://api.deepseek.com")` (currently inline at `:59-63`) into
  `build_chat_client() -> AsyncOpenAI`; refactor `BaseLLMBatchProcessor.__init__`
  to call it (byte-equivalent — pure DRY).
- Add `build_embedding_client() -> AsyncOpenAI` (`OPENROUTER_API_KEY` +
  `base_url=https://openrouter.ai/api/v1`); raise if key missing.
- Constants: `OPENROUTER_BASE_URL`, `EMBEDDING_MODEL="qwen/qwen3-embedding-8b"`,
  `EMBEDDING_DIM=1024`.
- Keys stay **raw/unprefixed**, read via `os.getenv` at the seam — **no**
  `Settings`/`config/` change (matches `DEEPSEEK_API_KEY`/`FRED_API_KEY`/`WANDB_API_KEY`).

### 5b. Deps + boundary
- `pyproject.toml`: new `[dependency-groups] agent = ["sqlite-vec>=0.1.0"]`
  (`openai` is already core). **Lazy-import** `sqlite_vec` in `agent/index.py`
  (`try/except ImportError`). Add `"sqlite_vec.*"` to the mypy
  `ignore_missing_imports` module list (`[[tool.mypy.overrides]]`, ~line 215).
- `tests/unit/test_import_boundaries.py` (~:120):
  `LAYER_BOUNDARIES["agent"] = {"cli", "pipeline", "dashboard"}`. `agent` may
  use `core`/`storage`/`ingestion` only. No hatch change (single
  `packages=["src/equity_lake"]` glob covers the new package).

### 5c. Package
- `agent/index.py` — sqlite-vec store (vec column of width `EMBEDDING_DIM`;
  metadata `ticker`/`source`/`chunk_id`/`text`/`url`).
- `agent/rag.py` — retrieval + DeepSeek generation + citations +
  refuse-with-citation.
- `agent/tools.py` — thin query tools over `duckdb_scan_for`
  (`storage/lake_reader.py:10`) / `EquityDataDB.query` (`storage/duckdb.py:115`)
  / `load_finding_cards`.
- `agent/eval.py` — the eval (§6).

---

## 6. The non-negotiable RAG eval

Retrieval + generation must hit **accuracy ≥ target AND citation-rate ≥
threshold**, and **refuse with a citation when no evidence clears the similarity
threshold**. **Design the eval set before tuning.** `agent/eval.py` green is a
hard gate before any dashboard/UI exposure (parent `20260805` §5).

---

## 7. Scars to keep avoiding

1. `uv sync --group X` **drops** other groups — combine:
   `uv sync --group agent --group ml --group viz --group backtesting`.
2. Backtesting is a `[dependency-groups]` entry → `--group backtesting`, never
   `--extra backtesting`.
3. New top-level package → extend `LAYER_BOUNDARIES`; no hatch change;
   lazy-import heavy deps (`sqlite_vec`, `fastapi`).
4. SDK/API keys stay **raw/unprefixed** via `os.getenv` at the seam; **never** in
   `Settings`.
5. `load_finding_cards` is **non-recursive** (`<base>/*.json`); per-ticker study
   cards (`data/findings/ml/<TICKER>/`) are NOT in the default `/findings`
   surface.
6. `openai` = the SDK, not the provider — point it via `base_url`; never add
   `OPENAI_API_KEY`.

---

## 8. Optional / non-blocking (do not let these block 2C)

- **Pooled multi-ticker ML** (a statistically defensible *single* verdict):
  per-ticker walk-forward split + pooled OOS → one card `tickers=[all]` with
  N×folds. Harness change to `run_comparison`/`run_ablation` + tests.
- **Raw SQL / market-data endpoint** in the 2B API via `EquityDataDB.query` /
  `duckdb_scan_for` (2B shipped without it).
- **W&B per-ticker run tagging**: `log_comparison` uses default run names, so the
  breadth study isn't ticker-distinguishable in the public project — a small
  change to tag runs by ticker would let it live in W&B.

---

## 9. Env state (current)

**`.env` (gitignored) has:** `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY` ✅,
`FRED_API_KEY`, `FINNHUB_API_KEY`,
`WANDB_API_KEY`/`WANDB_ENTITY=howt51`/`WANDB_PROJECT=equity_lake`, AWS/R2,
Reddit/StockTwits/SEC UA. **Correctly absent:** `OPENAI_API_KEY`. `.env.example`
has the `OPENROUTER_API_KEY=` placeholder (committed).
