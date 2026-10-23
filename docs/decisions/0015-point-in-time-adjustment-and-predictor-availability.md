# ADR-0015: Point-in-Time Adjustment and Predictor Availability

**Status:** Accepted decision; implementation and offline-contract review accepted 2026-10-18. Operational readiness is not implied.

**Recorded:** 2026-10-16; owner acceptance recorded 2026-10-17.

**Deciders:** Repository owner

**Related:** ADR-0011 (corporate actions), ADR-0014 (label availability), C03,
R06 and R15 in the [repository review](../finding/20261016-repository-review-and-equity-alerts.md)

## Context

ADR-0011 keeps raw Bronze prices immutable and makes adjustment an explicit
read-time choice. Existing consumers do not yet share one adjustment and
availability contract. Enrichment zeros, missing rows, unverified publication
clocks, and revised historical values can otherwise be mistaken for observed
neutral values or point-in-time knowledge. This ADR records the accepted
consumer contract; it does not authorize data rewrites, historical repair, or
model retraining.

## Earlier proposal alternatives (retained for traceability)

The pre-approval draft considered keeping raw prices, using split-only prices,
or separating split-adjusted predictors from a dividend-inclusive total-return
outcome; it also considered an explicit raw fallback and zero-compatible
feature fills. The accepted selection below is the shared split-only transform,
fail-closed adjusted reads, and missing-aware nulls with coverage/age flags.
The earlier alternatives are not open options after owner acceptance.

## Decision

The owner approved this decision with: **“Approve 1-9 and C10 implementation”.**

1. **Split-only shared read-time price semantics.** Use one shared, versioned
   read-time transformation for predictors, labels, barrier prices, and price
   returns. Keep raw Bronze immutable. Cash dividends are excluded; this is a
   split-adjusted price contract, not a total-return contract.
2. **Fail closed on action uncertainty.** Unknown, incomplete, stale, missing,
   or corrupt action coverage, and unverifiable historical action availability,
   make the adjusted result unavailable/error for the affected scope. Never
   fall back to raw values as if adjusted, and do not silently emit a raw
   result from an adjusted read. Empty action data alone is not proof of
   complete no-action coverage.
3. **Missing-aware, versioned predictors.** Preserve unavailable predictor
   values as null and add versioned coverage and age flags. A missing required
   input excludes the affected sample/evaluation; a missing optional input
   does not block a price-only rule. Measured neutral values remain distinct
   from missing observations.
4. **Source clocks and venue-session mapping.** Use verified publication or
   source-availability time and map it to the first observable session for the
   instrument's venue. Unknown clocks remain unknown; do not substitute
   ingestion time, calendar date equality, or fabricated historical vintages.
5. **Source-family availability rules.** News uses a seven-calendar-day
   coverage window and requires at least three distinct scored articles.
   Social is seven-calendar-day context and is not counted as news articles.
   SEC state may carry for at most 95 calendar days only when trusted source
   availability is established. Macro data without historical vintage
   context is excluded from strict predictors (it may not be represented as
   point-in-time-known macro information).
6. **Model compatibility.** Training, inference, labels/repair, and backtests
   must use compatible adjustment and missingness/availability semantics.
   Model metadata must identify those contracts. Unknown or incompatible
   legacy models are unavailable for this path; there is no silent legacy
   model reuse and no automatic retraining. Any retraining is separately
   authorized.
7. **Implementation boundary.** Version the relevant feature/label contract
   when implemented, coordinate readers/catalog/model compatibility, and keep
   raw Bronze unchanged. Acceptance of this ADR does not authorize a
   production migration, repair, or backfill.

## Implementation status and compatibility notes — snapshot 2026-10-17

