# Market Feasibility Pack — `hk_sg_equity` (RS-M02)

This page is the offline feasibility record for the `hk_sg_equity` price market, the
second of the four non-US markets (`cn_ashare`, `hk_sg_equity`, `jpx_equity`,
`krx_equity`) required by the residual plan (§2.9, §5). It follows the format of
`market-cn-ashare-feasibility.md` (RS-M01) and records what the repository proves
for this market and what it does not.

**No network call, no data acquisition or spend, no source or licence acquisition,
no FX conversion and no multi-market portfolio support occurred in producing this
pack.** Every row below is either a `file:line` reference into this repository or an
explicit gap with the input packet that would close it. No lot size, price limit,
settlement cycle or rights term is stated here: an invented market rule would be
worse than a declared gap, so each is listed as UNKNOWN with the exact input needed.

**Real-root use is blocked.** ADR-0030 decision 5 limits a real-source root to
`us_equity` (`core/paths.py:170` `REAL_ROOT_PRICE_MARKETS = ("us_equity",)`). A **NEW
ADR amending that decision** must be accepted before any `hk_sg_equity` row may land
under `data/lake_real/`, and no such extension is made here.

## This market is two venues, never one

`hk_sg_equity` is a *single market key over two separate venues*: HKEX (`XHKG`,
Hong Kong) and SGX (`XSES`, Singapore). This repository never models them as one
venue and this pack does not either:

| Fact | Hong Kong side | Singapore side |
| --- | --- | --- |
| Venue MIC | `XHKG` (`core/instruments.py:40`) | `XSES` (`core/instruments.py:42`) |
| Registry exchange (calendar id) | `XHKG` (`core/paths.py:157`) | `XSES` (`core/paths.py:157`) |
| MIC → calendar | `core/calendar.py:44` | `core/calendar.py:45` |
| Config section | `hk` (`config/tickers.yaml:650`) | `sg` (`config/tickers.yaml:789`) |
| Currency (declared, never converted) | `HKD` (`config/tickers.yaml:651`) | `SGD` (`config/tickers.yaml:790`) |
| Router split predicate | suffix `.HK` (`ingestion/router.py:61-73`) | suffix `.SI` (`ingestion/router.py:61-73`) |

Two currencies are declared and **no FX rate, conversion or cross-venue aggregation
exists anywhere in this repository**; a HKD row and an SGD row stay separate price
series. There is no market-level currency for `hk_sg_equity`, which is exactly why
`PRICE_MARKET_CONFIG_KEYS["hk_sg_equity"] == ("hk", "sg")`
(`ingestion/types.py:62`) returns a tuple rather than a single section key.

### Intersection vs union: the crux of this market

The two calendar functions deliberately disagree, and that disagreement is the
defining property of this market rather than a defect:

- `trading_days_between` (`core/calendar.py:75-94`) returns the **intersection**
  across a market's exchanges (`set.intersection(*session_sets)`, `:93`). Gap
  detection uses it, because a date expected for *every* ticker would otherwise
  flag false gaps for `.SI` rows on a Hong Kong-only session and for `.HK` rows on a
  Singapore-only session (docstring `core/calendar.py:76-87`).
- `is_trading_day` (`core/calendar.py:62-72`, `any(...)`, `:72`) and its caller
  `classify_market_sessions` (`core/calendar.py:141-156`) return the **union**: "is
  this market active somewhere today?". Orchestration and freshness need that
  answer, and the docstring at `core/calendar.py:144-148` forbids claiming
  market-wide completeness from a single-venue session.

The divergence is real and observable in the committed fixture window's own quarter:

| Date | `XHKG` session | `XSES` session | `is_trading_day` (union) | `trading_days_between` (intersection) |
| --- | --- | --- | --- | --- |
| 2024-06-10 | no | yes | `True` | excluded |
| 2024-06-17 | yes | no | `True` | excluded |

`tests/unit/test_market_hk_sg_equity.py` asserts exactly this pair, so no future
change can quietly collapse the two venues into one calendar answer.

## Verified present seams

