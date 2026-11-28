# Research proposal: can unstructured data improve equity predictions?

**Status:** Exploratory draft, 2026-11-27. No new source, model fit, provider call, document
export, purchase or pipeline change is authorized. A negative or underpowered result is valid.

## What already exists

The [unstructured ingestion design](../plans/20260614-unstructured-data-ingestion-design.md)
records the news/social, transcript and SEC collection paths. The present enrichment DAG
(`src/equity_lake/features/dag/enrichments_04.py`) has opt-in, identity- and clock-aware news and
SEC joins; it leaves legacy LLM article scores and analyst ratings unavailable without the required
metadata. The existing `ml/ablation.py` compares precomputed enriched and technical frames. Reuse
its comparison pattern, not its broad feature selection or date-only alignment as proof of a
text-specific, multi-instrument result.

The bottleneck is evidence, not ingest plumbing. `sources/sec_fulltext.py` handles 10-K/10-Q
sections and currently sets `published_at` from the filing date at midnight. That value is not
evidence of first public availability. `sources/transcripts.py` can fall back to midnight on the
requested date; call start time also does not prove when the full transcript was public. None of
these fallback values can enter a strict predictor. Working adapters and synthetic tests do not
establish historical coverage, snapshot rights or payable provider tiers.

## Options and recommended order

| Option | What to test | Trade-off |
| --- | --- | --- |
| Price-only | Keep the technical baseline | Cheapest and reproducible; no direct measure of disclosure content. |
| SEC 10-Q filings (recommended first) | Compare risk-factor text with the issuer's previous eligible 10-Q; measure novelty and uncertainty | Bounded inputs and auditable document keys; filings are sparse, seasonal and often follow earnings, and the current fetcher's clock is insufficient. |
| Qualified timestamped news | Test headline tone or novelty, above event counts and technical features | Denser than filings; syndication, edits, entitlement and publication latency are harder to prove. |
| Earnings-call transcripts | Compare prepared remarks and Q&A tone or guidance changes | Richer disclosure; requires a licensed snapshot with first transcript publication and revision times, not just the call date. |
| Embeddings or LLM extraction | Test whether richer representations beat simple text measures | More cost, reproducibility and training-data leakage risk; not a first milestone. |
| Social posts | Test attention separate from sentiment | High manipulation, identity, deletion and historical coverage burden; defer. |

## Phase 0 — one SEC feasibility packet, no fitting

**Question:** Can we reconstruct what text a close-of-session decision could have seen, with enough
coverage to test whether the text adds anything beyond price and knowledge that a filing occurred?

1. Start with synthetic ordinary, after-close, early-close, holiday, amended and missing-clock
   cases. For a later authorized real packet, pick one US venue or a small explicitly named
   US universe and fixed date window, with a scope not used for tuning other arms. Use one 10-Q
   section and a deterministic extractor. Don't ask for bulk text or a news subscription yet.
2. Ask for original document bytes or a rights-permitted immutable extract, accession/document ID,
   section, source URI and digest, receipt/processing provenance, verified issuer-to-instrument
   mapping effective at that date, source-issued acceptance/dissemination metadata and time zone,
   revision history, and retention/derived-use rights. Verify that any acceptance timestamp is a
   defensible bound on public visibility for that chosen document and feed; use verified public
   dissemination time if later. Also require action coverage with qualified clocks/expiry for
   the strict split-only price predictors and labels. A good text clock cannot fix a weak price basis.
3. Report covered issuer-sessions, distinct accepted filings, missing clocks/sections, revision and
   dedup rates, event-cluster concentration, and projected sample size by fold. Distinguish no
   observed filing in qualified coverage from missing coverage or parser failure. Approve a coverage,
   power floor and cost ceiling before seeing outcome results. If the scope cannot support a
   meaningful comparison, stop with a feasibility finding rather than widen or mine outcomes.

## Phase 1 — preregister a text-specific ablation, only if Phase 0 passes

**Working hypothesis:** 10-Q risk-factor novelty and uncertainty add predictive information about
five-session split-adjusted return direction beyond technical features and filing metadata alone.
Freeze the exact label, decision/entry/exit timing, universe, folds, minimum practical effect and
cost assumptions before fitting; execute no earlier than the next tradable opportunity consistent
with the decision cutoff. This is a separate exploratory track. The
[R02-D2 preregistration](20261124-01-r02-d2-preregistration-plan.md) stays frozen and blocked on its
own gates; do not inspect its protected scope or insert text into its feature set.

| Arm | Features | What the comparison measures |
| --- | --- | --- |
| A | Price/volume technical baseline | Performance without disclosure features. |
| B | A plus eligible filing indicator, age and coverage | Value of knowing when an event happened; controls for timing and coverage. |
| C | B plus frozen risk-section token novelty and finance uncertainty score | Incremental contribution of text content (primary C vs B). |

- Start with the same regularized logistic model and tuning budget for all arms. Fit vocabulary,
  scaling, imputation and tuning on training folds only; use a versioned fixed dictionary or one
  constructed without future data. Score the same `(market, MIC, symbol, decision session)` in
  each arm. Do not drop rows from only one arm; freeze eligibility and report exclusions,
  overall results and the eligible filing cohort. Use purged/embargoed walk-forward splits over
  sessions, with label availability and overlapping horizons enforced across instruments.
- Use paired out-of-sample Brier-score improvement as the primary metric; report log loss,
  calibration and net-of-cost performance under identical execution assumptions as secondary
  diagnostics. Use paired session blocks that keep instruments together for uncertainty; check
  fold and issuer concentration. Predeclare the practical effect and uncertainty gate, and
  report null, negative and underpowered results without trying more feature families on the same
  assessment period. Keep any final holdout untouched until separately authorized.
- Confidence requires invariants: adding a future filing or amendment cannot change an earlier
  row; after-cutoff text cannot affect that session; issuer mappings cannot cross listings;
  duplicate copies cannot inflate coverage; missing text stays null rather than neutral; shuffled
  labels or a predeclared placebo must not generate a claimed edge. Time-align using the venue
  calendar and the verified first observable session under
  [ADR-0015](../decisions/0015-point-in-time-adjustment-and-predictor-availability.md). Record
  source visibility and processing latency separately; don't backdate a processed feature or
  imply that offline replay proves live readiness. A clockless row is not repaired by lagging it.

## Outputs and stop conditions

Produce a value-free coverage report first. If a fit is later approved, retain a private frozen
manifest with dataset/digest and rights references, parser/dictionary version, feature snapshot,
clock contract, eligibility rules, fold membership and cost assumptions. Record paired metrics and
refusals in research evidence under [ADR-0025](../decisions/0025-immutable-research-evidence-and-run-manifests.md),
not in Gold, Platinum, public artifacts, live signals or the default model. Keep `promoted=false`.
No external model gets raw text without separate rights and owner approval. If a future encoder or
LLM cannot demonstrate a compatible training cutoff and versioned reproducible extraction, its
historical result is exploratory, not credible strict point-in-time evidence.

**Next decision:** approve a synthetic-only availability and coverage audit; name a candidate
US scope and deadline for the source-evidence review. No provider contact or data transfer is
needed for the synthetic work. Come back with the feasibility report and a separate run proposal,
not an automatic experiment or rollout.
