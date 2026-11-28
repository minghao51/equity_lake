# ADR-0040 — Verify encrypted off-host ledger recovery on an isolated copy

**Status:** Proposed (owner review; destination and key custodians not supplied).

**Recorded:** 2026-11-27

## Context

[ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md) decision 3
covers manual local verified exact-file backups, not remote or cloud export. A local
restore test does not prove recovery after host loss.

## Options

- **Local verified copies only:** stays in contract, but host loss can take all copies.
- **Manual encrypted export and isolated recovery drill:** tests recovery without giving
  an unattended process decryption keys or cloud deletion rights.
- **Scheduled remote backups:** lowers the recovery-point gap, but needs key recovery,
  scheduling, monitoring, retention and tested operator response.

## Decision (recommended)

Propose a narrow amendment to ADR-0031 decision 3 for a manual, client-side authenticated
encryption workflow. No export until the owner chooses a private off-host destination,
jurisdiction/access rules, encryption and key-recovery custodians, retention and cost,
and acceptable data-loss and restore-time targets. A disconnected encrypted medium kept
elsewhere and a private object store are both candidates; neither is selected here.
Keep local verified copies. Do not send the key with the ciphertext, send plaintext or
put account details, backup bytes, credentials or revealing metadata in CI or public logs.

Start with a synthetic export and recovery drill. Record the source backup verification
and a protected integrity record, export the ciphertext, then retrieve it on an isolated
host or workspace, authenticate/decrypt, and re-run ADR-0031 checks on a disposable copy.
Measure recovery time and recoverable age, exercise key recovery, and verify the original
ledger was untouched. Agree separately on a first real export and isolated restore drill;
never substitute a restore over the live ledger. A failed check leaves the local backup
in place and the off-host copy unverified.

## Consequences

This can reduce common-mode host loss at the cost of key custody and another copy of
private data. The amendment would not authorize backup deletion, scheduling, or a restore
over the current book.
