# Phase 2C (RAG agent) execution handoff — providers locked, corpus + key gated

**Date:** 2026-08-11 · **Workstream:** 2C (of Phase 2) · **Status:** 2A + 2B
complete & verified on `main`; 2C scoped — **provider + dim decisions confirmed & OpenRouter keyed (2026-08-11)**; only the **silver corpus** remains before real execution (seeding plan in §4.1)
**Companion (read first):**
[`20260810-phase2a-review-handoff.md`](./20260810-phase2a-review-handoff.md) and
[`20260805-phase2-handoff.md`](./20260805-phase2-handoff.md) §5 (the 2B/2C
on-ramp) / §6 (FindingCards) / §7 (change matrix).

> **You are a cold-start agent.** Read `AGENTS.md`, then the companions above,
> then this doc. This doc records (a) the verified end-state of Phase 2A + 2B
> now on `main`, (b) the **provider decision for 2C** (DeepSeek chat +
> OpenRouter `qwen/qwen3-embedding-8b` embeddings — **not** OpenAI), (c) the
> concrete 2C build plan with `file:line` anchors, and (d) the blockers that
> gate *real* execution (an empty silver corpus + a missing OpenRouter key).
> No 2C code is written yet.

---

## 1. Entry state (verified 2026-08-11, working tree has one uncommitted doc edit)

Seven 2A/2B commits on `main` (newest first):

| Hash | Subject |
|---|---|
| `bb0f0e3` | `feat(api): Phase 2B 'equity api serve' + Docker api stage + docs` |
| `3597433` | `feat(api): Phase 2B read routers — signals/models/predictions/backtests` |
| `f4334e1` | `feat(api): Phase 2B read API foundation — FastAPI app factory + findings router` |
| `9ce78c4` | `refactor(ml): Phase 2A P2 polish — shared scoring, no double-write, lgbm tune bagging` |
| `62c4722` | `fix(ml): macro-join dtype + W&B report deps; publish Phase 2A findings` |
| `d60f52b` | `feat(ml): Phase 2A P1 close-out — ticker scope, LGBM SHAP test, ML guide` |
| `9edd5c0` | `docs(plans): Phase 2A review handoff` (prior) |

**Uncommitted (working tree):** `.env.example` has a new
`OPENROUTER_API_KEY=` section (added 2026-08-11, next to `DEEPSEEK_API_KEY=`).
Fold it into the first 2C commit (§3b) or commit standalone — it is a tracked
file.

**Verified green:** `uv run ruff check .` (239 files) · `uv run ruff format
--check .` · `uv run mypy src` (151 files) · `uv run pytest tests/unit -n auto`
(EXIT 0; only the pre-existing EOD-data skip).

**Phase 2B verified live** (real uvicorn over the real lake): `GET /health`
→`{"status":"ok"}` · `GET /findings`→6 cards · `GET /models`→2 models ·
`GET /backtests`→9 arena runs · `GET /findings/{id}`→200 · missing→404.

### 1a. What 2A delivered (CLOSED — do not reopen)
- **§2 P1 + P2 all landed** (`d60f52b`, `9ce78c4`): FindingCard `scope.tickers`
  stamped on all ML cards; LightGBM SHAP test; ML user guide; shared
  `ml/_metrics.py` (ablation decoupled from `comparison`); `card_path()` (no
  CLI double-write); LightGBM `subsample_freq=1` during `--tune`; `ml train`↔
  `forecast --mode train` cross-link.
- **§3 ops:** 6 FindingCards materialized in `data/findings/` (3 Phase-1 + 3
  ML); public W&B project **https://wandb.ai/howt51/equity_lake** (2 runs + 2
  Reports); README links updated.
- **Cross-ticker ML breadth study** (auxiliary, gitignored): per-ticker cards
  under `data/findings/ml/{AAPL,MSFT,GOOGL,AMZN,META,NVDA}/` + summary at
  `data/findings/ml/CROSS_TICKER_SUMMARY.md`. Headline: **`enrichment-ablation`
  is robustly negative (5/6 tickers)**; the labeling and backend axes are noisy
  on short (~126-row) windows.

