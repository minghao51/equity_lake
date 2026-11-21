# H00 licensing + privacy review — sanitized demo-lake public page

**Recorded:** 2026-11-17
**Status:** Review COMPLETE. The sanitization contract in §4 is PROPOSED as the H00 build
gate; every recommendation here is a recommendation until the owner adopts it. Publication
of any page is blocked until §5 and §7 are resolved.
**Scope:** task H00 ("sanitized public hosting") as listed in
[the open queue](../handoffs/20261117-01-post-d01-landing.md) and
[specification §8](../plans/20261102-01-portfolio-and-research-implementation-spec.md).
**Verdict:** A sanitized demo-lake page can be built **without touching any
vendor-derived value**, because everything the current exporter reads resolves under the
synthetic demo root `data/lake/` and the real root is never discovered
([ADR-0030](../decisions/0030-second-real-source-lake-root.md) decisions 2 and 4). The
page must not be built or published with the current exporter and the current workflows
unchanged — §2 and §3 name five verified blockers.

## 1. Data classification

Verdicts: **as-is** (publishable unchanged), **sanitized** (publishable only through the
§4 allowlist), **never** (must not appear in any public artifact). Evidence for each class
was read in this checkout on 2026-11-17 (commands in §8 of this document's completion
note; on-disk facts stated inline).

