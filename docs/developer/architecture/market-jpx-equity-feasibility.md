# Market Feasibility Pack — `jpx_equity` (RS-M03)

This page is the offline feasibility record for the `jpx_equity` price market, the
third of the four non-US markets (`cn_ashare`, `hk_sg_equity`, `jpx_equity`,
`krx_equity`) required by the residual plan (§2.9, §5). It follows the format of
`market-cn-ashare-feasibility.md` (RS-M01) and records what the repository proves for
this market and what it does not.

**No network call, no data acquisition or spend, no source or licence acquisition,
no FX conversion and no multi-market portfolio support occurred in producing this
pack.** Every row below is either a `file:line` reference into this repository or an
explicit gap with the input packet that would close it. No lot size, price limit,
settlement cycle or rights term is stated here: an invented market rule would be
worse than a declared gap, so each is listed as UNKNOWN with the exact input needed.

**Real-root use is blocked.** ADR-0030 decision 5 limits a real-source root to
`us_equity` (`core/paths.py:170` `REAL_ROOT_PRICE_MARKETS = ("us_equity",)`). A **NEW
ADR amending that decision** must be accepted before any `jpx_equity` row may land
under `data/lake_real/`, and no such extension is made here.

## The `JPX` / `XTKS` vocabulary split, resolved

The registry declares this market's exchange as `"JPX"` (`core/paths.py:158`) while
the venue's instrument-identity MIC is `XTKS` (`core/instruments.py:63`
`MARKET_MICS["jpx_equity"] == frozenset({"XTKS"})`, reached from the source/config
label `TSE`/`JPX`/`TOKYO STOCK EXCHANGE` at `core/instruments.py:44-46`). This is a
two-vocabulary seam, not a second venue and not a defect:

- `core/calendar.py:33-34` states the rule: "MIC is instrument identity; calendar
  identifiers are a separate dependency-specific vocabulary."
- The registry's `("JPX",)` is a **calendar identifier**: `_MARKET_TO_EXCHANGE`
  (`core/calendar.py:27`) feeds it straight to `exchange_calendars`, which resolves
  both `"JPX"` and `"XTKS"` to the same calendar whose canonical name is `XTKS`.
  `is_trading_day`, `trading_days_between` and `classify_market_sessions` therefore
  never see a MIC at all.
- The MIC path is `_MIC_TO_CALENDAR["XTKS"] = "JPX"` (`core/calendar.py:46`), the
  single bridge `is_instrument_trading_day`, `instrument_sessions_between` and
  `instrument_session_close` use (`core/calendar.py:97-134`).

Consequence for consumers: a date question asked *per instrument* goes through
`XTKS`, a date question asked *per market* goes through `JPX`, and both land on the
same single exchange calendar. There is exactly one venue here, so the intersection
and union semantics that matter for `hk_sg_equity` (`core/calendar.py:62-94`) collapse
to the same set — asserted in `tests/unit/test_market_jpx_equity.py` so a future
second exchange cannot be added to this market silently.

## Verified present seams

| Seam | Evidence | Verified by |
| --- | --- | --- |
| Bronze directory constant | `core/paths.py:72` `JPX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "jpx_equity"` | `market_dir("jpx_equity")` / `market_dir("jpx")` |
| Registry row | `core/paths.py:158` `PriceMarket("jpx_equity", "jpx", "JPX_EQUITY_DIR", ("JPX",), "Asia/Tokyo")` | `PRICE_MARKETS["jpx_equity"]`, `canonical_market("jpx")` |
| Lake-relative dataset path | `core/paths.py:224` `PRICE_DATASET_PATHS` → `01_bronze/market_data/jpx_equity` | equality with `MARKET_DIR_MAP` |
| Directory map | `ingestion/types.py:93` `MARKET_DIR_MAP["jpx_equity"]` | round-trip write/read below |
| Valid-market vocabulary | `ingestion/types.py:41` `VALID_MARKETS` | membership assertion |
| Config section key | `ingestion/types.py:63` `PRICE_MARKET_CONFIG_KEYS["jpx_equity"] == ("jpx",)` | assertion |
| Currency | `config/tickers.yaml:888-889` (`jpx:` → `currency: JPY`), read through `core/config_models.py:75` `MarketConfig` | `TickerConfig().get_market_info("jpx").currency == "JPY"` |
| Symbol format | `config/tickers.yaml:1132` `jpx: "^\\d{4}\\.T$"` | fixture tickers match the declared format |
| Router entry | `ingestion/router.py:390` `"jpx_equity": _make_jpx_fetcher` (yfinance adapter `sources/jpx.py:28` `JPXEquityFetcher`) | `MARKET_REGISTRY` membership (no fetch executed) |
| Venue calendar | `core/calendar.py:27` `_MARKET_TO_EXCHANGE` (derived from the registry) → `("JPX",)`; `core/calendar.py:46` `_MIC_TO_CALENDAR` (`XTKS` → `JPX`) | `trading_days_between`, `is_trading_day`, per-MIC session checks |
| Timezone | registry `Asia/Tokyo`; `core/calendar.py:159` `market_timezone` | assertion on `str(market_timezone(...))` |
| Session close clock | `core/calendar.py:116` `instrument_session_close` (UTC, exact venue session) | aware-close and non-session assertions |
| Column contract | `core/schemas.py:95` `STANDARD_COLUMNS` (`volume` is a plain integer); `ingestion/writers.py:253` `validate_schema` generic OHLCV branch requires `ticker, date, open, high, low, close, volume` | fixture passes; each dropped required field fails closed |
| Instrument identity | `core/instruments.py:44-46` (`TSE`/`JPX`/`TOKYO STOCK EXCHANGE` → `XTKS`), `:63` `MARKET_MICS["jpx_equity"] == {"XTKS"}`; `:264` `prepare_source_identity_frame` | fixture states the venue label only; round-trip derives the MIC |
| Catalog row | `catalog/datasets.py:229-236` `jpx_equity_ohlcv` (bronze, path from `JPX_EQUITY_DIR`, `STANDARD_COLUMNS`) | unchanged by this stage (no catalog-affecting change) |
| Real-root scope | `core/paths.py:170` `REAL_ROOT_PRICE_MARKETS == ("us_equity",)` | assertion — this market is not permitted there |

