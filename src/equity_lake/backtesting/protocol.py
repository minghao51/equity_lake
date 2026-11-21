"""R00 research-protocol controls: freeze assumptions, refuse a changed run.

R00 (spec §7) is offline by construction: it freezes *what a research run is
allowed to assume* before any feature, model or backtest executes, and it never
ingests, fits, fetches or writes a lake table. Its record is an auxiliary artifact
under ``data/research/`` (AGENTS.md), rendered by the Pydantic models here.

The contract is deliberately narrow:

* **Freeze** — universe, window, holdout, execution clock/price, label horizon and
  maturity, purge/embargo, costs, metrics, baselines, acceptance tolerances and the
  numeric resource budget are one canonical, versioned payload. Its SHA-256 is the
  protocol identity, so two spellings of the same assumptions share one digest and
  any changed assumption produces a different one.
* **Refuse** — a run whose assumptions are absent (no frozen manifest) or changed
  (no frozen manifest carries the candidate digest) fails closed with the changed
  field named. There is no permissive mode and no automatic re-freeze: freezing is
  an explicit act that mints a new dated manifest.
* **Emit** — a frozen manifest records the protocol digest, the code identity
  (commit plus dirty-diff digest), a digest of non-secret configuration, and the
  digests of any frozen inputs the caller supplies. Secrets are stripped before
  hashing (reusing ADR-0025's rule), so a credential is never hashed or stored.

Reused vocabulary: :class:`~equity_lake.backtesting.evidence.CostApplication`
(fees/taxes are identity), :func:`universe_basis_for` (a universe basis is derived by
parsing, never guessed), :class:`CodeIdentity` and :func:`digest_non_secret_config`
(ADR-0025). R01/R02 then bind their E01 run manifests to
:meth:`ResearchProtocol.digest`; this module neither mints nor validates a backtest
run manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from equity_lake.backtesting.evidence import (
    CodeIdentity,
    CostApplication,
    UniverseBasis,
    digest_non_secret_config,
    probe_code_identity,
    universe_basis_for,
)
from equity_lake.core.paths import DATA_DIR

PROTOCOL_SCHEMA = "equity-research-protocol/1"
"""Version of the frozen-protocol payload; a changed field set moves it."""

MANIFEST_VERSION: Literal[1] = 1
"""Version of the frozen-manifest envelope (not part of the protocol digest)."""

PROTOCOL_FIELD_ORDER: tuple[str, ...] = (
    "protocol",
    "experiment",
    "universe",
    "start_date",
    "end_date",
    "holdout_start",
    "holdout_end",
    "entry_clock",
    "entry_price",
    "label_horizon_days",
    "label_maturity_days",
    "purge_days",
    "embargo_days",
    "costs",
    "metrics",
    "baselines",
    "acceptance_tolerances",
    "generator",
    "seeds",
    "resource_budget",
)
"""Canonical field order of the protocol digest preimage (never reorder)."""

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
#: Execution conventions the harness may actually implement; a free-text clock would
#: refuse nothing, so the frozen vocabulary is closed.
ENTRY_CLOCKS: tuple[str, ...] = ("next_session_open", "session_close")
ENTRY_PRICES: tuple[str, ...] = ("open", "close")
#: Budget keys every protocol must state explicitly; a missing budget is not "free".
RESOURCE_BUDGET_KEYS = ("max_runtime_seconds", "max_symbols", "max_sessions")


class ProtocolError(ValueError):
    """A research run's frozen assumptions are absent, changed or malformed."""


