# Market Feasibility Pack — `cn_ashare` (RS-M01)

This page is the offline feasibility record for the `cn_ashare` price market, the
first of the four non-US markets (`cn_ashare`, `hk_sg_equity`, `jpx_equity`,
`krx_equity`) required by the residual plan (§2.9, §5). It records what the
repository proves for this market and what it does not.

**No network call, no data acquisition or spend, no source or licence acquisition,
no FX conversion and no multi-market portfolio support occurred in producing this
pack.** Every row below is either a `file:line` reference into this repository or an
explicit gap with the input packet that would close it. No lot size, price limit,
settlement cycle or rights term is stated here: an invented market rule would be
worse than a declared gap, so each is listed as UNKNOWN with the exact input needed.

**Real-root use is blocked.** ADR-0030 decision 5 limits a real-source root to
`us_equity` (`core/paths.py:170` `REAL_ROOT_PRICE_MARKETS = ("us_equity",)`). A **NEW
ADR amending that decision** must be accepted before any `cn_ashare` row may land
under `data/lake_real/`, and no such extension is made here.

## Verified present seams

| Seam | Evidence | Verified by |
| --- | --- | --- |
| Bronze directory constant | `core/paths.py:70` `CN_ASHARE_DIR = BRONZE_MARKET_DATA_DIR / "cn_ashare"` | `market_dir("cn_ashare")` / `market_dir("cn")` |
| Registry row | `core/paths.py:156` `PriceMarket("cn_ashare", "cn", "CN_ASHARE_DIR", ("XSHG",), "Asia/Shanghai")` | `PRICE_MARKETS["cn_ashare"]`, `canonical_market("cn")` |
| Lake-relative dataset path | `core/paths.py:224` `PRICE_DATASET_PATHS` → `01_bronze/market_data/cn_ashare` | equality with `MARKET_DIR_MAP` |
| Directory map | `ingestion/types.py:93` `MARKET_DIR_MAP["cn_ashare"]` | round-trip write/read below |
| Valid-market vocabulary | `ingestion/types.py:41` `VALID_MARKETS` | membership assertion |
| Config section key | `ingestion/types.py:59` `PRICE_MARKET_CONFIG_KEYS["cn_ashare"] == ("cn",)` | assertion |
| Currency | `config/tickers.yaml:476-477` (`cn:` → `currency: CNY`), read through `core/config_models.py:75` `MarketConfig` | `TickerConfig().get_market_info("cn").currency` |
| Router entry | `ingestion/router.py:388` `"cn_ashare": _make_cn_fetcher` (akshare adapter `sources/cn.py`, efinance `sources/cn_efinance.py`, hybrid `sources/cn_hybrid.py`) | `MARKET_REGISTRY` membership (no fetch executed) |
| Venue calendar | `core/calendar.py:27` `_MARKET_TO_EXCHANGE` (derived from the registry) → `("XSHG",)`; `core/calendar.py:35-48` `_MIC_TO_CALENDAR` (`XSHG` → `XSHG`, `XSHE` → `XSHG`) | `trading_days_between`, `is_trading_day`, per-MIC session checks |
| Timezone | registry `Asia/Shanghai`; `core/calendar.py:159` `market_timezone` | assertion on `str(market_timezone(...))` |
| Session close clock | `core/calendar.py:116` `instrument_session_close` (UTC, exact venue session) | aware-close and non-session assertions |
| Column contract | `core/schemas.py:95` `STANDARD_COLUMNS` (`volume` is a plain integer); `ingestion/writers.py:253` `validate_schema` generic OHLCV branch requires `ticker, date, open, high, low, close, volume` | fixture passes; each dropped required field fails closed |
| Instrument identity | `core/instruments.py:36` (`SSE` → `XSHG`), `:38` (`SZSE` → `XSHE`), `:59-60` `MARKET_MICS["cn_ashare"] == {"XSHG","XSHE"}`; `:264` `prepare_source_identity_frame` | fixture states venue labels only; round-trip derives both MICs |
| Catalog row | `catalog/datasets.py:214-221` `cn_ashare_ohlcv` (bronze, path from `CN_ASHARE_DIR`, `STANDARD_COLUMNS`) | unchanged by this stage (no catalog-affecting change) |
| Real-root scope | `core/paths.py:170` `REAL_ROOT_PRICE_MARKETS == ("us_equity",)` | assertion — this market is not permitted there |

## Verified gaps (declared UNKNOWN, with the input packet that closes each)

The machine-readable form of these rows is `core/market_contracts.py`
(`MARKET_CONTRACTS["cn_ashare"]`); `require_capability` fails closed on every one of
them instead of letting a consumer treat an absent rule as satisfied.

