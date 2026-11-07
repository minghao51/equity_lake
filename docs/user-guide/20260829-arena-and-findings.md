# Strategy Arena, Backtest Reports, and Finding Cards

The arena and report commands turn lake data into evidence: `equity arena run`
sweeps the shipped strategies across cost regimes in one shared data load and
emits FindingCards, while `equity report backtest` publishes the immutable run +
attempt evidence for a single run. Everything lands under `data/findings/` —
auxiliary storage that is not cataloged or pointblank-validated; the Pydantic
`FindingCard` model is the write-boundary contract. Both commands read OHLCV from
the lake, so the window
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
| no requested run completed | actionable diagnostics, including `N requested pair(s) neither completed nor recorded a failure: …` naming every requested pair with neither outcome; no success banner and no run/card evidence, but the invocation **does** publish a `zero_completion` attempt record (its failure must outlive the terminal) | `1` |
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

Failed pairs are not evidence either. Each invocation publishes its own immutable
evidence — one attempt directory per invocation plus one run directory per requested
completed pair — and writes no legacy path at all. The legacy
`<strategy>__<regime>/` directories, the flat `benchmark__equity.parquet` and the
flat `<card-id>.json` cards that earlier runs produced stay on disk **frozen and
read-only**: they are never rewritten, migrated or relabelled, so a partial
invocation cannot refresh a failed pair's previously published evidence and nothing
is deleted automatically. Because run evidence is immutable, there are no
"directories from earlier runs" to confuse with this invocation's results.

## `equity report backtest`

Runs one strategy under one cost regime, prints the result summary, and publishes
that run's immutable evidence — but no FindingCards:

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
| `--output-dir`, `-o` | `data/findings` | Findings root for the run and attempt evidence |
| `--verbose`, `-v` | off | Debug logging |

Unknown strategy or regime names exit `1` with the accepted values listed.

Each invocation writes **one immutable run** under `<findings root>/runs/` and
**one attempt record** under `<findings root>/attempts/` — the same layouts and
writers an arena invocation uses (ADR-0025 Decisions 2, 3 and 5):

- the run's scope is that strategy, that cost regime **with the fee/tax pair the
  engine was actually configured with**, the result's window and ticker universe,
  its initial cash, and the strategy parameters that ran;
- the attempt record carries one `completed` outcome naming the published run ID,
  its run directory and the shared evaluation status (`evaluated` | `no_trades` |
  `ineligible`), plus a note naming this command.

`--output-dir` is the findings root for both, and the command writes **no**
`<strategy>__<regime>/` directory, no flat `benchmark__equity.parquet` and no flat
card. Its output is therefore latest-success evidence rather than a mutable legacy
directory: `/backtests` reports this run as the slug's `last_success`, with the
last attempt's status and age next to it. The command emits no FindingCards, so
`/findings` is unaffected.

**Costs.** A single report applies the static `COST_REGIMES` pair for
`--cost-regime`. The per-market `realistic` defaults belong to `equity arena run`,
which knows the venue; here the manifest and the attempt record both record the
pair the engine actually applied, never a re-resolved default.

**An undated result is refused.** When the backend supplies no session dates
(`BacktestResult.equity_dates is None`) the command prints why and exits `1`
without writing anything — no run directory, no attempt record and no legacy
directory, because a calendar is never invented from a curve's length. A backtest
that raises behaves as before: a clean error, exit `1`, nothing written.

**Evaluation cohort.** The attempt's evaluation status comes from the classifier
the arena uses, given the size of the frame the engine actually loaded. An engine
that loaded nothing through its own loader (a preloaded frame) has no recorded
size; the command then classifies the requested tickers and says so in the attempt
notes, instead of assuming an eligible universe.

## Artifact layout under `data/findings/`

`equity arena run` writes two immutable layouts and **no legacy path**:

- `runs/<UTC-timestamp>Z-<short-scope-hash>/` — one directory per requested
  completed `(strategy, cost regime)` pair (the run evidence);
