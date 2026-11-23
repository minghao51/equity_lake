# Golden fixture packs (RS-M01..RS-M04)

One directory per non-US price market, each holding the committed golden OHLCV pack
exercised by `tests/unit/test_market_<market>.py`.

**Everything here is synthetic.** No vendor row, no downloaded file and no licensed
data is present: the packs exist so the offline seams (column contract, directory
route, identity derivation, Delta round trip, venue calendar, currency) can be
tested without any network call or spend.

## Provenance

Each `ohlcv.csv` was generated once with the existing deterministic seeder
(`equity_lake.devtools.seeding.synthetic_ohlcv`, `OhlcvProfile(include_adj_close=True)`)
and then frozen as a golden file:

| Market | Instruments (venue label) | Seed | Sessions |
| --- | --- | --- | --- |
| `cn_ashare` | `600519` (SSE), `000001` (SZSE) | 4241 | 2024-06-03 … 2024-06-05 |
| `hk_sg_equity` | `0700.HK` (HKEX), `D05.SI` (SGX) | 4242 | 2024-06-03 … 2024-06-05 |
| `jpx_equity` | `7203.T` (TSE), `6758.T` (TSE) | 4243 | 2024-06-03 … 2024-06-05 |
| `krx_equity` | `005930` (KRX), `000660` (KRX) | 4244 | 2024-06-03 … 2024-06-05 |

Sessions come from `core.calendar.trading_days_between(<market>, 2024-06-03, 2024-06-05)`,
so every fixture date is a real venue session of that market's calendar and the
calendar assertions in the market test are checkable.

Columns: `ticker, exchange, date, open, high, low, close, volume, adj_close`.
`exchange` is the venue label each production source supplies; the MIC is derived by
the production identity seam (`core.instruments.prepare_source_identity_frame`), never
stated by the fixture, so the round-trip test proves the derivation instead of
assuming it.

## Why the digests are pinned

Each market test asserts the SHA-256 of its `ohlcv.csv`. Editing a pack without
updating the digest fails the test on purpose: that is what makes it golden, and it
is what the recorded mutation/restore proof in the stage ledger relies on.

## Regenerating

Regeneration is only needed when a pack is deliberately extended. Set the market,
its tickers/venue labels, seed and session window, then rewrite the CSV with
`pl.DataFrame.write_csv(..., float_precision=2)`; update the digest constant in the
matching test in the same commit. `tests/fixtures/markets` is tracked (the repository
`.gitignore` ignores `*.parquet` and `data/*`, not this path), so the CSVs are text
and reviewable.
