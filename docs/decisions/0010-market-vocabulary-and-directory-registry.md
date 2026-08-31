# ADR-0010: Market vocabulary and directory registry

**Status:** Proposed
**Recorded:** 2026-08-30

## Context

Two market vocabularies coexist with no shared converter:

- **Long keys** (`us_equity`, `cn_ashare`, `hk_sg_equity`, `jpx_equity`,
  `krx_equity`): the directory constants in `core/paths.py`, AGENTS.md, the
  default `market="us_equity"` in `core/dates.py`, `monitoring/health.py`,
  and `cli/bootstrap.py`.
- **Short keys** (`us`, `cn`, `hk_sg`, `jpx`, `krx`): `Settings.ingestion.
  default_markets` (code default and `config/settings.yaml`), the
  `Market` literal / `VALID_MARKETS` / `REQUIRED_PRICE_MARKETS` /
  `MARKET_DIR_MAP` keys in `ingestion/types.py`, and pipeline/backtesting
  internals.

Only the five **price markets** have this duality. The other ten entries of
`VALID_MARKETS` (`macro`, `us_news`, `rss_news`, `reddit_posts`,
`stocktwits_messages`, `us_earnings_transcripts`, `us_analyst_ratings`,
`sec_filings_fulltext`, `us_sec_financials`, `us_social_sentiment`) are
dataset identifiers with a single form; they are out of scope.

The split has concrete costs:

- Market→directory metadata is re-implemented in five private copies besides
  the declared-canonical `MARKET_DIR_MAP` (`ingestion/types.py`):
  `cli/bootstrap.py` `MARKET_DIRS` (long keys, 3 markets),
  `cli/commands/data.py` `_resolve_dataset_paths` (runtime reverse-map via
  path-suffix sniffing), `storage/duckdb.py` `MARKET_VIEWS` (inline tuple
  table), `monitoring/health.py` `_PRICE_MARKET_PATH_ATTRS` (reflective
  `getattr` list), and `backtesting/data_loader.py` `MARKET_DIRS` (short
  keys). `core/calendar.py` additionally hand-maintains
  `_MARKET_TO_EXCHANGE` and `_MARKET_TZ` in **both** key styles.
- `core/calendar.py` cannot be driven by one vocabulary, which was the root
  cause of the `_subtract_trading_days` infinite-loop bug (crash fixed by
  handoff 01/02; dual-form alias rows were added there as a stopgap).
- `cli/_app.py` `_resolve_date` resolves the pipeline trading date with the
  **US** calendar for all markets — harmless today only because ingestion
  re-fetches idempotently.

Per AGENTS.md this is a boundary change: an accepted ADR precedes
implementation.

## Decision

1. **Canonical vocabulary: long keys.** The five long keys are the canonical
   market identifiers everywhere in code and configuration: pipeline
   internals, storage, monitoring, backtesting, published per-market result
   payloads, and `config/settings.yaml`.

2. **Short keys become a derived alias, not a retirement.** Short keys are
   accepted **only at boundaries** — CLI flag values (`--markets us,cn`) and
   settings loading (`default_markets`) — and are normalized to long keys
   immediately at that boundary. The alias is *derived* from the registry
   (each entry carries its `alias`), never a hand-maintained second map.
   Retirement was considered and rejected: it would break every existing
   `settings.yaml`, cron script, and shell command using short flags
   simultaneously, while the derived alias costs one registry field, one
   `canonical_market()` normalizer, and one contract test. Short forms are
   deprecated input; removing them is a future decision, not this one.

3. **Single registry in `core/paths.py`.** One entry per price market:
   `market → {dir constant, alias (short key), exchanges (MIC codes),
   timezone}` — a frozen dataclass mapping `PRICE_MARKETS`, alongside the
   existing directory constants it references. `core/paths.py` stays pure
   static metadata with no filesystem I/O at import time.
   **Why `core/` and not `ingestion/types.py`:** `core/calendar.py` needs
   the exchange/timezone data and `core/settings.py` needs the valid-key
   set for `default_markets` normalization. Although ADR-0002's
   `LAYER_BOUNDARIES` does not literally list `ingestion` as forbidden for
   `core`, `ingestion` already imports `core`; placing the registry in
   `ingestion` would invert that dependency into a package cycle and
   contradict the layering ADR-0002 enforces (`core` as the stable base).

4. **Everything else reads the registry; the private copies are deleted.**
   - `ingestion/types.py` `MARKET_DIR_MAP`: price-market entries derived
     from the registry; enrichment dataset entries stay literal (they are
     dataset→path, not market vocabulary). `MARKET_DIR_REVERSE` stays
     derived from it.
   - `cli/bootstrap.py` `MARKET_DIRS`, `storage/duckdb.py` `MARKET_VIEWS`,
     `backtesting/data_loader.py` `MARKET_DIRS`: deleted, derived from the
     registry (view names stay long; keys become long).
   - `cli/commands/data.py` `_resolve_dataset_paths`: uses
     `canonical_market()` + `MARKET_DIR_MAP`; path-suffix sniffing removed.
   - `monitoring/health.py` `_PRICE_MARKET_PATH_ATTRS`: deleted together
     with the reflective `getattr`; iterate the registry (resolving `dir`
     at call time so test monkeypatching of registry entries still works).
   - `core/calendar.py` `_MARKET_TO_EXCHANGE` / `_MARKET_TZ`: derived from
     the registry in the single long-key form. The dual-form (short-key)
     rows added by the handoff-01/02 stopgap are deleted. Unknown market
     keys raise `ValueError` at the normalizer; the calendar loop-guard
     behavior from 01/02 is preserved by construction.