The current Gold contract is `FEATURE_SCHEMA_VERSION = 7` and
`availability_schema_version = 4`; source action-coverage proof schema version
is `2`. Persisted feature proof includes its own
`action_coverage_market`, `action_coverage_mic`, and `action_coverage_symbol`
identity, plus `action_coverage_source`, `action_coverage_start`,
`action_coverage_end`, `action_source_available_at`,
`action_source_valid_until`, `action_coverage_status`, and
`action_coverage_schema_version`. The row also carries `action_coverage` and
`action_source_age_days`. Proof identity must match the feature row's own
`(market, MIC, symbol)`; the verified source clock maps to the first observable
venue session and the source-issued expiry must cover the decision-session
close. Legacy rows/models lacking the required identity, adjustment,
availability, or coverage proof are unavailable on strict adjusted paths; no
silent raw fallback, automatic rewrite, or retraining occurs.

The implemented transform is split-only and read-time: it excludes cash
dividends and leaves raw Bronze price values unadjusted. Source freshness uses
the source-issued `source_valid_until`; no fixed expiry is guessed. The shipped
yfinance corporate-action path currently provides neither a verified source
publication clock nor complete source-issued coverage/expiry proof. It therefore
does **not** establish usable adjusted-price coverage. Adjusted consumers remain
unavailable until verified proof is supplied; this is not a claim about native
provider availability. Source families without verified clocks/identity remain
unavailable rather than being filled with neutral values.

At the 2026-10-17 status snapshot, implementation was under independent
review and the integrated C10 review/offline gates were not yet accepted. The
2026-10-18 supplement below supersedes that review status. No production repair,
backfill, rewrite, or model retraining is authorized.

## Compatibility, migration, and rollback

Compatibility work is report-first and scoped to deterministic synthetic
fixtures and temporary copies. It must demonstrate the selected contract across
feature generation, labels and label repair, barriers, rolling point-in-time
backtests, training, inference, and replay before any separately authorized
migration. Do not automatically inventory or repair production history. Raw
Bronze remains unadjusted; unknown action or predictor provenance remains
unknown and follows the fail-closed/required-input rules above. Readers and
models with unknown or different semantics are unavailable, not silently
compatible. Preserve snapshots and prior compatible readers/model artifacts for
any separately authorized rollback; never collapse identities or write adjusted
values over raw Bronze. Production source fetch, migration, repair, backfill,
and retraining each require separate owner authorization.

## Acceptance evidence required

The implementation and independent review must include local deterministic
synthetic tests for:

- Known split arithmetic across predictors, labels, barriers and price returns,
  exclusion of dividends, and proof that raw Bronze input remains unchanged.
- Missing, incomplete, stale, empty-but-unverified, corrupt, conflicting, and
  unreadable action coverage, plus an action whose source-availability time is
  after the decision cutoff; adjusted reads fail closed without raw fallback.
- Off-session source publication and mapping to the first observable venue
  session; unknown historical clocks stay unknown.
- Missing feed versus measured neutral input; null/coverage/age semantics;
  required versus optional inputs; news article distinctness/window; social
  context; trusted SEC carry age; and exclusion of macro without vintage
  context from strict predictors.
- Agreement among all selected consumers and rejection of unknown or
  incompatible legacy model/schema provenance without automatic retraining.

Tests use synthetic fixtures and temporary copies only. They do not repair
production history, backfill providers, or train production models.

## Consequences

Raw Bronze and ADR-0011's explicit read-time adjustment boundary remain in
force. Consumers may not advertise an adjusted result when action coverage or
historical availability is unverified. Missing predictor coverage is visible
and machine-readable; strict evaluation excludes required missing inputs and
unknown clocks. At the 2026-10-17 status snapshot, implementation review and
C10/offline verification remained pending; see the dated supplement below. No
production operation is authorized by this decision.

## Implementation/offline-contract acceptance supplement — 2026-10-18

The accepted split-only read-time transform, fail-closed proof/availability
handling, missing-aware versioned predictors, and `source_valid_until`
propagation were independently reviewed with the C10 offline contracts and
accepted. The final reviewer returned no C10 must-fix. The native yfinance
action path still lacks qualifying publication/coverage/expiry proof, so strict
adjusted reads remain unavailable; this acceptance neither fabricates source
proof nor establishes native provider availability. No production migration,
repair, backfill, retraining, provider fetch, or live-readiness claim is
included.
