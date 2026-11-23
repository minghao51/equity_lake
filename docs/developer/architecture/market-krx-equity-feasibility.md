# Market Feasibility Pack — `krx_equity` (RS-M04)

This page is the offline feasibility record for the `krx_equity` price market, the
fourth and last of the non-US markets (`cn_ashare`, `hk_sg_equity`, `jpx_equity`,
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
ADR amending that decision** must be accepted before any `krx_equity` row may land
under `data/lake_real/`, and no such extension is made here.

## Single venue, and a different provider seam

Unlike `hk_sg_equity`, this market is one venue: one registry exchange (`XKRX`,
`core/paths.py:159`), one calendar (`core/calendar.py:47`), one timezone
(`Asia/Seoul`), one config section (`krx`, `config/tickers.yaml:975`) and one currency
(`KRW`, `config/tickers.yaml:976`). Union and intersection calendar semantics
therefore coincide here.

The adapter is also the one non-yfinance price route among the four non-US markets:
`ingestion/router.py:391` wires `_make_krx_fetcher` (`ingestion/router.py:162-175`) to
`KRXEquityFetcher` (`sources/krx.py:19`), which is a plain `MarketDataFetcher`
subclass backed by FinanceDataReader rather than the shared yfinance base. The venue
label therefore has to come from that adapter or the exact ticker config, and the
identity mapping is explicit (`KRX` / `KOREA EXCHANGE` → `XKRX`,
`core/instruments.py:47-48`); no ticker suffix is interpreted anywhere
(`core/instruments.py:3-4`, `sources/base.py:210-270`).

## Verified present seams

| Seam | Evidence | Verified by |
| --- | --- | --- |
| Bronze directory constant | `core/paths.py:73` `KRX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "krx_equity"` | `market_dir("krx_equity")` / `market_dir("krx")` |
| Registry row | `core/paths.py:159` `PriceMarket("krx_equity", "krx", "KRX_EQUITY_DIR", ("XKRX",), "Asia/Seoul")` | `PRICE_MARKETS["krx_equity"]`, `canonical_market("krx")` |
| Lake-relative dataset path | `core/paths.py:224` `PRICE_DATASET_PATHS` → `01_bronze/market_data/krx_equity` | equality with `MARKET_DIR_MAP` |
| Directory map | `ingestion/types.py:93` `MARKET_DIR_MAP["krx_equity"]` | round-trip write/read below |
| Valid-market vocabulary | `ingestion/types.py:41` `VALID_MARKETS` | membership assertion |
| Config section key | `ingestion/types.py:64` `PRICE_MARKET_CONFIG_KEYS["krx_equity"] == ("krx",)` | assertion |
| Currency | `config/tickers.yaml:975-976` (`krx:` → `currency: KRW`), read through `core/config_models.py:75` `MarketConfig` | `TickerConfig().get_market_info("krx").currency == "KRW"` |
| Symbol format | `config/tickers.yaml:1133` `krx: "^\\d{6}$"` | fixture tickers match the declared format |
| Router entry | `ingestion/router.py:391` `"krx_equity": _make_krx_fetcher` → `sources/krx.py:19` `KRXEquityFetcher` | `MARKET_REGISTRY` membership (no fetch executed) |
| Venue calendar | `core/calendar.py:27` `_MARKET_TO_EXCHANGE` (derived from the registry) → `("XKRX",)`; `core/calendar.py:47` `_MIC_TO_CALENDAR` (`XKRX` → `XKRX`) | `trading_days_between`, `is_trading_day`, per-MIC session checks |
| Timezone | registry `Asia/Seoul`; `core/calendar.py:159` `market_timezone` | assertion on `str(market_timezone(...))` |
| Session close clock | `core/calendar.py:116` `instrument_session_close` (UTC, exact venue session) | aware-close, exact-close and non-session assertions |
| Column contract | `core/schemas.py:95` `STANDARD_COLUMNS` (`volume` is a plain integer); `ingestion/writers.py:253` `validate_schema` generic OHLCV branch requires `ticker, date, open, high, low, close, volume` | fixture passes; each dropped required field fails closed |
| Instrument identity | `core/instruments.py:47-48` (`KRX` / `KOREA EXCHANGE` → `XKRX`), `:64` `MARKET_MICS["krx_equity"] == {"XKRX"}`; `:264` `prepare_source_identity_frame`; fetch-side `sources/base.py:210` `_attach_source_identity` | fixture states the venue label only; round-trip derives the MIC |
| Catalog row | `catalog/datasets.py:237-244` `krx_equity_ohlcv` (bronze, path from `KRX_EQUITY_DIR`, `STANDARD_COLUMNS`) | unchanged by this stage (no catalog-affecting change) |
| Real-root scope | `core/paths.py:170` `REAL_ROOT_PRICE_MARKETS == ("us_equity",)` | assertion — this market is not permitted there |

## Verified gaps (declared UNKNOWN, with the input packet that closes each)

The machine-readable form of these rows is `core/market_contracts.py`
(`MARKET_CONTRACTS["krx_equity"]`); `require_capability` fails closed on every one of
them instead of letting a consumer treat an absent rule as satisfied.

| Gap | Evidence | Input packet required to close it |
| --- | --- | --- |
| Corporate actions (dividends/splits) | `ingestion/corporate_actions.py:207` refuses every market outside `CORPORATE_ACTION_MARKETS` (currently `us_equity`); no `krx_equity` action table or coverage proof exists | Korean corporate-action feed: source name, licence, per-row ex-date semantics, source-issued publication/vintage clock (never an ingest-time substitute), coverage start/end evidence |
| Volume / lot contract | `core/schemas.py:95` (`volume` plain integer) and `validation/schemas.py:82` (`volume >= 0`) are the only volume rules; no board lot, trading unit or unit-change rule exists under `src/` | Market trading-rule extract with trading unit / share lot per market segment and any change inside the sample window, effective dates, document id, licence, vintage |
| Price-limit band | `validation/schemas.py:61` `PriceDataSchema` asserts only positive OHLC, `high >= low/open/close` and non-negative volume; no daily limit, band or halt rule exists | Rule extract for daily price limits / bands / halts with effective dates, document id, licence, vintage, and how a limit-locked session is represented |
| Settlement cycle | `core/calendar.py:116` yields the scheduled close only; no settlement-date or T+N rule exists under `src/` | CSD settlement-cycle evidence (cycle length, effective dates, document id, licence) if a consumer ever needs a settlement date |
| Rights / allotment terms | `core/schemas.py:166` `CORPORATE_ACTION_TYPES = ("dividend", "split")`, enforced at `validation/schemas.py:261`; `core/schemas.py:154` has no rights or entitlement column | Rights/allotment event feed with terms (ratio, subscription price, currency, entitlement cut-off), source-issued publication clock, coverage evidence, licence |
| Provider venue label | `sources/krx.py:19-20` is the FinanceDataReader adapter, so its venue label is adapter-provided or config-derived and resolved only through the explicit map (`core/instruments.py:47-48`, `sources/base.py:245-258`); nothing in this repository proves the adapter always emits one label per board | A provider-contract note or sample payloads proving which venue/MIC label the adapter emits per board, if a consumer ever needs board-level identity rather than `XKRX` |
| Real-root use | ADR-0030 decision 5 (`core/paths.py:170`); this stage creates no ADR | A **NEW accepted ADR amending decision 5** plus the scoped source/licence packet above |

## Golden fixture pack

- File: `tests/fixtures/markets/krx_equity/ohlcv.csv`
  (SHA-256 `477a75d93488a066df69a4bde021ad124f3c315c3241fd83bb248a200a72ae12`).
  `tests/fixtures/` is tracked — the repository `.gitignore` ignores `*.parquet` and
  `data/*`, not this path — so the pack is text and reviewable.
- Contents: two KRX instruments, six rows, sessions 2024-06-03 … 2024-06-05 taken from
  `core/calendar.trading_days_between("krx_equity", …)`; values from the existing
  deterministic seeder `devtools/seeding.synthetic_ohlcv` (seed 4244), so nothing is
  downloaded and nothing is a vendor row. Provenance table:
  `tests/fixtures/markets/README.md`.
- Tests: `tests/unit/test_market_krx_equity.py` pins the digest, then round-trips
  `read fixture → validate_schema → upsert_dataset (temporary lake root) → read_delta`
  and asserts identity (`market`, derived `mic`, `symbol`, `date`) plus value
  equality. It also proves the fail-closed column contract, the venue calendar and
  clock, the currency declaration, the single-venue collapse of union and intersection
  semantics, the contract declarations below, and that corporate-action ingestion
  refuses this market without fetching or writing.
- No corporate-action fixture is committed for this market: the ingestion route
  refuses it, so a committed action pack would misrepresent support.

## Contract declarations (fail closed)

`core/market_contracts.py` declares, per market, a `Capability` for each of
`corporate_actions`, `volume_lot`, `price_limit_band`, `settlement_cycle`, `rights`,
each with `state` (`present`/`unknown`), `file:line` evidence and the input packet.
For `krx_equity` all five are `unknown`, and `require_capability("krx_equity", name)`
raises `MarketContractUnavailable` naming the missing evidence and packet — the
declaration cannot be mistaken for a satisfied contract. `CORPORATE_ACTION_MARKETS`
is derived from the same table, so the corporate-action route guard and this record
cannot drift.

## Not done here

No ADR is created or accepted; `REAL_ROOT_PRICE_MARKETS` is unchanged; no network
call, data acquisition, FX conversion or portfolio/multi-market support is added; no
corporate-action implementation is added; no catalog metadata changes, so
`data/catalog.jsonl` is untouched.