| Artifact class | Location (verified) | Verdict | Governing authority / evidence |
| --- | --- | --- | --- |
| Synthetic OHLCV prices | `data/lake/01_bronze/market_data/*` (783 parquet files) | sanitized | [ADR-0020](../decisions/0020-synthetic-demo-lake-as-backtestable-surface.md): seeded rows "must never be presented as market data"; they carry `identity_provenance = current_ticker_config` (verified in row values) |
| Synthetic corporate-action coverage proofs | `data/lake/02_silver/corporate_action_coverage/us_equity` (1 parquet + `_delta_log`) | as-is | [ADR-0020](../decisions/0020-synthetic-demo-lake-as-backtestable-surface.md) decision 2: the proof is labelled `source = synthetic_demo_seed` (verified: `coverage_status=complete`, `source_valid_until=2026-10-28`, 50 symbols). The label must stay rendered with the rows |
| Synthetic corporate-action *events* | none exist in `data/lake/` (verified: `02_silver` holds only `corporate_action_coverage`; synthetic prices are split-free by construction, ADR-0020) | sanitized *if ever seeded* | same as prices; the demo generator is the source of truth (ADR-0020 decision 2) |
| Synthetic provenance labels | `identity_provenance`, `source`, `action_coverage_source` columns/rows | as-is | [ADR-0027](../decisions/0027-row-bound-price-source-provenance.md) (`synthetic\|real\|unknown` vocabulary, `core/price_provenance.py`); ADR-0020. These *are* the stamps the page must show |
| Gold features | `data/lake/03_gold/features` (252 files) | sanitized | Derived from synthetic prices only: macro columns (`vix`, `treasury_10y`, `tips_yield`, `dxy`) are all `None` and `macro_available = 0` in the sampled partitions (verified); `data/lake/01_bronze/macro` does not exist. Row labels carry `action_coverage_source = synthetic_demo_seed` (verified). If macro ingestion is ever enabled (`FRED_API_KEY` exists in `.env`), this verdict reverts to "drop macro columns first" |
| Platinum predictions | `data/lake/04_platinum/predictions` (59 files) | sanitized + owner gate | Verified rows are fixtures: `model_version = "fake"`, `model_mode = "unknown"`. No ADR-0021 row-bound forecast provenance columns (`forecast_provenance_version`, `model_artifact_sha256`, …) are stored on these rows, so no forecast-proof claim may accompany them. Display itself is open question §7-2 |
| Signals | `data/signals/date=2026-10-27` | sanitized | Verified sample is synthetic-derived (`price = 328.69` from the demo lake; `sentiment_score`/`article_count` are `None` here). Any signal whose sentiment fields are populated from real news/sentiment tables becomes vendor-derived and leaves the allowlist |
| Health reports | `logs/health-report.json` | sanitized | Alert strings embed absolute filesystem paths (verified — §3, blocker B3). [Spec §8](../plans/20261102-01-portfolio-and-research-implementation-spec.md) wants failure/stale-data display; only allowlisted status fields may be shown |
| Pipeline results | `logs/pipeline_results_*.json` (none present now; writer at `src/equity_lake/cli/commands/pipeline.py:88-93`) | sanitized — raw file never | Contents are operator run metadata: `results["scope"]` carries `tickers`, `instruments` (model dumps), `requested_instruments`, `trading_date` (`src/equity_lake/pipeline.py:701-715`). [ADR-0025](../decisions/0025-immutable-research-evidence-and-run-manifests.md) decision 10: only allowlisted projections |
| Update history | `data/update_history.parquet` (absent in this checkout; reader `src/equity_lake/dashboard/_common.py:68-82`) | sanitized | Rows carry `source`, `symbol`, `updated_at`, `records` — an operator activity timeline; allowlist per ADR-0025 decision 10 |
| Research/arena findings and run manifests | `data/findings/` (verified present: `strategy-comparison.json`, `vs-benchmark.json`, `cost-regime.json`, `benchmark__equity.parquet`, 9 strategy×cost dirs) | sanitized + owner gate | Metrics are computed on the synthetic demo lake (ADR-0020 makes it backtestable), but manifests also record "the code commit plus a digest of the dirty diff" and run timestamps (ADR-0025 decision 3) — those fields are operator-level and dropped. Showing performance-shaped numbers is open question §7-1 |
| Private portfolio ledger and satellites | `data/portfolio/portfolio.sqlite3`, `marks.json`, `statement.csv`, backup/migration copies (ADR-0031) | **never** | [ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md) decisions 3 and 11, [ADR-0031](../decisions/0031-p00-4-ledger-backup-restore-migration-compaction.md), and the [user guide](../user-guide/20261103-private-portfolio-ledger.md) ("do not copy it into an export, a dashboard build, a notebook output or a public page"; "`data/portfolio` is private: do not commit it, export it, or point a dashboard build at it") |
| Watchlists and tracking state | `config/watchlist.yaml` (verified present), `data/watchlists/`, `data/tracking/` | **never** | [ADR-0018](../decisions/0018-private-watchlist-events-and-delivery.md) (private watchlist events); the P03 console reads the private `watchlist.yaml` for *held* flags (`src/equity_lake/dashboard/panels.py:35-36,226-243`) — held flags are ownership-adjacent |
| Real-source slice | `data/lake_real/` (1,861 files; verified sample rows are real yfinance prices, e.g. AAPL close 185.64 on 2024-01-02) | **never** | [ADR-0028](../decisions/0028-d01-real-source-slice.md) decision 2: "Lake rows and any row-derived price series stay local. No Pages/static export, public API projection, W&B projection or artifact publication may carry vendor rows or vendor-derived price values." [ADR-0030](../decisions/0030-second-real-source-lake-root.md) decision 4: not cataloged |
| D01 probe artifacts | `data/d01/probe-2026-11-14/` (`prices.parquet`, `actions.parquet`, `report.json`) | **never** | Vendor rows and vendor-derived values from the D01 probe — same ban as the real slice (ADR-0028 decision 2) |
| Secrets / env material | `.env` (verified present, 21 keys incl. `WANDB_ENTITY`, `SEC_USER_AGENT`) | **never** | [ADR-0004](../decisions/0004-single-settings-env-contract.md) raw-seam rule; ADR-0025 decision 3 ("Secrets are never hashed or copied into a manifest") |
| Generated catalog | `data/catalog.jsonl` (tracked in git; `.gitignore` line 23-25 carve-out verified via `git check-ignore`) | as-is, claims caveat | Schema metadata with lake-relative paths. Caveat: its `us_equity_ohlcv` description says "Source: yfinance (US), akshare (CN), …" — that text must not sit next to synthetic rows on a public page (§6) |
| Other local artifacts | `data/equity_data.duckdb`, `data/models/`, `data/backups/`, `data/signal_scans/`, `data/sample/` | **never** for H00 | Outside the H00 allowlist; none is reviewed as publishable. `data/sample` is `equity bootstrap sample` mock output that strict readers reject (ADR-0020 consequences) — no reason to publish it |

## 2. Licensing findings

**What ADR-0028 forbids.** Decision 2 of
[ADR-0028](../decisions/0028-d01-real-source-slice.md) is the binding text: the yfinance
slice is "local-only, no redistribution"; "Lake rows and any row-derived price series stay
local. No Pages/static export, public API projection, W&B projection or artifact
publication may carry vendor rows or vendor-derived price values. H00 remains gated on a
privacy/licensing review, not merely a privacy one." Decision 5's interim rule ("no real
price row may enter `data/lake/`") and decision 6 ("Demo rows never certify real rows")
keep the demo root clean of vendor material.

