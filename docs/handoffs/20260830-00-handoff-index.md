# Handoff Index — src/ Audit Remediation (2026-08-30)

Provenance: full-tree audit of `src/equity_lake/` (157 files, ~22.2K lines) performed by 7
parallel review agents (per-module + cross-cutting sweep). Key findings re-verified by
hand before execution. Executed 2026-08-30 → 2026-08-31 via the worker → reviewer →
full-gate → commit loop, one commit per workstream.

## Status: 8 of 9 workstreams LANDED — 07 (DRY consolidation) is the only one open

| ID | Handoff | Status | Commit |
|----|---------|--------|--------|
| 01 | Reconcile working tree + pipeline orchestrator bugs | ✅ Landed | `f904276` |
| 02 | Ingestion data correctness | ✅ Landed | `9d8b0be` |
| 03 | Storage & validation correctness | ✅ Landed | `502060b` |
| 04 | Safety rails (devtools, CLI, secrets) | ✅ Landed | `b74b713` |
| 05 | Market vocabulary + registry (ADR-0010) | ✅ Landed | `63a5301` |
| 06 | Dead-code sweep | ✅ Landed | `f41c886` |
| 07 | DRY consolidation | **OPEN — scope updated in-file** | — |
| 08 | ML & backtest integrity | ✅ Landed | `a46f0fc` |
| 09 | Monitoring, API, catalog | ✅ Landed | `624b182` |

Related: `71e1c1d` (ADR-0010 proposal) · `be7567b`→`2aa238c` (Intel oneAPI runtime
preset — post-audit addition, `ml/_intel.py` + `intel` dependency group).

Completed briefs (each with an **Outcome** section recording the landed commit and
deviations from the plan) moved to **`docs/archive/`**: `20260830-01`, `-02`, `-03`,
`-04`, `-05`, `-06`, `-08`, `-09`.

## Open work: 07

`20260830-07-p3-dry-consolidation.md` — read its **Scope update (2026-08-31)**
section first: several items were absorbed by 05/06/08 and must be skipped; the live
remainder is grouped by the original three worker scopes. Rules from the original
brief still bind: consolidation only (no behavior change), smallest diff, change-matrix
companions where applicable, full gate before hand-off.

## Loose ends flagged during execution (owner's list, not scheduled)

- Help-scan test debt: ~20 CLI commands lack the per-command help-scan test
  (handoff 04 §7, deliberately deferred).
- `seaborn` in the `ml` dependency group is unused (flagged in 06).
- `requests.exceptions.RetryError` not converted to `TransientError`
  (handoff 02 residual, low value).
- `docs/user-guide/20260813-rag-corpus-seeding.md` references the deleted
  Phase-2C agent as a future consumer.
- `equity monitor` exits 0 when unhealthy (cron callers can't branch;
  behavior change — needs an owner call).

## Ground rules (digest of AGENTS.md — bind into every task brief)

- Always `uv run <cmd>`; never bare `python`. Present a plan before code changes; change
  as little as possible; no new abstractions.
- tenacity (exponential, max 3) via `core/retry.py` for all fetchers — never hand-rolled retry.
- Polars primary; pandas only at yfinance/akshare/efinance boundaries.
- structlog everywhere; no `print()` in library code; no stdlib logging outside `core/logging.py`.
- Dry-run = no persistence, no LLM, no feature output, no ML inference.
- pointblank enforced by default at ingestion write boundaries (since handoff 03);
  auxiliary artifacts under `data/<name>/` with a Pydantic model at the write boundary;
  cataloged tables under `data/lake/` only.
- Markets: canonical long keys per accepted ADR-0010; short keys accepted as input
  aliases at CLI/config boundaries only; market metadata lives in `core/paths.PRICE_MARKETS`.
- Change matrix: schema/storage/CLI/pipeline/boundary changes carry the listed companions;
  boundary changes need an accepted ADR in `docs/decisions/` first.
- Markdown files: `YYYYMMDD-filename.md`.

## Validation (run before closing any workstream)

```bash
uv sync
uv run pytest
uv run pytest -n auto
uv run ruff check .
uv run ruff format --check .
uv run mypy            # expect 0 errors
# when pipeline/feature structure changed:
uv run equity catalog-generate
```
