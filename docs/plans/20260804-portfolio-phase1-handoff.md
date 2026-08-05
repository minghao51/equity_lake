# Phase 1 Handoff — Substrate + Strategy Lab

**Date:** 2026-08-04 · **Phase:** 1 of 3 · **Duration:** ~2 weeks
**Roadmap:** [`20260804-portfolio-roadmap.md`](./20260804-portfolio-roadmap.md) ·
**Map:** [`20260804-portfolio-implementation-map.md`](./20260804-portfolio-implementation-map.md)

## Goal

Make the lake real and produce the first evidence-backed comparison findings
(strategy, cost-regime, benchmark) — the quant-credibility artifact. Also fix
the README/roadmap drift so reviewers aren't confused on first impression.

## Entry assumptions (state at phase start)

- `data/` is essentially empty (~60K). All engines exist and are tested but
  have never run on a populated lake end-to-end.
- `VectorBacktestEngine`, the three strategies (`momentum`, `mean_reversion`,
  `trend_following`), and `signals/generators/meta_label.py` are available.
- `technical_roadmap.md` still references Click and a "v0.4.0" that contradicts
  `pyproject.toml` (0.1.0).

## Deliverables (file-level)

| # | Path | | What |
|---|---|---|---|
| 1 | `src/equity_lake/findings/{__init__,models,writer}.py` | ➕ | `FindingCard` schema + writer → `data/findings/<id>.json` |
| 2 | `src/equity_lake/devtools/seed_demo.py` | ➕ | Idempotent US-universe + FRED bootstrap |
| 3 | `cli/commands/admin.py`, `cli/bootstrap.py` | ✏️ | `equity demo seed` command |
| 4 | `src/equity_lake/backtesting/arena.py` | ➕ | Run all strategies + meta-labeled ensemble |
| 5 | `src/equity_lake/backtesting/report.py` | ➕ | Serialize `BacktestResult` → equity/drawdown/metrics artifacts |
| 6 | `cli/commands/{analysis,report}.py` + `cli/_app.py` + `__main__.py` | ✏️ | new `arena_app` + `report_app` sub-apps; `equity arena run`; `equity report backtest` (B1 — `backtest` stays flat; sub-apps declared in `_app.py`, wired in `__main__.py`) |
| 7 | `config/tickers.yaml` | ✏️ | `demo` universe profile (~50–100 US tickers) |
| 8 | `notebooks/11-strategy-lab.ipynb` | ➕ | Research-memo notebook |
| 9 | `README.md`, `docs/technical_roadmap.md` | ✏️ | Hero section; remove Click/v0.4.0 drift |
| 10 | `Makefile`, `.env.example` | ✏️ | `make demo` target; FRED key note |

## FindingCards produced (the contract output)

| id | axis | question |
|---|---|---|
| `strategy-comparison` | strategy | momentum vs mean-reversion vs trend vs meta-labeled ensemble — Sharpe/drawdown/turnover |
| `cost-regime` | cost | how Sharpe/returns degrade zero-cost → realistic-cost |
| `vs-spy` | benchmark | each strategy vs SPY buy-and-hold |

## Recon-driven corrections

See [`20260804-integration-recon.md`](./20260804-integration-recon.md) §B. Phase-1-specific:

- **B1** — reports go under a `report_app` sub-app (`equity report backtest`);
  `arena` under an `arena_app`; `demo seed` under a `demo_app` (or reuse
  `bootstrap_app`). `backtest` stays a flat top-level command.
- **`FINDINGS_DIR = DATA_DIR / "findings"`** added to `core/paths.py` (auxiliary,
  like `SIGNALS_DIR`); lazy-created by the writer; **not** in `ensure_dirs()`,
  **not** cataloged.
- **`FindingCard`** is a Pydantic model validated before JSON write (the JSON
  equivalent of pointblank at a Delta boundary).
- **`seed_demo`** reuses `run_daily_ingestion` directly with `skip_existing=True`
  (idempotent, resumable); it is **not** a pipeline stage.
- **`BacktestResult`** is read-only to `report.py` (frozen serialization
  contract); strategies keep `__init__(params=dict)` so `optimize()` works.
- **B2** — add `"findings": {"cli"}` to `LAYER_BOUNDARIES` if enforcement is wanted.

## Exit criteria + verification

```bash
make demo                                       # populates the lake, idempotent
uv run equity monitor                           # all green, data fresh
uv run python -c "import duckdb; print(duckdb.sql(\"select count(*) from 'data/lake/01_bronze/market_data/us_equity/**/*.parquet'\"))"
uv run equity arena run --universe demo         # emits BacktestResults + FindingCards
ls data/findings/                               # >=3 cards present
uv run pytest -q                                # fast suite green
uv run ruff check . && uv run mypy src          # clean
uv run pytest tests/unit/test_cli_unified.py -k demo   # new CLI command tested
```

- README hero has 1-line pitch, architecture diagram, and a "Live demo (coming)"
  placeholder. `technical_roadmap.md` contains no Click/v0.4.0 references.
- `notebooks/11-strategy-lab.ipynb` executes top-to-bottom and reads like a
  research memo (hypothesis → method → OOS → caveat).

## Risks / gotchas

- **yfinance rate limits** on a 5y/100-ticker backfill — already wrapped in
  `tenacity`; seed in batches and make `seed_demo` resumable.
- **Unimpressive backtest returns** — lead with methodology honesty (leakage-free
  purged CV, real costs). A clean negative is a stronger portfolio line than a
  cherry-picked Sharpe; record the verdict accordingly.
- **Non-US markets** — keep them "supported" but out of the demo path to avoid
  flaky sources muddying the showcase.

## Handoff to Phase 2

Phase 2 can rely on:
- A populated, queryable lake (US + macro), refreshed by `make demo`.
- A stable **`FindingCard` schema** (`findings/models.py`) — frozen for the rest
  of the project; Phase 2/3 only add new card `axis` values.
- A stable **`BacktestResult` serialization format** (`backtesting/report.py`)
  that the Phase 2/3 API serves unchanged.
- A fixed **`demo` ticker universe** so ML comparisons run on identical scope.
