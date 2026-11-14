"""E01-6 stage 2: the row-bound price-origin envelope at the write boundary.

Scope: ADR-0027 §2 (nullable additive storage, atomic value+marker, no fallback
label, no stale-marker preservation), §3 (who may assert a marker) and §4.1-§4.3
(frozen byte encodings with golden digests), plus decision D1 (generator-only
`synthetic`, real adapters keep producing absent/unknown), D2 (`price_source`
grammar), D5 (nullable Int16 + three nullable strings) and D6 (no backfill).

The three goldens are the hand-written preimages from the approved plan, not
recomputed from this implementation: a change to field order, decimal rendering,
sort order or separators must break one of them.

Every test uses ``tmp_path`` only: no ``data/`` read or write, no seeder run, no
provider/network/LLM call and no lake mutation.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable

from equity_lake.core.price_provenance import (  # type: ignore[import-untyped]
    PRICE_SOURCE_EVIDENCE_SCHEMA,
    PriceProvenanceError,
    canonical_decimal,
    envelope_projection_digest,
    generator_evidence_digest,
    generator_evidence_payload,
    prepare_price_provenance_frame,
    price_batch_digest,
    stamp_absent_provenance,
    validate_price_provenance,
)
from equity_lake.core.schemas import (  # type: ignore[import-untyped]
    PRICE_PROVENANCE_COLUMNS,
    PRICE_PROVENANCE_SCHEMA,
    PRICE_PROVENANCE_VERSION,
)
from equity_lake.ingestion import writers  # type: ignore[import-untyped]
from equity_lake.storage.delta import merge_delta, read_delta, write_delta  # type: ignore[import-untyped]

D0, D1 = date(2024, 1, 3), date(2024, 1, 4)
MIC = "XNAS"
SYMBOL = "SYNT"
MARKET = "us_equity"
PRICE_TABLE = "01_bronze/market_data/us_equity"
SOURCE = "generator:synthetic_demo_seed@1"

# Golden digests frozen in the approved E01-6 plan (ADR-0027 §4.1-§4.3).
GOLDEN_PRICE_BATCH = "ba8ca8a731c8a6f2bebc8055d32b79635a233a3f5f5365de50358a19bb736283"
GOLDEN_GENERATOR_EVIDENCE = "1f1a431537a128473a40551224a93fb435231a57d56eb99567603ff83b804959"
GOLDEN_ENVELOPE_PROJECTION = "341fc14bb53082ee902654272b7f2d1433d386510526544565b44921d53e5d9d"


def _batch() -> pl.DataFrame:
    """The two-row batch the plan's encodings were frozen against."""
    return pl.DataFrame(
        [
            {
                "market": MARKET,
                "mic": MIC,
                "symbol": SYMBOL,
                "date": D0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
                "adj_close": 100.5,
            },
            {
                "market": MARKET,
                "mic": MIC,
                "symbol": SYMBOL,
                "date": D1,
                "open": 100.5,
                "high": 102.0,
                "low": 100.0,
                "close": 101.5,
                "volume": 1200.0,
                "adj_close": 101.5,
            },
        ]
    )


def _synthetic(frame: pl.DataFrame, *, digest: str = GOLDEN_PRICE_BATCH, source: str = SOURCE) -> pl.DataFrame:
    """``frame`` with a complete synthetic claim (what the demo generator produces)."""
    return frame.with_columns(
        pl.lit(PRICE_PROVENANCE_VERSION, dtype=pl.Int16).alias("price_provenance_version"),
        pl.lit("synthetic", dtype=pl.String).alias("price_provenance"),
        pl.lit(source, dtype=pl.String).alias("price_source"),
        pl.lit(digest, dtype=pl.String).alias("price_source_sha256"),
    )


def _ohlcv(*, ticker: str = "AAPL", day: date = date(2026, 6, 2)) -> pl.DataFrame:
    return pl.DataFrame([{"ticker": ticker, "mic": MIC, "date": day, "open": 10.0, "high": 20.0, "low": 9.0, "close": 15.0, "volume": 1000}])


# ---------------------------------------------------------------------------
# 1. Frozen encodings (D3 / ADR-0027 §4.1-§4.3)
# ---------------------------------------------------------------------------


def test_price_batch_digest_matches_the_frozen_golden() -> None:
    """The batch preimage is exactly the plan's lines, order and number rendering.

    Row order is *not* part of the payload: the two lines are sorted, so the same batch
    read in any order hashes the same (and a reversed frame must not move the digest).

    Falsified by: a float repr (``100.0``), an exponent, a trailing newline, an
    unsorted line order or a different field order/separator.
    """
    assert price_batch_digest(_batch()) == GOLDEN_PRICE_BATCH
    assert price_batch_digest(_batch().reverse()) == GOLDEN_PRICE_BATCH


