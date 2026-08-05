# Phase 3 Handoff — Risk Analytics + Showcase

**Date:** 2026-08-04 · **Phase:** 3 of 3 · **Duration:** ~3–4 weeks
**Roadmap:** [`20260804-portfolio-roadmap.md`](./20260804-portfolio-roadmap.md) ·
**Map:** [`20260804-portfolio-implementation-map.md`](./20260804-portfolio-implementation-map.md)
**Depends on:** Phase 2 (frozen FastAPI contract, 6 FindingCards, W&B project, RAG agent)

## Goal

Round out the quant story (risk + portfolio construction) and turn everything
into a finished, always-on product: a public React app whose centerpiece is the
**Findings** page publishing every comparison with honest conclusions.

## Entry assumptions (from Phase 2 handoff)

- FastAPI read API live with `/signals /predictions /backtests /models /findings`.
- 6 `FindingCard`s in `data/findings/` spanning all 6 axes.
- Public W&B project + Reports; RAG agent + eval baseline.

## Deliverables (file-level)

### 3A — Risk + portfolio

| # | Path | | What |
|---|---|---|---|
| 1 | `src/equity_lake/ml/risk.py` | ➕ | parametric + historical VaR/CVaR, rolling beta, factor exposures |
| 2 | `src/equity_lake/portfolio/optimizer.py` | ➕ | mean-variance efficient frontier (cvxpy) — **top-level `portfolio/`** (B11) |
| 3 | `src/equity_lake/signals/portfolio.py` | ➕ | portfolio-rebalance signal generator |
| 4 | `pyproject.toml` | ✏️ | `risk` group: `cvxpy` (or `scipy.optimize`) |

### 3B — React showcase + deploy

| # | Path | | What |
|---|---|---|---|
| 5 | `web/` (new, outside Python tree) | ➕ | Next.js/React app: Strategy Lab, Model Explorer, **Findings**, Chat, Portfolio |
| 6 | `src/equity_lake/api/routers/{risk,portfolio,chat}.py` | ✏️ | new endpoints (additive; existing contract frozen) |
| 7 | `fly.toml`, `Dockerfile.api` | ➕ | always-on Fly.io deploy (FastAPI serves snapshots) |
| 8 | `.github/workflows/snapshot.yml` | ➕ | nightly pipeline → push snapshots; mirrors `pages.yml` schedule |
| 9 | `docs/case-studies/*.md` | ➕ | findings-driven write-ups (meta-labeling, enrichment, risk) |
| 10 | `README.md` | ✏️ | screenshots/GIF, top-findings-up-front, live-demo + W&B links |

## FindingCards produced (optional)

| id | axis | question |
|---|---|---|
| `factor-exposure` | risk | rolling beta/factor exposures of the ensemble vs benchmark |
| `efficient-frontier` | strategy | does mean-variance allocation beat equal-weight on OOS Sharpe? |

(Both are nice-to-have; the Findings page ships with the 6 cards from P1/P2 even
if these slip.)

## Recon-driven corrections

See [`20260804-integration-recon.md`](./20260804-integration-recon.md) §B. Phase-3-specific:

- **B11** — `portfolio/` is a **top-level** package (hatch auto-covers it);
  risk/portfolio outputs are **JSON reports** (+ FindingCard), **not** Platinum
  tables → **no `change-equity-schema` chain** (no `core/schemas.py` constants,
  no catalog entry, no pointblank validator). Add `"portfolio": {"cli"}` to
  `LAYER_BOUNDARIES` if enforcement is wanted.
- **B10** — `fastapi`/`uvicorn` are **core** deps (prod stage runs `--no-dev`).
  New `fly.toml` + `.github/workflows/deploy-fly.yml` separate from `pages.yml`
  (needs `FLY_API_TOKEN`; different permissions). `snapshot.yml` reuses
  `schedule.cron` literally (`sync_schedule` only validates `pages.yml`).
- **`risk`/`portfolio` commands** under `risk_app` / a portfolio-aware sub-app;
  follow the CLI guardrail (help + test + user-guide).

## Exit criteria + verification

```bash
# Risk + portfolio
uv run equity risk report --universe demo      # VaR/CVaR/factors artifacts
uv run pytest tests/unit -q                    # incl. risk/portfolio tests

# API additions (contract additive)
curl localhost:8000/risk/<ticker>
curl localhost:8000/portfolio/frontier

# Deploy
fly deploy                                    # or whatever the chosen host requires
open https://<your-app>.fly.dev/findings      # renders all FindingCards with evidence
# nightly snapshot.yml runs green on schedule; app healthcheck passes
```

- Public URL live; Findings page shows every comparison with claim → evidence →
  honest conclusion (negatives included).
- README links: live app, W&B project, case studies; top findings stated up front.
- A **3-minute demo video** walkthrough recorded and linked from the README.

## Risks / gotchas

- **React scope creep** — cap at the 5 named pages; resist feature creep. The
  Findings page is the centerpiece, everything else supports it.
- **Hosting cost** — Fly.io hobby tier (or free if within limits); the
  snapshot-read pattern keeps the app stateless and cheap.
- **Snapshot staleness** — `snapshot.yml` healthcheck + a "last refreshed" stamp
  on the UI; fail loudly if the nightly run breaks.
- **cvxpy solver** in Docker — ensure the wheel installs cleanly in the slim
  image (may need build deps; pin in `Dockerfile.api`).

## Handoff (project complete)

After Phase 3, ongoing maintenance is **only the nightly `snapshot.yml` run**.
The portfolio is "done" when:

- A reviewer can click a single public URL and explore strategy P&L, ML rigor
  (with W&B Reports), risk analytics, and chat with the RAG agent.
- The README leads with **top findings** (what worked / what didn't), not a
  feature list.
- Every comparison the roadmap promised is published as an evidence-backed card.

## Maintenance & follow-ups (post-portfolio, not in scope)

- Expand demo universe / add a non-US showcase once a reliable free source is found.
- Add a second model family (e.g. gradient-boosted alternatives) only if a
  finding warrants it.
- Promote the RAG agent eval from 20 → 100 questions once usage patterns emerge.
