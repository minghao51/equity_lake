# Pipeline Monitoring and Alerting

`equity monitor` is the post-run health check. Run it after ingestion or the
full pipeline (manually or from cron) to catch stale markets, null-heavy data,
and feature gaps before they compound.

> **No log-file check.** An earlier `Pipeline Logs` check scanned
> `monitor_pipeline.log` / `ingest_daily.log` / `sync_from_s3.log` — files no
> component in this repository writes — so it always passed. It was removed
> rather than repointed at another guess; alerting on artifacts nothing
> produces is false confidence. Logs are structlog JSON on stderr (cron
> redirects capture them).

## `equity monitor`

```bash
dotenvx run -- uv run equity monitor
dotenvx run -- uv run equity monitor --verbose
dotenvx run -- uv run equity monitor --output-json site/health-report.json
dotenvx run -- uv run equity monitor --max-age-days 3 --null-threshold 2.0
```

| Flag | Default | Description |
|---|---|---|
| `--max-age-days` | from `settings.monitoring.max_age_days` (2) | Maximum data age before a market counts as stale |
| `--null-threshold` | from `settings.monitoring.null_threshold_pct` (5.0) | Null % of close/volume that raises a data-quality alert |
| `--output-json` | `logs/health-report.json` | Path for the JSON report |
| `--verbose`, `-v` | off | Debug logging, plus per-market quality detail |

After health checks complete, the JSON report is written for healthy **and
unhealthy** results; `--output-json` overrides the default location and missing
parent directories are created. Unhealthy results exit 1 only after report
persistence and monitor cleanup. Unexpected execution/report errors also close
the monitor and fail the command; a failed save is not a new successful report.

The report preserves `alerts`, `metrics`, and `timestamp`, and adds `healthy`
and `check_results` (named boolean outcomes). Zero recorded checks never claim
`healthy: true`. Existing readers of the original fields remain compatible.

## Health checks

The monitor runs four checks and prints a pass/fail table; alerts are
dispatched through the alerter (printed once, as `[SEVERITY] message` lines):

| Check | What it verifies |
|---|---|
| Data Freshness | Latest `date` per price market is within its freshness expectation (per-market override or `--max-age-days`), measured against the market's most recent trading day |
| Data Quality | Null close/volume percentages over the last 7 days stay under `--null-threshold` |
| Feature Store | `data/lake/03_gold/features/` has rows in the last 7 days and its latest date is fresh; a read failure fails the check |
| Unstructured Freshness | `bronze/raw_articles`, `silver/processed_articles`, `silver/sec_extractions` are non-empty and within their per-table expectation (news/articles daily, SEC extractions quarterly) |

Any per-check exception (corrupt table, unreadable Delta log) fails that
check and raises an alert — a check can never mask a failure as healthy.
Alerts are deduplicated by `(check, target)` so repeated runs of a long-lived
monitor don't pile up one alert per market per check.

Market coverage is registry-driven: freshness and quality iterate a price-market
registry (`monitoring/health.py`), so every market registered there — currently
all five equity markets — is monitored automatically, and a newly registered
market is picked up without touching the checks.

## Feeding the dashboard

`equity dashboard build` renders a Health page from the saved health report.
The loader looks for `health-report.json` in the dashboard output directory,
then `site/`, then the canonical `logs/` location — so running `monitor`
before `dashboard build` is sufficient:

```bash
dotenvx run -- uv run equity monitor
dotenvx run -- uv run equity dashboard build --output-dir site
```

or explicitly export into the site during the daily flow:

```bash
dotenvx run -- uv run equity monitor --output-json site/health-report.json
```

## Alerting

When any check produces alerts, the monitor dispatches them through an
`Alerter` from `monitoring/alerting.py`, with severity `"error"` when a check
failed and `"warning"` when alerts exist but all checks passed:

- **ConsoleAlerter** — prints each alert as `[SEVERITY] message` to the
  terminal (this is what you see in cron logs). Alerts are printed exactly
  once: the monitor computes and dispatches, the CLI renders the pass/fail
  table.
