# Strategy Arena, Backtest Reports, and Finding Cards

The arena and report commands turn lake data into evidence: `equity arena run`
sweeps the shipped strategies across cost regimes in one shared data load and
emits FindingCards, while `equity report backtest` produces the artifacts for a
single run. Everything lands under `data/findings/` — auxiliary storage that is
not cataloged or pointblank-validated; the Pydantic `FindingCard` model is the
write-boundary contract. Both commands read OHLCV from the lake, so the window
must already be ingested (see [Ingestion](ingestion.md)), and the lake must carry
point-in-time action coverage (ADR-0015) — an unseeded or coverage-less lake
produces no decisions at all, not neutral ones.

For an offline walkthrough, `equity demo seed` writes a synthetic lake under
`data/sample/` with canonical identity and synthetic coverage proofs, so it is
backtestable directly via `--lake` (ADR-0020):

```bash
uv run equity demo seed --years 5
uv run equity arena run --lake data/sample --start-date 2025-01-02 --end-date 2026-06-30
```

Runtime scales with decision dates × instruments: the engine rebuilds each
window point-in-time, so keep demo windows short.

## `equity arena run`

Loads the tickers once, then runs every selected strategy under every selected
cost regime by re-evaluating the engine against the shared frame. An
equal-weight buy-and-hold benchmark over the same tickers and window is built
from the same load as a **fixed-share** curve: equal capital is allocated once
at the first evaluation date (`q_i = initial_cash / (N * close_i(inception))`)
and held without rebalancing, so `equity(t) = sum(q_i * close_i(t))` on the
shared split-neutral marks. A run that raises is logged, recorded as a failed
pair with a sanitized reason, and skipped — one failure does not abort the other
runs (see [Completion, failure, and evaluation status](#completion-failure-and-evaluation-status)).

The benchmark is deliberately conservative. It needs a complete panel: every
scoped instrument priced at inception and at every date in the cohort's date
union. An incomplete panel, a duplicate identity/date key, a missing or
non-positive/non-finite mark, a partial canonical identity, or a non-positive
`initial_cash` raises `ValueError` instead of carrying a stale price, starting
late, dropping an instrument or filling zero — there is no mixed-calendar
promise and no carry/delisting policy. Identity is `(market, MIC, symbol)` when
those columns exist, so the same ticker on two venues stays two instruments.
`run_arena` additionally rejects a requested instrument with no loaded rows;
the standalone `equal_weight_buyhold` helper can only validate the identities
in the frame it is given. Only a truly empty frame yields an empty benchmark
curve: a non-empty load without a `close` column raises `ValueError` naming the
missing and present columns, and `run_arena` never returns an outcome whose
benchmark is empty, short of the load's date union, or non-finite. The inception
point is `initial_cash` only to floating-point tolerance (a fixed-share sum can
be off by about one ULP), so compare it with a relative tolerance, not `==`.
Cards disclose the policy in `scope.benchmark_policy` and
`scope.benchmark_missing_mark_policy`, and a caller-supplied card `scope` can add
keys but cannot replace those disclosures (or `scope.benchmark_asymmetry` /
`scope.sharpe_risk_free_rate`).

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

Momentum selects from candidates ordered by past return descending, then canonical
identity ascending, so a run's selected names do not depend on row order; its
default `min_stocks` of 10 means a rebalance date with fewer than ten eligible
instruments keeps the previous book. On the default five-ticker arena the loaded
universe is itself below that minimum, so the run is reported `ineligible`
(`universe 5 < min_stocks 10`) instead of loosening the default.

Cost regimes are per-leg fee and tax ratios (`COST_REGIMES` in
`src/equity_lake/backtesting/arena.py`; defaults in `engine.py`). On
single-market runs the `realistic` regime resolves sell-tax defaults per
market (`MARKET_COST_DEFAULTS` in `arena.py`): US/JPX/KRX pay no transaction
tax, CN pays A-share stamp duty (0.05% on sells), and `hk_sg` uses HK-style
0.1%. Multi-market runs fall back to the engine defaults below (one engine run
cannot mix per-venue taxes); pass `run_arena(market_costs=...)` for overrides.

| Regime | Fee ratio | Tax ratio |
|---|---|---|
| `zero` | 0 | 0 |
| `realistic` | `0.001425` (0.1425%, `DEFAULT_FEE_RATIO`) | per-market (see above); engine-default fallback `0.003` (`DEFAULT_TAX_RATIO`) |
| `high` | `0.005` (0.5%) | `0.003` (`DEFAULT_TAX_RATIO`) |

Strategy Sharpe values (engine and FindingCards alike) share one convention
(`backtesting/metrics.py`): excess returns over a 2% annual risk-free rate,
annualized with 252 trading days. Cards state the rf in `scope.sharpe_risk_free_rate`,
disclose the benchmark's zero-cost/zero-lag asymmetry in
`scope.benchmark_asymmetry`, and state the benchmark construction and missing-mark
policies in `scope.benchmark_policy` / `scope.benchmark_missing_mark_policy`.
The fixed-share baseline is a correctness fix, not evidence that any strategy
works.

## Completion, failure, and evaluation status

Every requested `(strategy, cost_regime)` pair is attempted independently —
strategy and engine construction sit inside the same per-pair isolation boundary,
so a constructor that raises is one failed pair instead of an arena abort. A run
that raises is recorded on the in-memory outcome — `requested` holds every pair,
`failed` holds the failed ones with an intact `error_type` and a sanitized reason
capped at 200 characters — and never aborts the remaining runs. Sanitization
removes credentials (`authorization=<redacted>` consumes the rest of the message,
including `Bearer`/`Basic` credentials, while a bare `Bearer abc`/`bearer=abc`/
`Basic xyz` scheme+credential becomes `<redacted>` and `api_key`/`token`/`secret`/
`password` become `key=<redacted>`) and every whitespace token that carries a path
separator (`/abs/path`, `relative/path`, the backslash-only `relative\private\secret.txt`,
`C:\Users\x\secret`, `~/private/file`, and a path split by spaces all become `<path>`,
with repeats collapsed). Only `Exception` subclasses are isolated:
`SystemExit` and `KeyboardInterrupt` still propagate. The summary prints requested/
completed/failed/non-evaluable counts, the failed pairs and each run's evaluation
status, and branches on the completion state:

| State | Output | Exit |
| --- | --- | --- |
| every requested pair completed | `Arena complete: <c> of <r> requested pair(s) completed` | `0` |
| any requested pair failed | `PARTIAL` label, counts, failed pairs; the successful runs' artifacts are still written | `1` |
| a requested pair is missing without a recorded failure | `PARTIAL` label, counts, plus `N requested pair(s) neither completed nor recorded a failure: …` naming each pair; the successful runs' artifacts are still written | `1` |
| no requested run completed | actionable diagnostics, including `N requested pair(s) neither completed nor recorded a failure: …` naming every requested pair with neither outcome; no success banner, no new cards, and the artifact writer is never called | `1` |
| a completed run (or recorded failure) outside the request | named as out of scope in the diagnostics; never counted as success, never published | — (does not change the exit code) |
| a pair is recorded both completed and failed | `PARTIAL` label, counts, plus `N requested pair(s) are recorded both completed and failed: …` naming each pair | `1` |
| artifact write failure | red `arena artifact write failed: …` | `1` |

The completed count is measured against the requested set, not against the
length of the run list: `1 of 2 requested pair(s) completed` with an empty
`failed` list is a partial invocation and exits `1`. The same holds for the
zero-completion guard: it fires when no *requested* pair completed, so an
outcome that also carries a completed run outside the request publishes nothing
and exits `1`, naming those pairs as `completed run(s) … outside this
invocation's request` and stating that they are not published because the
requested set produced no completed evidence. Recorded pairs outside the request
are never counted as success, never written into the findings directory, and
never satisfy a missing requested pair; when the request itself did complete they
are only named in a yellow out-of-scope line. The requested set is the
one `run_arena` recorded; a legacy outcome that carries no `requested` pairs
falls back to its completed plus failed pairs, so library fixtures keep their
behaviour. A pair that is neither completed nor failed is reported as *missing
evidence*, never as a recorded error, and a pair that is recorded as both a
completed run and a failure is diagnosed as a contradiction rather than being
counted as a success.

Requested pairs are deduplicated in first-occurrence order before any count is
computed or printed, and `run_arena` collapses duplicate selections the same
way. Duplicate `--strategies`/`--cost-regimes` entries are therefore not an
error: `--strategies momentum,momentum --cost-regimes zero` is one requested
pair, runs the pair once, and reports `1 of 1`, never `1 of 2`.

There is no `--allow-partial` flag: a partial arena is a partial result and a
scheduler must see the nonzero exit. A completed computation is not the same as
evaluable evidence, so every completed run carries an `evaluation_status`:

| Status | Meaning |
| --- | --- |
| `evaluated` | the run has trades and is admissible card evidence |
| `no_trades` | the strategy executed nothing on this load — no position evidence |
| `ineligible` | the strategy declares an integer `min_stocks` and the loaded universe is smaller; the note reads like `universe 5 < min_stocks 10` |

Only `evaluated` runs feed the `strategy-comparison`, `cost-regime` and
`vs-benchmark` cards. A `no_trades` or `ineligible` run never becomes a measured
zero Sharpe or a positive strategy verdict, and it cannot make a strategy appear
to beat or match the benchmark. Non-evaluable runs do not fail the CLI by
themselves — a fully executed cash-only/no-condition run is printed with its
status and exits `0` — but the default five-ticker arena cannot satisfy
momentum's `min_stocks=10`, so that run reports `ineligible`
(`universe 5 < min_stocks 10`) rather than lowering the default.

Failed pairs are not evidence either. Artifact directories are keyed by
`<strategy>__<regime>`, so directories from earlier runs can remain under the
findings root (immutable per-run layout is E01's boundary): a partial invocation
never refreshed a failed pair's previously published evidence, and nothing is
deleted automatically.

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
| `benchmark__equity.parquet` | arena only | Fixed-share equal-weight buy-and-hold equity curve |
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

Card admission: only runs whose `evaluation_status` is `evaluated` are read.
`no_trades` and `ineligible` runs are excluded from every metric and verdict, and
the scope discloses the accounting (`requested_runs`, `completed_runs`,
`failed_runs`, `non_evaluable_runs`) so a card is never read as covering a pair
that produced no evidence.

The `strategy-comparison`, `cost-regime` and `vs-benchmark` cards read
`sharpe_ratio` from the result's `metrics` dict, never through the convenience
property that returns `0` for a missing key. A missing or non-finite (NaN/inf)
Sharpe — strategy or benchmark side — is unavailable comparison data: the key is
omitted from `card.metrics`, the verdict is `inconclusive`, and the conclusion
names what could not be measured. An unavailable metric is never a measured zero
and never a positive verdict.

The arena emits three cards, one per Phase-1 axis:

| Card id | Axis | Question |
|---|---|---|
| `strategy-comparison` | `strategy` | Which strategy dominates at realistic cost? |
| `cost-regime` | `cost` | How do trading costs degrade Sharpe? |
| `vs-benchmark` | `benchmark` | Does any active strategy beat equal-weight buy-and-hold after costs? |

### The `cost-regime` card

The `cost-regime` card compares one representative strategy at `zero` and
`realistic` cost. It reports the measured Sharpe values under the keys
`zero.sharpe` / `realistic.sharpe` (plus `<regime>.sharpe` for any other regime
that ran) and the signed change `cost_regime.sharpe_delta = realistic − zero`.
The verdict is a numerical comparison of those two measured values, not a
significance test:

| Condition | Verdict | Reading |
|---|---|---|
| `delta < -1e-12` | `negative` | realistic costs lowered Sharpe by the stated signed amount |
| `delta > 1e-12` | `positive` | an **observed difference** only; the conclusion states that cost/execution interaction is not evidence of a causal benefit |
| `\|delta\| <= 1e-12` | `inconclusive` | no measured cost effect |

Values are read from the result's `metrics` dict and must be present and
finite. A missing or non-finite `sharpe_ratio` is unknown, not zero: the
unavailable numeric key is omitted from `card.metrics` (and so is the delta),
the verdict is `inconclusive`, and the conclusion names the side that could not
be measured. No NaN/inf is ever written as evidence. The card makes no turnover
claim and states no percentage change — only absolute signed Sharpe deltas.

The delta itself is validated too: two individually finite Sharpes (e.g.
`-1.7e308` and `1.7e308`) can still overflow when subtracted. In that case the
delta is not usable evidence, so `cost_regime.sharpe_delta` is omitted, the
verdict is `inconclusive`, and the conclusion states that the change could not
be measured finitely. Emitting the overflowing value would serialize as `null`
and make the card unreadable by `load_finding_cards`.

Ambiguous evidence is never resolved by picking a run. If the representative
strategy has more than one run for the same cost regime, the card is
`inconclusive`: the conclusion names the duplicated regime(s) with their run
counts, each duplicated regime's `<regime>.sharpe` key is omitted (no single
value can be attributed to it), the unique regimes keep their keys, and
`cost_regime.sharpe_delta` is not reported. Using "the last run in the list"
would make the measured comparison depend on list order. This applies to any
duplicated regime of the strategy — including one that is not itself compared
(e.g. two `high` runs) — because the card's regime evidence is then ambiguous
as a whole.

A cost card is only built when both a `zero` and a `realistic` run exist for the
representative strategy; a single regime (or `zero` + `high`) emits no cost
card rather than a fabricated comparison. The tolerance is inclusive:
exactly `±1e-12` is `inconclusive`, and the classification uses the measured
float difference (at Sharpe `1.0` the spacing between adjacent doubles is
~`2.2e-16`, so `1.0 + 1e-12` measures just outside the tolerance).

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