def test_generator_evidence_payload_and_digest_match_the_frozen_golden() -> None:
    """The generator's input manifest is canonical JSON with the documented keys.

    Falsified by: hashing only the generator name, dropping the revision/seed/parameters/
    sessions, changing key order or separators, or using the batch digest as the evidence
    digest (they are different claims).
    """
    payload = generator_evidence_payload(
        generator="synthetic_demo_seed",
        generator_revision="1",
        identities=[f"{MARKET}|{MIC}|{SYMBOL}"],
        parameters={"days": 2, "profile": "demo_v1", "seed": 7},
        price_batch_sha256=GOLDEN_PRICE_BATCH,
        sessions_start=D0,
        sessions_end=D1,
    )
    assert payload["schema"] == PRICE_SOURCE_EVIDENCE_SCHEMA == "price-source-evidence/1"
    assert generator_evidence_digest(payload) == GOLDEN_GENERATOR_EVIDENCE
    assert generator_evidence_digest(payload) != GOLDEN_PRICE_BATCH


def test_envelope_projection_digest_matches_the_frozen_golden() -> None:
    """The envelope projection covers identity, date, version, kind, source and digest.

    The plan's example rows carry the *batch* digest in the ``price_source_sha256``
    column: the projection treats that column as opaque, so this golden pins the
    encoding, not which digest a generator chooses to put there.

    Falsified by: omitting the version/kind/source/digest fields, rendering a null as
    anything but ``-``, or hashing the price values instead of the envelope.
    """
    marked = _synthetic(_batch(), digest=GOLDEN_PRICE_BATCH)
    assert envelope_projection_digest(marked) == GOLDEN_ENVELOPE_PROJECTION

    # Any change to a marker must move the projection digest.
    other = _synthetic(_batch(), digest=GOLDEN_GENERATOR_EVIDENCE)
    assert envelope_projection_digest(other) != GOLDEN_ENVELOPE_PROJECTION


def test_absent_evidence_projects_as_a_null_marker() -> None:
    """A row with no envelope renders ``-`` in every marker field (never a label).

    Falsified by: inferring a kind for an unmarked row while computing the digest.
    """
    assert envelope_projection_digest(_batch()) == envelope_projection_digest(stamp_absent_provenance(_batch()))
    assert envelope_projection_digest(stamp_absent_provenance(_batch())) != GOLDEN_ENVELOPE_PROJECTION


@pytest.mark.parametrize(
    ("value", "expected"),
    [(100.0, "100"), (100.5, "100.5"), (1000, "1000"), (0.00001, "0.00001"), (1e-7, "0.0000001"), (123456789.125, "123456789.125")],
)
def test_decimals_are_canonical_plain_text(value: float, expected: str) -> None:
    """Prices and volumes never render as a float repr or an exponent.

    ``1e-07`` is the case that distinguishes ``str(float)`` from a canonical decimal, so
    this — not the batch digest — is the regression for the rendering rule.
    """
    assert canonical_decimal(value) == expected
    assert "e" not in expected and "E" not in expected


def test_a_hyphenated_symbol_is_encoded_verbatim() -> None:
    """``BRK-B`` is a real ticker: only a bare ``-`` field means null.

    Falsified by: treating the null sentinel as a substring, which would refuse every
    hyphenated symbol (and ``000001-SZ``) at the write boundary.
    """
    frame = pl.DataFrame(
        [
            {
                "market": MARKET,
                "mic": "XNYS",
                "symbol": "BRK-B",
                "date": D0,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
            }
        ]
    )
    assert len(price_batch_digest(frame)) == 64
    with pytest.raises(PriceProvenanceError, match="null sentinel"):
        price_batch_digest(frame.with_columns(pl.lit("-").alias("symbol")))


# ---------------------------------------------------------------------------
# 2. The write-boundary contract (ADR-0027 §2, D2/D5)
# ---------------------------------------------------------------------------


def test_a_batch_without_evidence_is_stamped_as_typed_nulls() -> None:
    """Absent evidence materialises four typed null columns, never a label.

    Falsified by: omitting the columns (mixed table schemas), inventing ``unknown`` with
    a source, or stamping a non-null version on a row whose origin nobody can establish.
    """
    prepared = prepare_price_provenance_frame(_batch())
    assert prepared.schema["price_provenance_version"] == PRICE_PROVENANCE_SCHEMA["price_provenance_version"]
    for column in PRICE_PROVENANCE_COLUMNS:
        assert prepared[column].null_count() == prepared.height


