# Handoff 05 — P2: Market vocabulary & directory registry (ADR first)

Priority: P2. Depends on: 01. **Boundary change → an accepted ADR in `docs/decisions/`
is required BEFORE implementation** (AGENTS.md decision order).
Suggested dispatch: `planner` drafts the ADR → owner accepts → one `worker` implements →
`reviewer`. This unblocks handoff 07 items 1–2.

## Problem (systemic, verified)

Two market vocabularies coexist with **no shared converter**:

- **Long keys** (`us_equity`, `cn_ashare`, `hk_sg_equity`, `jpx_equity`, `krx_equity`):
  `core/paths.py` constants, `core/calendar.py:21-27` `_MARKET_TO_EXCHANGE`,
  `core/dates.py`, AGENTS.md, `monitoring/health.py:53-59`.
- **Short keys** (`us`, `cn`, `hk_sg`): `core/settings.py:30`
  `default_markets=["us","cn","hk_sg"]`, `config/settings.yaml`, `pipeline.py`
  markets throughout, `ingestion/types.py` `REQUIRED_PRICE_MARKETS` /
  `OPTIONAL_ENRICHMENT_MARKETS` / `MARKET_DIR_MAP` keys.

Consequences already felt:

- `core/calendar.py` can't be driven by pipeline market IDs; `_subtract_trading_days`
  loops forever on an unknown key (that crash is fixed in 01/02, but the root cause is
  the vocabulary split).
- Market→directory mapping re-implemented in **5+ places**, three different key styles:
  - `ingestion/types.py:90` `MARKET_DIR_MAP` (declared canonical, long+short mix)
  - `cli/bootstrap.py:33` private `MARKET_DIRS`
  - `cli/commands/data.py:24-41` runtime reverse-map from medallion paths
  - `storage/duckdb.py:47-55` inline tuple table
  - `monitoring/health.py:50-56` reflective `getattr(paths, …)` list
  - `backtesting/data_loader.py` `MARKET_DIRS`
- US-calendar bias: `cli/_app.py:46` resolves the pipeline trading date with the **US**
  calendar by default for all markets (harmless today only because ingestion re-fetches).

## ADR contents (planner)

1. Canonical vocabulary: long keys everywhere in code/config; short keys either retired
   or defined as a strict alias map (`MarketAlias` in `ingestion/types.py`, derived, not
   hand-maintained).
2. Single registry: market → `{dir constant, short key, exchanges}` in one module
   (`ingestion/types.py` or `core/paths.py`); everything else reads it. Delete the five
   private copies.
3. Trading-date resolution becomes per-market (`equity pipeline --market cn_ashare`
   resolves with the CN calendar).
4. Migration: which config keys change (`settings.yaml default_markets`), what stays
   accepted as input (aliases at the CLI boundary only), test updates.
5. Explicitly non-goals: no new markets; no per-market config models beyond the registry.

## Implementation tasks (worker, after ADR accepted)

- [ ] Introduce the canonical registry + `long_to_short`/`short_to_long` helpers; make
      `MARKET_DIR_MAP` derived from `core/paths.py` (it already partially is).
- [ ] Replace the five private registries; delete `health.py`'s reflective getattr.
- [ ] Fix `cli/commands/data.py` to use the converter instead of path-suffix sniffing.
- [ ] Per-market trading-date resolution in `cli/_app.py` (fall back to US only when no
      market context exists, and log it).
- [ ] Update `config/settings.yaml` defaults + `tests/unit/test_source_storage_contracts.py`
      (it pins registry ↔ `VALID_MARKETS` ↔ `MARKET_DIR_MAP` ↔ catalog consistency).
- [ ] Docs: user guide market references; `ARCHITECTURE.md` if the registry module moves.

## Acceptance criteria

- Exactly one module defines market metadata; grep finds no other
  `{"us_equity": …}`-style dict literals (contract test enforces).
- `resolve_trading_date` accepts both vocabularies via the documented alias rule and
  never hangs (loop guard test from 01/02 still green).
- `equity pipeline --market cn_ashare` on a CN-only holiday resolves the CN trading date
  (integration test with a mocked calendar).

## Validation

```bash
uv run pytest tests/unit/test_source_storage_contracts.py tests/unit/test_import_boundaries.py -q
uv run pytest -n auto && uv run ruff check . && uv run mypy
```
