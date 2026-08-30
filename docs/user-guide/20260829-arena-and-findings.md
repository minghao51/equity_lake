# Strategy Arena, Backtest Reports, and Finding Cards

The arena and report commands turn lake data into evidence: `equity arena run`
sweeps the shipped strategies across cost regimes in one shared data load and
emits FindingCards, while `equity report backtest` produces the artifacts for a
single run. Everything lands under `data/findings/` — auxiliary storage that is
not cataloged or pointblank-validated; the Pydantic `FindingCard` model is the
write-boundary contract. Both commands read OHLCV from the lake, so the window
must already be ingested (see [Ingestion](ingestion.md)).

## `equity arena run`

Loads the tickers once, then runs every selected strategy under every selected
cost regime by re-evaluating the engine against the shared frame. An
equal-weight buy-and-hold benchmark over the same tickers and window is built
from the same load and scaled to the initial cash. A run that raises is logged
and skipped — one failure does not abort the arena.

```bash
dotenvx run -- uv run equity arena run --start-date 2026-01-01 --end-date 2026-06-30
uv run equity arena run --strategies momentum,mean_reversion \
  --cost-regimes zero,realistic --start-date 2026-01-01 --end-date 2026-06-30
```

| Flag | Default | Description |
|---|---|---|
| `--start-date` | required | Start date (YYYY-MM-DD) |
| `--end-date` | required | End date (YYYY-MM-DD) |
| `--tickers`, `-t` | `AAPL,MSFT,GOOGL,AMZN,NVDA` | Comma-separated tickers |
| `--markets` | `us` | Comma-separated market codes |
| `--strategies` | all | Comma-separated subset of the registry below |
| `--cost-regimes` | all | Comma-separated subset of `zero,realistic,high` |
| `--initial-cash` | `100000` | Starting capital per run (and benchmark scale) |
| `--output-dir`, `-o` | `data/findings` | Findings root for artifacts and cards |
| `--verbose`, `-v` | off | Debug logging |

The arena uses its own strategy registry, mapping arena names to the shipped
strategy classes (each with its own default parameters):

| Registry name | Strategy class |
|---|---|
| `momentum` | `CrossSectionalMomentumStrategy` |
| `mean_reversion` | `BBMeanReversionStrategy` |
| `trend_following` | `SMACrossoverStrategy` |

Cost regimes are per-leg fee and tax ratios (`COST_REGIMES` in
`src/equity_lake/backtesting/arena.py`; defaults in `engine.py`):

| Regime | Fee ratio | Tax ratio |
|---|---|---|
| `zero` | 0 | 0 |
| `realistic` | `0.001425` (0.1425%, `DEFAULT_FEE_RATIO`) | `0.003` (0.3%, `DEFAULT_TAX_RATIO`) |
| `high` | `0.005` (0.5%) | `0.003` (`DEFAULT_TAX_RATIO`) |

`realistic` mirrors the engine defaults, so arena numbers under that regime
match a plain `VectorBacktestEngine` run with no cost config.

## `equity report backtest`

Runs one strategy under one cost regime, prints the result summary, and writes
the report artifacts — but no FindingCards:

```bash
uv run equity report backtest --strategy trend_following \
  --start-date 2026-01-01 --end-date 2026-06-30 --cost-regime high
```

| Flag | Default | Description |
|---|---|---|
| `--strategy`, `-s` | `momentum` | Registry name (see table above) |
| `--start-date` | required | Start date (YYYY-MM-DD) |
| `--end-date` | required | End date (YYYY-MM-DD) |
| `--tickers`, `-t` | `AAPL,MSFT,GOOGL,AMZN,NVDA` | Comma-separated tickers |
| `--markets` | `us` | Comma-separated market codes |
| `--cost-regime` | `realistic` | One of `zero`, `realistic`, `high` |
| `--initial-cash` | `100000` | Starting capital |
| `--output-dir`, `-o` | `data/findings` | Findings root |
| `--verbose`, `-v` | off | Debug logging |

Unknown strategy or regime names exit `1` with the accepted values listed.

## Artifact layout under `data/findings/`

`write_arena_artifacts` and `write_backtest_report` (`backtesting/report.py`)
produce the files below. The layout is keyed by `<strategy>__<regime>`, not by
run date — re-running with the same names overwrites the previous artifacts.

| Path | Producer | Contents |
|---|---|---|
| `<strategy>__<regime>/equity.parquet` | arena, report | Equity curve (t, equity) |
| `<strategy>__<regime>/drawdown.parquet` | arena, report | Drawdown fraction below running peak |
| `<strategy>__<regime>/metrics.json` | arena, report | Strategy, regime, and the full `BacktestResult` metrics dict |
| `<strategy>__<regime>/trades.json` | arena, report | Trade-by-trade execution log |
| `benchmark__equity.parquet` | arena only | Equal-weight buy-and-hold equity curve |
| `<card-id>.json` | arena, `ml compare/ablate` | One FindingCard per file |

## FindingCards

A `FindingCard` (`findings/models.py`, Pydantic with `extra="forbid"`) is one
evidence-backed conclusion from a comparison:

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | Stable unique identifier, e.g. `strategy-comparison` |
| `axis` | literal | `labeling`, `model`, `ablation`, `strategy`, `cost`, `benchmark`, or `risk` |
| `claim` | `str` | One-line hypothesis being tested |
| `verdict` | literal | `positive`, `negative`, or `inconclusive` |
| `conclusion` | `str` | Honest one-line takeaway of what was found |
| `metrics` | `dict[str, float]` | Numeric evidence (Sharpes, returns, accuracies) |
| `evidence_refs` | `list[str]` | Paths to backing artifacts under the findings root |
| `run_date` | `date` | When the comparison was run |
| `scope` | `dict` | Reproducibility metadata: tickers, window, costs, seed |

A `negative` verdict is a valid — and encouraged — result, not an error:
"no strategy beats buy-and-hold after costs" is a strong portfolio conclusion.
`write_finding_card` (`findings/writer.py`) validates the model before touching
disk and writes `data/findings/<id>.json`; `load_finding_cards` reads back
best-effort, skipping corrupt files so one bad artifact never breaks consumers.

The arena emits three cards, one per Phase-1 axis:

| Card id | Axis | Question |
|---|---|---|
| `strategy-comparison` | `strategy` | Which strategy dominates at realistic cost? |
| `cost-regime` | `cost` | How do trading costs degrade Sharpe? |
| `vs-benchmark` | `benchmark` | Does any active strategy beat equal-weight buy-and-hold after costs? |

## Consumers

FindingCards are a shared currency across the toolchain:

- `equity ml compare` writes `meta-label-vs-direction` (axis `labeling`) and
  `xgb-vs-lgbm` (axis `model`); `equity ml ablate` writes
  `enrichment-ablation` (axis `ablation`) — see
  [ML Rigor](20260810-ml-rigor.md).
- The read-only API serves every card as JSON: `GET /findings` and
  `GET /findings/{id}` — see [Read API](20260811-read-api.md).

## Related

- [Backtesting](backtesting.md)
- [ML Rigor](20260810-ml-rigor.md)
- [Read API](20260811-read-api.md)
- [CLI Reference](20260406-cli-reference.md)