@pytest.mark.parametrize(
    ("column", "value", "match"),
    [
        ("price_provenance_version", 99, "only version 1"),
        ("price_provenance", "legacy", r"must be one of"),
        ("price_source", "synthetic_demo_seed", "must match"),
        ("price_source_sha256", "not-a-digest", "lowercase hex sha256"),
    ],
)
def test_a_malformed_claim_is_refused(column: str, value: object, match: str) -> None:
    """Each field of a supported claim is checked; nothing is silently dropped."""
    frame = _synthetic(_batch()).with_columns(pl.lit(value, dtype=PRICE_PROVENANCE_SCHEMA[column]).alias(column))
    with pytest.raises(PriceProvenanceError, match=match):
        validate_price_provenance(frame)


def test_a_partial_or_contradictory_claim_is_refused() -> None:
    """A version without a kind, or a kind with a stub source, is not a claim.

    Falsified by: accepting a versioned row with no kind (a label that proves nothing) or
    an ``unknown`` row that still names a source and digest.
    """
    no_kind = _synthetic(_batch()).with_columns(pl.lit(None, dtype=pl.String).alias("price_provenance"))
    with pytest.raises(PriceProvenanceError, match="must name its provenance"):
        validate_price_provenance(no_kind)

    markerless_version = _batch().with_columns(pl.lit(PRICE_PROVENANCE_VERSION, dtype=pl.Int16).alias("price_provenance_version"))
    with pytest.raises(PriceProvenanceError, match="partial envelope"):
        validate_price_provenance(markerless_version)

    unknown_with_source = _synthetic(_batch()).with_columns(pl.lit("unknown", dtype=pl.String).alias("price_provenance"))
    with pytest.raises(PriceProvenanceError, match="names no source and no digest"):
        validate_price_provenance(unknown_with_source)


def test_legacy_all_null_rows_are_valid_and_stay_unknown() -> None:
    """The legacy posture is not a defect and is never relabelled (D6).

    Falsified by: refusing an all-null envelope, or upgrading one to a kind on read.
    """
    legacy = stamp_absent_provenance(_batch())
    validate_price_provenance(legacy)  # no exception
    assert legacy["price_provenance"].null_count() == legacy.height


def test_a_missing_column_is_a_partial_envelope() -> None:
    """Half an envelope is refused rather than completed from the batch."""
    partial = _synthetic(_batch()).drop("price_source_sha256")
    with pytest.raises(PriceProvenanceError, match="partial envelope"):
        validate_price_provenance(partial)


# ---------------------------------------------------------------------------
# 3. Delta writes: atomicity, schema evolution and no stale-marker preservation
# ---------------------------------------------------------------------------


def test_a_price_table_write_materialises_the_envelope_columns(tmp_path: Path) -> None:
    """The direct writer route stamps nulls on a price table and leaves other tables alone."""
    assert write_delta(_batch(), PRICE_TABLE, mode="overwrite", lake_dir=tmp_path) is True
    stored = read_delta(PRICE_TABLE, lake_dir=tmp_path)
    assert set(PRICE_PROVENANCE_COLUMNS).issubset(stored.columns)
    assert stored["price_provenance"].null_count() == stored.height
    delta_types = {field.name: str(field.type) for field in DeltaTable(str(tmp_path / PRICE_TABLE)).schema().fields}
    assert "short" in delta_types["price_provenance_version"]
    assert stored.schema["price_provenance_version"] == pl.Int16

    macro = pl.DataFrame(
        {
            "date": [D0],
            "indicator": ["vix"],
            "value": [1.0],
            "source": ["yfinance"],
            "updated_at": pl.Series([datetime(2024, 1, 3, tzinfo=UTC)], dtype=pl.Datetime("us", "UTC")),
        }
    )
    write_delta(macro, "01_bronze/macro", mode="overwrite", lake_dir=tmp_path)
    assert "price_provenance" not in read_delta("01_bronze/macro", lake_dir=tmp_path).columns


def test_a_marked_batch_round_trips_verbatim(tmp_path: Path) -> None:
    """A generator claim is stored exactly as asserted, on every row."""
    marked = _synthetic(_batch())
    assert write_delta(marked, PRICE_TABLE, mode="overwrite", lake_dir=tmp_path) is True
    stored = read_delta(PRICE_TABLE, lake_dir=tmp_path).sort("date")
    assert stored["price_provenance"].unique().to_list() == ["synthetic"]
    assert stored["price_source"].unique().to_list() == [SOURCE]
    assert stored["price_source_sha256"].unique().to_list() == [GOLDEN_PRICE_BATCH]
    assert stored["price_provenance_version"].to_list() == [PRICE_PROVENANCE_VERSION, PRICE_PROVENANCE_VERSION]


