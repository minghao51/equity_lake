# ADR-0022: Future Forecast-Provenance Envelope Versions

**Status:** Accepted (2026-10-31). Codifies the behaviour that already ships for
`forecast_provenance_version > FORECAST_PROVENANCE_VERSION`: a recognised-but-unsupported
envelope stays visible and non-usable with its version printed verbatim, the write
boundary refuses to emit a version it cannot validate, and version 2 follows the
amend-not-edit procedure below. No code change is implied by acceptance; ADR-0021
remains immutable and in force for version-1 rows.

**Recorded:** 2026-10-31

**Deciders:** Repository owner

**Related:** ADR-0021 (row-bound forecast provenance and prediction usability),
ADR-0017 (bounded additive evolution and replay), ADR-0014 (label lifecycle and
availability), [20261030-01 R10](../handoffs/20261030-01-postpreload-findings-and-remaining-issues.md),
[20261030-02](../handoffs/20261030-02-row-bound-forecast-provenance-implementation.md)

## Context

ADR-0021 §2.1 defines `forecast_provenance_version` as the envelope that versions the
proof set, and §2.6 requires a new ADR plus a version bump to change either the
evidence set or its rules. It never defines what a version *newer* than the reader
knows means. That is not a hypothetical: the predictions Delta table outlives any
single binary. A pinned read snapshot, a rolling deploy, a rebuilt wheel, or a table
merged by a newer worker can all put a version-2 row in front of a reader whose
`FORECAST_PROVENANCE_VERSION` is 1.

The three existing readings are all wrong for that row:

- **`historical`** means "the evidence is complete and internally consistent, but
  bound to a *superseded* contract". A newer contract is not older history;
  reporting a v2 row as historical would misreport a future envelope as a past one,
  and would tell a consumer the row's rules are known and merely dated.
- **`unknown` with a null version** means the documented "no proof" row written by
  inference paths that cannot supply evidence. The row does carry a proof — one
  this reader cannot interpret — so collapsing it into the legacy null state loses
  the evidence and destroys the only signal a consumer has that the table has moved
  ahead of the reader.
- **`invalid`** means the row contradicts itself. A future-envelope row is not
  self-contradictory; this reader simply lacks the rules to judge it. Calling it
  corrupt would blame the row for the reader's age.

The implementation already takes the only remaining reading: the row is
*recognised but unsupported*, reported as `unknown` with
`forecast_provenance_version_is_unsupported_by_this_reader:<version>`
(`api/deps.py::_forecast_provenance_status`). That behaviour was shipped as a safe
interim — never usable, version reported verbatim — because ADR-0021 cannot be
edited to say so. It is pinned by
`tests/unit/test_api.py::test_unsupported_future_envelope_version_is_unknown_not_historical`.
The write boundary is already closed in the mirror direction:
`validate_predictions` accepts only a null version or the current one, so this
binary cannot emit an envelope it cannot validate.

Two things were therefore undefined: whether that interim is the contract, and who
moves first when a version 2 is proposed.

## Decision

1. **Meaning.** A row whose stored `forecast_provenance_version` is an integer
   greater than this reader's `FORECAST_PROVENANCE_VERSION` is a
   *recognised-but-unsupported envelope*: a proof written under a contract newer
   than this binary understands. It is not a corrupt row, and it is not "no proof".
   It stays visible, stays non-usable, and its version number is reported verbatim.
   It is never downgraded to the legacy null/`no_supported_row_bound_forecast_proof_is_persisted`
   state and never silently upgraded to `valid`/`current_usable`.

2. **Reader contract (current behaviour, now binding).** For such a row the read API
   reports, exactly as it does today:
   - `api_provenance_status = "unknown"` with
     `api_provenance_reason = "forecast_provenance_version_is_unsupported_by_this_reader:<version>"`,
     where `<version>` is the stored integer printed unchanged (no clamping to the
     current version, no rewrite to the nearest known version).
   - `api_usability_status = "not_current_usable"` with the existing reason
     `forecast_provenance_unknown`; no new usability reason token is introduced.
   - The row keeps every stored field and is returned by every read that selects
     it. Status never filters rows (ADR-0021 §2.4).
   - The other axes keep their independent derivations. The envelope versions the
     proof set and its rules, not `prediction_semantics_version`; a v2-envelope row
     is not made `established` by carrying an unknown envelope, and it is not
     made unusable *by* the envelope if its E4 columns are current and its
     semantics are otherwise established — it is unusable because provenance is
     `unknown`.
   - **Precedence.** The E1–E4 rules this reader does know are evaluated first, on
     the shared columns. A future-envelope row that also violates one of them — a
     missing, mistyped, or contradictory shared column — is reported `invalid`
     with `row_bound_forecast_proof_contradicts_the_row:<violations>`; the
     unsupported-envelope reason is used when the row is otherwise consistent with
     the version-1 rules. A non-integer version is `invalid`
     (`forecast_provenance_version_is_not_an_integer`), not an unsupported
     envelope. Either way the row is never `valid` and never `current_usable`, so a
     consumer that only reads usability cannot observe the difference.