**Can a demo-lake page be built without touching vendor-derived values at all? Yes — plainly
yes.** Verified reasons:

1. The exporter's dataset routes are fixed to the canonical demo root: `MARKET_DATASETS`
   (`src/equity_lake/dashboard/_common.py:23-27`) resolves through `market_dir()` to the
   `*_DIR` constants under `LAKE_DIR = PROJECT_ROOT/data/lake` (`core/paths.py:37-42`).
   ADR-0030 decision 4 states the consequence explicitly: "catalog-driven surfaces
   (dashboard `MARKET_DATASETS`, catalog readers) do not see the real root".
2. The real root is selected only by explicit `--lake-dir`; there is no discovery, no
   fallback, no "both roots" run (ADR-0030 decision 2). Nothing in
   `src/equity_lake/dashboard/` references `REAL_LAKE_DIR` (verified by grep).
3. Every demo-lake value the page would show is generator output: prices and features are
   synthetic-derived (§1, macro columns verified empty), and the coverage proofs are
   labelled `source = synthetic_demo_seed` by the generator itself (ADR-0020 decision 2).

If a page could only be built by including real rows, it must not be built — that
condition does not arise here, and the §4 build check makes it fail closed if it ever does.

**Residual licensing risk (the reason this section exists).** The currently deployed Pages
pipeline is *not* the demo-lake page and does not honour the above by construction:
`.github/workflows/pages.yml` runs `uv run equity pipeline --save-results || true` on a
weekday cron. On a fresh CI checkout `data/lake/` is empty (`.gitignore` keeps `data/*` out
of git except `data/catalog.jsonl`), so that step *fetches real vendor data* — the
`us_equity` route is yfinance, free-tier, no key required (ADR-0028 decision 1) — into the
runner's `data/lake/`, and `equity dashboard build` then publishes summaries computed over
that landing. Row counts and latest dates are not "price values", so this may sit outside
the letter of decision 2's price-value clause, but it publishes vendor-derived metadata and
produces a page that is not the sanitized demo-lake page H00 describes. Under ADR-0028's
"local-only, no redistribution" position this is the single largest licensing exposure and
must be closed before any H00 build (§5, blocker B5).

## 3. Privacy findings

Leak inventory in the current export path (`DashboardExporter.write()` → `site/
dashboard-data.json` + five HTML pages). Each row names the exact field, the evidence, and
the removal required by §4.

