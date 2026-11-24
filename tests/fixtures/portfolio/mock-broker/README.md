# Mock broker pack — a realistic synthetic private ledger

This directory is a **synthetic** exercise of the private-ledger import boundary. It exists so the
import, replay and reconciliation path can be driven with data shaped like a real year rather than
with a four-row fixture. It is not a broker-export mapping (ADR-0024 approves none), it names no
real account, and every figure is invented.

`equity portfolio import` accepts exactly one format, so these files are written in the frozen
canonical CSV schema. The system of record is the SQLite ledger the import creates; the CSV is only
the way data enters it.

## Files

| File | Purpose |
| --- | --- |
| `ledger.csv` | Ten events over one account, covering every approved kind. |
| `statement-2024-03-29.csv` | Broker snapshot after the buy and the netted dividend. Must match. |
| `statement-2024-06-14.csv` | Snapshot after the sale and the 4:1 split. Must match. |
| `statement-2024-06-21.csv` | Snapshot after the withdrawal. Must match. |
| `statement-2024-06-14-quantity-drift.csv` | Same instant, but the broker claims 404 shares: a dividend reinvestment the ledger never recorded. Must **not** match. |
| `marks-2024-06-14.json` | Stored book marks for the two held positions, so `positions` and `summary` can value the book. |

## The year in events

| # | Date | Kind | Instrument | Effect |
| --- | --- | --- | --- | --- |
| 1 | 2024-01-02 | `OPENING` | cash | +25,000.00 |
| 2 | 2024-01-02 | `OPENING` | AAPL, 100 sh, basis 15,000.00 | opening position |
| 3 | 2024-01-02 | `OPENING` | MSFT, 50 sh, basis 17,500.00 | opening position |
| 4 | 2024-02-15 | `BUY` | NVDA, 40 sh @ 600.00, fee 5.00 | −24,005.00 cash |
| 5 | 2024-03-15 | `CASH_DIVIDEND` | AAPL | +24.00 net (25.00 gross, 1.00 withheld) |
| 6 | 2024-04-01 | `DEPOSIT` | — | +10,000.00 |
| 7 | 2024-05-10 | `SELL` | MSFT, 50 sh @ 380.00, fee 9.50 | +18,990.50 cash |
| 8 | 2024-06-10 | `SPLIT` | AAPL, ratio 4 | 100 → 400 shares, basis unchanged |
| 9 | 2024-06-12 | `FEE` | — | −35.00 |
| 10 | 2024-06-20 | `WITHDRAWAL` | — | −2,500.00 |

## Hand-checked arithmetic

Cash is the running sum of `cash_effect`; a fee is already inside the cash figure, never added twice.

- After event 5: `25,000.00 − 24,005.00 + 24.00 = 1,019.00` — the 2024-03-29 statement.
- After event 9: `1,019.00 + 10,000.00 + 18,990.50 − 35.00 = 29,974.50` — the 2024-06-14 statement.
- After event 10: `29,974.50 − 2,500.00 = 27,474.50` — the 2024-06-21 statement.

Positions follow the same replay:

- 2024-03-29: AAPL 100, MSFT 50, NVDA 40.
- 2024-06-14 and 2024-06-21: AAPL 400 (split applied), NVDA 40, MSFT gone (sold).

Realized gain on the sale is proceeds against basis: `18,990.50 − 17,500.00 = 1,490.50`.
Net dividend income stays income, never basis: `24.00`. The split moves no cash and states no price,
fee or basis, because ADR-0029 fixes its `quantity` as the ratio.

## Marks are an input, not a lookup

`positions` and `summary` refuse without `--marks`, because there is no quote service, mark producer
or network fallback (ADR-0024 decision 8): a missing mark is an error, never a silent zero. Each mark
quotes an explicit `session` and `as_of`, and the document validator requires that instant to equal the
exact venue's scheduled close — `2024-06-14T20:00:00Z` is 16:00 America/New_York on that Friday, the
NASDaq close. Provenance is stated as `synthetic`; upstream provenance of a book mark is not a research
price-source certification.

## What the tests assert

`tests/unit/test_portfolio_mock_pack.py` imports this pack into a temporary ledger (never
`data/portfolio`), then asserts the replayed book, the split, the realized gain, all three matching
snapshots, the deliberate drift being reported with exit 1, and that re-importing the same file
inserts nothing. Reconciliation tolerances are the code's own: `0.01` cash, `0.00000001` quantity. They also assert that
valuation works only through the stored marks (`NAV 120014.50`), that `positions` refuses without a marks
file, and that `summary` keeps external flows separate from profit and repeats the schema limit that gross
dividend income and withholding cannot be established from a net-only ledger.