def test_replacing_values_replaces_the_marker_instead_of_keeping_it(tmp_path: Path) -> None:
    """A new value for the same key carries its own evidence, never the old marker.

    ADR-0027 §2: never preserve an earlier row's marker when replacing its values from
    another source, and never fill a new row's missing marker from the target row. A
    merge that kept the old ``synthetic`` claim next to re-fetched values would be a
    false claim.

    Falsified by: a merge/upsert that updates values but leaves provenance columns out of
    the update clause.
    """
    table = PRICE_TABLE
    assert write_delta(_synthetic(_batch()), table, mode="overwrite", lake_dir=tmp_path) is True

    updated = _batch().with_columns(pl.col("close") + 1.0)
    assert merge_delta(updated, table, key_columns=["market", "mic", "symbol", "date"], lake_dir=tmp_path) is True
    stored = read_delta(table, lake_dir=tmp_path).sort("date")
    assert stored["price_provenance"].null_count() == stored.height
    assert stored["price_source"].null_count() == stored.height
    assert stored["close"].to_list() == [101.5, 102.5]


# ---------------------------------------------------------------------------
# 4. The canonical writer route (`upsert_dataset`)
# ---------------------------------------------------------------------------


def test_the_canonical_price_route_stamps_and_persists_absent_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real source batch lands with an all-null envelope — no inferred claim."""
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    assert writers.upsert_dataset(_ohlcv(), MARKET, date(2026, 6, 2)) is True
    stored = read_delta(MARKET, lake_dir=tmp_path)
    assert stored["price_provenance"].null_count() == stored.height
    assert stored["price_provenance_version"].null_count() == stored.height


def test_the_canonical_route_refuses_a_malformed_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad envelope fails the write; it is never downgraded to a healthy unknown.

    Falsified by: logging the defect and writing the rows anyway, or by dropping only the
    offending rows and reporting success.
    """
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    bad = _synthetic(_ohlcv()).with_columns(
        pl.lit("real", dtype=pl.String).alias("price_provenance"),
        pl.lit(7, dtype=pl.Int16).alias("price_provenance_version"),
    )
    assert writers.upsert_dataset(bad, MARKET, date(2026, 6, 2)) is False
    assert not (tmp_path / MARKET).exists()