## Verified gaps (declared UNKNOWN, with the input packet that closes each)

The machine-readable form of these rows is `core/market_contracts.py`
(`MARKET_CONTRACTS["jpx_equity"]`); `require_capability` fails closed on every one of
them instead of letting a consumer treat an absent rule as satisfied.

| Gap | Evidence | Input packet required to close it |
| --- | --- | --- |
| Corporate actions (dividends/splits) | `ingestion/corporate_actions.py:207` refuses every market outside `CORPORATE_ACTION_MARKETS` (currently `us_equity`); no `jpx_equity` action table or coverage proof exists | Japanese corporate-action feed: source name, licence, per-row ex-date semantics, source-issued publication/vintage clock (never an ingest-time substitute), coverage start/end evidence |
| Volume / lot contract | `core/schemas.py:95` (`volume` plain integer) and `validation/schemas.py:82` (`volume >= 0`) are the only volume rules; no trading unit, board lot or unit-change rule exists under `src/` | Trading-rule extract with trading unit per board and any change inside the sample window, effective dates, document id, licence, vintage |
| Price-limit band | `validation/schemas.py:61` `PriceDataSchema` asserts only positive OHLC, `high >= low/open/close` and non-negative volume; no daily limit, band or halt rule exists | Rule extract for daily limits / dynamic bands / halts with effective dates, document id, licence, vintage, and how a limit-locked session is represented |
| Settlement cycle | `core/calendar.py:116` yields the scheduled close only; no settlement-date or T+N rule exists under `src/` | CSD settlement-cycle evidence (cycle length, effective dates, document id, licence) if a consumer ever needs a settlement date |
| Rights / allotment terms | `core/schemas.py:166` `CORPORATE_ACTION_TYPES = ("dividend", "split")`, enforced at `validation/schemas.py:261`; `core/schemas.py:154` has no rights or entitlement column | Rights/allotment event feed with terms (ratio, subscription price, currency, entitlement cut-off), source-issued publication clock, coverage evidence, licence |
| Calendar-identifier vocabulary | The registry exchange (`core/paths.py:158`) is a calendar identifier (`JPX`), not the venue MIC (`XTKS`, `core/instruments.py:63`); the bridge is `core/calendar.py:46` | A decision, only if a second JPX-operated venue (or a per-venue calendar split) ever must be represented; today one calendar covers the market and no split is implied |
| Real-root use | ADR-0030 decision 5 (`core/paths.py:170`); this stage creates no ADR | A **NEW accepted ADR amending decision 5** plus the scoped source/licence packet above |

## Golden fixture pack

- File: `tests/fixtures/markets/jpx_equity/ohlcv.csv`
  (SHA-256 `e6691d0074a60ab4d0ceb2c4757280e9d5863d3873f23f41746d50ba4c76dfbc`).
  `tests/fixtures/` is tracked — the repository `.gitignore` ignores `*.parquet` and
  `data/*`, not this path — so the pack is text and reviewable.
- Contents: two TSE-listed instruments, six rows, sessions 2024-06-03 … 2024-06-05
  taken from `core/calendar.trading_days_between("jpx_equity", …)`; values from the
  existing deterministic seeder `devtools/seeding.synthetic_ohlcv` (seed 4243), so
  nothing is downloaded and nothing is a vendor row. Provenance table:
  `tests/fixtures/markets/README.md`.
- Tests: `tests/unit/test_market_jpx_equity.py` pins the digest, then round-trips
  `read fixture → validate_schema → upsert_dataset (temporary lake root) → read_delta`
  and asserts identity (`market`, derived `mic`, `symbol`, `date`) plus value
  equality. It also proves the fail-closed column contract, the venue calendar and
  clock, the currency declaration, the `JPX`↔`XTKS` single-venue collapse, the
  contract declarations below, and that corporate-action ingestion refuses this
  market without fetching or writing.
- No corporate-action fixture is committed for this market: the ingestion route
  refuses it, so a committed action pack would misrepresent support.

## Contract declarations (fail closed)

`core/market_contracts.py` declares, per market, a `Capability` for each of
`corporate_actions`, `volume_lot`, `price_limit_band`, `settlement_cycle`, `rights`,
each with `state` (`present`/`unknown`), `file:line` evidence and the input packet.
For `jpx_equity` all five are `unknown`, and `require_capability("jpx_equity", name)`
raises `MarketContractUnavailable` naming the missing evidence and packet — the
declaration cannot be mistaken for a satisfied contract. `CORPORATE_ACTION_MARKETS`
is derived from the same table, so the corporate-action route guard and this record
cannot drift.

## Not done here

No ADR is created or accepted; `REAL_ROOT_PRICE_MARKETS` is unchanged; no network
call, data acquisition, FX conversion or portfolio/multi-market support is added; no
corporate-action implementation is added; no catalog metadata changes, so
`data/catalog.jsonl` is untouched.
