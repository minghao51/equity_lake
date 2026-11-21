# H00 — owner decisions on the sanitized demo-lake page (2026-11-21)

The owner delegated these decisions ("close the last 2 … with your best judgement"). They
adopt the review's recommended option where one exists, and they bind the H00 build.
Source: `docs/reports/20261117-h00-sanitized-demo-lake-page-review.md` §7.

1. **Backtest metrics on synthetic data.** Allowed, only with the fixed disclaimer and no
   per-symbol performance: *"Computed on synthetic demo data; not indicative of any real
   strategy's performance and not investment advice."* Aggregate cohort/arena figures are
   permitted; per-symbol returns are not.
2. **Predictions display.** Counts and schema inventory only. No fixture prediction rows,
   no `model_version = "fake"` content, no probability values.
3. **Hosting visibility.** No deployment in this stage: build and preview on loopback only.
   Publishing (and therefore any public operator identity) requires a separate explicit
   approval and is not performed here.
4. **Producer/retention.** Rebuilt on demand by the operator; staleness threshold 30 days;
   a stale page shows the last build date and a "stale — rebuild due" banner instead of
   freshly presented numbers. No scheduled CI deployment.
5. **Composition.** One composed sanitized artifact for the demo lake; the catalog text is
   included only through the allowlist and filtered per item 8. The catalog site is not
   redeployed by this stage.
6. **Exact wording.** Adopt the review's banner and disclaimer strings: synthetic-data
   banner, the item-1 performance disclaimer, and "demo data only — not investment advice".
7. **Operator-run status.** Demo-lake content plus prose only. No health, update or
   operator-run status appears, which also removes the B3/B4 classes by scope.
8. **Catalog text.** Excluded: no vendor/source descriptions ("Source: yfinance …",
   akshare, efinance, JPX/KRX), no provider attribution text, no dataset descriptions that
   name a vendor. Only allowlisted field names, counts, schemas and dates appear.

Every build must prove these by checks, not promises: serialized bytes contain none of the
forbidden classes (absolute paths, `.env` values, ledger/watchlist content, real-slice rows,
digests-as-identifiers, raw alert text, `pipeline_results` content, vendor strings), and the
canonical dashboard output is unchanged.