class ResourceBudget(BaseModel):
    """Numeric resource budget frozen before the run (R00).

    Computational scale is not economic capacity (spec §7): these bounds limit what
    the harness may spend, never what capital a strategy could absorb.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_runtime_seconds: float = Field(..., gt=0.0)
    max_symbols: int = Field(..., gt=0)
    max_sessions: int = Field(..., gt=0)


class ResearchProtocol(BaseModel):
    """The frozen assumptions of one research experiment (R00).

    Set-like fields are canonicalised (sorted, de-duplicated) so input order never
    changes the digest. The holdout must lie inside the run window and the entry
    clock/price must be from the closed vocabulary: an unimplementable execution
    convention is refused here, not discovered later as a surprising result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: str = Field(default=PROTOCOL_SCHEMA, min_length=1)
    experiment: str = Field(..., min_length=1, description="Experiment identity, e.g. 'R01-baseline-us-2024'")
    universe: list[str] = Field(..., min_length=1, description="Canonical display keys or bare tickers")
    start_date: date
    end_date: date
    holdout_start: date
    holdout_end: date
    entry_clock: str = Field(default="next_session_open")
    entry_price: str = Field(default="open")
    label_horizon_days: int = Field(..., gt=0)
    label_maturity_days: int = Field(..., ge=0, description="Sessions after the label's last input before it is usable")
    purge_days: int = Field(..., ge=0)
    embargo_days: int = Field(..., ge=0)
    costs: list[CostApplication] = Field(..., min_length=1)
    metrics: list[str] = Field(..., min_length=1)
    baselines: list[str] = Field(..., min_length=1)
    acceptance_tolerances: dict[str, float] = Field(..., min_length=1)
    generator: str | None = Field(default=None, description="Synthetic generator identity for control runs")
    seeds: list[int] = Field(default_factory=list, description="Predeclared seeds; never chosen after seeing results")
    resource_budget: ResourceBudget

    @field_validator("experiment", "protocol")
    @classmethod
    def _strip_identity(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("protocol/experiment identity must be non-empty")
        return stripped

    @field_validator("universe", "metrics", "baselines")
    @classmethod
    def _canonicalise(cls, value: list[str]) -> list[str]:
        canonical = sorted({item.strip() for item in value})
        if not canonical or any(not item for item in canonical):
            raise ValueError("frozen identities must be non-empty after canonicalisation")
        return canonical

    @field_validator("entry_clock")
    @classmethod
    def _known_clock(cls, value: str) -> str:
        if value not in ENTRY_CLOCKS:
            raise ValueError(f"unknown entry clock {value!r} (expected one of {ENTRY_CLOCKS})")
        return value

    @field_validator("entry_price")
    @classmethod
    def _known_price(cls, value: str) -> str:
        if value not in ENTRY_PRICES:
            raise ValueError(f"unknown entry price {value!r} (expected one of {ENTRY_PRICES})")
        return value

    @field_validator("costs")
    @classmethod
    def _canonicalise_costs(cls, value: list[CostApplication]) -> list[CostApplication]:
        regimes = [cost.regime for cost in value]
        if len(regimes) != len(set(regimes)):
            raise ValueError("frozen costs must name each regime at most once")
        return sorted(value, key=lambda cost: cost.regime)

    @field_validator("acceptance_tolerances")
    @classmethod
    def _finite_tolerances(cls, value: dict[str, float]) -> dict[str, float]:
        for key, tolerance in value.items():
            if not key.strip():
                raise ValueError("acceptance tolerance names must be non-empty")
            if tolerance < 0.0:
                raise ValueError(f"acceptance tolerance {key!r} must be non-negative")
        return dict(sorted(value.items()))

    @field_validator("seeds")
    @classmethod
    def _predeclared_seeds(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("frozen seeds must be unique")
        if any(seed < 0 for seed in value):
            raise ValueError("frozen seeds must be non-negative")
        return sorted(value)

    @model_validator(mode="after")
    def _validate_window(self) -> ResearchProtocol:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if not (self.start_date <= self.holdout_start <= self.holdout_end <= self.end_date):
            raise ValueError("holdout must lie inside the run window")
        if self.generator is None and self.seeds:
            raise ValueError("predeclared seeds require a generator identity")
        return self

    @property
    def universe_basis(self) -> UniverseBasis:
        """How the universe was expressed, parsed rather than guessed (E01c)."""
        return universe_basis_for(self.universe)

    def protocol_payload(self) -> dict[str, Any]:
        """Canonical mapping in :data:`PROTOCOL_FIELD_ORDER` — the digest preimage."""
        values: dict[str, Any] = {
            "protocol": self.protocol,
            "experiment": self.experiment,
            "universe": list(self.universe),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "holdout_start": self.holdout_start.isoformat(),
            "holdout_end": self.holdout_end.isoformat(),
            "entry_clock": self.entry_clock,
            "entry_price": self.entry_price,
            "label_horizon_days": self.label_horizon_days,
            "label_maturity_days": self.label_maturity_days,
            "purge_days": self.purge_days,
            "embargo_days": self.embargo_days,
            "costs": [{"regime": cost.regime, "fee_ratio": cost.fee_ratio, "tax_ratio": cost.tax_ratio} for cost in self.costs],
            "metrics": list(self.metrics),
            "baselines": list(self.baselines),
            "acceptance_tolerances": dict(self.acceptance_tolerances),
            "generator": self.generator,
            "seeds": list(self.seeds),
            "resource_budget": self.resource_budget.model_dump(),
        }
        return {field: values[field] for field in PROTOCOL_FIELD_ORDER}

    def canonical_json(self) -> str:
        """Compact canonical JSON of :meth:`protocol_payload`."""
        return json.dumps(self.protocol_payload(), separators=(",", ":"), ensure_ascii=True, allow_nan=False)

    def digest(self) -> str:
        """SHA-256 protocol identity: assumptions only, never code or time."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class FrozenProtocol(BaseModel):
    """The dated frozen record of one protocol — the R00 run manifest.

    It binds the protocol digest to the code that will run it, a digest of the
    non-secret configuration in force, and any frozen input digests. It carries no
    lake rows, no prices and no secrets; the embedded protocol is the canonical
    payload, so a manifest is self-describing and independently verifiable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: Literal[1] = MANIFEST_VERSION
    protocol_digest: str
    recorded_at: datetime
    protocol: ResearchProtocol
    code: CodeIdentity | None = None
    config_sha256: str | None = None
    input_digests: dict[str, str] = Field(default_factory=dict)

    @field_validator("recorded_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware UTC")
        return value.astimezone(UTC)

    @field_validator("protocol_digest")
    @classmethod
    def _digest_shape(cls, value: str) -> str:
        if re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("protocol_digest must be a lowercase hex sha256 digest")
        return value

    @field_validator("config_sha256")
    @classmethod
    def _config_digest_shape(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(_SHA256_PATTERN, value) is None:
            raise ValueError("config_sha256 must be a lowercase hex sha256 digest")
        return value

    @field_validator("input_digests")
    @classmethod
    def _input_digest_shapes(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            if not name.strip():
                raise ValueError("input digest names must be non-empty")
            if re.fullmatch(_SHA256_PATTERN, digest) is None:
                raise ValueError(f"input digest {name!r} must be a lowercase hex sha256 digest")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _digest_binds_protocol(self) -> FrozenProtocol:
        if self.protocol.digest() != self.protocol_digest:
            raise ValueError("protocol_digest does not match the embedded protocol")
        return self

    def canonical_json(self) -> str:
        """The manifest bytes; identical inputs always reproduce them exactly."""
        payload = {
            "manifest_version": self.manifest_version,
            "protocol_digest": self.protocol_digest,
            "recorded_at": self.recorded_at.astimezone(UTC).isoformat(),
            "protocol": self.protocol.protocol_payload(),
            "code": None if self.code is None else self.code.model_dump(),
            "config_sha256": self.config_sha256,
            "input_digests": dict(self.input_digests),
        }
        return json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def protocols_root(root: Path | None = None) -> Path:
    """Directory holding frozen manifests (auxiliary storage, never a lake table)."""
    return (Path(root) if root is not None else DATA_DIR) / "research" / "protocols"


def freeze_protocol(
    protocol: ResearchProtocol,
    *,
    recorded_at: datetime | None = None,
    root: Path | None = None,
    code: CodeIdentity | None = None,
    config: dict[str, Any] | None = None,
    input_digests: dict[str, str] | None = None,
) -> tuple[FrozenProtocol, Path]:
    """Write the dated frozen manifest and return it with its path.

    The manifest content is a pure function of the inputs (a supplied
    ``recorded_at`` is the only timestamp), so freezing the same assumptions twice
    reproduces identical bytes. An existing manifest with identical bytes is reused;
    a different one at the same name is refused rather than overwritten.

    Raises:
        ProtocolError: when the target exists with different content.
    """
    stamp = (recorded_at or datetime.now(UTC)).astimezone(UTC)
    record = FrozenProtocol(
        protocol_digest=protocol.digest(),
        recorded_at=stamp,
        protocol=protocol,
        code=code if code is not None else probe_code_identity(),
        config_sha256=digest_non_secret_config(config),
        input_digests=input_digests or {},
    )
    directory = protocols_root(root)
    path = directory / f"{stamp:%Y%m%d}T{stamp:%H%M%S}Z-{protocol.digest()[:12]}.manifest.json"
    payload = record.canonical_json()
    if path.exists():
        if path.read_text(encoding="utf-8") == payload:
            return record, path
        raise ProtocolError(f"a different frozen manifest already exists at {path.name}; refusing to overwrite it")
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".protocol-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return record, path


def load_frozen_protocols(root: Path | None = None) -> list[FrozenProtocol]:
    """Read every frozen manifest, refusing any file that does not re-validate.

    A manifest whose digest no longer matches its embedded protocol is a tampered or
    corrupt record: it is refused, never returned as evidence.
    """
    directory = protocols_root(root)
    if not directory.exists():
        return []
    records: list[FrozenProtocol] = []
    for path in sorted(directory.glob("*.manifest.json")):
        try:
            records.append(FrozenProtocol.model_validate_json(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            raise ProtocolError(f"frozen manifest {path.name} is corrupt or was modified: {exc}") from exc
    return records


def changed_fields(frozen: ResearchProtocol, candidate: ResearchProtocol) -> list[str]:
    """Top-level protocol fields whose frozen value differs from *candidate*."""
    frozen_payload = frozen.protocol_payload()
    candidate_payload = candidate.protocol_payload()
    return [field for field in PROTOCOL_FIELD_ORDER if frozen_payload[field] != candidate_payload[field]]


def select_frozen_protocol(
    experiment: str,
    *,
    root: Path | None = None,
    digest: str | None = None,
) -> FrozenProtocol:
    """Select the one frozen manifest a run may bind itself to, or refuse (R01).

    A run names the experiment it claims to execute under (and, optionally, the
    protocol digest it expects), so the binding is checked against the frozen
    records instead of being asserted by the caller.

    Refusals (all :class:`ProtocolError`, the run never starts):

    * **absent** — no frozen manifest names ``experiment``.
    * **digest claim unmatched** — ``digest`` is given and no frozen record carries it.
    * **ambiguous** — several frozen records for the experiment carry *different*
      digests and the claim does not disambiguate them. Two freezes of the same
      experiment are two different sets of assumptions; a run may not pick one.

    An identical re-freeze (same bytes) collapses to one record, so it is not
    ambiguous.
    """
    identity = experiment.strip()
    if not identity:
        raise ProtocolError("a run that binds a frozen protocol must name its experiment")
    records = [record for record in load_frozen_protocols(root) if record.protocol.experiment == identity]
    if not records:
        raise ProtocolError(f"assumptions are absent: no frozen protocol manifest for experiment {identity!r}; freeze the protocol before running")
    digests = sorted({record.protocol_digest for record in records})
    if digest is not None:
        claimed = digest.strip()
        matching = [record for record in records if record.protocol_digest == claimed]
        if not matching:
            raise ProtocolError(
                f"protocol digest {claimed[:12]} does not match a frozen record for experiment {identity!r}: "
                f"frozen digests are {', '.join(entry[:12] for entry in digests)}. A run never runs under an unfrozen protocol"
            )
        return matching[-1]
    if len(digests) > 1:
        raise ProtocolError(
            f"frozen protocol is ambiguous for experiment {identity!r}: {len(digests)} distinct digests "
            f"({', '.join(entry[:12] for entry in digests)}); state the protocol digest to bind one"
        )
    return records[-1]


def conformance_violations(
    record: FrozenProtocol,
    *,
    universe: Sequence[str],
    start_date: date,
    end_date: date,
    cost_regimes: Sequence[str],
) -> list[str]:
    """Assumptions a candidate run states differently from the frozen record (R01).

    Pure and read-only: the run's *own* recorded facts (the universe it actually
    consulted, its window, the cost regimes it applied) are compared with the frozen
    protocol. An empty list means the run conforms. The rules are deliberately the
    four that make a research run comparable:

    * **universe** — the run's universe is the frozen universe exactly. A different
      set is a selection/survivorship decision made after the freeze.
    * **window** — the run lies inside the frozen window.
    * **holdout** — the run's window never overlaps the frozen holdout.
    * **costs** — the run applies exactly the frozen cost regimes.
    """
    frozen = record.protocol
    violations: list[str] = []
    candidate_universe = sorted({str(entry).strip() for entry in universe if str(entry).strip()})
    if candidate_universe != list(frozen.universe):
        added = sorted(set(candidate_universe) - set(frozen.universe))
        missing = sorted(set(frozen.universe) - set(candidate_universe))
        violations.append(
            f"universe differs from the frozen universe (added {added or 'none'}, missing {missing or 'none'}); "
            "the frozen universe is fixed, so a different set is a selection decision made after the freeze"
        )
    if start_date < frozen.start_date:
        violations.append(f"window starts {start_date.isoformat()}, before the frozen start {frozen.start_date.isoformat()}")
    if end_date > frozen.end_date:
        violations.append(f"window ends {end_date.isoformat()}, after the frozen end {frozen.end_date.isoformat()}")
    if start_date <= frozen.holdout_end and end_date >= frozen.holdout_start:
        violations.append(
            f"window {start_date.isoformat()}..{end_date.isoformat()} overlaps the frozen holdout "
            f"{frozen.holdout_start.isoformat()}..{frozen.holdout_end.isoformat()}; the holdout is untouched by a protocol-bound run"
        )
    candidate_regimes = sorted({str(regime).strip() for regime in cost_regimes if str(regime).strip()})
    frozen_regimes = sorted(cost.regime for cost in frozen.costs)
    if candidate_regimes != frozen_regimes:
        violations.append(f"cost regimes {candidate_regimes} differ from the frozen {frozen_regimes}")
    return violations


def require_run_conformance(
    record: FrozenProtocol,
    *,
    universe: Sequence[str],
    start_date: date,
    end_date: date,
    cost_regimes: Sequence[str],
) -> None:
    """Refuse a run whose recorded facts violate the frozen assumptions (R01).

    Raises:
        ProtocolError: naming every violation, when the run does not conform.
    """
    violations = conformance_violations(record, universe=universe, start_date=start_date, end_date=end_date, cost_regimes=cost_regimes)
    if violations:
        raise ProtocolError(
            f"run does not conform to frozen protocol {record.protocol_digest[:12]} for experiment "
            f"{record.protocol.experiment!r}: {'; '.join(violations)}"
        )


def cost_disclosures(record: FrozenProtocol, applied: Sequence[CostApplication]) -> list[str]:
    """Disclosed asymmetries between the frozen cost assumptions and what ran (R01).

    A ratio that differs from the frozen one is a real research decision (the engine
    resolves a regime's fees its own way), so it is never silently re-frozen and never
    hidden: it is stated next to the evidence. Regimes are matched by name, which is
    what :func:`conformance_violations` requires to be identical.
    """
    frozen = {cost.regime: cost for cost in record.protocol.costs}
    disclosures: list[str] = []
    for cost in applied:
        expected = frozen.get(cost.regime)
        if expected is None:
            continue
        if (cost.fee_ratio, cost.tax_ratio) != (expected.fee_ratio, expected.tax_ratio):
            disclosures.append(
                f"cost asymmetry (disclosed): regime {cost.regime!r} applied fee_ratio {cost.fee_ratio} / tax_ratio {cost.tax_ratio}, "
                f"while the frozen protocol states fee_ratio {expected.fee_ratio} / tax_ratio {expected.tax_ratio}"
            )
    return disclosures


def protocol_binding(record: FrozenProtocol) -> dict[str, str]:
    """The scope-parameter binding an E01 run manifest records (no schema change).

    The digest and the experiment identity travel in ``RunScope.parameters``, so they
    are part of the run-ID preimage (a protocol-bound run is a different run from an
    unbound one) and readable from the stored manifest without a new field.
    """
    return {"protocol_digest": record.protocol_digest, "protocol_experiment": record.protocol.experiment}


def require_frozen_protocol(protocol: ResearchProtocol, *, root: Path | None = None) -> FrozenProtocol:
    """Return the matching frozen manifest, or refuse the run.

    This is the gate R00 puts in front of every research run: no manifest for the
    experiment refuses (assumptions absent), and a manifest whose assumptions differ
    refuses (assumptions changed) with the changed fields named. A protocol that
    freezes cleanly is returned exactly as recorded, so R01/R02 can bind their E01
    manifests to ``record.protocol_digest``.

    Raises:
        ProtocolError: when the assumptions are absent or changed.
    """
    records = [record for record in load_frozen_protocols(root) if record.protocol.experiment == protocol.experiment]
    if not records:
        raise ProtocolError(
            f"assumptions are absent: no frozen protocol manifest for experiment {protocol.experiment!r}; freeze the protocol before running"
        )
    digest = protocol.digest()
    for record in records:
        if record.protocol_digest == digest:
            return record
    latest = records[-1]
    changed = changed_fields(latest.protocol, protocol)
    raise ProtocolError(
        f"assumptions are changed for experiment {protocol.experiment!r}: frozen {latest.protocol_digest[:12]}, "
        f"candidate {digest[:12]}; changed fields: {', '.join(changed) if changed else '(unknown)'}. "
        "Freezing a new protocol is an explicit act, never an automatic fallback"
    )


__all__ = [
    "ENTRY_CLOCKS",
    "ENTRY_PRICES",
    "MANIFEST_VERSION",
    "PROTOCOL_FIELD_ORDER",
    "PROTOCOL_SCHEMA",
    "RESOURCE_BUDGET_KEYS",
    "FrozenProtocol",
    "ProtocolError",
    "ResearchProtocol",
    "ResourceBudget",
    "changed_fields",
    "conformance_violations",
    "cost_disclosures",
    "freeze_protocol",
    "load_frozen_protocols",
    "protocol_binding",
    "protocols_root",
    "require_frozen_protocol",
    "require_run_conformance",
    "select_frozen_protocol",
]