- `attempts/<UTC-timestamp>Z-<short-attempt-hash>/` — one directory per
  invocation (the attempt evidence), owning the three comparison cards and the
  benchmark copy.

The pre-E01 **legacy** `<strategy>__<regime>/` directories, the flat
`benchmark__equity.parquet` and the flat `<card-id>.json` cards are a frozen,
read-only compatibility view over evidence that already exists. They stay where
they are and stay readable, and an arena invocation never creates, refreshes or
overwrites one — so a legacy card can never silently inherit a later run's curve.

### Immutable dated runs

The arena writer finalises **one immutable run per requested completed
(strategy, cost regime) pair** under `data/findings/runs/`. Each run directory
records that pair's own scope (strategy, the cost regime **with the fees and taxes
that regime actually applied**, window, universe, initial cash, the strategy
parameters the run was built with, and a bounded non-secret `config_digest` of the
markets and market-cost override in force) and its own dated curve.

The recorded fees/taxes are the ones the run was actually costed with: the arena
records the resolved pair it configured the engine with on each completed run, and
the writer records that value. It never re-resolves a market default, so a
`market_costs` override or a per-venue default reaches the manifest verbatim.

A run's ID is minted from that scope alone, and the applied fees/taxes are part of
it: two invocations that differ only in the resolved fee or tax ratio are different
runs, never one scope under two cost applications. Costs are recorded per regime in
the manifest's `costs` and serialized for the ID in regime-name order, so the same
scope written with a different costs ordering keeps one ID.

A run whose backend curve carries no dates is **skipped, never repaired**: no run
directory and no manifest claim dates for it (no calendar is invented from the
curve length), the skip is reported with its reason in the
`arena_run_evidence_skipped_undated` log event and in the writer's evidence
summary, and the other runs of the same invocation still finalise. No legacy
artifact is written for that pair either. A run that does not record the
costs its engine applied (a legacy or hand-built outcome) is skipped the same way —
reason `applied costs not recorded`, reported in the writer's evidence summary and
the `arena_run_evidence_skipped_costs_not_recorded` log event — because a manifest
that guesses a default cost application is worse than one that admits the costs are
unknown; the cards are still written.

If finalising one run fails, every remaining run is still attempted, the cards
already written are kept, no partial run directory is published, and the command
reports the write failure and exits `1`.

A run written by `finalise_run` (`backtesting/evidence.py`, E01 write side) lands
in `data/findings/runs/<UTC-timestamp>Z-<short-scope-hash>/` and is never
overwritten by a later invocation of the same scope (a collision appends `-2`,
`-3`, … to the directory name):

