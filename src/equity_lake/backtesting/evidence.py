"""Immutable research-run evidence: run manifests and run finalisation (E01a).

ADR-0025 (Decisions 1-4, 6, 9 and the frozen E01-1/E01-4 decisions) makes a
research run an addressable record instead of a directory of files. This module
is the **write side**: it mints a scope-only run ID, builds a manifest, writes
dated artifacts into a temporary sibling directory, re-reads and validates them
(dates, row counts and values), and then publishes them into a directory it claims
with one atomic ``os.mkdir``.

E01c adds the read-seam binding and the canonical-universe basis (contract items 1
and 2): the manifest records **how** its ``universe`` entries were obtained
(``universe_basis``: ``canonical`` display keys parsed out of the identity model, or
``ticker_only`` bare tickers) and every input snapshot carries the Delta ``version``
its read consumed. Both are facts the read seam supplies; neither is inferred from a
path, a table name, or an identity column.

**Provenance** is a derivation, not an assertion. Snapshots prove *which
bytes/versions were read*; they never prove where the price values came from, so a
run that cannot carry row-level price-origin evidence stays ``unknown``
(ADR-0021/0022, ADR-0027). E01-6 reader support lives here first: protocol
``equity-run-manifest/4`` adds one :class:`PriceOriginSummary` per price snapshot
and this module re-derives ``data_provenance`` from those summaries. Writers still
mint v3 manifests, which carry no summaries and therefore derive ``unknown`` on
every shipped path — the correct current output, not a defect.

Layout (ADR-0025 Decision 2)::

    <root>/runs/<UTC-timestamp>Z-<short-hash>/     # e.g. runs/20261104T101500Z-1f2e3d4c5b6a/
        equity.parquet      # (date, equity)   — real backend sessions
        drawdown.parquet    # (date, drawdown)
        metrics.json
        trades.json
        <card-id>.json      # optional FindingCards produced by the run
        manifest.json       # written last, inside the claimed directory, never before the artifacts exist

A directory is a **finalized run** only when ``manifest.json`` parses and every
artifact it lists exists with the recorded size and SHA-256 (see
:func:`validate_run_directory`). Run evidence is auxiliary storage under
``data/findings/`` — not cataloged, not pointblank-validated (ADR-0025
Decision 1, ADR-0006).

Run identity (E01-1): ``run_id`` is the SHA-256 of the *scope only*, serialized in
the documented field order of :data:`SCOPE_FIELD_ORDER`. A cost regime's identity
is its **name plus the fees and taxes actually applied** for it, so two runs that
differ only in the resolved ``fee_ratio``/``tax_ratio`` are different scopes and
never share a run ID. The code commit and the dirty-diff digest live in the
manifest and never change a scope-identical run ID; collisions are resolved by
atomically claiming the next free name (``-2``, ``-3``, ...), never by
overwriting. Secrets are never read, hashed, or copied: only a digest of non-secret
configuration is recorded, and secret-named keys are excluded before hashing.

Input snapshots (E01-4 / Decision 4) are content digests taken at the read seam:
the readers record the Delta ``version`` they consumed
(``read_delta_with_version``, ``load_corporate_action_inputs_with_versions``,
``BacktestDataLoader.snapshots``) and this module only stores them. A version
collected after a computation is explicitly *not* snapshot binding, and a snapshot
never upgrades provenance by itself.

Finalisation is not a claim that *something* was written: before any value is
compared, a **refusal gate** (:func:`_refuse_unmeasurable_values`) rejects a curve
that carries a null or non-finite (NaN, ``±inf``) equity or drawdown value, because
such a curve is not measured evidence (F03: never persist NaN as measured
evidence; ADR-0025: a corrupt or incomplete run must never look successful). Only a
curve that passes that gate is re-read and compared element-wise to the
``BacktestResult`` that produced it (see :func:`_compare_values`), on top of the
date and row-count checks, before any manifest exists.

A finite curve may still carry a non-finite **metric** (e.g. ``sharpe_ratio=NaN``):
the dated writer records it as ``null`` instead of persisting ``NaN``
(``json.dumps(..., allow_nan=False)``) and the manifest records its availability as
``non_finite``, and finalisation re-parses ``metrics.json`` strictly — no
``NaN``/``Infinity`` token, every ``null`` metric backed by a ``missing``/
``non_finite`` entry and every finite one by ``measured``
(:func:`_validate_metrics_artifact`) — so an unavailable number can never be
published as measured evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from equity_lake.backtesting.report import (
    drawdown_series,
    write_dated_backtest_report,
)
from equity_lake.backtesting.result import BacktestResult
from equity_lake.core.instruments import InstrumentKey  # noqa: TC001 - runtime validation, not typing only
from equity_lake.core.paths import FINDINGS_DIR
from equity_lake.core.schemas import (
    PRICE_ORIGIN_UNKNOWN_REASONS,
    PRICE_SNAPSHOT_NAME_PREFIX,
    PRICE_SOURCE_PATTERN,
)
from equity_lake.findings import FindingCard, write_finding_card

logger = structlog.get_logger(__name__)

#: Evidence-format protocol identifier. Part of the scope (E01-1): a protocol bump
#: is a different scope and therefore a different run ID. Version 2 adds the applied
#: fees/taxes to the cost-regime identity, so IDs minted under version 1 (whose
#: preimage could not distinguish two differently-costed scopes) are not comparable
#: with current IDs and must not be re-read as if they were. Version 3 (E01c) is the
#: canonical-universe and read-seam-snapshot protocol: a run's universe may be
#: canonical display keys instead of bare tickers, and its inputs are bound at the
#: read seam. The universe *values* differ between the two bases, so the ID changes
#: with the basis by construction — ``universe_basis`` is deliberately not a scope
#: field (it would be a redundant second spelling of the same fact).
RUN_PROTOCOL = "equity-run-manifest/3"

#: Protocol v4 (ADR-0027 §5): v3's read-seam snapshot binding plus one
#: :class:`PriceOriginSummary` per price snapshot, from which ``data_provenance`` is
#: re-derived. Reader support lands **before** any v4 writer (decision D4), so this
#: constant exists while :data:`RUN_PROTOCOL` still mints v3 evidence. The protocol is
#: a scope field, so v4 intentionally mints different scope IDs for the same scope;
#: it is not a relabelling of any stored v3 run, whose evidence stays byte-identical.
RUN_PROTOCOL_V4 = "equity-run-manifest/4"

#: Subdirectory of the findings root that holds immutable run directories.
RUNS_DIRNAME = "runs"

MANIFEST_NAME = "manifest.json"
EQUITY_ARTIFACT = "equity.parquet"
DRAWDOWN_ARTIFACT = "drawdown.parquet"
METRICS_ARTIFACT = "metrics.json"
TRADES_ARTIFACT = "trades.json"

#: Artifacts every finalized run must carry.
REQUIRED_RUN_ARTIFACTS: tuple[str, ...] = (EQUITY_ARTIFACT, DRAWDOWN_ARTIFACT, METRICS_ARTIFACT, TRADES_ARTIFACT)

#: Documented canonical scope field order (E01-1). :meth:`RunScope.run_id` hashes
#: exactly these fields, in this order, and nothing else — no commit, no dirty
#: digest, no timestamps, no artifact hashes. ``costs`` *is* the cost-regime
#: identity: one ``{"regime", "fee_ratio", "tax_ratio"}`` mapping per regime, in
#: that key order, sorted by regime name, so a scope that differs only in the fees
#: or taxes actually applied hashes differently while the same scope serialized
#: with a different costs ordering hashes identically.
SCOPE_FIELD_ORDER: tuple[str, ...] = (
    "protocol",
    "universe",
    "start_date",
    "end_date",
    "strategies",
    "costs",
    "initial_cash",
    "parameters",
)

#: Metrics a run reports on, so "missing" is explicit rather than merely absent.
REPORTED_METRICS: tuple[str, ...] = (
    "total_return",
    "cagr",
    "max_drawdown",
    "volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "win_rate",
    "num_trades",
)

#: Bound for the collision loop: each attempt tries the next discriminated directory
#: name with an atomic ``os.mkdir`` claim (the manifest is written with that name).
_MAX_PUBLISH_ATTEMPTS = 64

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
#: Compiled ADR-0027 D2 source grammar (shared with the write boundary).
_PRICE_SOURCE_RE = re.compile(PRICE_SOURCE_PATTERN)
#: The only price-origin summary encoding this reader interprets (ADR-0027 §4.4).
_PRICE_ORIGIN_SUMMARY_SCHEMA_VERSION = 1
_TEMP_PREFIX = ".partial-"

# Secret-named keys are dropped before a configuration digest is computed: no
# dotenvx/.env material, API key, token, or credential value is ever read, hashed,
# or copied into a manifest (ADR-0025 Decision 3).
_SECRET_KEY_PATTERN = re.compile(
    r"(api[-_]?key|secret|token|passw|credential|authori[sz]ation|private[-_]?key|access[-_]?key|session[-_]?key|\.env)",
    re.IGNORECASE,
)

Provenance = Literal["synthetic", "real", "mixed", "unknown"]
#: Why a price-origin aggregate is ``unknown`` (ADR-0027 §4). The closed set is
#: shared with the read seam as ``schemas.PRICE_ORIGIN_UNKNOWN_REASONS`` (pinned equal
#: by ``test_unknown_reasons_are_the_shared_closed_set``); ``empty_scope`` is derived
#: for a zero-row scope, the rest describe missing or unreadable row evidence.
UnknownReason = Literal["missing_envelope", "unsupported_version", "incomplete_binding", "untracked_input", "empty_scope"]
MetricStatus = Literal["measured", "missing", "non_finite"]
#: How a manifest's ``universe`` entries were obtained (E01-1 / ADR-0016).
#:
#: - ``canonical``: every entry is a canonical ``InstrumentKey`` display key
#:   (``market/MIC/percent-quoted-symbol``) and was parsed back through the identity
#:   model, so no MIC was filled and no venue was guessed.
#: - ``ticker_only``: at least one entry is not a parseable display key, so the
#:   universe is bare tickers and carries no venue claim at all.
#:
#: It is derived by **parsing**, never by guessing, and it is not part of the run-ID
#: preimage: the identity values themselves already differ between the two bases.
UniverseBasis = Literal["canonical", "ticker_only"]

__all__ = [
    "DRAWDOWN_ARTIFACT",
    "EQUITY_ARTIFACT",
    "MANIFEST_NAME",
    "METRICS_ARTIFACT",
    "REPORTED_METRICS",
    "REQUIRED_RUN_ARTIFACTS",
    "RUNS_DIRNAME",
    "RUN_PROTOCOL",
    "RUN_PROTOCOL_V4",
    "SCOPE_FIELD_ORDER",
    "TRADES_ARTIFACT",
    "ArtifactRecord",
    "CodeIdentity",
    "CostApplication",
    "ExecutionTiming",
    "FinalisedRun",
    "InputSnapshot",
    "MetricAvailability",
    "PriceOriginCounts",
    "PriceOriginSummary",
    "RunManifest",
    "RunScope",
    "RunValidation",
    "UniverseBasis",
    "artifact_records",
    "claim_evidence_directory",
    "digest_non_secret_config",
    "discard_directory",
    "file_digest",
    "finalise_run",
    "is_canonical_universe",
    "is_finalized_run",
    "is_price_snapshot_name",
    "metric_availability_from_result",
    "parse_display_key",
    "price_origin_aggregate",
    "probe_code_identity",
    "publish_evidence_directory",
    "read_json_artifact_strictly",
    "runs_root_for",
    "snapshot_from_bytes",
    "snapshot_from_file",
    "universe_basis_for",
    "validate_run_directory",
    "write_card_strictly",
]


# ---------------------------------------------------------------------------
# Canonicalisation helpers (E01-1)
# ---------------------------------------------------------------------------


def _canonical_value(value: Any) -> Any:
    """Recursively order mappings and stringify non-JSON scalars for hashing."""
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_secret_key(key: str) -> bool:
    """Whether a configuration key names a secret (dropped before digesting)."""
    return bool(_SECRET_KEY_PATTERN.search(key))


def _find_secret_keys(value: Any, path: str = "") -> list[str]:
    """Dotted paths of secret-named keys anywhere in a nested mapping/list."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if _is_secret_key(str(key)):
                found.append(key_path)
            found.extend(_find_secret_keys(item, key_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_find_secret_keys(item, f"{path}[{index}]"))
    return found


def _strip_secret_keys(value: Any) -> Any:
    """Copy of a nested config with every secret-named key removed."""
    if isinstance(value, Mapping):
        return {str(key): _strip_secret_keys(item) for key, item in value.items() if not _is_secret_key(str(key))}
    if isinstance(value, (list, tuple)):
        return [_strip_secret_keys(item) for item in value]
    return value


def parse_display_key(entry: str) -> InstrumentKey:
    """Parse a canonical ``InstrumentKey`` display key, or raise ``ValueError``.

    The accepted shape is exactly :attr:`InstrumentKey.display_key`:
    ``market/MIC/percent-quoted-symbol``. The parsed key must serialize back to
    *exactly* the input string, so a non-canonical spelling (a market alias, a
    lower-case percent escape, a missing or extra segment) is refused rather than
    silently normalised. Validation is the existing identity model's: the market
    must be a known price market and the MIC must be one supported for it, so no
    MIC is ever filled in and no venue is guessed (ADR-0016 Decision 1/2).

    Raises:
        ValueError: when *entry* is not a canonical display key.
    """
    segments = entry.split("/")
    if len(segments) != 3:
        raise ValueError(f"not a canonical instrument display key: {entry!r}")
    market, mic, quoted_symbol = segments
    if not market.strip() or not mic.strip() or not quoted_symbol:
        raise ValueError(f"not a canonical instrument display key: {entry!r}")
    try:
        key = InstrumentKey(market=market, mic=mic, symbol=unquote(quoted_symbol))
    except (ValueError, TypeError) as exc:  # pydantic validation errors are ValueErrors
        raise ValueError(f"not a canonical instrument display key: {entry!r}") from exc
    if key.display_key != entry:
        raise ValueError(f"not a canonical instrument display key: {entry!r}")
    return key


def is_canonical_universe(universe: Sequence[str]) -> bool:
    """Whether **every** entry parses as a canonical display key (no guessing)."""
    if not universe:
        return False
    for entry in universe:
        try:
            parse_display_key(entry)
        except ValueError:
            return False
    return True


def universe_basis_for(universe: Sequence[str]) -> UniverseBasis:
    """Derive a universe basis by parsing, never by guessing (E01-1 / ADR-0016).

    ``canonical`` only when *every* entry parses back through
    :func:`parse_display_key`; anything else — a bare ticker, a malformed display
    key, an empty universe — is ``ticker_only``, which claims no venue at all.
    """
    return "canonical" if is_canonical_universe(universe) else "ticker_only"


def digest_non_secret_config(config: Mapping[str, Any] | None) -> str | None:
    """SHA-256 of the non-secret configuration actually in force, or ``None``.

    Secret-named keys are removed **before** the digest is computed, so a secret
    value is never hashed. The configuration itself is never stored in the
    manifest — only this digest. Returns ``None`` for an empty/absent config so a
    run that recorded no configuration does not claim one.
    """
    if not config:
        return None
    stripped = _strip_secret_keys(_canonical_value(config))
    if not stripped:
        return None
    payload = json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Manifest models (Pydantic v2 write boundary, ADR-0006 / ADR-0025)
# ---------------------------------------------------------------------------


class CostApplication(BaseModel):
    """Fees and taxes actually applied for one cost regime (ADR-0025 Decision 3).

    The regime name and the two resolved ratios together are the cost-regime
    identity that enters the run ID (E01-1): a scope whose ``fee_ratio`` or
    ``tax_ratio`` differs is a different scope, never the same run ID under a
    different cost application.
    """

    model_config = ConfigDict(extra="forbid")

    regime: str = Field(..., min_length=1, description="Cost regime identity, e.g. 'realistic'")
    fee_ratio: float = Field(..., ge=0.0, description="Fee ratio applied per trade")
    tax_ratio: float = Field(..., ge=0.0, description="Tax ratio applied on sells")

    @field_validator("regime")
    @classmethod
    def _strip_regime(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("cost regime identity must be non-empty")
        return stripped

    @field_validator("fee_ratio", "tax_ratio")
    @classmethod
    def _finite_ratio(cls, value: float) -> float:
        """A non-finite ratio has no canonical preimage, so it is refused outright."""
        if not math.isfinite(value):
            raise ValueError("applied cost ratios must be finite")
        return value


class ExecutionTiming(BaseModel):
    """Execution-timing assumptions of the run (ADR-0025 Decision 3)."""

    model_config = ConfigDict(extra="forbid")

    trade_at_price: str = Field(default="close", min_length=1, description="Price column the backend traded at")
    resample: str | None = Field(default=None, description="Backend resample rule, if any")
    note: str = Field(default="", description="Free-text note, e.g. decision/execution session convention")


class CodeIdentity(BaseModel):
    """Code commit plus a digest of the dirty diff, if any (E01-1; never in the ID)."""

    model_config = ConfigDict(extra="forbid")

    commit: str | None = Field(default=None, description="git HEAD commit, or None when unavailable")
    dirty_digest: str | None = Field(default=None, description="sha256 of status+diff when the tree is dirty")

    @field_validator("dirty_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("dirty_digest must be a lowercase hex sha256 digest")
        return value


class InputSnapshot(BaseModel):
    """Content hash (or immutable snapshot reference) of an input **as read**.

    E01-4: the hash must be taken at the read seam. A Delta input records the
    ``version`` its read consumed together with a digest of the immutable
    ``(path, version)`` reference; a non-Delta input records one aggregate content
    digest of the exact files scanned and leaves ``version`` ``None``. Neither is a
    post-hoc Delta version lookup (ADR-0025 Decision 4).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Input identity, e.g. 'fixture:ohclv.parquet'")
    sha256: str = Field(..., description="Lowercase hex sha256 of the bytes read (or of the immutable snapshot reference)")
    source: str = Field(..., min_length=1, description="Read seam, e.g. 'fixture:file', 'lake:delta', 'lake:parquet'")
    version: str | None = Field(
        default=None,
        description="Delta version consumed by the read (as text), or None for a content digest of an enumerated file set",
    )

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("sha256 must be a lowercase hex sha256 digest")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str | None) -> str | None:
        """A version is a non-empty decimal string or ``None`` — never ``''``."""
        if value is None:
            return None
        if not value.strip():
            raise ValueError("version must be None or a non-empty string")
        return value


def _snapshot_binding(snapshot: InputSnapshot) -> tuple[str, str, str, str | None]:
    """The exact reference a summary must reproduce to bind a snapshot (name, digest, source, version)."""
    return (snapshot.name, snapshot.sha256, snapshot.source, snapshot.version)


def is_price_snapshot_name(name: str) -> bool:
    """Whether a read-seam snapshot name is a price snapshot (``price_marks:<market>``).

    The loader names the mark snapshot of each market this way
    (``backtesting/data_loader.py``), and that name is the only thing a price-origin
    summary is allowed to describe: action, coverage or fixture snapshots carry no
    price rows, so they never bind an origin summary.
    """
    prefix, _, market = name.partition(PRICE_SNAPSHOT_NAME_PREFIX)
    return prefix == "" and bool(market)


class PriceOriginCounts(BaseModel):
    """Row counts by origin inside one price snapshot (ADR-0027 §4.4).

    ``synthetic + real + unknown`` must equal the owning summary's ``consumed_rows``,
    so counts can never describe only a subset of what the summary claims to cover.
    When ``unknown`` is non-zero the reason counts must add up to it exactly and their
    keys are the closed set in :data:`UnknownReason`; when it is zero no reason may be
    recorded. A zero-row reason is refused so "no reasons" and "reasons that cancel
    out" can never look alike.
    """

    model_config = ConfigDict(extra="forbid")

    synthetic: int = Field(..., ge=0, description="Rows asserted ``synthetic``")
    real: int = Field(..., ge=0, description="Rows asserted ``real``")
    unknown: int = Field(..., ge=0, description="Rows with no supported evidence")
    unknown_reasons: dict[UnknownReason, int] = Field(
        default_factory=dict,
        description="Unknown rows per closed-set reason; empty when unknown is zero",
    )

    @field_validator("unknown_reasons", mode="before")
    @classmethod
    def _reasons_are_the_closed_set(cls, value: object) -> object:
        """Refuse a reason outside the closed set, and the one reason that is derived.

        ``empty_scope`` describes a zero-row *scope*, so it is derived by the aggregate
        and can never be a per-row count: allowing it here would let a nonempty
        snapshot label rows with a whole-scope condition.
        """
        if isinstance(value, Mapping):
            unknown_keys = sorted(str(key) for key in value if key not in PRICE_ORIGIN_UNKNOWN_REASONS)
            if unknown_keys:
                raise ValueError(f"unknown_reasons keys must be one of {list(PRICE_ORIGIN_UNKNOWN_REASONS)}, got {unknown_keys}")
            if "empty_scope" in value:
                raise ValueError("unknown_reasons must not record 'empty_scope': it is derived from a zero-row scope, never counted per row")
        return value

    @field_validator("unknown_reasons")
    @classmethod
    def _reasons_are_positive_and_ordered(cls, value: dict[UnknownReason, int]) -> dict[UnknownReason, int]:
        non_positive = sorted(reason for reason, count in value.items() if count <= 0)
        if non_positive:
            raise ValueError(f"unknown_reasons counts must be positive, got {non_positive}")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _reasons_add_up_to_unknown(self) -> PriceOriginCounts:
        """Every unknown row names exactly one reason (ADR-0027 §4.4)."""
        if self.unknown == 0:
            if self.unknown_reasons:
                raise ValueError("a summary with no unknown rows must not record unknown_reasons")
            return self
        counted = sum(self.unknown_reasons.values())
        if counted != self.unknown:
            raise ValueError(f"unknown_reasons must add up to the unknown row count ({self.unknown}), got {counted}")
        return self


class PriceOriginSummary(BaseModel):
    """One price snapshot's row-bound origin evidence, recorded in a v4 manifest.

    ADR-0027 §4 asks for an explicit, per-snapshot summary rather than one table-wide
    label: a lake can hold legacy, real and synthetic rows at once, so a flag derived
    from a path, a table name or a terminal summary would mislabel mixed scopes. This
    is a local, auditable assertion bound to the exact snapshot whose rows were
    consumed — not authentication, not correctness, not a licence to read a provider.

    ``schema_version`` is readable for a future encoding but only version 1 is
    interpreted: a v1-shaped summary carrying a future ``schema_version`` makes the
    aggregate ``unknown`` with its stored version visible instead of being read under
    version-1 rules. A *structurally* different future encoding fails validation
    outright (``extra="forbid"``) rather than being guessed at — the "readable as
    unknown" rule of ADR-0027 §4.5 governs the row envelope, not this manifest record.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, description="Summary encoding version; only 1 is interpreted")
    snapshot: InputSnapshot = Field(..., description="The read-seam price snapshot this summary describes, verbatim")
    consumed_rows: int = Field(..., ge=0, description="Price rows the read consumed for this snapshot")
    counts: PriceOriginCounts
    sources: list[str] = Field(default_factory=list, description="price_source identifiers seen in this snapshot (D2 grammar)")
    envelope_sha256: str = Field(..., description="SHA-256 of the canonical identity/date/envelope projection (D3)")

    @field_validator("snapshot")
    @classmethod
    def _describes_a_price_snapshot(cls, value: InputSnapshot) -> InputSnapshot:
        if not is_price_snapshot_name(value.name):
            raise ValueError(f"a price-origin summary must describe a price snapshot ('{PRICE_SNAPSHOT_NAME_PREFIX}<market>'), not {value.name!r}")
        return value

    @field_validator("sources")
    @classmethod
    def _validate_sources(cls, value: list[str]) -> list[str]:
        """Every named source matches the D2 grammar, once each, canonically ordered."""
        if len(set(value)) != len(value):
            raise ValueError("price_origin_summary sources must be unique")
        malformed = sorted(entry for entry in value if _PRICE_SOURCE_RE.fullmatch(entry) is None)
        if malformed:
            raise ValueError(f"price_source identifiers must match {PRICE_SOURCE_PATTERN!r}, got {malformed}")
        return sorted(value)

    @field_validator("envelope_sha256")
    @classmethod
    def _validate_envelope_sha256(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("envelope_sha256 must be a lowercase hex sha256 digest")
        return value

    @model_validator(mode="after")
    def _counts_cover_the_consumed_rows(self) -> PriceOriginSummary:
        """Counts must cover the consumed scope exactly, and a supported row names a source.

        The second check is what stops a supported label appearing without the
        producer identity ADR-0027 §2 requires alongside it: no fallback label is ever
        inferred from an incomplete envelope.
        """
        covered = self.counts.synthetic + self.counts.real + self.counts.unknown
        if covered != self.consumed_rows:
            raise ValueError(f"counts must cover exactly the consumed rows ({self.consumed_rows}), got {covered}")
        if self.counts.synthetic + self.counts.real > 0 and not self.sources:
            raise ValueError("a supported (synthetic/real) count must name at least one price_source")
        return self


def price_origin_aggregate(
    summaries: Sequence[PriceOriginSummary],
    snapshots: Sequence[InputSnapshot],
) -> tuple[Provenance, tuple[str, ...]]:
    """Re-derive ``data_provenance`` from summaries bound to recorded snapshots.

    ADR-0027 §4.5: the aggregate describes **price** origin over the consumed price
    inputs, and it is always re-derived rather than trusted, so a claim cannot be
    upgraded by assertion.

    Rules, in order: every price snapshot needs exactly one summary, and every summary
    must bind (name, digest, source, version) to a recorded snapshot; a name that was
    read more than once with different content is not covered by the one summary that
    could match it; an unsupported summary version, a zero-row scope or any unknown
    row makes the aggregate ``unknown``; otherwise all-synthetic is ``synthetic``,
    all-real is ``real`` and a mix is ``mixed`` (an aggregate only — never a row
    kind). Snapshots without summaries, and rows with no read seam at all, are
    ``unknown``.

    Returns:
        The derived provenance and the reasons it is ``unknown`` (empty otherwise).
        Reasons are informational for the refusal message; they are never stored as
        a claim.
    """
    reasons: list[str] = []
    price_snapshots = [snapshot for snapshot in snapshots if is_price_snapshot_name(snapshot.name)]
    if not summaries:
        return ("unknown", ("missing_envelope",) if price_snapshots else ("untracked_input",))

    recorded = {_snapshot_binding(snapshot) for snapshot in snapshots}
    described: dict[str, tuple[str, str, str, str | None]] = {}
    for summary in summaries:
        binding = _snapshot_binding(summary.snapshot)
        if binding not in recorded:
            reasons.append("incomplete_binding")
        described[summary.snapshot.name] = binding
    for snapshot in price_snapshots:
        if snapshot.name not in described:
            reasons.append("missing_envelope")
        elif described[snapshot.name] != _snapshot_binding(snapshot):
            # The same snapshot name was read twice with different bytes, and at most one
            # reading can be the one the summary describes. Coverage is therefore by
            # exact reference, not by name: picking the first would bind a claim to
            # bytes it never described.
            reasons.append("incomplete_binding")

    for summary in summaries:
        if summary.schema_version != _PRICE_ORIGIN_SUMMARY_SCHEMA_VERSION:
            reasons.append("unsupported_version")
        if summary.consumed_rows == 0:
            reasons.append("empty_scope")
        reasons.extend(sorted(summary.counts.unknown_reasons) if summary.counts.unknown else [])

    if reasons:
        return ("unknown", tuple(dict.fromkeys(reasons)))

    has_synthetic = any(summary.counts.synthetic for summary in summaries)
    has_real = any(summary.counts.real for summary in summaries)
    if has_synthetic and has_real:
        return ("mixed", ())
    if has_real:
        return ("real", ())
    if has_synthetic:
        return ("synthetic", ())
    # Defensive: full coverage with no unknown rows cannot be empty, but an empty
    # claim is unknown rather than synthetic (ADR-0027 §4.4).
    return ("unknown", ("empty_scope",))


class ArtifactRecord(BaseModel):
    """One finalized artifact: path relative to the run directory, digest, size."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, description="Path relative to the run directory")
    sha256: str = Field(..., description="Lowercase hex sha256 of the artifact bytes")
    bytes: int = Field(..., ge=0, description="Artifact size in bytes")

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("sha256 must be a lowercase hex sha256 digest")
        return value

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if value == MANIFEST_NAME or Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("artifact path must be a manifest-excluded relative path inside the run directory")
        return value


class MetricAvailability(BaseModel):
    """Whether a metric was measured, missing, or non-finite (ADR-0025 Decision 5)."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(..., min_length=1)
    status: MetricStatus
    value: float | None = Field(default=None, description="Measured value, None unless status is 'measured'")

    @model_validator(mode="after")
    def _value_matches_status(self) -> MetricAvailability:
        """Only a measured metric carries a value, and that value must be finite.

        An unavailable number (``missing``/``non_finite``) records its status and
        omits the value: it is never persisted, never zero-filled.
        """
        if self.status == "measured":
            if self.value is None or not math.isfinite(self.value):
                raise ValueError("a 'measured' metric must record a finite value")
            return self
        if self.value is not None:
            raise ValueError(f"a '{self.status}' metric must not record a value: an unavailable number is omitted, never persisted")
        return self


class RunScope(BaseModel):
    """The identity-bearing scope of a run (E01-1).

    ``run_id`` hashes exactly :data:`SCOPE_FIELD_ORDER` in that order, from this
    canonicalised model: ``universe`` and ``strategies`` are sorted and
    de-duplicated, ``costs`` is sorted by regime name (one entry per regime, so the
    regime name *and* the fees/taxes actually applied are identity) and
    ``parameters`` mapping keys are ordered, so two invocations that describe the
    same scope hash identically regardless of input ordering. Code identity,
    timestamps and artifact hashes are **not** scope.
    """

    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(default=RUN_PROTOCOL, min_length=1, description="Evidence protocol/version identity")
    universe: list[str] = Field(..., min_length=1, description="Canonical instrument identities consulted")
    start_date: date
    end_date: date
    strategies: list[str] = Field(..., min_length=1, description="Strategy identities in the run")
    costs: list[CostApplication] = Field(
        ...,
        min_length=1,
        description="Cost-regime identities in the run: regime name plus the fees and taxes actually applied",
    )
    initial_cash: float = Field(..., gt=0.0, description="Starting capital")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Strategy/model parameters actually used")

    @field_validator("universe", "strategies")
    @classmethod
    def _canonicalise(cls, value: list[str]) -> list[str]:
        canonical = sorted({item.strip() for item in value})
        if not canonical or any(not item for item in canonical):
            raise ValueError("scope identities must be non-empty after canonicalisation")
        return canonical

    @field_validator("costs")
    @classmethod
    def _canonicalise_costs(cls, value: list[CostApplication]) -> list[CostApplication]:
        """One entry per regime, ordered by regime name: the documented cost order."""
        regimes = [cost.regime for cost in value]
        if len(regimes) != len(set(regimes)):
            raise ValueError("scope costs must name each cost regime at most once")
        return sorted(value, key=lambda cost: cost.regime)

    @field_validator("initial_cash")
    @classmethod
    def _finite_cash(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("initial_cash must be finite")
        return value

    @field_validator("parameters")
    @classmethod
    def _reject_secret_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        secret_keys = _find_secret_keys(value)
        if secret_keys:
            raise ValueError(f"scope parameters must not carry secret-named keys: {secret_keys}")
        return value

    @model_validator(mode="after")
    def _validate_window(self) -> RunScope:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self

    @property
    def cost_regimes(self) -> list[str]:
        """Regime names this scope applies, derived from :attr:`costs` (sorted)."""
        return [cost.regime for cost in self.costs]

    def scope_payload(self) -> dict[str, Any]:
        """The canonical scope mapping in the documented field order.

        The key order is derived from :data:`SCOPE_FIELD_ORDER` rather than written
        out here, so the documented order and the run-ID preimage cannot drift apart
        (``test_scope_payload_order_matches_scope_field_order``).
        """
        values: dict[str, Any] = {
            "protocol": self.protocol,
            "universe": list(self.universe),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "strategies": list(self.strategies),
            "costs": [{"regime": cost.regime, "fee_ratio": cost.fee_ratio, "tax_ratio": cost.tax_ratio} for cost in self.costs],
            "initial_cash": self.initial_cash,
            "parameters": _canonical_value(self.parameters),
        }
        return {field: values[field] for field in SCOPE_FIELD_ORDER}

    def canonical_scope_json(self) -> str:
        """Compact canonical JSON of :meth:`scope_payload` — the run-ID preimage."""
        return json.dumps(self.scope_payload(), separators=(",", ":"), ensure_ascii=True, allow_nan=False)

    def run_id(self) -> str:
        """Scope-only SHA-256 run ID (E01-1)."""
        return hashlib.sha256(self.canonical_scope_json().encode("utf-8")).hexdigest()


class RunManifest(BaseModel):
    """The record that makes a run directory addressable evidence (ADR-0025 Decision 3)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="Scope-only sha256 run ID (E01-1)")
    run_dir: str = Field(..., min_length=1, description="Finalized directory name under <root>/runs/")
    created_at: datetime = Field(..., description="Aware UTC timestamp when the run was built")
    finalized_at: datetime = Field(..., description="Aware UTC timestamp when artifacts validated")
    protocol: str = Field(..., min_length=1)
    start_date: date
    end_date: date
    universe: list[str] = Field(..., min_length=1)
    #: How ``universe`` entries were obtained (E01c). Required, deliberately **not**
    #: in the run-ID preimage: the identity values themselves already differ between
    #: the two bases, so the ID still changes with the basis while no redundant field
    #: enters ``SCOPE_FIELD_ORDER``.
    universe_basis: UniverseBasis = Field(
        ...,
        description="'canonical' when every universe entry is a parsed InstrumentKey display key, else 'ticker_only'",
    )
    strategies: list[str] = Field(..., min_length=1)
    cost_regimes: list[str] = Field(..., min_length=1)
    initial_cash: float
    parameters: dict[str, Any] = Field(default_factory=dict)
    costs: list[CostApplication] = Field(default_factory=list)
    execution: ExecutionTiming = Field(default_factory=ExecutionTiming)
    seed: int | None = Field(default=None, description="Seed actually used, or None when the run has none")
    code: CodeIdentity = Field(default_factory=CodeIdentity)
    config_digest: str | None = Field(default=None, description="Digest of non-secret configuration only")
    data_provenance: Provenance = "unknown"
    input_snapshots: list[InputSnapshot] = Field(default_factory=list)
    #: One row-bound origin summary per price snapshot (protocol v4, ADR-0027 §4).
    #: Empty on every v3 manifest and on a v4 manifest with no price evidence, and
    #: then — and only then — ``data_provenance`` derives ``unknown``.
    price_origin_summaries: list[PriceOriginSummary] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    metric_availability: list[MetricAvailability] = Field(default_factory=list)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("run_id must be a lowercase hex sha256 digest")
        return value

    @field_validator("config_digest")
    @classmethod
    def _validate_config_digest(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("config_digest must be a lowercase hex sha256 digest")
        return value

    @field_validator("created_at", "finalized_at")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware UTC")
        return value.astimezone(UTC)

    @field_validator("metric_availability")
    @classmethod
    def _unique_metrics(cls, value: list[MetricAvailability]) -> list[MetricAvailability]:
        names = [entry.metric for entry in value]
        if len(names) != len(set(names)):
            raise ValueError("metric_availability entries must be unique per metric")
        return value

    @field_validator("artifacts")
    @classmethod
    def _unique_artifact_paths(cls, value: list[ArtifactRecord]) -> list[ArtifactRecord]:
        paths = [entry.path for entry in value]
        if len(paths) != len(set(paths)):
            raise ValueError("artifacts entries must be unique per path")
        return value

    @model_validator(mode="after")
    def _universe_basis_matches_universe(self) -> RunManifest:
        """A ``canonical`` claim must be reproducible from the recorded universe.

        The basis is derived by parsing the universe entries, so an explicit claim is
        only accepted when it agrees with what the entries actually are: a
        ``canonical`` label over an unparseable entry (or an empty universe) is
        refused rather than recorded. That is what keeps a run from being *upgraded*
        to canonical identity by assertion (ADR-0016, ADR-0021/0022: never upgrade
        evidence by inference).
        """
        derived = universe_basis_for(self.universe)
        if self.universe_basis != derived:
            raise ValueError(
                f"universe_basis {self.universe_basis!r} does not match the recorded universe, which parses as {derived!r}: "
                "a canonical claim requires every universe entry to be an InstrumentKey display key"
            )
        return self

    @model_validator(mode="after")
    def _summaries_are_v4_evidence_only(self) -> RunManifest:
        """A summary only exists in a v4 manifest, once per price snapshot (D4/§4).

        v3 evidence is parsed under its own rules and stays byte-identical, so it can
        never acquire a summary or a stronger label. One summary per snapshot keeps
        the aggregate unambiguous: two summaries for one snapshot could disagree and
        there would be no rule to prefer either.
        """
        if not self.price_origin_summaries:
            return self
        if self.protocol != RUN_PROTOCOL_V4:
            raise ValueError(
                f"price_origin_summaries require protocol {RUN_PROTOCOL_V4!r}; this manifest is {self.protocol!r}, "
                "and stored v3 evidence never acquires v4 fields"
            )
        names = [summary.snapshot.name for summary in self.price_origin_summaries]
        if len(names) != len(set(names)):
            raise ValueError("price_origin_summaries must record at most one summary per price snapshot")
        return self

    @model_validator(mode="after")
    def _provenance_matches_its_protocol_rules(self) -> RunManifest:
        """Provenance is checked under the rules of *its own* protocol (ADR-0027 §5).

        v4 re-derives the claim from its summaries and refuses any disagreement: a
        claim that outruns — or understates — its evidence would publish a successful
        run whose recorded label nobody can audit, and snapshots alone never prove
        where the price values came from.

        v3 keeps the rules it was written under (ADR-0025 Decision 3, as strengthened
        for new evidence by ADR-0027): its claim stays self-asserted but must still be
        backed by input snapshots, and it can never carry a summary. Stored v3
        evidence therefore keeps parsing exactly as before — "retain explicit
        parsing/validation of existing protocol-v3 evidence under its own rules" —
        rather than being retro-invalidated by a later, stronger contract. Writing a
        v3 claim remains possible only through the same pre-existing API, and no
        shipped caller asserts one; from the next stage every new run is v4.

        Any *other* protocol is refused rather than read as if it were v3: this reader
        cannot interpret a format it was not written for, so a future manifest is not
        evidence to it (the forward-compatible "readable as unknown" rule of
        ADR-0027 §4.5 governs the row envelope, not the run protocol).
        """
        if self.protocol == RUN_PROTOCOL:
            if self.data_provenance != "unknown" and not self.input_snapshots:
                raise ValueError(
                    "data_provenance other than 'unknown' requires input_snapshots evidence under protocol "
                    f"{self.protocol!r}; a path or a table name alone never proves where the marks came from"
                )
            return self

        if self.protocol != RUN_PROTOCOL_V4:
            raise ValueError(
                f"unsupported evidence protocol {self.protocol!r}: this reader interprets {RUN_PROTOCOL!r} and {RUN_PROTOCOL_V4!r} only, "
                "and guessing at an unknown format would record a claim nobody validated"
            )

        derived, reasons = price_origin_aggregate(self.price_origin_summaries, self.input_snapshots)
        if self.data_provenance != derived:
            detail = ", ".join(reasons) if reasons else "none"
            raise ValueError(
                f"data_provenance {self.data_provenance!r} is refused: the recorded price-origin evidence derives {derived!r} "
                f"(reasons: {detail}). Under protocol {RUN_PROTOCOL_V4!r} a non-'unknown' claim requires complete, "
                "snapshot-bound price_origin_summaries; input snapshots and a path or table name never prove where the marks came from"
            )
        return self

    @model_validator(mode="after")
    def _cost_regimes_name_the_applied_costs(self) -> RunManifest:
        """The recorded regime names must be exactly the regimes the costs describe."""
        if sorted(self.cost_regimes) != [cost.regime for cost in self.costs]:
            raise ValueError("cost_regimes must name exactly the cost regimes recorded in costs, once each")
        return self

    @model_validator(mode="after")
    def _run_id_matches_scope(self) -> RunManifest:
        """Re-derive the ID from the recorded scope, which includes the applied costs.

        This is what makes the manifest self-verifying: the ``run_id`` in the file is
        only accepted when hashing *this manifest's own* scope preimage (protocol,
        universe, window, strategies, the applied fees/taxes per regime, cash and
        parameters) reproduces it.
        """
        if self.run_id != self.scope().run_id():
            raise ValueError("run_id does not match the scope fields recorded in this manifest")
        return self

    def scope(self) -> RunScope:
        """Rebuild the :class:`RunScope` this manifest claims."""
        return RunScope(
            protocol=self.protocol,
            universe=self.universe,
            start_date=self.start_date,
            end_date=self.end_date,
            strategies=self.strategies,
            costs=list(self.costs),
            initial_cash=self.initial_cash,
            parameters=self.parameters,
        )


class FinalisedRun(BaseModel):
    """A published run directory plus the manifest that finalised it."""

    model_config = ConfigDict(extra="forbid")

    directory: Path
    manifest: RunManifest


class RunValidation(BaseModel):
    """Verdict of :func:`validate_run_directory`; ``reasons`` is empty when intact."""

    model_config = ConfigDict(extra="forbid")

    finalized: bool
    directory: str
    reasons: list[str] = Field(default_factory=list)
    manifest: RunManifest | None = None


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------


def runs_root_for(root: Path | None = None) -> Path:
    """``<findings root>/runs`` — the parent of every immutable run directory."""
    return (root or FINDINGS_DIR) / RUNS_DIRNAME


def file_digest(path: Path) -> tuple[str, int]:
    """SHA-256 and byte size of a file's content."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def snapshot_from_bytes(payload: bytes, *, name: str, source: str) -> InputSnapshot:
    """Hash bytes captured at the read seam (E01-4)."""
    return InputSnapshot(name=name, sha256=hashlib.sha256(payload).hexdigest(), source=source)


def snapshot_from_file(path: Path, *, name: str | None = None, source: str = "fixture:file") -> InputSnapshot:
    """Hash a file's content **at read time** (E01-4).

    Reading the bytes is the whole point: a table version looked up after the
    computation describes the table as it is now, not the snapshot consumed.
    """
    digest, _ = file_digest(path)
    return InputSnapshot(name=name or path.name, sha256=digest, source=source)


def probe_code_identity(repo_root: Path | None = None, *, timeout: float = 10.0) -> CodeIdentity:
    """Commit plus dirty-diff digest for the working tree, never failing the run.

    A dirty tree produces a digest of ``git status --porcelain`` plus
    ``git diff HEAD``; a missing git binary or a non-repository directory leaves
    both fields ``None`` (the manifest then honestly records "unknown code").
    """

    def _git(*args: str) -> str:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],  # noqa: S607 - git resolved from PATH like the rest of the toolchain
            cwd=str(repo_root) if repo_root is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {completed.stderr.strip()[:200]}")
        return completed.stdout

    try:
        commit = _git("rev-parse", "HEAD").strip() or None
        status = _git("status", "--porcelain")
        diff = _git("diff", "HEAD")
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        logger.warning("code_identity_unavailable", error=str(exc))
        return CodeIdentity()
    if not status.strip():
        return CodeIdentity(commit=commit)
    digest = hashlib.sha256(b"status\0" + status.encode("utf-8") + b"\0diff\0" + diff.encode("utf-8")).hexdigest()
    return CodeIdentity(commit=commit, dirty_digest=digest)


def metric_availability_from_result(
    result: BacktestResult,
    *,
    metrics: Sequence[str] | None = None,
) -> list[MetricAvailability]:
    """Per-metric availability of a result (Decision 5).

    ``missing`` means the run did not measure the metric; ``non_finite`` means it
    measured NaN/inf. Neither is ever reported as a measured zero, and neither
    records a value: the number is omitted, never persisted (F03 precedent). This is
    the same classification the dated writer applies to ``metrics.json`` (a
    non-finite metric is written as ``null``), and finalisation cross-checks that the
    two agree.
    """
    wanted = list(metrics) if metrics is not None else list(dict.fromkeys([*REPORTED_METRICS, *result.metrics]))
    entries: list[MetricAvailability] = []
    for name in wanted:
        if name not in result.metrics:
            entries.append(MetricAvailability(metric=name, status="missing"))
            continue
        value: Any = result.metrics[name]
        if value is None:
            entries.append(MetricAvailability(metric=name, status="missing"))
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            entries.append(MetricAvailability(metric=name, status="non_finite"))
            continue
        entries.append(MetricAvailability(metric=name, status="measured", value=numeric))
    return entries


# ---------------------------------------------------------------------------
# Artifact validation and finalisation (ADR-0025 Decision 2)
# ---------------------------------------------------------------------------


def _refuse_unmeasurable_values(artifact: str, values: Sequence[Any]) -> None:
    """Refusal gate: a null or non-finite curve value is never finalized as evidence.

    A finalized run is a checksum-certified claim that the curve *was measured*, so a
    ``None``/null or non-finite (``NaN``, ``+inf``, ``-inf``) value in the equity or
    drawdown column is refused outright, naming the artifact and the row. This runs
    **before** any value comparison: the comparison in :func:`_compare_values` is the
    internal consistency check (written column vs. the result that produced it), not
    a measurement test — it treats ``NaN`` as equal to ``NaN`` on purpose, so it
    would otherwise certify a non-finite curve as validated evidence.

    The refusal is deliberate, not a fallback: there is no repair (no drop, no
    zero-fill, no reindexing) because a repaired curve would still not be the run's
    measured equity.

    Raises:
        ValueError: naming the artifact and the first offending row.
    """
    for index, value in enumerate(values):
        if value is None:
            raise ValueError(
                f"{artifact} value at row {index} is null: finalisation refuses null curve values, because a "
                "finalized run certifies measured evidence and a missing point is not a measurement "
                "(ADR-0025 Decision 2; never persist NaN as measured evidence)"
            )
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"{artifact} value at row {index} is not a number ({value!r}): finalisation refuses a curve value it "
                "cannot read as a measurement (ADR-0025 Decision 2)"
            ) from None
        if not math.isfinite(numeric):
            raise ValueError(
                f"{artifact} value at row {index} is non-finite ({numeric!r}): finalisation refuses non-finite curve "
                "values, because a finalized run certifies measured evidence and NaN/inf is not a measurement "
                "(ADR-0025 Decision 2; never persist NaN as measured evidence)"
            )


def _compare_values(written: Sequence[Any], expected: Sequence[Any], *, artifact: str, source: str) -> None:
    """Prove the written column reproduces the run's values element-wise.

    This is the **internal consistency check** — written artifact vs. the result that
    produced it — and it runs only after :func:`_refuse_unmeasurable_values` has
    refused null/non-finite evidence, so it is never a measurement test.

    Comparison rule: the two sequences must have the same length, and each pair is
    compared with exact float equality (``==``, no tolerance) after coercion to
    ``float``; ``NaN`` equals only ``NaN`` and never a number, ``+inf``/``-inf``
    compare exactly (so ``+inf != -inf``), and ``None``/null equals only
    ``None``/null. The first disagreement is reported with its row index and both
    values, because a mutated curve must never be published as *validated*.

    Raises:
        ValueError: on a length mismatch or the first mismatching value.
    """
    if len(written) != len(expected):
        raise ValueError(f"{artifact} carries {len(written)} values for {len(expected)} {source} values")
    for index, (actual, want) in enumerate(zip(written, expected, strict=True)):
        if actual is None or want is None:
            if actual is None and want is None:
                continue
            raise ValueError(f"{artifact} value at row {index} is null where the {source} has {want!r} (written: {actual!r})")
        actual_value, want_value = float(actual), float(want)
        if actual_value == want_value:
            continue
        if math.isnan(actual_value) and math.isnan(want_value):
            continue
        raise ValueError(f"{artifact} value at row {index} does not reproduce the {source}: written {actual_value!r}, expected {want_value!r}")


class _NonFiniteJsonToken(ValueError):
    """Raised when a run JSON artifact carries a ``NaN``/``Infinity``/``-Infinity`` token."""


def _non_finite_token_rejecter(artifact: str) -> Callable[[str], Any]:
    """Build a ``json`` ``parse_constant`` hook that refuses any non-finite literal.

    The artifact is named in the refusal so a tampered ``trades.json`` or card can be
    told apart from a tampered ``metrics.json``; ``json.loads`` accepts these tokens by
    default, so a permissive parse cannot tell a persisted non-finite value from a
    measured one.
    """

    def reject(token: str) -> Any:
        raise _NonFiniteJsonToken(
            f"{artifact} carries a non-finite JSON token {token!r}: NaN/Infinity is never persisted as measured "
            "evidence; an unavailable measurement is recorded as null (metrics additionally carry a "
            "'missing'/'non_finite' availability entry) and a non-finite trade or card value refuses the run "
            "(ADR-0025; F03 precedent)"
        )

    return reject


def read_json_artifact_strictly(path: Path, artifact: str) -> Any:
    """Parse a run JSON artifact, refusing non-finite tokens and unparsable text.

    ``json.loads`` accepts ``NaN``/``Infinity``/``-Infinity`` by default, so a
    permissive parse cannot tell a persisted non-finite value from a measured one.
    The ``parse_constant`` hook turns every such token into a refusal naming the
    artifact, and any other parse/read failure is reported as unparsable evidence.

    Raises:
        ValueError: on a missing/unreadable file, a non-finite token, or JSON that
            does not parse.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{artifact} is missing or unparsable: {exc}") from exc
    try:
        return json.loads(text, parse_constant=_non_finite_token_rejecter(artifact))
    except _NonFiniteJsonToken:
        raise
    except ValueError as exc:
        raise ValueError(f"{artifact} is missing or unparsable: {exc}") from exc


def _read_metrics_strictly(path: Path) -> Any:
    """Parse ``metrics.json`` strictly (see :func:`read_json_artifact_strictly`)."""
    return read_json_artifact_strictly(path, METRICS_ARTIFACT)


def _validate_metrics_artifact(directory: Path, availability: Sequence[MetricAvailability]) -> None:
    """Prove ``metrics.json`` carries no non-finite token and matches its availability.

    A ``null`` metric is only acceptable when the manifest records it as ``missing``
    or ``non_finite``; a finite value is only acceptable when the manifest records it
    as ``measured`` with that same value. A non-finite token, a missing availability
    entry, or any status/value disagreement is refused, so a run can never publish a
    persisted ``NaN`` or an unlabelled ``null`` as evidence
    (ADR-0025 Decision 5; F03 precedent: an unavailable number is omitted, never
    persisted).

    Raises:
        ValueError: on a missing/unparsable document, a non-finite token, a
            non-object ``metrics`` mapping, or an availability mismatch.
    """
    payload = _read_metrics_strictly(directory / METRICS_ARTIFACT)
    if not isinstance(payload, Mapping):
        raise ValueError(f"{METRICS_ARTIFACT} must be a JSON object, got {type(payload).__name__}")
    metrics = payload.get("metrics", {})
    if not isinstance(metrics, Mapping):
        raise ValueError(f"{METRICS_ARTIFACT} 'metrics' must be a JSON object, got {type(metrics).__name__}")
    entries = {entry.metric: entry for entry in availability}
    for raw_name, value in metrics.items():
        name = str(raw_name)
        entry = entries.get(name)
        if value is None:
            if entry is None:
                raise ValueError(
                    f"{METRICS_ARTIFACT} metric '{name}' is null and has no availability entry: every null metric must be "
                    "recorded as 'missing' or 'non_finite' in the manifest"
                )
            if entry.status not in ("missing", "non_finite"):
                raise ValueError(
                    f"{METRICS_ARTIFACT} metric '{name}' is null but its availability is {entry.status!r}: a null metric "
                    "must be recorded as 'missing' or 'non_finite'"
                )
            continue
        if entry is None:
            raise ValueError(
                f"{METRICS_ARTIFACT} metric '{name}' has no availability entry: every measured value must be recorded as 'measured' in the manifest"
            )
        if entry.status != "measured":
            raise ValueError(
                f"{METRICS_ARTIFACT} metric '{name}' is {value!r} but its availability is {entry.status!r}: a finite "
                "value must be recorded as 'measured'"
            )
        if entry.value is None or not _same_number(value, entry.value):
            raise ValueError(
                f"{METRICS_ARTIFACT} metric '{name}' is {value!r} but its availability records {entry.value!r}: the "
                "manifest and the metrics document must agree"
            )


def _same_number(left: Any, right: Any) -> bool:
    """Exact numeric equality after float coercion; non-numbers never match."""
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return False


def _validate_dated_artifacts(directory: Path, result: BacktestResult, availability: Sequence[MetricAvailability]) -> None:
    """Re-read the written artifacts and prove they reproduce the run's evidence.

    Dates, row counts, drawdown non-positivity **and the values themselves** are
    checked: a curve carrying a null or non-finite equity/drawdown value is refused
    first (:func:`_refuse_unmeasurable_values`), then the equity column must equal
    ``result.equity_curve`` element-wise and the drawdown column must equal
    :func:`drawdown_series` of that curve, so a writer that mutates values is refused
    instead of publishing a "validated" run.

    Raises:
        ValueError: on a missing/mis-shaped artifact, a date column that does not
            reproduce the backend sessions, a null or non-finite curve value, a row
            count or value that does not reproduce the result, a ``metrics.json``
            that carries a non-finite token or disagrees with the recorded metric
            availability, or an unparsable JSON artifact.
    """
    dates = result.equity_dates
    if dates is None:
        raise ValueError("dated evidence unavailable: the result carries no backend dates")

    equity_path = directory / EQUITY_ARTIFACT
    drawdown_path = directory / DRAWDOWN_ARTIFACT
    if not equity_path.exists() or not drawdown_path.exists():
        raise ValueError("dated artifacts missing after write")

    equity = pl.read_parquet(equity_path)
    if list(equity.columns) != ["date", "equity"]:
        raise ValueError(f"{EQUITY_ARTIFACT} must carry exactly ['date', 'equity'], got {list(equity.columns)}")
    if equity.schema["date"] != pl.Date:
        raise ValueError(f"{EQUITY_ARTIFACT} date column must be pl.Date, got {equity.schema['date']}")
    if equity.height != len(dates):
        raise ValueError(f"{EQUITY_ARTIFACT} carries {equity.height} rows for {len(dates)} backend dates")
    if equity["date"].to_list() != list(dates):
        raise ValueError(f"{EQUITY_ARTIFACT} session dates do not reproduce the backend timestamps")
    # Refusal gate first: a null/non-finite curve is not measured evidence, so it is
    # refused before the internal consistency comparison (which would accept NaN).
    _refuse_unmeasurable_values(EQUITY_ARTIFACT, equity["equity"].to_list())
    _compare_values(equity["equity"].to_list(), result.equity_curve.to_list(), artifact=EQUITY_ARTIFACT, source="equity curve")

    drawdown = pl.read_parquet(drawdown_path)
    if list(drawdown.columns) != ["date", "drawdown"]:
        raise ValueError(f"{DRAWDOWN_ARTIFACT} must carry exactly ['date', 'drawdown'], got {list(drawdown.columns)}")
    if drawdown["date"].to_list() != list(dates):
        raise ValueError(f"{DRAWDOWN_ARTIFACT} session dates do not match {EQUITY_ARTIFACT}")
    expected = drawdown_series(result.equity_curve)
    if drawdown.height != expected.len():
        raise ValueError(f"{DRAWDOWN_ARTIFACT} row count does not match the equity curve")
    _refuse_unmeasurable_values(DRAWDOWN_ARTIFACT, drawdown["drawdown"].to_list())
    _compare_values(drawdown["drawdown"].to_list(), expected.to_list(), artifact=DRAWDOWN_ARTIFACT, source="equity curve")
    if not drawdown.is_empty() and bool((drawdown["drawdown"] > 1e-12).any()):
        raise ValueError("drawdown must never be positive (fraction below the running peak)")

    _validate_metrics_artifact(directory, availability)
    # Strict parse: a persisted ``NaN`` token in trades.json is refused, never accepted
    # as measured evidence (a permissive ``json.loads`` would accept it silently).
    trades = read_json_artifact_strictly(directory / TRADES_ARTIFACT, TRADES_ARTIFACT)
    if not isinstance(trades, list):
        raise ValueError(f"{TRADES_ARTIFACT} must be a JSON list")


def _validate_json_artifacts(directory: Path, artifacts: Sequence[ArtifactRecord]) -> list[str]:
    """Prove every recorded JSON artifact parses without a non-finite token.

    ``trades.json`` and any card JSON recorded in the manifest are parsed strictly:
    ``json.loads`` accepts ``NaN``/``Infinity``/``-Infinity`` by default, so a
    tampered trade or card document could otherwise be read back as evidence. A
    missing artifact is skipped here — it already carries its own reason — and each
    parse failure becomes one distinct reason instead of a crash, so a caller can
    tell tampered JSON from a missing file.
    """
    reasons: list[str] = []
    card_artifacts = sorted(
        record.path for record in artifacts if record.path.endswith(".json") and record.path not in (MANIFEST_NAME, METRICS_ARTIFACT, TRADES_ARTIFACT)
    )
    for name in [TRADES_ARTIFACT, *card_artifacts]:
        path = directory / name
        if not path.is_file():
            continue  # reported as "artifact missing" by the caller
        try:
            read_json_artifact_strictly(path, name)
        except ValueError as exc:
            reasons.append(str(exc))
    return reasons


def artifact_records(directory: Path, *, exclude: Sequence[str] = (MANIFEST_NAME,)) -> list[ArtifactRecord]:
    """Digest every artifact in an evidence directory except its marker document.

    ``exclude`` names the directory's own manifest/marker file(s), which are always
    written after the artifacts and never digest themselves. Shared by the run
    finaliser (``manifest.json``) and the attempt publisher (``attempt.json``).
    """
    records: list[ArtifactRecord] = []
    for path in sorted(p for p in directory.iterdir() if p.is_file() and p.name not in exclude):
        digest, size = file_digest(path)
        records.append(ArtifactRecord(path=path.name, sha256=digest, bytes=size))
    return records


def _write_manifest(directory: Path, manifest: RunManifest) -> None:
    """Write the manifest inside the directory the publisher just claimed.

    A v3 manifest is written **exactly** as v3 readers already know it: the additive
    ``price_origin_summaries`` key exists only on v4 evidence, so it is emitted only
    when summaries are actually recorded. No stored v3 run gains a summary, a label
    or a protocol bump (ADR-0027 §5).
    """
    exclude = {"price_origin_summaries"} if not manifest.price_origin_summaries else set()
    (directory / MANIFEST_NAME).write_text(manifest.model_dump_json(indent=2, exclude=exclude), encoding="utf-8")


def discard_directory(directory: Path) -> None:
    """Best-effort removal of a directory this invocation owns, never raising.

    Cleanup must not mask the failure that triggered it, so an already-gone or
    unwritable directory is logged rather than re-raised.
    """
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        logger.warning("run_cleanup_failed", directory=str(directory), error=str(exc))


def claim_evidence_directory(parent: Path, base_name: str) -> Path:
    """Atomically claim the first free discriminated directory name under ``parent``.

    The claim is ``os.mkdir`` on the candidate name: it either creates the directory
    or raises ``FileExistsError``, so a directory that appears between a name check
    and the claim is **never replaced** (``os.rename`` onto an existing empty
    directory would silently overwrite it). A collision appends ``-2``, ``-3``, ...
    (E01-1), so two identical-scope invocations coexist. Shared by the run publisher
    (:func:`_publish`) and the attempt publisher
    (:func:`equity_lake.backtesting.attempts.write_attempt_record`).

    Raises:
        RuntimeError: when every discriminated candidate is already taken.
    """
    for attempt in range(1, _MAX_PUBLISH_ATTEMPTS + 1):
        name = base_name if attempt == 1 else f"{base_name}-{attempt}"
        target = parent / name
        try:
            os.mkdir(target)
        except FileExistsError:
            logger.warning("evidence_publish_collision", candidate=name)
            continue
        return target
    raise RuntimeError(f"could not publish evidence: {_MAX_PUBLISH_ATTEMPTS} collisions for {base_name}")


def publish_evidence_directory(
    tmp_dir: Path,
    base_name: str,
    finalize: Callable[[Path], None],
    *,
    exclude: Sequence[str] = (),
) -> Path:
    """Claim a published directory, move the built artifacts in, then finalise last.

    ``finalize`` writes the directory's own marker document (``manifest.json`` for a
    run, ``attempt.json`` for an attempt) **after** every artifact already exists in
    the claimed directory, so a half-published directory is never a finalized piece
    of evidence. On any failure only the directory this attempt claimed is removed
    (never a pre-existing one) and the error is re-raised; the temporary directory
    stays the caller's to clean up.

    Returns:
        The claimed directory whose name is the claimed (possibly discriminated) name.
    """
    target = claim_evidence_directory(tmp_dir.parent, base_name)
    try:
        for path in sorted(tmp_dir.iterdir()):
            if path.name in exclude:
                continue
            os.rename(path, target / path.name)
        finalize(target)
    except BaseException:
        discard_directory(target)  # only the directory this attempt claimed
        raise
    discard_directory(tmp_dir)
    return target


def _publish(tmp_dir: Path, base_name: str, manifest: RunManifest) -> RunManifest:
    """Publish a finalized run: claim the name, move artifacts in, write the manifest last.

    A collision appends ``-2``, ``-3``, ... (E01-1), so two identical-scope
    invocations coexist; an existing directory is never replaced.
    """

    def _finalize(target: Path) -> None:
        _write_manifest(target, manifest.model_copy(update={"run_dir": target.name}))

    # A pre-publish manifest inside tmp_dir is never evidence, so it is never moved.
    target = publish_evidence_directory(tmp_dir, base_name, _finalize, exclude=(MANIFEST_NAME,))
    published = manifest.model_copy(update={"run_dir": target.name})
    logger.info("run_published", run_dir=str(target), run_id=published.run_id)
    return published


def _resolve_base_name(run_id: str, now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{run_id[:12]}"


def write_card_strictly(card: FindingCard, directory: Path) -> Path:
    """Write one finding card after proving it carries no non-finite number.

    A card's ``metrics`` mapping is a measurement, so a non-finite value there is a
    data defect rather than an unavailable number. Pydantic would silently encode
    ``NaN``/``Infinity`` as JSON ``null`` (its ``ser_json_inf_nan`` default), i.e. it
    would *sanitise* the defect into a measured-looking document. The payload is
    therefore walked first — naming the artifact and field — and the written file is
    re-read strictly, so a card artifact inside a finalized run (or attempt) can
    never carry a non-finite JSON token. The refusal propagates out of finalisation
    before any evidence directory is published.

    This is the factored-out card helper the attempt publisher reuses
    (:mod:`equity_lake.backtesting.attempts`); the strict serialization itself lives
    in :func:`equity_lake.findings.writer.write_finding_card`, so there is one
    implementation of the rule.

    Raises:
        ValueError: naming the card artifact and the offending key, e.g.
            ``strategy-comparison.json field 'metrics.sharpe' is non-finite``.
    """
    artifact = f"{card.id}.json"
    path = write_finding_card(card, base=directory, refuse_non_finite=True)
    # Belt and braces: the writer owns its serializer, so prove the bytes on disk parse
    # without a non-finite token before the card is digested into the manifest.
    read_json_artifact_strictly(path, artifact)
    return path


def finalise_run(
    result: BacktestResult,
    *,
    scope: RunScope,
    universe_basis: UniverseBasis | None = None,
    execution: ExecutionTiming | None = None,
    seed: int | None = None,
    config: Mapping[str, Any] | None = None,
    config_digest: str | None = None,
    data_provenance: Provenance = "unknown",
    input_snapshots: Sequence[InputSnapshot] = (),
    price_origin_summaries: Sequence[PriceOriginSummary] = (),
    code: CodeIdentity | None = None,
    cards: Sequence[FindingCard] = (),
    metric_availability: Sequence[MetricAvailability] | None = None,
    repo_root: Path | None = None,
    root: Path | None = None,
    now: datetime | None = None,
) -> FinalisedRun:
    """Write one immutable, finalized run directory from a backtest result.

    The directory is built in a temporary sibling of ``<root>/runs`` so a partial
    write is never visible as a run: artifacts are written, re-read, and validated
    (dates, row counts and the **curve/drawdown values themselves**), hashed, and
    only then is ``manifest.json`` written into a directory the publisher
    atomically claimed, with a single ``os.mkdir`` claim that never replaces an
    existing run. Any failure removes the claim and the temporary directory and
    leaves no finalized run (ADR-0025 Decision 2).

    Args:
        result: The backtest result; it must carry ``equity_dates`` (dated
            evidence), otherwise finalisation fails rather than writing an
            undated "dated" run.
        scope: The identity-bearing scope (E01-1); its ``run_id()`` is the run ID and
            its ``costs`` are the fees/taxes recorded in the manifest, so the ID and
            the recorded cost application can never drift apart.
        universe_basis: How ``scope.universe`` entries were obtained. ``None`` (the
            default) **derives** it by parsing every entry as an
            :class:`~equity_lake.core.instruments.InstrumentKey` display key: all of
            them parse (or the universe is empty) -> ``canonical``, otherwise
            ``ticker_only``. An explicit value always wins but is still validated
            against the universe it describes — a ``canonical`` claim over an
            unparseable entry is refused, never recorded.
        execution: Execution-timing assumptions.
        seed: Seed actually used, or ``None`` when there is none.
        config: Non-secret configuration in force — digested, never stored.
        config_digest: Pre-computed non-secret config digest (alternative to ``config``).
        data_provenance: ``synthetic | real | mixed | unknown``. A v4 scope must
            support any non-``unknown`` value with complete ``price_origin_summaries``
            bound to the recorded ``input_snapshots`` (ADR-0027 §4.5); a v3 scope keeps
            ADR-0025 Decision 3's weaker rule, where snapshots alone suffice, so stored
            v3 evidence is never retro-invalidated. Every new run is v4.
        input_snapshots: Input content hashes taken at the read seam (E01-4).
        price_origin_summaries: One row-bound origin summary per price snapshot
            (protocol v4). Omitted on every shipped path, which is why shipped runs
            derive ``unknown``.
        code: Code identity; probed from git when omitted.
        cards: FindingCards to record inside the run directory.
        metric_availability: Measured/missing/non-finite metrics; derived when omitted.
        repo_root: Repository root for code-identity probing.
        root: Findings root (injectable for tests); defaults to ``FINDINGS_DIR``.
        now: Aware UTC build timestamp (injectable for tests).

    Returns:
        The published :class:`FinalisedRun`.
    """
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    runs_root = runs_root_for(root)
    runs_root.mkdir(parents=True, exist_ok=True)

    run_id = scope.run_id()
    if result.equity_dates is None:
        raise ValueError(
            "dated evidence unavailable: BacktestResult.equity_dates is None; a finalized run never records an undated curve as dated evidence"
        )

    digest = config_digest if config_digest is not None else digest_non_secret_config(config)
    code_identity = code if code is not None else probe_code_identity(repo_root)
    availability = list(metric_availability) if metric_availability is not None else metric_availability_from_result(result)
    # Never guessed: an omitted basis is derived by parsing the recorded universe, and
    # an explicit one is checked against that same universe before any directory exists
    # (a ``canonical`` claim over an unparseable entry is refused here, not recorded).
    basis: UniverseBasis = universe_basis if universe_basis is not None else universe_basis_for(scope.universe)
    if basis == "canonical" and not is_canonical_universe(scope.universe):
        raise ValueError(
            "universe_basis 'canonical' was claimed for a universe that does not parse as InstrumentKey display keys: "
            f"{list(scope.universe)!r}. A venue is never inferred, so this run would need its real display keys."
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX, dir=runs_root))
    try:
        write_dated_backtest_report(
            result,
            tmp_dir,
            strategy=scope.strategies[0] if len(scope.strategies) == 1 else None,
            cost_regime=scope.cost_regimes[0] if len(scope.cost_regimes) == 1 else None,
        )
        for card in cards:
            write_card_strictly(card, tmp_dir)
        _validate_dated_artifacts(tmp_dir, result, availability)

        artifacts = artifact_records(tmp_dir)
        missing = [name for name in REQUIRED_RUN_ARTIFACTS if name not in {record.path for record in artifacts}]
        if missing:
            raise ValueError(f"finalized run would be missing required artifacts: {missing}")

        manifest = RunManifest(
            run_id=run_id,
            run_dir=_resolve_base_name(run_id, created_at),
            created_at=created_at,
            finalized_at=datetime.now(UTC),
            protocol=scope.protocol,
            start_date=scope.start_date,
            end_date=scope.end_date,
            universe=list(scope.universe),
            universe_basis=basis,
            strategies=list(scope.strategies),
            cost_regimes=scope.cost_regimes,
            initial_cash=scope.initial_cash,
            parameters=_canonical_value(scope.parameters),
            costs=list(scope.costs),
            execution=execution or ExecutionTiming(),
            seed=seed,
            code=code_identity,
            config_digest=digest,
            data_provenance=data_provenance,
            input_snapshots=list(input_snapshots),
            price_origin_summaries=list(price_origin_summaries),
            artifacts=artifacts,
            metric_availability=availability,
        )
        published = _publish(tmp_dir, manifest.run_dir, manifest)
    except BaseException:
        discard_directory(tmp_dir)  # the temporary directory only; a claimed run dir is handled by _publish
        raise

    return FinalisedRun(directory=runs_root / published.run_dir, manifest=published)


# ---------------------------------------------------------------------------
# Validator (ADR-0025 Decision 2 / Decision 9)
# ---------------------------------------------------------------------------


def validate_run_directory(directory: Path) -> RunValidation:
    """Decide whether ``directory`` is a finalized, intact run.

    A run is finalized only when ``manifest.json`` parses as a
    :class:`RunManifest`, its ``run_dir`` matches the directory name, every
    required artifact is present, every recorded artifact matches its recorded size
    and SHA-256, and ``metrics.json`` parses **strictly** — no ``NaN``/``Infinity``
    token — with every ``null`` metric recorded as ``missing``/``non_finite`` and
    every finite one recorded as ``measured`` (:func:`_validate_metrics_artifact`).
    ``reasons`` lists every failing check, so a caller can report *why* evidence is
    not usable instead of silently skipping it.
    """
    reasons: list[str] = []
    if not directory.is_dir():
        return RunValidation(finalized=False, directory=str(directory), reasons=["run directory does not exist"])

    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        return RunValidation(
            finalized=False,
            directory=str(directory),
            reasons=["manifest.json missing: the directory was never finalized"],
        )
    try:
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse/validation failure is a verdict, not a crash
        return RunValidation(finalized=False, directory=str(directory), reasons=[f"manifest.json invalid: {exc}"])

    if manifest.run_dir != directory.name:
        reasons.append(f"manifest run_dir '{manifest.run_dir}' does not match directory '{directory.name}'")

    listed = {record.path for record in manifest.artifacts}
    for name in REQUIRED_RUN_ARTIFACTS:
        if name not in listed:
            reasons.append(f"manifest does not record required artifact '{name}'")

    for record in manifest.artifacts:
        artifact = directory / record.path
        if not artifact.is_file():
            reasons.append(f"artifact missing: '{record.path}'")
            continue
        digest, size = file_digest(artifact)
        if size != record.bytes:
            reasons.append(f"artifact size mismatch: '{record.path}' (recorded {record.bytes}, found {size})")
        if digest != record.sha256:
            reasons.append(f"artifact hash mismatch: '{record.path}'")

    try:
        _validate_metrics_artifact(directory, manifest.metric_availability)
    except ValueError as exc:
        # A distinct reason (non-finite token, missing availability entry, status
        # mismatch) so a caller can tell tampered metrics from a missing artifact.
        reasons.append(str(exc))

    # trades.json and every recorded card JSON are parsed strictly too: a non-finite
    # token is a data defect, not evidence, so the directory is not a run.
    reasons.extend(_validate_json_artifacts(directory, manifest.artifacts))

    return RunValidation(
        finalized=not reasons,
        directory=str(directory),
        reasons=reasons,
        manifest=manifest if not reasons else None,
    )


def is_finalized_run(directory: Path) -> bool:
    """Whether ``directory`` is a finalized, intact run (see the validator)."""
    return validate_run_directory(directory).finalized


def iter_run_directories(root: Path | None = None) -> Iterable[Path]:
    """Every candidate run directory under ``<root>/runs`` (temporaries excluded).

    A candidate is only *evidence* once :func:`validate_run_directory` says so.
    """
    runs_root = runs_root_for(root)
    if not runs_root.is_dir():
        return []
    return sorted(path for path in runs_root.iterdir() if path.is_dir() and not path.name.startswith(_TEMP_PREFIX))