| Seam | Evidence | Verified by |
| --- | --- | --- |
| Bronze directory constant | `core/paths.py:71` `HK_SG_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "hk_sg_equity"` | `market_dir("hk_sg_equity")` / `market_dir("hk_sg")` |
| Registry row | `core/paths.py:157` `PriceMarket("hk_sg_equity", "hk_sg", "HK_SG_EQUITY_DIR", ("XHKG", "XSES"), "Asia/Hong_Kong")` | `PRICE_MARKETS["hk_sg_equity"]`, `canonical_market("hk_sg")` |
| Lake-relative dataset path | `core/paths.py:224` `PRICE_DATASET_PATHS` → `01_bronze/market_data/hk_sg_equity` | equality with `MARKET_DIR_MAP` |
| Directory map | `ingestion/types.py:93` `MARKET_DIR_MAP["hk_sg_equity"]` | round-trip write/read below |
| Valid-market vocabulary | `ingestion/types.py:41` `VALID_MARKETS` | membership assertion |
| Config section keys | `ingestion/types.py:62` `PRICE_MARKET_CONFIG_KEYS["hk_sg_equity"] == ("hk", "sg")` | assertion |
| Currencies | `config/tickers.yaml:650-651` (`hk:` → `currency: HKD`) and `:789-790` (`sg:` → `currency: SGD`), read through `core/config_models.py:75` `MarketConfig` | `TickerConfig().get_market_info("hk").currency == "HKD"`, `("sg") == "SGD"` |
| Router entry | `ingestion/router.py:389` `"hk_sg_equity": _make_hk_sg_fetcher` (yfinance adapter `sources/hk_sg.py:55` `HKSGEquityFetcher`) | `MARKET_REGISTRY` membership (no fetch executed) |
| Explicit-scope split | `ingestion/router.py:61-73` `_split_hk_sg_tickers` — `.HK` → HK fetcher, `.SI` → SG fetcher, anything else raises | split assertion below (no fetcher constructed) |
| Venue calendars | `core/calendar.py:27` `_MARKET_TO_EXCHANGE` (derived from the registry) → `("XHKG", "XSES")`; `core/calendar.py:44-45` `_MIC_TO_CALENDAR` (`XHKG` → `XHKG`, `XSES` → `XSES`) | `trading_days_between`, `is_trading_day`, `is_instrument_trading_day`, per-MIC session checks |
| Session close clock | `core/calendar.py:116` `instrument_session_close` (UTC, exact venue session) | aware-close and non-session assertions per MIC |
| Column contract | `core/schemas.py:95` `STANDARD_COLUMNS` (`volume` is a plain integer); `ingestion/writers.py:253` `validate_schema` generic OHLCV branch requires `ticker, date, open, high, low, close, volume` | fixture passes; each dropped required field fails closed |
| Instrument identity | `core/instruments.py:40` (`HKEX` → `XHKG`), `:42` (`SGX` → `XSES`), `:62` `MARKET_MICS["hk_sg_equity"] == {"XHKG","XSES"}`; `:264` `prepare_source_identity_frame` | fixture states venue labels only; round-trip derives both MICs |
| Catalog row | `catalog/datasets.py:221-228` `hk_sg_equity_ohlcv` (bronze, path from `HK_SG_EQUITY_DIR`, `STANDARD_COLUMNS`) | unchanged by this stage (no catalog-affecting change) |
| Real-root scope | `core/paths.py:170` `REAL_ROOT_PRICE_MARKETS == ("us_equity",)` | assertion — this market is not permitted there |

## Verified gaps (declared UNKNOWN, with the input packet that closes each)

The machine-readable form of these rows is `core/market_contracts.py`
(`MARKET_CONTRACTS["hk_sg_equity"]`); `require_capability` fails closed on every one
of them instead of letting a consumer treat an absent rule as satisfied.