| # | Leak | Exact field / evidence | Removal |
| --- | --- | --- | --- |
| B1 | Absolute filesystem path (username + workspace layout, e.g. `/home/howt/work/equity_lake/data/lake/…`) | `datasets[].path` in `dashboard-data.json`, written as `str(path)` at `src/equity_lake/dashboard/_common.py:41,53,56,64` and rendered in the `datasets.html` "Path" column at `src/equity_lake/dashboard/exporter.py:330`. `PROJECT_ROOT` is absolute (`core/paths.py:37`, `Path(__file__).resolve().parents[3]`) | Drop `path` from the allowlist entirely; never serialize it (ADR-0025 decision 10: absent fields are dropped, not hoped harmless) |
| B2 | Private-directory path | `config.console.private_dir` inside the full settings dump written at `src/equity_lake/dashboard/exporter.py:79` (`"config": _redact_secrets(self.settings.model_dump())`). `ConsoleSettings.private_dir` (`src/equity_lake/core/settings.py`) is the directory holding the private `marks.json`/`statement.csv`. The redaction regex `_SECRET_KEY_RE` (`src/equity_lake/core/logging.py:22`) matches `private_key`, **not** `private_dir` | Drop the `config` block; if any settings are shown, use a fixed field list (project name/version/environment, schedule cron/timezone) |
| B3 | Absolute paths in health alerts | `health.alerts` rendered in `index.html`/`health.html` and stored in `dashboard-data.json`. Verified in `logs/health-report.json`: DuckDB error strings contain `/home/howt/work/equity_lake/data/lake/01_bronze/market_data/cn_ashare/**/*.parquet` verbatim | Never render raw alert/error text; map to the canned status vocabulary (`dashboard/components.py` `Notice` states) with fixed strings |
| B4 | Operator run metadata | `updates.pipeline_results[].content` is a raw passthrough of `logs/pipeline_results_*.json` (`exporter.py:115-118`, rendered `exporter.py:398-399`): includes `scope.tickers`, `scope.instruments`, `requested_instruments`, `trading_date`, stage/ML dumps. A symbol scope can mirror the operator's holdings | Never serialize `content`; an allowlisted status summary (stage, success/skipped, counts) only |
| B5 | Unapproved, unattested automated publish | `.github/workflows/pages.yml`: `schedule: cron "0 1 * * 1-5"` and `push: branches [main]` build **and deploy** (`actions/deploy-pages@v5`) with no approval step; `|| true` masks pipeline/monitor failures so a stale or wrong-root build publishes silently. `.github/workflows/catalog-deploy.yml` deploys a second artifact to the **same** `github-pages` environment — the two deployments replace each other, which [spec §8](../plans/20261102-01-portfolio-and-research-implementation-spec.md) explicitly forbids | §5 gate: single composed workflow, offline build, required-reviewer environment |
| B6 | Run/operator timestamps | `generated_at` (`exporter.py:68`), health `timestamp`, `updates.history[].updated_at`, run-manifest timestamps and `model_trained_at` (ADR-0025 decision 3, ADR-0021 E1) | Only the page's own build timestamp survives (§4); private-run and training timestamps are dropped |
| B7 | File digests as correlatable identifiers | `source_digest`/`content_digest` (ledger, ADR-0031 property table), `price_source_sha256` (real rows, ADR-0027), `model_artifact_sha256` and the dirty-diff digest (ADR-0025 decision 3) | Not in the allowlist; the §4 scan fails the build on any unapproved 64-hex string |
| B8 | Ledger artifacts | `data/portfolio/portfolio.sqlite3`, `marks.json`, `statement.csv`, backup copies (ADR-0031) | Never read by the build; §4 scan proves absence of the names and the account label |
| B9 | Watchlist / held flags | `config/watchlist.yaml`, `data/watchlists/`, `data/tracking/` (ADR-0018; read seam at `dashboard/panels.py:226-243`) | Never read by the build; §4 scan proves absence |
| B10 | `.env` material | `.env` verified present with 21 keys (`AWS_*`, `DEEPSEEK_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY`, `OPENROUTER_API_KEY`, `REDDIT_*`, `SEC_USER_AGENT`, `STOCKTWITS_*`, `WANDB_API_KEY`, `WANDB_ENTITY`, …). Note `WANDB_ENTITY` and `*_USER_AGENT` do **not** match `_SECRET_KEY_RE` | The build has no dotenv in scope; the §4 scan compares every `.env` value (in memory, never logged) against the artifact |

Not removable by sanitization, therefore an owner fact rather than a defect: the Pages site
is hosted under the operator's GitHub account (remote `git@github.com:minghao51/equity_lake.git`,
verified), so the account name is public by construction if the Pages site is public.

## 4. The sanitization contract (PROPOSED — binding for any H00 build once adopted)

Shape follows [ADR-0025](../decisions/0025-immutable-research-evidence-and-run-manifests.md)
decision 10: "A sanitized allowlist should govern exported projections — fields absent from
the allowlist are dropped rather than serialized and hoped to be harmless."

### 4.1 Allowlist — the page may contain exactly

- Page chrome: title, subtitle, navigation, and static prose written by the builder.
- The synthetic provenance banner (§4.2) on every page.
- Dataset inventory rows for demo-root datasets only: `name`, `rows`, `symbols`,
  `latest_date` (no `path`, B1).
- Coverage-proof summary: `source`, `coverage_status`, `coverage_start`, `coverage_end`,
  `source_valid_until` — rendered together with the banner label.
- Schema/feature inventory: column names, schema versions (`feature_schema_version`,
  `identity_schema_version`, `coverage_schema_version`), row/symbol counts.
- Optionally, and only per the owner's §7 answers: backtest/fixture metrics with their
  mandatory disclaimer.
- Build metadata: one UTC build timestamp labelled "page built at", the builder's code
  version string, and the sanitization report (§4.3).
- Failure/staleness state of the demo pipeline, as fixed status strings (no raw error text).

Everything else is dropped at the single serialization sink. No second output path may
write payload material (the JSON data file and the HTML pages consume the same allowlisted
payload).

### 4.2 Required stamps (verified names)

- Every page, first viewport: the banner **"Synthetic demonstration data — generated
  offline by `equity demo seed`; not market data"** (wording is a recommendation; exact
  text is owner-approved per §7-6). Anchored in ADR-0020: the demo lake "must never be
  presented as market data".