| Path | Contents |
|---|---|
| `equity.parquet` | Equity curve as `(date, equity)` — the backend's real session dates, gaps included |
| `drawdown.parquet` | Drawdown fraction below the running peak, same sessions |
| `metrics.json` | Strategy, regime, and the full `BacktestResult` metrics dict |
| `trades.json` | Trade-by-trade execution log |
| `<card-id>.json` | FindingCards produced by that run, when any (the arena writer keeps its three comparison cards in the invocation's **attempt** directory, because they compare runs rather than describe one) |
| `manifest.json` | The run manifest: scope-only run ID (its scope includes the applied fees/taxes, which the ID re-verifies against), scope, costs, execution timing, seed, code commit + dirty-digest, non-secret config digest, provenance (`synthetic`, `real`, `mixed`, or `unknown`), the universe basis (`canonical` or `ticker_only`), read-seam input snapshots (`name`, `sha256`, `source`, Delta `version`), per-artifact checksums, metric availability |

The manifest is written last: artifacts are re-read, their dates **and values**
checked against the run's `BacktestResult`, and hashed first; then the run is
published into a directory claimed with an atomic `os.mkdir` that never replaces an
existing directory, so a failed run leaves no run directory behind and a same-scope
name collision takes the `-2`, `-3`, … discriminator instead of overwriting. A
curve carrying a null or non-finite (NaN/`±inf`) equity or drawdown value is
**refused before that comparison**, with the artifact and row named: a finalized run
certifies *measured* evidence, so such a point is never dropped, zero-filled or
reindexed into a published curve. A non-finite *metric* (e.g. `sharpe_ratio`) does
not refuse the run: `metrics.json` records it as `null` with a `non_finite`
availability entry in the manifest — never a persisted `NaN`, never a coerced `0` —
and the validator parses that file strictly, refusing any `NaN`/`Infinity` token or a
`null` metric whose recorded availability disagrees, while a non-finite *trade* or
card value is a data defect rather than an unavailable measurement, so it **refuses
the run** with the artifact, entry and field named (`trades.json entry 3 field
'price' is non-finite`) instead of being sanitised to `null`, `null` trade fields
(an open position's pnl/exit price) stay `null`, and the validator parses
`trades.json` and every recorded card JSON strictly as well.
`validate_run_directory` reports any run that is incomplete or tampered with. Dates
always come from the backend; a run whose backend supplied no timestamps stays
undated and is not written in this layout.

### Immutable attempt directories

Every invocation publishes exactly one attempt directory under
`data/findings/attempts/` (E01b.1), even when nothing completed:

| Path | Contents |
|---|---|
| `benchmark__equity.parquet` | An immutable copy of the fixed-share benchmark curve (only when the curve is non-empty) |
| `<card-id>.json` | The three comparison cards (`strategy-comparison`, `cost-regime`, `vs-benchmark`), written strictly (a non-finite card value refuses the attempt) |
| `attempt.json` | Written **last**: protocol, the inputs-only `attempt_id`, the requested pairs, one `PairOutcome` per pair (`completed`, `failed`, `missing`, `contradictory`, `unevidenced`), `status`, one `run_write_failures` entry per run-evidence write that raised, timestamps, window, universe, initial cash, markets, the applied `costs` whose fees/taxes are known, artifact checksums and notes |

The `attempt_id` hashes the **inputs** — protocol, the requested pairs, the
canonical universe, the exact date window, the initial cash, the markets and the
applied costs — in one documented field order. It is not an invocation instant:
two identical invocations share an `attempt_id` and get distinct directories (the
timestamp plus a `-2`, `-3`, … discriminator live in the directory name).
`status` is exactly what the recorded outcomes justify:

| Status | Meaning |
| --- | --- |
| `complete` | every requested pair is `completed` (and there was at least one) |
| `partial` | at least one `completed` and at least one non-`completed` |
| `zero_completion` | no `completed` outcome (includes a request that produced nothing, whose `notes` name why nothing ran) |
| `write_failed` | at least one run-evidence write raised; it may coexist with completed outcomes |

`write_failed` is enforced, not just labelled: the record must carry at least one
`run_write_failures` entry (`strategy`, `cost_regime`, `error_type`, `reason`) naming
the write that raised, every other status must carry none, and each entry's pair must
be one the invocation requested. A record whose pairs all completed can therefore
never claim `write_failed`, and a `write_failed` record can never omit its failure.

The **last attempt** is deliberately distinct from the **last successful
comparable run**: a failed attempt cannot erase the last success, and the age of
that success stays visible next to the attempt's status. `equity arena run` prints
a bounded, read-only "previous evidence" block for the requested pairs before it
starts (last successful run age, or `none`, and the last attempt's status/age), and
`GET /backtests` exposes both as `last_success` and `last_attempt` (see the
[Read API](20260811-read-api.md)).

When a run-evidence write fails, the attempt is still published with
`status='write_failed'` (naming the failed pair and the runs that did finalise)
**before** the command reports the failure and exits `1`, so the failure is durable
evidence rather than lost terminal output.

#### Evidence references on attempt-bound cards

`build_finding_cards` emits *unbound* refs in the frozen legacy vocabulary
(`<strategy>__<regime>/` and `benchmark__equity.parquet`), because those are the
names the cards have always used. Those names identify a mutable legacy location,
so the arena writer binds them to immutable evidence before persisting a card
(`bind_cards_to_evidence`, ADR-0025 Decision 8). Only a ref that is exactly
`<strategy>__<regime>/` for a pair this invocation **requested** is rewritten: the
candidate set is explicit, never inferred from string shape, so a slash-terminated
token that merely looks like a legacy slug (`custom__token/`, `run:abc__def/`,
`C:\reports\foo__bar/`) or a path below a pair directory
(`momentum__zero/extra.json`) is preserved verbatim. A card in an attempt directory
never carries a legacy slug path:

| Written ref | What it identifies (display-only in E01b) |
| --- | --- |
| `run:<run_id>` | The validated run this invocation finalised for that pair. Manual resolution only: read `<run_id>` (or the `run_dir`) from the pair's `completed` outcome in the attempt's `attempt.json`, then read the run manifest at `data/findings/runs/<run_dir>/manifest.json`, which is the address of the artifact set (`equity.parquet`, `metrics.json`, …) and re-derives (and re-checks) that same ID. |
| `attempt:<attempt_id>/benchmark__equity.parquet` | The attempt's **own** immutable benchmark copy under `data/findings/attempts/<attempt dir>/`, never the frozen flat legacy `benchmark__equity.parquet`. Manual resolution only: `<attempt_id>` is the input hash recorded in that attempt's `attempt.json`, and the file sits in the same attempt directory. Written only when the curve is non-empty; otherwise the ref is `unevidenced:benchmark` (next row). |
| `unevidenced:<strategy>__<regime>` | A pair that was requested but has no immutable run (an undated curve, unrecorded applied costs, or a failed write), so there is no run ID to name. Manual resolution only: the attempt record's matching `PairOutcome` records the reason. |
| `unevidenced:benchmark` | The attempt's own immutable benchmark artifact is **absent**: the benchmark curve was empty, so the attempt directory holds no `benchmark__equity.parquet` and the ref names that absent artifact instead of a path (never `attempt:<id>/benchmark__equity.parquet`). `benchmark` here is a reserved name, not a `<strategy>__<regime>` slug. Manual resolution only: there is nothing to open — an absent benchmark copy is a fact about the comparison, not a file to look up. |

These four tokens are **display-only references** in E01b: a stable,
machine-readable vocabulary on the card, not a link any code dereferences. **No
shipped code path resolves them yet** — `/findings`, `/findings/{card_id}` and
`evidence_view.finding_cards` return the card with its refs verbatim — so an
operator follows the manual path in the table. A resolver is not part of this
change.

Duplicate refs are collapsed preserving first-seen order, and any other ref is
preserved verbatim: an unknown pointer is reported honestly rather than guessed at
or dropped. Frozen legacy cards keep their verbatim legacy refs — they are read, never
rewritten, so the binding rules above describe new attempt-bound cards only.

### Provenance stays `unknown` (E01-6)

Provenance
stays `unknown` on every path, snapshots or not. E01c does record the canonical
universe and the read-seam input snapshots, but neither is a provenance upgrade: a
snapshot proves *which bytes or Delta versions were read*, not *which price source
produced the marks*. Nothing in the lake persists a price-source marker yet — that
is open item **E01-6**, with its own ADR and storage stage — so a run that cannot
name its source stays `unknown` and is never promoted to `real`. Nothing here
fabricates one, and E01 is not complete until E01-6 lands.

### Canonical universe and basis

Each manifest records `universe_basis`: `canonical`
when every `universe` entry is an `InstrumentKey` display key
(`market/MIC/percent-quoted-symbol`) that parses back through the identity model, or
`ticker_only` when at least one entry does not (a bare ticker, a malformed display
key). The basis is **derived by parsing, never guessed**: no MIC is filled, no venue
is inferred from a ticker, and configuration cannot resolve one (ADR-0016). An
explicit `canonical` claim over an unparseable entry is refused. The basis is not a
run-ID scope field — the identity values themselves differ between the two bases, so
the ID changes anyway. `equity arena run` derives its universe from the one loaded
frame's identity columns; `equity report backtest` uses the display keys its engine
recorded while loading (falling back to the requested tickers, `ticker_only`, when
the engine was handed a preloaded frame).

### Read-seam input snapshots

Snapshots are captured *inside* the read that produced
the data, never looked up afterwards (a version read after the computation describes
the table as it is then):

| Snapshot `name` | `source` | `sha256` | `version` |
|---|---|---|---|
| `price_marks:<market>` | `lake:delta` | digest of the immutable `(table path, version)` reference | the Delta version read (as text) |
| `price_marks:<market>` | `lake:parquet` | one aggregate digest over the sorted parquet file set actually enumerated | `null` |
| `action_events:<market>` / `action_coverage:<market>` | `lake:delta` | same reference digest | the Delta version read |

The Delta digest is **not** a content hash of every data file in the table: it binds
the immutable `(path, version)` reference the read consumed. A non-Delta lake costs
an extra read pass: the loader enumerates the same `<root>/**/*.parquet` glob the scan
uses, sorted, before the scan, streams each file, and records one aggregate digest per
market over `sorted [(relative_path, bytes, file_sha256)]` — so adding, removing or
rewriting a parquet file changes it, non-parquet files are excluded, and the result
does not depend on enumeration order. Two documented residuals follow from that: a
file-set change *after* enumeration is not detected, and **in-memory fixture action
inputs have no read seam at all**, so they record nothing rather than a fabricated
hash. An absent action table records no version either. A run built from
`preloaded_data` read nothing here, so its manifest carries no snapshots.

### Legacy directories (frozen, read-only)

`write_backtest_report` (`backtesting/report.py`) produces the files below. It is
the frozen-legacy fixture/legacy helper and **no command uses it**: both
`equity arena run` and `equity report backtest` publish immutable run + attempt
evidence, and no shipped command creates or overwrites a `<strategy>__<regime>/`
directory. This layout is keyed by `<strategy>__<regime>`, not by run date:
re-running the legacy writer with the same names overwrites the previous
artifacts, which is exactly why it is frozen.

| Path | Producer | Contents |
|---|---|---|
| `<strategy>__<regime>/equity.parquet` | legacy helper | Equity curve (t, equity) |
| `<strategy>__<regime>/drawdown.parquet` | legacy helper | Drawdown fraction below running peak |
| `<strategy>__<regime>/metrics.json` | legacy helper | Strategy, regime, and the full `BacktestResult` metrics dict |
| `<strategy>__<regime>/trades.json` | legacy helper | Trade-by-trade execution log |
| `benchmark__equity.parquet` | legacy helper (arena before E01b.1) | Fixed-share equal-weight buy-and-hold equity curve |
| `<card-id>.json` | legacy helper, `ml compare/ablate` | One FindingCard per file |

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
| `evidence_refs` | `list[str]` | Pointers to backing artifacts: on attempt-bound arena cards a bound `run:`/`attempt:`/`unevidenced:` token (see [Evidence references on attempt-bound cards](#evidence-references-on-attempt-bound-cards)), on legacy cards the verbatim legacy paths |
| `run_date` | `date` | When the comparison was run |
| `scope` | `dict` | Reproducibility metadata: tickers, window, costs, seed |

Cards live in the attempt directory that produced them, not as flat
`<id>.json` files at the findings root, so each card is bound to one immutable
invocation. `/findings` resolves an id to the newest attempt-bound card and falls
back to a frozen legacy flat card, so the three ids and their verdict semantics are
unchanged for consumers.

A `negative` verdict is a valid — and encouraged — result, not an error:
"no strategy beats buy-and-hold after costs" is a strong portfolio conclusion.
`write_finding_card` (`findings/writer.py`) validates the model before touching
disk: the ML comparison/ablation writers persist a flat `data/findings/<id>.json`,
while arena cards are written into the attempt directory that produced them.
`load_finding_cards` reads back the flat (legacy) cards best-effort, skipping
corrupt files so one bad card never breaks consumers; `/findings` resolves ids
attempt-bound cards first (see the layout section above).

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
