# ADR-0020: Synthetic Demo Lake as a Backtestable Surface

**Status:** Accepted decision.

**Recorded:** 2026-10-29.

**Deciders:** Repository owner

**Related:** ADR-0011 (corporate actions), ADR-0015 (point-in-time adjustment
and predictor availability), ADR-0016 (canonical instrument identity), ADR-0006
(auxiliary data paths)

## Context

`equity demo seed` writes a synthetic US price table into an auxiliary lake so
the arena showcase, the Strategy Lab notebook, and the read API can be
demonstrated offline. In practice that path produced data no strict consumer
could read:

1. **No identity.** Seeded rows carried only `ticker`/`date`/OHLCV. Every
   consumer that enforces ADR-0016 (`normalize_identity_frame`) failed closed
   with `No trusted historical identity for legacy symbol '...'` — the arena
   loader, the signal backtest generator, and the ML feature loader alike.
2. **No action coverage.** Even with identity, the arena's point-in-time gate
   (ADR-0015) refused every decision date: `corporate-action coverage is empty
   and unverified`. Seeded data therefore yielded zero runs.
3. **No non-canonical lake access.** `BacktestDataLoader` resolved prices via
   `market_dir(market)` and read corporate-action coverage through
   `load_corporate_action_inputs` without a lake root, so an auxiliary lake was
   unreachable even when correctly seeded.

The result was a showcase path documented in the README that failed on first
run, and a demo→arena seam with no end-to-end test (unit tests all injected
`preloaded_data`, bypassing the loader entirely).

## Decision

1. **Seeded rows carry the canonical identity contract.** `demo seed` stamps
   `market`/`mic`/`symbol`/`identity_schema_version` resolved from
   `config/tickers.yaml` via the existing `TickerMetadata.instrument_key(…)`
   seam. Venue is never inferred from ticker spelling: a symbol absent from the
   config fails closed with an actionable error rather than being seeded under
   a guessed MIC.
2. **Seeded lakes carry a source-scoped coverage proof.** The seeder writes one
   `complete`, empty-interval `ActionCoverageProof` per instrument to
   `02_silver/corporate_action_coverage/<market>`, with `source =
   "synthetic_demo_seed"`. Synthetic prices are split-free by construction, so
   the generator *is* the source of that assertion; the proof is observable from
   the first seeded session and fresh through the last. This is not a fabricated
   provenance claim about a real vendor.
3. **Auxiliary lakes are first-class read roots for backtesting.** `lake_dir`
   threads through `BacktestDataLoader` (price reads and action-coverage reads),
   `run_arena`, and `equity arena run --lake`. Without an override the existing
   registry lookups (`market_dir`, runtime `*_DIR` patches) remain in effect.
4. **Failures stay loud and actionable.** An empty load reports the remedy
   (ingest the window, or `demo seed` plus `--lake`) instead of a bare
   "empty load".

## Consequences

- `make demo` + `equity arena run --lake data/sample` produces FindingCards
  fully offline (verified: 9 runs across 3 strategies × 3 cost regimes).
- The demo lake is now a supported *read* surface for backtesting. It remains
  synthetic and must never be presented as market data: seeded rows are labeled
  `identity_provenance = current_ticker_config` and coverage proofs are labeled
  `source = synthetic_demo_seed`, so both are auditable downstream.
- Point-in-time gates keep their strength: this ADR adds a legitimate synthetic
  source, it does not weaken the requirement that coverage be source-scoped and
  fresh. Real-vendor coverage continues to be established only by real ingests.
- Seeded sessions follow the exchange calendar: synthetic bars never claim a
  closed session, so the ADR-0014 next-session horizon and every calendar-aware
  consumer agree with the stored rows. `demo seed` filters through
  `core.calendar.is_trading_day` for its single-venue market; `bootstrap sample`
  seeds a mixed-venue market (`hk_sg_equity`) only on intersection sessions
  (`core.calendar.trading_days_between`), because its rows carry no MIC and a
  union-session date would place a bar on a closure for one of the venues.
- Feature/prediction preloading is *not* covered here. Labels require the
  immediately preceding session to already exist in Gold (ADR-0014). Finalizing
  on dates whose preceding session is absent is now a pending no-op rather than
  a `label_finalization_failed` error: an empty stored read is reported as
  unmatched keys and writes nothing, while a non-empty frame with an unknown
  `identity_schema_version` still fails closed. A dense backfill is still
  required before the ML/prediction surfaces can be preloaded, and that remains
  a separate decision.
- `equity bootstrap sample` intentionally keeps its loose sandbox layout
  (`<output-dir>/<market>`, no identity columns, no coverage proofs). It is a
  mock-data generator, not a read surface, and strict readers reject its output
  by design. The documented equivalent for a strict-consumable lake is
  `equity demo seed`; aligning the layout would be a storage change that needs
  its own ADR, so the divergence is recorded in the CLI reference instead.
- `equity validate check` reads partitioned directories as one dataset. The
  earlier per-file concatenation silently dropped `date=` Hive partition
  columns, which made valid lake tables fail schema validation.