| Gap | Evidence | Input packet required to close it |
| --- | --- | --- |
| Corporate actions (dividends/splits) | `ingestion/corporate_actions.py:207` refuses every market outside `CORPORATE_ACTION_MARKETS` (currently `us_equity`); no `cn_ashare` action table or coverage proof exists | A-share corporate-action feed: source name, licence, per-row ex-date semantics, source-issued publication/vintage clock (never an ingest-time substitute), coverage start/end evidence |
| Volume / lot contract | `core/schemas.py:95` (`volume` plain integer) and `validation/schemas.py:82` (`volume >= 0`) are the only volume rules; no board lot, tradable unit or unit-change rule exists under `src/` | Exchange trading-rule extract with board lot / tradable unit per board and any change inside the sample window, effective dates, document id, licence, vintage |
| Price-limit band | `validation/schemas.py:61` `PriceDataSchema` asserts only positive OHLC, `high >= low/open/close` and non-negative volume; no band or halt rule exists | Rule extract for daily limits / dynamic bands / halts with effective dates, document id, licence, vintage, and how a limit-locked session is represented |
| Settlement cycle | `core/calendar.py:116` yields the scheduled close only; no settlement-date or T+N rule exists under `src/` | CSD settlement-cycle evidence (cycle length, effective dates, document id, licence) if a consumer ever needs a settlement date |
| Rights / allotment terms | `core/schemas.py:166` `CORPORATE_ACTION_TYPES = ("dividend", "split")`, enforced at `validation/schemas.py:261`; `core/schemas.py:154` has no rights or entitlement column | Rights event feed with terms (ratio, subscription price, currency, entitlement cut-off), source-issued publication clock, coverage evidence, licence |
| Calendar vocabulary conflation | The registry lists only `("XSHG",)` (`core/paths.py:156`) while identity admits `XSHE` (`core/instruments.py:60`); SZSE sessions come from the deliberate `XSHE` → `XSHG` mapping (`core/calendar.py:39-45`, no supported `XSHE` calendar in `exchange_calendars`) | Venue-pair session-divergence evidence plus a supported Shenzhen calendar before any SZSE-only holiday/session can be represented |
| Real-root use | ADR-0030 decision 5 (`core/paths.py:170`); this stage creates no ADR | A **NEW accepted ADR amending decision 5** plus the scoped source/licence packet above |

## Golden fixture pack

- File: `tests/fixtures/markets/cn_ashare/ohlcv.csv`
  (SHA-256 `82861035d70e43935cc3b9404b43cb2d2a28814dc7f6a64c0ffd67e0a0370d59`).
  `tests/fixtures/` is tracked — the repository `.gitignore` ignores `*.parquet` and
  `data/*`, not this path — so the pack is text and reviewable.
- Contents: two SSE/SZSE venues, six rows, sessions 2024-06-03 … 2024-06-05 taken
  from `core/calendar.trading_days_between("cn_ashare", …)`; values from the existing
  deterministic seeder `devtools/seeding.synthetic_ohlcv` (seed 4241), so nothing is
  downloaded and nothing is a vendor row. Provenance table:
  `tests/fixtures/markets/README.md`.
- Tests: `tests/unit/test_market_cn_ashare.py` pins the digest, then round-trips
  `read fixture → validate_schema → upsert_dataset (temporary lake root) → read_delta`
  and asserts identity (`market`, derived `mic`, `symbol`, `date`) plus value
  equality. It also proves the fail-closed column contract, the venue calendar and
  clocks, the currency declaration, the contract declarations below, and that
  corporate-action ingestion refuses this market without fetching or writing.
- No corporate-action fixture is committed for this market: the ingestion route
  refuses it, so a committed action pack would misrepresent support.

## Contract declarations (fail closed)

`core/market_contracts.py` declares, per market, a `Capability` for each of
`corporate_actions`, `volume_lot`, `price_limit_band`, `settlement_cycle`, `rights`,
each with `state` (`present`/`unknown`), `file:line` evidence and the input packet.
For `cn_ashare` all five are `unknown`, and `require_capability("cn_ashare", name)`
raises `MarketContractUnavailable` naming the missing evidence and packet — the
declaration cannot be mistaken for a satisfied contract. `CORPORATE_ACTION_MARKETS`
is derived from the same table, so the corporate-action route guard and this record
cannot drift.

## Not done here

No ADR is created or accepted; `REAL_ROOT_PRICE_MARKETS` is unchanged; no network
call, data acquisition, FX conversion or portfolio/multi-market support is added; no
corporate-action implementation is added; no catalog metadata changes, so
`data/catalog.jsonl` is untouched.