3. **Write boundary moves first, and refuses.** `validate_predictions` (via
   `_forecast_provenance_violated`) requires per row either a null
   `forecast_provenance_version` (a documented "no proof") or
   `forecast_provenance_version == FORECAST_PROVENANCE_VERSION` with a complete,
   self-consistent E1–E4 set. A version *newer* than the code knows fails that
   check and rejects the batch (fail closed, ADR-0021 §2.5);
   `_forecast_provenance_columns` only ever writes the current version or null. No
   binary may write an envelope it cannot validate: forward compatibility is
   asymmetric — a reader tolerates a newer envelope, a writer never produces one.

4. **Evolution procedure for version 2 (and every later version).** A new version is
   a new ADR amending ADR-0021's evidence set; 0021 is never edited, and it stays
   accepted because version-1 rows keep its semantics.
   - **Amend, do not edit.** The new ADR restates the version-2 evidence set: which
     E1–E4 columns are added, removed, re-defined, or re-typed, the new derivation
     rules, and what a version-1 row still means. It is linked from the ADR index
     (and from this record's index neighbours); no cross-link is added inside
     0021 itself. The index row for 0021 may gain a one-line note that a later
     record amends the evidence set for v2 — the index is not the record.
   - **Schema and catalog bump.** `FORECAST_PROVENANCE_VERSION` (and
     `PREDICTION_SEMANTICS_VERSION` if semantics change) are bumped in
     `core/schemas.py`; new columns arrive by nullable additive evolution
     (ADR-0017), never by rewriting v1 rows; the catalog is regenerated with
     `uv run equity catalog-generate` in the same change.
   - **Reader before writer.** A reader that understands v2 ships first and must
     keep deriving v1 rows exactly as ADR-0021 requires. Only after that reader is
     deployed (and, for the preload path, after the pinned refit boundaries are
     known to be writing under the new contract) may a writer emit v2 rows. This
     ordering is what makes the mixed table ordinary rather than corrupt.
   - **Downgrade rule.** Once any v2 row exists, an older reader must keep
     reporting `unknown` with
     `forecast_provenance_version_is_unsupported_by_this_reader:2`. It must never
     say `valid`, never say `historical`, never say `current_usable`, and the
     downgrade path must never rewrite, downcast, or delete stored rows to make
     them readable — no "compatibility" rewrite exists.
   - **Pinned by tests in the same change.** The v2 ADR's implementation carries a
     reader test per version-state reason, a writer test that an unknown/future
     version rejects the batch, and a downgrade test that an older reader keeps
     reporting `unknown`/non-usable on a v2 row.

5. **Non-goals.** This record does not add a provenance status or a new usability
   vocabulary (`valid | historical | invalid | unknown` is unchanged; there is no
   `unsupported` status and no capability boolean field), does not backfill,
   rewrite, relabel, or delete any existing v1 row, does not change
   freshness/`model_age_days` rules, does not change `prediction_semantics_*`
   derivation, does not define version 2, and does not schedule it.

6. **Interim until a v2 ADR exists.** The behaviour is exactly what ships today:
   `unknown` plus
   `forecast_provenance_version_is_unsupported_by_this_reader:<version>`, never
   usable, version verbatim, row visible. The pinning test is
   `tests/unit/test_api.py::test_unsupported_future_envelope_version_is_unknown_not_historical`
   (`tests/unit/test_api.py:594`), with the reader branch in
   `src/equity_lake/api/deps.py::_forecast_provenance_status` and the writer refusal
   in `src/equity_lake/ml/__init__.py::_forecast_provenance_violated`.

## Consequences

**Enables.** Rolling upgrades and pinned/merged snapshots are safe: an older reader
never claims a newer row is usable, never calls it corrupt, and never hides it.
Consumers can tell three previously conflated states apart — no proof, proof under
a superseded contract, and proof under a contract newer than the reader — because
the version number survives verbatim. The write boundary cannot poison the table
with an envelope this binary cannot validate, so at least one side of every
mixed-version encounter is guaranteed to be conservative.

**Costs.**

- `unknown` is now ambiguous unless the reason is read: consumers that treat
  `unknown` as "legacy row, no proof" must also inspect `api_provenance_reason`.
  The distinction lives in prose, so a consumer needing the number parses the
  reason suffix; no structured version field was added (Decision 5).
- A future-envelope row that happens to violate a version-1 rule is reported
  `invalid` rather than unsupported (Decision 2 precedence). This is deliberate —
  the reader can assess the shared columns — but it means the reason does not
  always name the envelope as the cause. Both readings are non-usable.
- Two "unknown"s with different meanings oblige the read-API guide to keep
  explaining both, and every new version adds a reason-code suffix format that
  readers should not treat as stable prose beyond the numeric suffix.

**Residual limits (accepted).**

1. This record establishes only that an unsupported envelope is *not usable and not
   corrupt*. It says nothing about what a v2 row means, and a consumer must not
   infer version-1 semantics from a version-2 row's stored `direction`,
   `probability`, or `threshold_used`.
2. The envelope is self-asserted. A forged `2` is indistinguishable from a genuine
   v2 row to this reader; the guarantee is "never usable by a reader that does not
   know the rules", not writer authentication.
3. A version-2 table mixed with a version-1 binary is expected to look partly
   `unknown` indefinitely if the downgrade is permanent. That is the intended
   trade: permanently conservative rather than temporarily wrong.

## Alternatives considered

- **Report a future envelope as `historical`.** Rejected: `historical` is defined as
  complete evidence bound to a superseded contract. Reporting a newer contract as
  older inverts the meaning and tells consumers the rules are known.
- **Report it as the legacy null/`unknown` state.** Rejected: it discards a proof
  that exists and erases the only signal that the table has moved ahead of the
  reader.
- **Report it as `invalid`.** Rejected: there is no contradiction. Blaming the row
  for an unknown contract would also make "invalid" useless as a data-quality
  signal.
- **Fail the read (HTTP 503) or filter such rows out.** Rejected: ADR-0021 §2.4
  forbids filtering rows by status, and failing a whole pinned-snapshot read
  because one row is newer punishes every other row for a deploy-ordering fact.
- **Upgrade by inference or downcast the stored version.** Rejected: ADR-0021 §2.6
  forbids upgrading rows by inference, filename, or current configuration, and a
  downcast rewrite would fabricate an envelope the row never had.
- **Let the writer emit future envelopes with a warning.** Rejected: a binary must
  not write what its own validator cannot check (ADR-0021 §2.5, fail closed).
- **Add a fifth provenance status `unsupported`, or a
  `api_provenance_envelope_supported` boolean.** Deferred: it is a reader-contract
  and consumer-vocabulary change that would need its own record and a migration
  for every consumer of `api_usability_reasons`. The reason code carries the
  distinction today (Decision 5).
- **Add a structured `api_provenance_envelope_version` field.** Deferred with the
  above; the verbatim suffix is sufficient while the state must never be usable.

## References

- [ADR-0021: Row-Bound Forecast Provenance and Prediction Usability](0021-row-bound-forecast-provenance.md)
  — the proof set, derivation rules, and immutability obligation this record
  extends for versions newer than the reader knows.
- [ADR-0017: Bounded Additive Evolution and Replay](0017-bounded-additive-evolution-and-replay.md)
  — additive columns for a version-2 evidence set, without table rewrites.
- [ADR-0014: Label Lifecycle and Availability](0014-label-lifecycle-and-availability.md)
  — unchanged by this record; freshness and availability rules are out of scope.
- `src/equity_lake/api/deps.py` — `_forecast_provenance_status`, `_row_int`, and the
  `forecast_provenance_version_is_unsupported_by_this_reader:{envelope}` reason.
- `src/equity_lake/ml/__init__.py` — `validate_predictions`,
  `_forecast_provenance_violated` (writer refusal), `_forecast_provenance_columns`.
- `src/equity_lake/core/schemas.py` — `FORECAST_PROVENANCE_VERSION`,
  `FORECAST_PROVENANCE_COLUMNS`, `PREDICTION_SEMANTICS_VERSION`.
- `tests/unit/test_api.py::test_unsupported_future_envelope_version_is_unknown_not_historical`
  — pins the reader's status, reason, and non-usability for a future envelope.
- [Read API guide](../user-guide/20260811-read-api.md) — the provenance and
  usability section, including the unsupported-envelope sentence.
- [20261030-01](../handoffs/20261030-01-postpreload-findings-and-remaining-issues.md)
  R10 (the decision gap) and
  [20261030-02](../handoffs/20261030-02-row-bound-forecast-provenance-implementation.md)
  (what was built, including the gap found in review).
