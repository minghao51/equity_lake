"""Row-bound price-origin provenance: the frozen envelope contract (ADR-0027).

ADR-0027 puts the origin assertion **on each price row** rather than on a table, a
path or a source configuration, because one lake can hold legacy, synthetic and real
rows at once. This module owns everything about that envelope that is shared across
layers — the nullable columns, the byte encodings, and the write-boundary checks — so
the writers, the seeder, the validation boundary and (later) the read seam cannot
disagree about what a claim means. It lives in ``core`` like
:mod:`equity_lake.core.availability`, for the same reason: it is a contract several
layers import, not a storage detail.

Frozen encodings (ADR-0027 §4). Every payload is UTF-8, has no BOM, no trailing
newline, ``"\n"`` separators and lexicographically sorted lines; JSON is dumped with
``sort_keys=True, separators=(",", ":")`` and ``ensure_ascii=True``; decimals are
canonical plain text (never an exponent or a float repr); a null field is ``-``. No
payload may contain a path, hostname, timestamp, environment value, credential, URL or
token: the source grammar is what keeps those out.

Three digests are pinned by golden fixtures in
``tests/unit/test_price_provenance_envelope.py``:

1. :func:`price_batch_digest` — the exact price batch (identity, date and OHLCV).
2. :func:`generator_evidence_digest` — the generator's deterministic input manifest,
   which is what a ``synthetic`` row's ``price_source_sha256`` holds.
3. :func:`envelope_projection_digest` — the consumed rows' identity/date/envelope
   projection, which is what a manifest's price-origin summary records.

Asserting a claim is not permission to fetch one: ADR-0027 leaves real-provider
evidence and source access to D01, and no function here reads a network, a
configuration or a stored table.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import polars as pl

from equity_lake.core.schemas import (
    PRICE_PROVENANCE_COLUMNS,
    PRICE_PROVENANCE_KINDS,
    PRICE_PROVENANCE_SCHEMA,
    PRICE_PROVENANCE_VERSION,
    PRICE_SOURCE_PATTERN,
)

#: Version-1 evidence preimage schema identifier (ADR-0027 §4.2).
PRICE_SOURCE_EVIDENCE_SCHEMA = "price-source-evidence/1"

#: Fields of a price-batch line, in the documented order (ADR-0027 §4.1).
PRICE_BATCH_FIELDS: tuple[str, ...] = (
    "market",
    "mic",
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
)

#: Fields of an envelope-projection line, in the documented order (ADR-0027 §4.3).
ENVELOPE_PROJECTION_FIELDS: tuple[str, ...] = (
    "market",
    "mic",
    "symbol",
    "date",
    "price_provenance_version",
    "price_provenance",
    "price_source",
    "price_source_sha256",
)

#: Rendered in place of a null field.
_NULL_FIELD = "-"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PRICE_SOURCE_RE = re.compile(PRICE_SOURCE_PATTERN)


class PriceProvenanceError(ValueError):
    """A price-origin envelope that no writer may persist.

    Raised for a partial, malformed, contradictory or unsupported claim. The write
    boundary refuses the batch instead of dropping the offending rows or downgrading
    the defect to a healthy ``unknown`` (ADR-0027 §2/§4).
    """


def canonical_decimal(value: Any) -> str:
    """Render a number as canonical plain text — never an exponent, never a float repr.

    ``100.0`` becomes ``100`` and ``100.5`` stays ``100.5``, so two encoders cannot
    disagree about the same value. Non-finite values are refused: ``nan``/``inf`` are
    not canonical text and must never reach a digest preimage.
    """
    if isinstance(value, bool):
        raise PriceProvenanceError(f"a boolean is not a price value: {value!r}")
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, (float, Decimal)):
        raise PriceProvenanceError(f"not a numeric price value: {value!r}")
    text = format(Decimal(str(value)), "f")
    if text in {"nan", "inf", "-inf", "Infinity", "-Infinity"}:
        raise PriceProvenanceError(f"a non-finite price value is not canonical evidence: {value!r}")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _render(value: Any) -> str:
    """Render one field: ``-`` for null, ISO date, canonical decimal or the raw text.

    A ``datetime`` renders as its date: these encodings are session-grained, and a frame
    read back from parquet/Delta may carry a midnight timestamp where the writer had a
    date, which must not change the digest. A timestamp carrying an actual time of day is
    refused instead of being silently narrowed, because two different observations would
    otherwise collide in the digest.
    """
    if value is None:
        return _NULL_FIELD
    if isinstance(value, datetime):
        if value.time() != time.min:
            raise PriceProvenanceError(f"a session-grained field must not carry an intraday time: {value!r}")
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        raise PriceProvenanceError(f"a boolean is not evidence: {value!r}")
    if isinstance(value, (int, float, Decimal)):
        return canonical_decimal(value)
    text = str(value)
    if "\n" in text or "|" in text:
        raise PriceProvenanceError(f"field value {value!r} would break the canonical line encoding")
    if text == _NULL_FIELD:
        # A literal '-' field would be indistinguishable from a null once encoded, so it
        # is refused rather than silently read back as "no value". Hyphens *inside* a
        # value are legitimate (``BRK-B``, ``000001-SZ``) and are encoded verbatim.
        raise PriceProvenanceError(f"field value {value!r} is the null sentinel and cannot be encoded")
    return text


def _digest_lines(lines: Iterable[str]) -> str:
    """SHA-256 of sorted, newline-separated, unterminated canonical lines."""
    payload = "\n".join(sorted(lines))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def price_batch_digest(frame: pl.DataFrame, *, fields: Sequence[str] = PRICE_BATCH_FIELDS) -> str:
    """SHA-256 of the exact price batch (ADR-0027 §4.1).

    Lines are ``market|MIC|symbol|date|open|high|low|close|volume|adj_close`` per row,
    sorted lexicographically. A missing column is rendered as a null field, so the
    digest depends on the batch's content rather than on which optional columns a
    particular fetch happened to carry.
    """
    return _digest_lines(_lines(frame, fields))


def generator_evidence_payload(
    *,
    generator: str,
    generator_revision: str,
    identities: Sequence[str],
    parameters: Mapping[str, Any],
    price_batch_sha256: str,
    sessions_start: date,
    sessions_end: date,
) -> dict[str, Any]:
    """The generator's deterministic input manifest (ADR-0027 §4.2), as a mapping.

    A generator hashes what it was told to generate — its revision, seed and tuning
    parameters, the exact identity/session scope and the digest of the batch it
    produced — because it has no response bytes to hash. A configuration hash or a
    source name alone would be insufficient.
    """
    if not identities:
        raise PriceProvenanceError("generator evidence needs at least one identity")
    if _SHA256_PATTERN.fullmatch(price_batch_sha256) is None:
        raise PriceProvenanceError(f"price_batch_sha256 must be a lowercase hex sha256 digest, got {price_batch_sha256!r}")
    if sessions_end < sessions_start:
        raise PriceProvenanceError("generator evidence sessions must not run backwards")
    return {
        "generator": generator,
        "generator_revision": str(generator_revision),
        "identities": sorted(set(identities)),
        "parameters": _canonical_json_value(parameters),
        "price_batch_sha256": price_batch_sha256,
        "schema": PRICE_SOURCE_EVIDENCE_SCHEMA,
        "sessions": {"end": sessions_end.isoformat(), "start": sessions_start.isoformat()},
    }


def generator_evidence_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of :func:`generator_evidence_payload`."""
    encoded = json.dumps(_canonical_json_value(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def envelope_projection_digest(frame: pl.DataFrame) -> str:
    """SHA-256 of the consumed rows' identity/date/envelope projection (ADR-0027 §4.3).

    Lines are ``market|MIC|symbol|date|version|provenance|source|source_sha256`` with
    ``-`` for null, so the digest changes when a marker, its version or its bytes
    change — not merely when the price values do.
    """
    return _digest_lines(_lines(frame, ENVELOPE_PROJECTION_FIELDS))


def _lines(frame: pl.DataFrame, fields: Sequence[str]) -> list[str]:
    expressions = [pl.col(column).alias(column) if column in frame.columns else pl.lit(None).alias(column) for column in fields]
    return ["|".join(_render(value) for value in row) for row in frame.select(expressions).iter_rows()]


def _canonical_json_value(value: Any) -> Any:
    """Order mappings and stringify non-JSON scalars so the preimage is stable."""
    if isinstance(value, Mapping):
        return {str(key): _canonical_json_value(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def stamp_absent_provenance(frame: pl.DataFrame) -> pl.DataFrame:
    """Add the envelope columns as typed nulls where the batch carries none.

    A source that cannot establish origin supplies no evidence, and "absent" is what
    legacy rows already mean. Materialising the columns (rather than omitting them)
    keeps every price table on one schema, so a DuckDB ``UNION ALL`` across markets
    cannot misalign, while leaving the claim null.
    """
    missing = [column for column in PRICE_PROVENANCE_COLUMNS if column not in frame.columns]
    if not missing:
        return frame
    return frame.with_columns([pl.lit(None, dtype=PRICE_PROVENANCE_SCHEMA[column]).alias(column) for column in missing])


def prepare_price_provenance_frame(frame: pl.DataFrame) -> pl.DataFrame:
    """Stamp absent evidence, normalise the storage dtypes, then validate every claim.

    This is the single entry point a price write boundary calls: it never invents a
    claim, never converts a malformed one into a healthy ``unknown``, and never rewrites
    the values it is given. It *does* normalise the envelope's dtypes, so what lands is
    always the documented contract (nullable ``Int16`` version plus three strings) even
    when a caller built the frame from Python objects (``Int64``) — the catalog and the
    DuckDB view describe one schema, and a write must not contradict it.
    """
    prepared = _normalise_envelope_dtypes(stamp_absent_provenance(frame))
    validate_price_provenance(prepared)
    return prepared


def _normalise_envelope_dtypes(frame: pl.DataFrame) -> pl.DataFrame:
    """Coerce the version to ``Int16`` and refuse a mistyped marker column.

    A wrong string column is refused rather than cast: silently stringifying an enum would
    turn a producer bug into a stored claim. A wider integer version is safe to narrow
    only because :func:`validate_price_provenance` then requires the value to be exactly
    the supported version, so no information is lost (and an out-of-range value fails the
    cast rather than becoming null).
    """
    for column in ("price_provenance", "price_source", "price_source_sha256"):
        dtype = frame.schema[column]
        if dtype != pl.String:
            raise PriceProvenanceError(f"envelope column {column!r} must be String, got {dtype}")
    version_dtype = frame.schema["price_provenance_version"]
    if version_dtype == PRICE_PROVENANCE_SCHEMA["price_provenance_version"]:
        return frame
    integer_dtypes = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
    if version_dtype not in integer_dtypes:
        raise PriceProvenanceError(f"envelope column 'price_provenance_version' must be an integer version, got {version_dtype}")
    try:
        return frame.with_columns(pl.col("price_provenance_version").cast(PRICE_PROVENANCE_SCHEMA["price_provenance_version"]))
    except pl.exceptions.InvalidOperationError as exc:  # pragma: no cover - defensive: narrowing already-validated values
        raise PriceProvenanceError(f"envelope version cannot be narrowed to Int16: {exc}") from exc


def validate_price_provenance(frame: pl.DataFrame) -> None:
    """Refuse a partial, malformed, contradictory or unsupported envelope.

    Rules (ADR-0027 §2, version 1): a null version means no envelope at all, so every
    other field must be null too; a known version carries one of
    ``synthetic|real|unknown``; ``unknown`` names no source and no digest; a
    ``synthetic``/``real`` row needs both, with the source matching the D2 grammar and
    the digest a lowercase SHA-256. Legacy all-null rows are valid and stay
    ``unknown`` — they are never relabelled here.

    Raises:
        PriceProvenanceError: naming the offending claim and why it cannot be stored.
    """
    present = [column for column in PRICE_PROVENANCE_COLUMNS if column in frame.columns]
    if not present:
        return
    if len(present) != len(PRICE_PROVENANCE_COLUMNS):
        missing = [column for column in PRICE_PROVENANCE_COLUMNS if column not in frame.columns]
        raise PriceProvenanceError(f"partial envelope: the batch carries {present} but not {missing}")
    for column in ("price_provenance", "price_source", "price_source_sha256"):
        if frame.schema[column] != pl.String:
            raise PriceProvenanceError(f"envelope column {column!r} must be String, got {frame.schema[column]}")
    version_dtype = frame.schema["price_provenance_version"]
    if version_dtype not in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
        raise PriceProvenanceError(f"envelope column 'price_provenance_version' must be an integer version, got {version_dtype}")
    if frame.is_empty():
        return
    if frame.select(PRICE_PROVENANCE_COLUMNS).null_count().row(0) == (frame.height,) * len(PRICE_PROVENANCE_COLUMNS):
        return  # legacy posture: no row carries any evidence

    for claim in frame.select(PRICE_PROVENANCE_COLUMNS).unique().iter_rows(named=True):
        _validate_claim(claim)


def _validate_claim(claim: Mapping[str, Any]) -> None:
    """Validate one distinct ``(version, kind, source, digest)`` claim."""
    version = claim["price_provenance_version"]
    kind = claim["price_provenance"]
    source = claim["price_source"]
    digest = claim["price_source_sha256"]
    described = f"(version={version!r}, provenance={kind!r}, source={source!r}, digest={digest!r})"

    if version is None:
        if kind is not None or source is not None or digest is not None:
            raise PriceProvenanceError(f"partial envelope {described}: a marker without a version is not a claim")
        return
    if isinstance(version, bool) or not isinstance(version, int):
        raise PriceProvenanceError(f"invalid envelope {described}: price_provenance_version must be an integer")
    if version != PRICE_PROVENANCE_VERSION:
        raise PriceProvenanceError(f"unsupported envelope {described}: only version {PRICE_PROVENANCE_VERSION} may be written")
    if kind is None:
        raise PriceProvenanceError(f"partial envelope {described}: a versioned row must name its provenance")
    if kind not in PRICE_PROVENANCE_KINDS:
        raise PriceProvenanceError(f"invalid envelope {described}: provenance must be one of {list(PRICE_PROVENANCE_KINDS)}")

    if kind == "unknown":
        if source is not None or digest is not None:
            raise PriceProvenanceError(f"contradictory envelope {described}: 'unknown' names no source and no digest")
        return
    if not isinstance(source, str) or _PRICE_SOURCE_RE.fullmatch(source) is None:
        raise PriceProvenanceError(f"invalid envelope {described}: price_source must match {PRICE_SOURCE_PATTERN!r}")
    if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
        raise PriceProvenanceError(f"invalid envelope {described}: price_source_sha256 must be a lowercase hex sha256 digest")


__all__ = [
    "ENVELOPE_PROJECTION_FIELDS",
    "PRICE_BATCH_FIELDS",
    "PRICE_SOURCE_EVIDENCE_SCHEMA",
    "PriceProvenanceError",
    "canonical_decimal",
    "envelope_projection_digest",
    "generator_evidence_digest",
    "generator_evidence_payload",
    "prepare_price_provenance_frame",
    "price_batch_digest",
    "stamp_absent_provenance",
    "validate_price_provenance",
]