- Every section touching prices, features, predictions or signals, rendered adjacent to
  the numbers: `source = synthetic_demo_seed` and `identity_provenance = current_ticker_config`
  (the two verified provenance labels; ADR-0020 decisions 1-2).
- The machine-readable payload must carry `"provenance": "synthetic"` plus the
  `source_valid_until` of the coverage proof, so a consumer can audit what the page shows.
- The page must never claim row-level `price_provenance` for demo rows: the seeded Bronze
  files carry **no** `price_provenance`/`price_source`/`price_source_sha256` columns
  (verified schema), even though the generated `data/catalog.jsonl` `us_equity_ohlcv`
  description advertises them. Surface the discrepancy honestly or say nothing.

### 4.3 Provable absence — checks, not promises

The build fails closed unless all of these pass; the machine-readable result is written as
`sanitization-report.json` inside the artifact and is itself subject to the same scan.

1. **Root attestation.** Every input path must resolve under `data/lake/` (or the fixed
   prose sources); the build asserts `core.paths.is_real_source_root(...) is False` for
   every lake input and refuses `data/lake_real/`, `data/d01/`, `data/portfolio/`,
   `data/watchlists/`, `data/tracking/`, `.env` as inputs at all (ADR-0030 decision 2
   containment logic is the precedent).
2. **Synthetic attestation.** Before any number is published, each contributing table's
   labels are checked: `source == "synthetic_demo_seed"` on the coverage proof,
   `identity_provenance == "current_ticker_config"` on price rows,
   `action_coverage_source == "synthetic_demo_seed"` on features. A missing or different
   label aborts the build (ADR-0028 decision 6: demo rows never certify real rows — and
   here, no uncertified row is published).