| Gap | Evidence | Input packet required to close it |
| --- | --- | --- |
| Corporate actions (dividends/splits) | `ingestion/corporate_actions.py:207` refuses every market outside `CORPORATE_ACTION_MARKETS` (currently `us_equity`); no `hk_sg_equity` action table or coverage proof exists | Two-venue corporate-action feed(s): source name, licence, per-row ex-date semantics, source-issued publication/vintage clock (never an ingest-time substitute), coverage start/end evidence — stated **per venue**, since an HK ex-date and an SG ex-date come from different sources |
| Volume / lot contract | `core/schemas.py:95` (`volume` plain integer) and `validation/schemas.py:82` (`volume >= 0`) are the only volume rules; no board lot, tradable unit or unit-change rule exists under `src/` | Per-exchange trading-rule extract with board lot / tradable unit per board (HKEX and SGX separately) and any change inside the sample window, effective dates, document id, licence, vintage |
| Price-limit band | `validation/schemas.py:61` `PriceDataSchema` asserts only positive OHLC, `high >= low/open/close` and non-negative volume; no band, halt or VCM rule exists | Per-exchange rule extract for daily limits / dynamic bands / halts / volatility controls with effective dates, document id, licence, vintage, and how a limit-locked session is represented |
| Settlement cycle | `core/calendar.py:116` yields the scheduled close only; no settlement-date or T+N rule exists under `src/` | Two CSD settlement-cycle evidence sets (cycle length, effective dates, document id, licence) if a consumer ever needs a settlement date |
| Rights / allotment terms | `core/schemas.py:166` `CORPORATE_ACTION_TYPES = ("dividend", "split")`, enforced at `validation/schemas.py:261`; `core/schemas.py:154` has no rights or entitlement column | Rights/allotment event feed per venue with terms (ratio, subscription price, currency, entitlement cut-off), source-issued publication clock, coverage evidence, licence |
| Market-level timezone | The registry declares one timezone for the pair (`core/paths.py:157`, `Asia/Hong_Kong`), read by `core/calendar.py:159` `market_timezone`; Singapore is `Asia/Singapore`. `instrument_session_close` is the exact-venue seam and is correct per MIC, but `market_timezone("hk_sg_equity")` answers with the Hong Kong zone | A decision on whether a per-venue timezone accessor is ever needed; until then consumers must use the exact-MIC clocks, and no code may treat `market_timezone` as an SG clock |
| Real-root use | ADR-0030 decision 5 (`core/paths.py:170`); this stage creates no ADR | A **NEW accepted ADR amending decision 5** plus the scoped two-venue source/licence packet above |

## Golden fixture pack

- File: `tests/fixtures/markets/hk_sg_equity/ohlcv.csv`
  (SHA-256 `8571b7aac4d19b1d48e096fae6f21a87637123852a7224d552d143ecda59a360`).
  `tests/fixtures/` is tracked — the repository `.gitignore` ignores `*.parquet` and
  `data/*`, not this path — so the pack is text and reviewable.
- Contents: two venues, two instruments, six rows, sessions 2024-06-03 … 2024-06-05
  taken from `core/calendar.trading_days_between("hk_sg_equity", …)`; values from the
  existing deterministic seeder `devtools/seeding.synthetic_ohlcv` (seed 4242), so
  nothing is downloaded and nothing is a vendor row. Provenance table:
  `tests/fixtures/markets/README.md`.
- Tests: `tests/unit/test_market_hk_sg_equity.py` pins the digest, then round-trips
  `read fixture → validate_schema → upsert_dataset (temporary lake root) → read_delta`
  and asserts identity (`market`, derived `mic`, `symbol`, `date`) plus value
  equality, proves both currencies are declared without conversion, proves the two
  venue calendars differ (intersection ≠ union), and proves the fail-closed column
  contract, the fail-closed contract declarations, and that corporate-action
  ingestion refuses this market without fetching or writing.
- No corporate-action fixture is committed for this market: the ingestion route
  refuses it, so a committed action pack would misrepresent support.

## Contract declarations (fail closed)

`core/market_contracts.py` declares, per market, a `Capability` for each of
`corporate_actions`, `volume_lot`, `price_limit_band`, `settlement_cycle`, `rights`,
each with `state` (`present`/`unknown`), `file:line` evidence and the input packet.
For `hk_sg_equity` all five are `unknown`, and `require_capability("hk_sg_equity",
name)` raises `MarketContractUnavailable` naming the missing evidence and packet —
the declaration cannot be mistaken for a satisfied contract. `CORPORATE_ACTION_MARKETS`
is derived from the same table, so the corporate-action route guard and this record
cannot drift.

## Not done here

No ADR is created or accepted; `REAL_ROOT_PRICE_MARKETS` is unchanged; no network
call, data acquisition, FX conversion or portfolio/multi-market support is added; the
two venues are never merged into one calendar, one currency or one price series; no
corporate-action implementation is added; no catalog metadata changes, so
`data/catalog.jsonl` is untouched.
