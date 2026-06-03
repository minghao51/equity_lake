# Config Guide

This directory contains the checked-in configuration for `equity-lake`.

The files here do not all serve the same purpose. Some define the shared system
defaults, some define the broad data universe, and some define user-facing
subsets for analysis.

## At A Glance

- `settings.yaml`: default application settings such as storage paths, schedule,
  and dashboard output.
- `tickers.yaml`: the canonical ticker universe and metadata used by ingestion
  and filtering.
- `watchlist.yaml`: a smaller set of names you currently care about for signal
  scanning.
- `signals.yaml`: how signal generation behaves.

## Why `tickers.yaml` And `watchlist.yaml` Are Separate

The separation is intentional:

- `tickers.yaml` answers: "What can this project ingest and classify?"
- `watchlist.yaml` answers: "What do I want signal output for right now?"

That difference matters because the ingestion pipeline and the signal scanner do
not operate at the same scope.

### `tickers.yaml`

Use `tickers.yaml` when you want to define or update the shared market universe.

It includes:

- market structure such as `us`, `cn`, and `hk_sg`
- ticker metadata such as `exchange`, `sector`, and `tags`
- operational controls such as `active` and `priority`
- reusable groupings for ingestion filters

Examples of changes that belong in `tickers.yaml`:

- add `AMD` to the US universe so the daily pipeline can ingest it
- mark a ticker `active: false` to stop fetching it
- add a new tag like `semiconductor`
- raise the priority of a name you want fetched first

Typical commands that depend on this file:

```bash
uv run equity-daily --list-tickers
uv run equity-daily --groups faang
uv run equity-daily --tags blue-chip --min-priority 8
uv run equity-pipeline --markets us
```

### `watchlist.yaml`

Use `watchlist.yaml` when you want to define the smaller set of tickers to scan
for buy/sell/hold signals.

It includes:

- a watchlist name and description
- a short list of selected tickers
- optional lightweight groups such as `tech` or `ev`
- optional metadata such as a benchmark

Examples of changes that belong in `watchlist.yaml`:

- add `AMZN` because you started following it this month
- remove `TSLA` because it is no longer in your portfolio
- create a `dividend` group for reporting
- change the benchmark from `SPY` to `QQQ`

Typical commands that depend on this file:

```bash
uv run equity-signal scan
uv run equity-signal scan --watchlist config/watchlist.yaml
uv run equity-signal scan --format md --output signals.md
```

## Practical Example

If you want the system to ingest `AMD` every day but you do not want signal
output for it yet:

1. Add `AMD` to `tickers.yaml`
2. Do not add it to `watchlist.yaml`

If you want signal output for `AAPL`, `MSFT`, and `NVDA` from the already
ingested universe:

1. Keep those names present and active in `tickers.yaml`
2. Put only those names in `watchlist.yaml`

## Why This Split Helps

- The ingestion universe can stay broad without making every signal scan noisy.
- Portfolio or research focus can change frequently without editing the main
  market metadata file.
- Shared metadata stays centralized in one place instead of being repeated in
  multiple watchlists.

## `signals.yaml`

`signals.yaml` is separate for a different reason: it configures signal
generation behavior, not ticker membership.

Edit it when you want to change things like:

- whether backtest, sentiment, or ML generators are enabled
- thresholds for buy/sell behavior
- lookback windows
- aggregation boosts

Example:

```bash
uv run equity-signal scan --config config/signals.yaml
```

## `settings.yaml`

`settings.yaml` holds the default app-level settings for runtime behavior.

Examples:

- where data and logs are stored
- the default GitHub Actions schedule
- the dashboard output directory

Environment variables with the `EQUITY_LAKE_*` prefix can override matching
settings from `settings.yaml` at runtime.

## Editing Rules Of Thumb

- Edit `settings.yaml` for application defaults.
- Edit `tickers.yaml` for the shared ingestible universe and ticker metadata.
- Edit `watchlist.yaml` for your current portfolio or research subset.
- Edit `signals.yaml` for how signal generation works.