def test_dry_run_writes_no_marker_and_no_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run stays side-effect free: no rows, no stamp, no table."""
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    assert writers.upsert_dataset(_synthetic(_ohlcv()), MARKET, date(2026, 6, 2), dry_run=True) is True
    assert not (tmp_path / MARKET).exists()


def test_the_claim_digest_is_reproducible_from_the_stored_batch(tmp_path: Path) -> None:
    """An auditor can recompute the batch digest from what was stored.

    Falsified by: hashing something the writer does not persist (a log line, a path, a
    timestamp), which would make the recorded digest unverifiable later.
    """
    marked = _synthetic(_batch())
    assert write_delta(marked, PRICE_TABLE, mode="overwrite", lake_dir=tmp_path) is True
    stored = read_delta(PRICE_TABLE, lake_dir=tmp_path)
    assert price_batch_digest(stored) == GOLDEN_PRICE_BATCH
    # Independent oracle: the frozen golden is the hash of these literal lines, so it
    # does not depend on this module's rendering being self-consistently wrong.
    payload = "\n".join(
        sorted(
            [
                "us_equity|XNAS|SYNT|2024-01-03|100|101|99|100.5|1000|100.5",
                "us_equity|XNAS|SYNT|2024-01-04|100.5|102|100|101.5|1200|101.5",
            ]
        )
    )
    assert hashlib.sha256(payload.encode()).hexdigest() == GOLDEN_PRICE_BATCH


# ---------------------------------------------------------------------------
# 5. Storage dtypes, session granularity, legacy evolution and idempotency
# ---------------------------------------------------------------------------


def test_the_version_column_is_normalised_to_the_documented_dtype() -> None:
    """Whatever integer width a caller used, the stored version is Int16 (D5).

    The catalog and the DuckDB view describe one schema, so a writer must not persist a
    ``bigint`` version just because a caller built the frame from Python ints.

    Falsified by: stamping/validating values without normalising the column dtype (the
    claim would be correct while the table schema contradicted its own catalog).
    """
    marked = _batch().with_columns(
        pl.lit(1).alias("price_provenance_version"),  # Int32 from a Python int
        pl.lit("synthetic").alias("price_provenance"),
        pl.lit(SOURCE).alias("price_source"),
        pl.lit(GOLDEN_PRICE_BATCH).alias("price_source_sha256"),
    )
    assert marked.schema["price_provenance_version"] != pl.Int16
    prepared = prepare_price_provenance_frame(marked)
    assert prepared.schema["price_provenance_version"] == pl.Int16

    # An all-null legacy posture is normalised too: the early "no evidence" path must not
    # skip the dtype contract either.
    legacy = _batch().with_columns(pl.lit(None, dtype=pl.Int32).alias("price_provenance_version"))
    assert prepare_price_provenance_frame(legacy).schema["price_provenance_version"] == pl.Int16


def test_a_mistyped_marker_column_is_refused_rather_than_cast() -> None:
    """A non-string marker is a producer bug, not something to stringify silently.

    Falsified by: casting arbitrary values into the envelope, which would let an enum or a
    numeric code become a stored claim.
    """
    wrong_source = _synthetic(_batch()).with_columns(pl.lit(7, dtype=pl.Int64).alias("price_source"))
    with pytest.raises(PriceProvenanceError, match="must be String"):
        prepare_price_provenance_frame(wrong_source)

    float_version = _synthetic(_batch()).with_columns(pl.lit(1.0).alias("price_provenance_version"))
    with pytest.raises(PriceProvenanceError, match="must be an integer version"):
        prepare_price_provenance_frame(float_version)


def test_an_intraday_timestamp_is_refused_not_silently_narrowed() -> None:
    """These encodings are session-grained: two observations must not collide.

    A midnight timestamp (what a parquet/Delta read-back of a date column can produce) is
    the same session date and hashes identically; a real time of day is refused.

    Falsified by: dropping the time component unconditionally, which would make 09:30 and
    16:00 bars hash the same.
    """
    midnight = _batch().with_columns(pl.col("date").cast(pl.Datetime("us")).alias("date"))
    assert price_batch_digest(midnight) == GOLDEN_PRICE_BATCH

    intraday = _batch().with_columns((pl.col("date").cast(pl.Datetime("us")) + pl.duration(hours=9, minutes=30)).alias("date"))
    with pytest.raises(PriceProvenanceError, match="must not carry an intraday time"):
        price_batch_digest(intraday)


def test_appending_a_marked_batch_evolves_a_legacy_price_table(tmp_path: Path) -> None:
    """A pre-envelope table gains the columns; its old rows stay null (D6).

    This is the direct ``write_delta`` route (bootstrap/migration shape) rather than a
    merge, so it pins that schema evolution and the envelope rule agree.

    Falsified by: an append that either drops the new columns or backfills the legacy rows.
    """
    from deltalake import write_deltalake

    table_path = tmp_path / PRICE_TABLE
    # A session *before* the appended batch, so the legacy row is uniquely identified by
    # date: reusing an appended date would tie two rows and make the assertion depend on
    # sort order rather than on which row was backfilled.
    legacy_day = date(2024, 1, 2)
    legacy = pl.DataFrame({"market": [MARKET], "mic": [MIC], "symbol": [SYMBOL], "date": [legacy_day], "close": [99.0]})
    write_deltalake(str(table_path), legacy.to_arrow(), mode="overwrite", partition_by=["date"])

    assert write_delta(_synthetic(_batch()), PRICE_TABLE, mode="append", lake_dir=tmp_path, schema_mode="merge") is True
    stored = read_delta(PRICE_TABLE, lake_dir=tmp_path).sort("date")
    assert set(PRICE_PROVENANCE_COLUMNS).issubset(stored.columns)
    assert stored["price_provenance"].to_list() == [None, "synthetic", "synthetic"]


def test_repeating_a_marked_merge_is_idempotent(tmp_path: Path) -> None:
    """Re-applying the same evidenced batch changes neither rows nor markers."""
    marked = _synthetic(_batch())
    keys = ["market", "mic", "symbol", "date"]
    assert merge_delta(marked, PRICE_TABLE, key_columns=keys, lake_dir=tmp_path) is True
    assert merge_delta(marked, PRICE_TABLE, key_columns=keys, lake_dir=tmp_path) is True
    stored = read_delta(PRICE_TABLE, lake_dir=tmp_path)
    assert stored.height == 2
    assert stored["price_provenance"].unique().to_list() == ["synthetic"]
    assert stored["price_source_sha256"].unique().to_list() == [GOLDEN_PRICE_BATCH]