5. **Per-market trading-date resolution.** `cli/_app.py` `_resolve_date`
   gains a `market` parameter (default `us_equity`). A command with a
   single market context resolves with that market's calendar
   (`equity pipeline --market cn_ashare` uses the XSHG calendar).
   **Fallback rule (documented):** when no market context exists — or a
   run spans multiple markets — the run-level date resolves with the US
   calendar and a warning is logged stating the assumption; ingestion's
   idempotent re-fetch remains the safety net. Per-market date resolution
   inside a multi-market run is a future refinement, not this decision.

## Migration plan

1. Add `PRICE_MARKETS` registry + `canonical_market()` / derived
   `long_to_short` / `short_to_long` helpers to `core/paths.py` (stdlib
   `dataclasses` only; no new dependency edges — ADR-0002 boundaries
   unchanged).
2. `ingestion/types.py`: `Market`, `VALID_MARKETS`, `REQUIRED_PRICE_MARKETS`
   price entries switch to long keys; `MARKET_DIR_MAP` price entries
   derived from the registry; orchestrator/pipeline entry points normalize
   incoming `markets` via `canonical_market()` once, at the boundary.
3. `core/settings.py`: `default_markets` default becomes
   `["us_equity", "cn_ashare", "hk_sg_equity"]`; a `field_validator`
   normalizes short→long so existing YAML/env input keeps loading.
   `config/settings.yaml`: rewrite the three price entries to long keys;
   enrichment identifiers unchanged.
4. CLI: market flag values accepted in both forms and canonicalized at
   parse; `_resolve_date` wired per Decision 5; `data.py`
   `_resolve_dataset_paths` per Decision 4.
5. Delete the five private copies and the calendar's dual-form dicts
   (Decision 4), including the `market in ["jpx", "krx"]` literal in
   `backtesting/data_loader.py` (long-key/registry form).
6. Tests: update `tests/unit/test_source_storage_contracts.py` (pins
   registry ↔ `VALID_MARKETS` ↔ `MARKET_DIR_MAP` ↔ catalog, now long-keyed);
   add a contract test that no `{"<market>": <path>}`-style dict literal
   exists outside the registry module; keep the 01/02 loop-guard test green;
   add the mocked-calendar integration test (CN holiday → CN trading date);
   CLI tests cover both flag vocabularies.
7. Docs: user-guide market references; `ARCHITECTURE.md` (canonical copy in
   `docs/developer/architecture/`) if the registry location is described;
   this record. AGENTS.md's mapping sentence remains true (`MARKET_DIR_MAP`
   still lives in `ingestion/types.py`, now derived).

**Non-goals:** no new markets (the five-market set is fixed); no per-market
config models beyond the registry entry; no renaming of the ten enrichment
dataset identifiers; no directory or storage-layout changes; no removal of
short-key input (deprecation only).

## Consequences

- **What breaks (internal):** code and tests that pin short keys or compare
  against them (`backtesting/data_loader.py`, pipeline stage checks); the
  published `results["ingestion"]["markets"]` payload keys become long keys
  (dashboard/monitoring consumers updated in the same change). User-facing
  breakage is none: `settings.yaml` short values and short CLI flags keep
  working through the boundary normalizer, now marked deprecated.
- **What gets simpler:** exactly one module defines market metadata; the
  calendar, DuckDB views, health checks, bootstrap, backtests, and CLI all
  read it; `data.py` stops sniffing path suffixes; `health.py` stops
  reflecting over module attributes; adding a market later becomes one
  registry entry plus the change-matrix work (still a non-goal here).
  Non-US single-market runs resolve correct local trading dates, removing
  the US-calendar bias.
- **ADR-0002 relation:** the registry lives in `core`, so `core` gains no
  import of `ingestion` (or anything else new); `LAYER_BOUNDARIES` is
  unchanged, and the existing cycle risk (`core/calendar.py` ↔
  `ingestion/types.py`) is eliminated by derivation.
- **Calendar aliases:** the short-key rows added to `_MARKET_TO_EXCHANGE` /
  `_MARKET_TZ` by the handoff-01/02 fix were transitional; they are
  superseded by registry derivation and deleted. `resolve_trading_date`
  keeps accepting both vocabularies via the documented alias rule and
  never hangs (unknown keys raise at the normalizer).
- Reintroducing a second vocabulary or a hand-maintained market map would
  require superseding this record.

## Implementation risks (for the implementing worker)

- `health.py` monkeypatching contract: current reflective `getattr` honors
  tests that patch `paths.US_EQUITY_DIR`; the registry must resolve `dir`
  at call time (or tests patch registry entries).
- Published payload key change (`results["ingestion"]["markets"]`) may have
  undocumented consumers (dashboard, api); grep before switching.
- `VALID_MARKETS` long-key switch touches the router contract —
  `MARKET_REGISTRY` keys must move in lockstep or
  `test_source_storage_contracts.py` fails (that test is the intended
  tripwire, not a bug).
- Settings validator must not silently rewrite *unknown* keys — normalize
  only the five known aliases, raise otherwise, or config typos become
  silent no-ops.
