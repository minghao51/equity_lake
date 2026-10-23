# ADR-0019: Lake containment for CLI Delta maintenance

**Status:** Accepted
**Recorded:** 2026-10-20

## Context

The bounded I03 source review identified that the three generic Delta-maintenance
CLI commands resolve market names and otherwise pass identifiers to the shared
storage layer, whose `delta_table_path` intentionally joins a table argument to
a configurable lake root. Full-path and noncatalog-table compatibility are
intentional; the plain shared join is a generic storage capability, not evidence
of a demonstrated exploit or production use. The finding and its limits are
recorded in the [prediction-test write investigation](../finding/20261018-prediction-test-write-investigation.md).

The source boundary is `src/equity_lake/cli/commands/data.py`; the approved
behavior is covered by temporary-root tests in
`tests/unit/test_delta_maintenance_path_safety.py` and the existing maintenance
contracts in `tests/unit/test_cli_unified.py`.

The owner approved the following CLI-only containment policy on 2026-10-20.
It does not authorize or imply any maintenance against production data.

## Decision

- Restrict `delta-vacuum`, `delta-compact`, and `delta-migrate` CLI targets to
  paths relative to the configured lake root. Preserve short and canonical
  price-market identifiers, the `features` and `predictions` routes, mapped
  dataset identifiers, full lake-relative medallion paths, and safe unknown
  relative paths. Do not impose a catalog-only allowlist; valid noncatalog lake
  tables remain addressable.
- Reject empty identifiers, absolute target paths, and any requested path with
  a `..` component. Resolve the configured lake root first, then resolve each
  target against it. Permit symlinks whose resolved target remains inside that
  root; reject targets resolving outside it. Pass the effective normalized
  lake-relative route to storage so an in-lake symlink target has consistent
  semantics regardless of whether it was named by route or path.
- Validate the complete requested target list before invoking any maintenance
  operation. Invalid input must report a clear error and exit nonzero.
- Keep these checks at the CLI boundary. Do not change the shared
  `delta_table_path`/root handling: storage callers also use configurable roots
  for auxiliary Delta artifacts outside the lake.
- Add no external opt-in flag or new signals maintenance route.

## Consequences and limits

The CLI rejects accidental path traversal and out-of-lake symlink targets while
retaining compatible safe routes and noncatalog lake-relative paths. Resolving
and validating a path is a local operator containment measure, not an OS
sandbox and not protection against a malicious concurrent symlink replacement
between validation and use. It is not a claim that the earlier generic join was
an exploit, that an attack occurred, or that production data was accessed.

The policy is exercised only with temporary directory fixtures and mocked CLI
maintenance seams. No vacuum, compaction, or migration is performed as part of
this decision or implementation review. The generic storage API remains
intentionally capable of using caller-supplied roots and is outside this
CLI-specific boundary.