3. **Post-build negative scan** over every file in the artifact directory; any hit fails
   the build:
   - absolute-path patterns: `/home/`, `/Users/`, `C:\`, the literal `PROJECT_ROOT`, and
     the repository checkout path;
   - real-slice fingerprints: the strings `lake_real`, `d01`, `probe-`, and known real-row
     canary values taken from `data/lake_real` (e.g. the float rendering of the AAPL
     2024-01-02 close `185.63999938964844` and the MSFT close `370.8699951171875`, both
     verified in the `date=2024-01-02` partition);
   - ledger artifacts: `portfolio.sqlite3`, `marks.json`, `statement.csv`, `ledger_events`,
     `source_digest`, `content_digest`, and the configured account label;
   - secret scan: every value of `.env` (loaded into memory for comparison, never logged)
     must be absent, plus generic `API_KEY`/`token=` shapes;
   - digest scan: any 64-hex string not explicitly allowlisted (none are).
4. **Workflow gate.** The scan runs as a required step *before*
   `actions/upload-pages-artifact`; on failure no artifact is uploaded.

## 5. The publish gate

1. **Offline build only.** The H00 artifact is composed from the local demo-lake snapshot
   and static prose, with no source credentials in the job and no ingestion step.
   Recommendation: the H00 producer is a *new* workflow; the scheduled `equity pipeline`
   run in `pages.yml` is rejected as the H00 producer (it lands vendor rows on the runner
   — §2) and `pages.yml`'s auto-deploy must be retired or gated before the first H00
   publish. This is a change to existing files; it is described here and scheduled by the
   owner.
2. **Loopback preview only.** The built artifact is reviewed from a loopback static serve
   (`127.0.0.1`); no public preview environment, no PR-facing deploy
   (`pages.yml` currently builds on `pull_request`; only `deploy` is gated — keep it that
   way and keep previews off the Pages project).
3. **Explicit human approval before publication.** Publish runs only via
   `workflow_dispatch` against a GitHub `pages` environment with required reviewers — the
   reviewer (the owner) inspects the artifact and the sanitization report, then approves.
   No `schedule:` and no `push:` trigger may deploy the H00 artifact.
4. **Rollback if a leak is found.** Unpublish immediately (disable the Pages site or
   redeploy the last artifact known to pass the scan), delete the leaked artifact and the
   deployment record, treat the leak as a disclosure incident (assume caches and forks
   copied it), rotate any secret the scan says appeared (by contract none should), re-run
   the §4.3 checks, and record the incident in `docs/reports/`.
5. **Who/what blocks an accidental publish.** Today: **nothing** — `pages.yml` deploys on
   a weekday cron and on push to `main` with no approval (verified), so the first gate work
   is removing that path. Afterwards: (a) the required-reviewer environment is the standing
   human block; (b) the §4.3 scan failing closed is the standing machine block; (c) branch
   protection prevents merging a workflow that drops the gate or re-adds a trigger; (d) the
   single-workflow composition rule (one composed artifact, per
   [spec §8](../plans/20261102-01-portfolio-and-research-implementation-spec.md)) retires
   `catalog-deploy.yml`'s independent deploy to the same `github-pages` environment, where
   deployments currently replace each other.

## 6. Claims discipline

**The page may state**

- "Synthetic demonstration data generated offline (`equity demo seed`)" — with the ADR-0020
  framing that the generator is the source of every assertion.
- Dataset/schema/feature inventory and coverage-proof values, each with its
  `synthetic_demo_seed` label.
- That the demo lake is a backtestable *surface* (ADR-0020) — as architecture description,
  not as performance.
- The page's own build time and the demo data's stated window (`source_valid_until`).

**The page must never state**

- Any real-market or live-market claim: "real data", "live prices", "actual market data",
  "real performance" — ADR-0020: the seeded rows "must never be presented as market data".
- Any performance/return figure on real markets. (Synthetic-data backtest metrics are open
  question §7-1; until answered, no metrics appear.)
- Anything described as `unadjusted` (ADR-0028 decision 4 supersedes that wording for real
  rows; the demo page has no price-basis claim to make at all).
- Vendor attribution of displayed rows. The catalog description "Source: yfinance (US),
  akshare (CN), efinance (HK/SG), JPX/KRX APIs" (`data/catalog.jsonl`,
  `us_equity_ohlcv`) must not appear next to synthetic rows; it describes ingestion
  routes, not the provenance of what the page shows.
- Any imminent-availability claim ("live data soon", "real data next quarter", "W&B
  integration coming"): [spec §8](../plans/20261102-01-portfolio-and-research-implementation-spec.md)
  — "No deployment authorization is implied by documentation/tests" — and a freshness or
  availability claim the page cannot support is exactly the failure mode the spec warns
  about.
- Any operator- or account-level fact (paths, run cadence, holdings, symbol scope).

## 7. Open questions for the owner

Decisions this review cannot make:

1. **Backtest metrics on synthetic data.** May the page show arena output
   (`data/findings/strategy-comparison.json`, `vs-benchmark.json`, cost-regime results),
   and with which mandatory disclaimer (e.g. "computed on synthetic data; not indicative of
   any real strategy's performance")? The review's recommendation is: allowed only with a
   fixed disclaimer and no per-symbol performance; rejected alternative: showing them
   bare, which reads as a performance claim.
2. **Predictions display.** May fixture predictions (`model_version = "fake"`) appear at
   all, or does the page show only feature/schema inventory? Recommendation: counts and
   schema only.
3. **Hosting visibility.** Is the target Pages site public? If yes, the operator's GitHub
   account name is unavoidably public (§3, end).
4. **Producer/retention.** Who rebuilds the page, how often, what staleness threshold, and
   what the page shows when stale (spec §8 requires deciding this first).
5. **Composition.** Does one composed artifact replace both current deployments
   (dashboard + catalog site), or does the catalog site stay separate? Spec §8 says one
   composed artifact; the catalog's vendor-attribution description text (§6) then needs
   rewording or filtering — owner call.
6. **Exact wording.** Approval of the banner and disclaimer strings in §4.2/§7-1.
7. **Operator-run status.** May sanitized health/update status appear at all (it describes
   the private environment's activity), or is the page demo-lake content plus prose only?
   Recommendation: demo-lake content plus prose only; it also removes blockers B3/B4 by
   scope rather than by filtering.
8. **Catalog text.** Confirm that the `us_equity_ohlcv` "Source: yfinance …" description
   is excluded from the synthetic page (this review recommends exclusion).

## What this review does not claim

- It does not verify what any *currently deployed* Pages site contains; the GitHub
  deployment state is outside this checkout. The workflow files are what would deploy.
- It does not decide that backtest metrics or predictions are safe to publish — those are
  §7 items.
- It does not authorize any build, deployment or change to existing files. Blockers B1-B5
  are described for the owner to schedule.
- "Publishable as-is" verdicts assert licensing/privacy clearance only; the claims rules
  in §6 bind every published line regardless of verdict.
