# ADR-0011: Corporate Actions as a First-Class Dataset

**Status:** Accepted (2026-08-31)
**Date:** 2026-08-31
**Deciders:** Repository owner
**Related:** ADR-0001 (medallion layout + generated catalog), ADR-0003 (Polars-first),
ADR-0007 (pointblank at ingestion boundaries), ADR-0010 (market vocabulary)

## Context

Price ingestion stores raw OHLCV keyed by `(ticker, trading_date)`. Two market
events change what a historical price *means* without any trading on our side:

- **Splits** (e.g. 2-for-1) halve the nominal price series overnight.
- **Dividends** create a total-return stream distinct from the price stream.

Today, backtests and features consume raw closes. Consequences:

1. A split produces a phantom −50% overnight return that flows into labels
   (`next_day_return`), indicators, and the ML target — silently poisoning
   samples around the ex-date.
2. Backtest equity curves understate returns for dividend payers; Sharpe
   comparisons vs SPY total return are not apples-to-apples.
3. The active roadmap's Month-1 exit criterion is a *defensible, cost-aware*
   OOS backtest; corporate-action correctness is table stakes for that claim.

The superseded `technical_roadmap.md` sketched corporate actions as an optional
method on a plugin loader (`get_corporate_actions`). We have no plugin layer
(deliberately — see the active roadmap's out-of-scope list), but the underlying
data need stands. yfinance exposes `Ticker.dividends` / `Ticker.splits` and
`Ticker.actions` free of charge; akshare exposes equivalent CN endpoints.

## Decision

1. **Store corporate actions explicitly, never bake adjustments into stored
   prices.** Add a new dataset `corporate_actions` (bronze: raw vendor rows;
   silver: typed, validated rows) with the schema:

   `ticker: str, ex_date: date, action: {"dividend" | "split"}, value: float,
   source: str, ingested_at: datetime`

   - One row per (ticker, ex_date, action). `value` = cash dividend per share
     or split ratio (e.g. `0.5` for 2-for-1).
   - Raw prices in `market_data` remain immutable vendor closes — the lake
     stays an honest record of what the vendor reported on each date.

2. **Adjust at read time, point-in-time correct.** A shared helper in
   `storage/lake_reader.py` (e.g. `with_price_adjustment(df, actions, method)`)
   returns back/forward-adjusted price frames by joining actions `ASOF` ex-date.
   Features, ML loaders, and the backtest data loader opt in explicitly; the
   default stays raw so existing consumers and catalogs are unchanged until
   each consumer is migrated deliberately.

3. **Ingestion rides the existing price route.** `YFinanceBaseFetcher` gains a
   corporate-actions fetch (per ticker, incremental by max stored ex-date);
   CN follows via akshare in a second step. The market vocabulary, router, and
   orchestrator flow from ADR-0010 apply unchanged.

4. **Pointblank schema at the silver boundary** per ADR-0007: non-negative
   dividend values, positive split ratios, unique `(ticker, ex_date, action)`,
   ex_date not in the future.

## Alternatives considered

- **Store adjusted closes alongside raw (vendor-provided `Adj Close`).**
  Rejected as primary: vendor adjustments are computed against *today's* actions
  and are silently rewritten historically — storing them breaks point-in-time
  reproducibility and our immutability principle. Kept as a cross-check column
  at most.
- **Apply adjustments at ingestion (rewrite history on each event).** Rejected:
  mutates stored facts, requires rewrite cascades across medallion layers, and
  makes "what did we know on date D" unanswerable.
- **Do nothing; document the caveat.** Rejected: phantom split returns in ML
  labels are a correctness bug, not a documentation issue.

## Consequences

- **Change matrix (all in scope on implementation):** new source route (router,
  type/map, schema/validation, config, tests, source docs, catalog), schema
  change (constants, validators, catalog, reader compatibility, migration
  note), storage change (writer, reader, health checks, idempotency tests),
  catalog regeneration, Hamilton tags for any feature-side adjustment nodes.
- Backtest/feature outputs change **only for consumers that opt in**, and only
  around ex-dates — expected and desired; must be called out in any report that
  mixes pre/post-adjustment runs.
- Splits/dividends history for delisted-or-changed tickers is best-effort from
  yfinance; gaps surface as validation warnings, not failures.
- Estimated scope: ingestion route + schema + reader helper + tests + catalog ≈
  2–3 focused sessions.

## Status

Proposed — implementation begins only after the owner accepts this ADR.