- **WebhookAlerter** — POSTs a JSON payload `{"severity", "alerts", "metrics"}`
  to a URL via `httpx` (10s timeout, `Content-Type: application/json`).
  Delivery runs through the shared tenacity retry helper (3 attempts,
  exponential backoff); a final failure is logged as an error
  (`webhook_alert_delivery_failed`) and never fails the monitor run.
- **CompositeAlerter** — fans one alert batch out to several alerters; a
  failure in one alerter doesn't block the others.
- **`build_alerter(webhook_url=None)`** — the factory used by the monitor; it
  always includes `ConsoleAlerter` and adds a `WebhookAlerter` when a webhook
  URL is supplied.

The CLI passes `settings.alerting.webhook_url` to `build_alerter()`. Configure
`alerting.webhook_url` privately or set `EQUITY_ALERTING__WEBHOOK_URL` through
your secret manager/dotenvx. With no URL, delivery remains console-only.
This sends **pipeline-health alerts**, not watchlist BUY/SELL notifications.
The generic JSON payload is not automatically a channel-native message.

Treat webhook URLs as credentials. Dashboard/settings show, get and export
redact them; delivery still uses the real private value. Alert retry/failure
logs retain safe error type/status diagnostics, not request URLs. CLI logging
suppresses debug/info wire logs from httpx, httpcore, urllib3 and requests even
under `--verbose`. Other provider exception strings are not covered by a
universal value-level secret scrubber. If a webhook was previously published,
inspect prior artifacts and rotate it; redaction does not retract old exports.

## Scheduling

The `schedule` group in `config/settings.yaml` carries the intended cadence
(`enabled`, `cron`, `timezone`; the shipped default cron is `0 1 * * 1-5`).
Typical weekday cron entries pair monitoring with the pipeline. These host-cron
times use the host scheduler's timezone; changing application settings does not
convert them. GitHub Actions cron uses UTC, and the schedule-sync utility copies
only the cron expression (it does not convert `schedule.timezone`). Compose's
`daily` service is one-shot with no restart loop and must be invoked by an
external scheduler, not left running as a timer:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity pipeline >> logs/cron-pipeline.log 2>&1
0 20 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity monitor >> logs/cron-monitor.log 2>&1
```

Common overrides:

- `EQUITY_MONITORING__MAX_AGE_DAYS`
- `EQUITY_MONITORING__NULL_THRESHOLD_PCT`
- `EQUITY_MONITORING__MARKET_MAX_AGE_DAYS` — JSON dict of per price-market
  freshness overrides, e.g. `{"krx_equity": 3}`
- `EQUITY_MONITORING__TABLE_MAX_AGE_DAYS` — JSON dict of per-table freshness
  expectations for the unstructured check; defaults to
  `{"bronze/raw_articles": 2, "silver/processed_articles": 2, "silver/sec_extractions": 95}`
  (news daily, SEC quarterly; add an entry around 35 for a transcripts table
  if one is ever split out)
- `EQUITY_SCHEDULE__CRON`
- `EQUITY_SCHEDULE__TIMEZONE`

See the [pipeline user guide](pipeline.md) for the full configuration
precedence rules.

## Troubleshooting

```bash
uv run equity monitor --help
```

- A market flagged stale right after a holiday is expected — freshness is
  measured against the market's own trading calendar; raise `--max-age-days`
  or set `EQUITY_MONITORING__MARKET_MAX_AGE_DAYS` if the alert is noisy.
- The feature-store check fails when `data/lake/03_gold/features/` is absent
  **or unreadable** — run `equity pipeline` (or the feature stage) to
  (re)build it. A corrupt directory must fail the check, not read as healthy.
- Empty (but present) unstructured tables raise stale/empty alerts; directories
  that don't exist yet are skipped silently — check the RSS/
  Reddit/StockTwits/transcripts ingestion path before assuming a price-source
  problem. SEC extractions only alert past the quarterly expectation
  (95 days by default), so a filing-gap is not alert fatigue.

## Related

- [Pipeline user guide](pipeline.md) — daily operation and configuration
- [CLI reference](20260406-cli-reference.md) — full command and flag index
- [Dashboard hosting](20260406-dashboard-hosting.md) — building and serving the static site