### 1b. What 2B delivered (CLOSED — the read API)
`src/equity_lake/api/` — `main.create_app()` (lazy FastAPI), `deps.py` thin
getters, routers `{health, findings, signals, models, predictions, backtests}`.
`fastapi`+`uvicorn` are **core** deps; `equity api serve` runs uvicorn;
Dockerfile has an `api` stage. Boundary-enforced: `api` may use
`core`/`storage`/`findings` only (`tests/unit/test_import_boundaries.py`,
`LAYER_BOUNDARIES["api"] = {"cli","pipeline"}`). Reuse facts for 2C:
- `EquityDataDB.query(sql)` (`storage/duckdb.py:115`) and
  `duckdb_scan_for(path)` (`storage/lake_reader.py:10`) are available but **2B
  did not need them** — a raw market-data/query endpoint is an optional future
  addition.
- `read_delta("04_platinum/predictions")` works because
  `delta_table_path` joins `lake_dir/market` (`storage/delta.py:28`).

---

## 2. The 2C provider decision — LOCKED (do not reintroduce `OPENAI_API_KEY`)

Two things were conflated in the original `20260805` §5 line ("`agent` group:
openai + sqlite-vec"): **the `openai` PyPI package is a provider-agnostic HTTP
client SDK**, not OpenAI-the-company. You point it at any OpenAI-compatible
endpoint via `base_url`. The repo **already** does this for chat.

**The chat seam already exists** — `src/equity_lake/ingestion/llm_base.py:59-63`
builds `AsyncOpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
base_url="https://api.deepseek.com")` inside `BaseLLMBatchProcessor.__init__`.
2C reuses DeepSeek for chat and adds OpenRouter for embeddings.

| Role | Provider | Env key (raw, unprefixed) | base_url | Model |
|---|---|---|---|---|
| Chat (RAG generation) | **DeepSeek** | `DEEPSEEK_API_KEY` ✅ in `.env` | `https://api.deepseek.com` | `deepseek-v4-flash` (unchanged repo default) |
| Embeddings (RAG index) | **OpenRouter** | `OPENROUTER_API_KEY` ❌ not yet in `.env` | `https://openrouter.ai/api/v1` | `qwen/qwen3-embedding-8b` |

`OPENAI_API_KEY` is **not** set, **not** referenced anywhere, and must stay
that way. Verified facts (OpenRouter + Qwen docs, 2026-08-11):
- DeepSeek: OpenAI-compatible; `client.chat.completions.create(...)` with
  `response_format={"type":"json_object"}` (see `llm_base.py` `process_batch`).
- OpenRouter: OpenAI-compatible **embeddings** endpoint
  (`POST /embeddings`); `client.embeddings.create(model=..., input=...)`.
- `qwen/qwen3-embedding-8b`: native dim **4096**, **MRL-truncatable to 32–4096**
  via a `dimensions` param; 32k context. (Sources: openrouter.ai/docs/api_reference/embeddings,
  openrouter.ai/qwen/qwen3-embedding-8b, huggingface.co/Qwen/Qwen3-Embedding-8B.)

---

## 3. 2C build plan (file:line anchored)

### 3a. B8 client factories — `src/equity_lake/ingestion/llm_base.py`
1. Add `build_chat_client() -> AsyncOpenAI` (DeepSeek; raise if
   `DEEPSEEK_API_KEY` missing) — extract the construction currently inline at
   `llm_base.py:59-63`.
2. Refactor `BaseLLMBatchProcessor.__init__` to call `build_chat_client()`
   (**behavior-identical** — pure DRY; the XGBoost-path zero-regression
   discipline does not apply here, but keep the DeepSeek client byte-equivalent).
3. Add `build_embedding_client() -> AsyncOpenAI` (OpenRouter; raise if
   `OPENROUTER_API_KEY` missing).
4. Add module constants:
   `OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"`,
   `EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"`,
   `EMBEDDING_DIM = 1024` (**DECIDED 2026-08-11** — MRL-truncated, lean index).
   Caveat to verify at impl: if OpenRouter does **not** forward `dimensions`
   to the model, fall back to native 4096 (store full vectors, or truncate
   client-side). Confirm with one probe call before locking the sqlite-vec
   column width.

### 3b. Secrets — raw/unprefixed per AGENTS.md (NEVER in `Settings`)
5. User adds `OPENROUTER_API_KEY=...` to **`.env`** (gitignored). The
   `.env.example` placeholder already exists (added 2026-08-11). Keys are read
   via `os.getenv` at the factory seam — **no** `Settings`/`config/` change
   (matches the `DEEPSEEK_API_KEY`/`FRED_API_KEY`/`WANDB_API_KEY` convention).

### 3c. Deps
6. New `[dependency-groups] agent = ["sqlite-vec>=0.1.0"]` in `pyproject.toml`
   (`openai` is already core). **Lazy-import** `sqlite_vec` inside
   `agent/index.py` (`try/except ImportError`), import only when building the
   vector store. Add `"sqlite_vec.*"` to the mypy `ignore_missing_imports`
   module list (pyproject `[[tool.mypy.overrides]]`, ~line 215).
   Remember Phase-1 scar #1: `uv sync --group agent --group ml --group viz
   --group backtesting` (combine groups; bare `uv sync` drops the others).

### 3d. `agent/` package + boundary
7. `src/equity_lake/agent/{__init__,rag,index,tools,eval}.py`:
   - `index.py` — chunk silver articles, embed via `build_embedding_client()`,
     store in **sqlite-vec** (one vec column of width `EMBEDDING_DIM`,
     `ticker`/`source`/`chunk_id`/`text`/`url` metadata).
   - `rag.py` — embed query → sqlite-vec KNN → assemble context → answer via
     `build_chat_client()` (DeepSeek); **emit citations** (source id + snippet)
     and **refuse-with-citation when no evidence clears the similarity
     threshold**.
   - `tools.py` — thin query tools over `duckdb_scan_for` /
     `EquityDataDB.query` / `load_finding_cards` the agent may call.
   - `eval.py` — the **non-negotiable** RAG eval (§4).
8. `LAYER_BOUNDARIES["agent"] = {"cli", "pipeline", "dashboard"}` in
   `tests/unit/test_import_boundaries.py` (~line 120). `agent` may use
   `core`/`storage`/`ingestion` (for the B8 factories + `duckdb_scan_for`),
   nothing else. No hatch change (single `packages=["src/equity_lake"]` glob
   covers the new package).

### 3e. Corpus the index reads from
Silver article dirs (both currently **empty** — see §4): 
`SILVER_PROCESSED_ARTICLES_DIR = data/lake/02_silver/processed_articles`,
`SILVER_SEC_EXTRACTIONS_DIR = data/lake/02_silver/sec_extractions`
(`core/paths.py`). `agent/index.py` should chunk whatever parquet lands there;
design the chunker against the actual silver schema (inspect a populated frame
before finalizing chunk size / overlap).

### 3f. Change-matrix checklist (new top-level package)
- [ ] `agent/` added; `LAYER_BOUNDARIES["agent"]` set (§3d-8).
- [ ] `sqlite-vec` in a new `agent` group; lazy-imported; mypy override (§3c).
- [ ] B8 factories in `llm_base.py`; `BaseLLMBatchProcessor` refactored to use
      `build_chat_client()` (§3a).
- [x] `.env.example` `OPENROUTER_API_KEY=` + key in `.env` (both done 2026-08-11).
- [ ] RAG eval (§4) green before any UI/dashboard exposure.

---

## 4. Blockers gating *real* 2C execution (independent of the provider choice)

1. **Empty corpus — seeding plan (confirmed 2026-08-11).**
   `02_silver/processed_articles` and `sec_extractions` have **0 parquet**.
   Both are produced by **DeepSeek bronze→silver enrichment** of
   `bronze/raw_articles` (all keys present: `DEEPSEEK_API_KEY`, `SEC_USER_AGENT`,
   `FINNHUB_API_KEY`):

   | Corpus table | Producer | Trigger |
   |---|---|---|
   | `sec_extractions` | `sec_processor.process_sec_bronze_to_silver` | `equity sec --tickers <demo> --lookback <days> --process` (fetch EDGAR→bronze **and** enrich→silver in one command) |
   | `processed_articles` | `bronze_silver.process_bronze_to_silver` | fetch transcripts→bronze (`equity transcripts --tickers <demo>`), then enrich via `equity pipeline --markets us_earnings_transcripts` (gate at `pipeline.py:122`) |

   Small-scope first run (control DeepSeek cost), then verify + scale:
   ```bash
   dotenvx run -- uv run equity sec --tickers AAPL,MSFT,GOOGL --lookback 180 --process
   dotenvx run -- uv run equity transcripts --tickers AAPL,MSFT,GOOGL
   dotenvx run -- uv run equity pipeline --markets us_earnings_transcripts
   ```
   Verify: `ls data/lake/02_silver/{sec_extractions,processed_articles}/` + DuckDB
   row counts. Expand scope after confirming the silver schema — the `index.py`
   chunker is designed against the real columns (the one design item still open).
2. **`OPENROUTER_API_KEY` — RESOLVED (2026-08-11):** key added to `.env`.
   (Cost awareness stands: real OpenRouter embeddings + DeepSeek chat spend
   tokens — still scope-limit the first index/eval runs.)
3. **The RAG eval is non-negotiable** (parent §5): retrieval + generation must
   hit **target accuracy AND a citation rate threshold**, and **refuse with a
   citation when no evidence clears the threshold**. `agent/eval.py` must be
   green before any dashboard/UI exposure. Design the eval set before tuning.

---

## 5. Decisions (confirmed 2026-08-11)

1. **Embedding dimension — DECIDED: `EMBEDDING_DIM = 1024`** (MRL-truncated,
   lean index). Locks the sqlite-vec column width. (Still probe one real call to
   confirm OpenRouter honors `dimensions` — §3a-4 caveat.)
2. **Sequencing — DECIDED: proceed** (not wait). `OPENROUTER_API_KEY` is set,
   so real embedding calls are possible the moment the corpus exists.
3. **OPEN** — chunking strategy (chunk size / overlap / per-source): finalize
   against a *populated* silver frame (post-seed); do not guess.

---

## 6. Suggested sequencing for the next thread

1. **Decisions are confirmed** (dim=1024, proceed, OpenRouter keyed). First
   task: **seed the corpus** per §4.1 (small scope, verify the silver schema),
   then design the chunker against the real columns.
2. **(If scaffolding-first)** Implement §3a (B8 factories + refactor),
   §3c (sqlite-vec group + mypy), §3d (`agent/` skeleton + boundary),
   `agent/index.py` + `agent/eval.py` with **mocked** clients; full gate green;
   commit. No token spend.
3. **Seed the corpus** (§4-1): run news + SEC ingestion into the two silver
   dirs; verify parquet exists.
4. **Probe the embedding endpoint** (§3a-4 caveat): one real call to confirm
   OpenRouter honors `dimensions`; lock `EMBEDDING_DIM` + the vec column.
5. **Build the real index** (real OpenRouter embeddings) + **run the eval**
   (real DeepSeek) against a designed eval set; iterate to the accuracy +
   citation-rate targets (§4-3).
6. (Optional) expose RAG through the 2B API (a `/rag/ask` router) or the
   dashboard — **only after** the eval is green.

---

## 7. Phase-1 scars to keep avoiding (carry-forward from prior handoffs)

1. `uv sync --group X` **removes** other groups — always combine:
   `uv sync --group agent --group ml --group viz --group backtesting`.
2. Backtesting is a `[dependency-groups]` entry → `--group backtesting`, never
   `--extra backtesting`.
3. New top-level package → extend `LAYER_BOUNDARIES`; no hatch change (glob
   covers it); lazy-import heavy deps (`sqlite_vec`, `fastapi`).
4. SDK/API keys stay **raw/unprefixed** (`DEEPSEEK_API_KEY`,
   `OPENROUTER_API_KEY`, `FRED_API_KEY`, …), read via `os.getenv` at the
   client seam, **never** declared in `Settings`.
5. `load_finding_cards` is **non-recursive** (`<base>/*.json`); per-ticker
   study cards live under `data/findings/ml/<TICKER>/` and are NOT in the
   default findings surface (2B `/findings` serves the 6 flat showcase cards).
6. The `openai` package is the **SDK**, not the provider — point it at
   DeepSeek / OpenRouter via `base_url`. Never add `OPENAI_API_KEY`.

---

## 8. Optional / non-blocking (do not let these block 2C)

- **Pooled multi-ticker ML harness** (the "merge" path): per-ticker
  walk-forward split + pooled OOS → one card with `tickers=[all]` and N×folds
  for a *statistically* defensible single verdict. Currently breadth-only
  (per-ticker cards). Harness change to `run_comparison`/`run_ablation` + tests.
- **Raw market-data / SQL endpoint** in the 2B API via `EquityDataDB.query` /
  `duckdb_scan_for` (2B shipped without it).
- **W&B per-ticker run tagging**: `log_comparison` uses default run names, so
  the breadth study isn't ticker-distinguishable in W&B; a small change to tag
  runs by ticker would let the cross-ticker study live in the public project.

---

### Quick env-state reference (do not commit secrets)
**`.env` (gitignored) has:** `DEEPSEEK_API_KEY`, `FRED_API_KEY`,
`FINNHUB_API_KEY`, `WANDB_API_KEY`/`WANDB_ENTITY=howt51`/`WANDB_PROJECT=equity_lake`,
AWS/R2, Reddit/StockTwits/SEC UA. **Missing for 2C:** `OPENROUTER_API_KEY`.
**Correctly absent:** `OPENAI_API_KEY`.
