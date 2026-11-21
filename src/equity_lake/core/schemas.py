"""Canonical column schemas and data-type definitions."""

from __future__ import annotations

import polars as pl

# Shared Gold feature contract versions (ADR-0015).
FEATURE_SCHEMA_VERSION = 8

# Row-bound forecast provenance (ADR-0021). The envelope version gates the
# proof rules: a version-1 row carries the E1-E4 evidence below and is checked
# at the predictions write boundary. Direction/probability derivation and the
# folder schema constants are unchanged.
FORECAST_PROVENANCE_VERSION = 1
PREDICTION_SEMANTICS_VERSION = 1
PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE = "next_session_return_positive"
PREDICTION_TARGET_CANDIDATE_EXECUTE = "candidate_execute"

# All nullable, additive prediction columns (ADR-0017). ``model_version`` stays
# filename-derived and is never proof; ``feature_schema_compatibility`` is not
# persisted because it is derivable from the two schema-version columns plus the
# stored ``feature_schema_version``.
FORECAST_PROVENANCE_COLUMNS = [
    "forecast_provenance_version",
    "model_provenance_version",
    "model_artifact_sha256",
    "model_trained_at",
    "model_age_days",
    "model_max_age_days",
    "training_feature_schema_version",
    "inference_feature_schema_version",
    "price_adjustment_version",
    "availability_schema_version",
    "missing_value_policy",
    "candidate_strategy_fingerprint",
    "horizon_days",
    "threshold_used",
    "threshold_source",
    "prediction_target",
    "prediction_semantics_version",
]

# Row-bound price-origin provenance (ADR-0027). The vocabulary below is shared by the
# manifest reader/validator, the write boundary and the read seam. Version 1 is the
# supported envelope: four nullable, additive columns on the existing Bronze price
# tables (ADR-0017). A supported row carries its source id and the digest of its exact
# price batch; a version-1 ``unknown`` row (and every legacy row) carries null source
# and digest and stays ``unknown`` forever (no backfill).
PRICE_PROVENANCE_VERSION = 1
PRICE_PROVENANCE_COLUMNS = [
    "price_provenance_version",
    "price_provenance",
    "price_source",
    "price_source_sha256",
]
#: Row kinds. ``mixed`` exists only as a read-seam aggregate, never on a row.
PRICE_PROVENANCE_KINDS = ("synthetic", "real", "unknown")
#: Polars dtypes every writer materialises for a price batch, so an absent-evidence
#: column is a typed null rather than an omitted column.
PRICE_PROVENANCE_SCHEMA: dict[str, pl.DataType] = {
    "price_provenance_version": pl.Int16(),
    "price_provenance": pl.String(),
    "price_source": pl.String(),
    "price_source_sha256": pl.String(),
}
#: Non-secret producer identity grammar (ADR-0027 decision D2):
#: ``generator|provider:<lowercase-id>@<positive-revision>``. The grammar is what
#: keeps a credential, URL, query token or absolute path out of recorded evidence.
PRICE_SOURCE_PATTERN = r"^(generator|provider):[a-z0-9][a-z0-9_]{2,31}@[1-9][0-9]{0,3}$"
#: Closed set of reasons an aggregate is ``unknown`` (ADR-0027 §4). ``empty_scope``
#: is derived from a zero-row scope; the others describe missing or unreadable row
#: evidence. Shared with the read seam, which classifies rows before projection.
PRICE_ORIGIN_UNKNOWN_REASONS = (
    "missing_envelope",
    "unsupported_version",
    "incomplete_binding",
    "untracked_input",
    "empty_scope",
)
#: Read-seam name prefix of a price snapshot (``price_marks:<market>``), shared by the
#: loader that records them and the manifest validator that requires one price-origin
#: summary per price snapshot.
PRICE_SNAPSHOT_NAME_PREFIX = "price_marks:"
#: Read-seam name prefix of a corporate-action coverage snapshot
#: (``action_coverage:<market>``). Only these snapshots record the ADR-0032 coverage
#: basis they were read under (see ``backtesting/data_loader.py`` and
#: ``backtesting/evidence.py``).
ACTION_COVERAGE_SNAPSHOT_NAME_PREFIX = "action_coverage:"

# Canonical shared-table identity (ADR-0016). Bronze OHLCV remains unchanged;
# identity is added only when explicit source provenance is available.
INSTRUMENT_IDENTITY_COLUMNS = ["market", "mic", "symbol"]
INSTRUMENT_DATE_KEY_COLUMNS = [*INSTRUMENT_IDENTITY_COLUMNS, "date"]

STANDARD_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    # Optional provider/current-config identity metadata is propagated when
    # available; ingestion write boundaries reject unidentified new price rows.
    "market",
    "mic",
    "symbol",
    "exchange",
    "identity_schema_version",
    "identity_provenance",
    # Row-bound price origin (ADR-0027). Nullable and additive: absent evidence is
    # null/unknown, never inferred from the path, the source name or the identity.
    *PRICE_PROVENANCE_COLUMNS,
]

NEWS_COLUMNS = [
    "ticker",
    "date",
    "datetime",
    "source",
    "headline",
    "summary",
    "url",
    "category",
    "sentiment_score",
    "sentiment_label",
    "relevance_score",
]

SOCIAL_COLUMNS = [
    "ticker",
    "date",
    "datetime",
    "source",
    "mention_count",
    "positive_score",
    "negative_score",
    "score",
    "social_metric",
]

MACRO_COLUMNS = [
    "date",
    "indicator",
    "value",
    "source",
    "updated_at",
]

# Corporate actions (ADR-0011): one row per (ticker, ex_date, action).
# ``value`` is the cash dividend per share (``dividend``) or the split ratio
# (``split``, e.g. 0.5 for a 2-for-1 split).
CORPORATE_ACTION_COLUMNS = [
    "ticker",
    "ex_date",
    "action",
    "value",
    "source",
    "ingested_at",
    # Source publication/availability clock; nullable means unknown and is
    # unusable for a strict point-in-time adjustment (ADR-0015).
    "source_available_at",
]
CORPORATE_ACTION_REQUIRED_COLUMNS = ["ticker", "ex_date", "action", "value", "source", "ingested_at"]
CORPORATE_ACTION_TYPES = ("dividend", "split")

CORPORATE_ACTION_COVERAGE_COLUMNS = [
    "market",
    "mic",
    "symbol",
    "source",
    "coverage_start",
    "coverage_end",
    "source_available_at",
    "source_valid_until",
    "coverage_status",
    "coverage_schema_version",
    # Named coverage basis (ADR-0032 decision 5): ``source_clock`` or
    # ``vendor_events_no_clock``. A row without the column predates it and is strict.
    "coverage_basis",
]
#: Coverage source label of the canonical demo seed. Shared by the demo generator and
#: the read seam, which refuses to pair synthetic rows with a vendor proof or vice versa.
SYNTHETIC_COVERAGE_SOURCE = "synthetic_demo_seed"

# The legacy ingestion timestamp remains naive UTC. A source clock is stored as
# timezone-aware UTC and is never synthesized from ingestion time.
CORPORATE_ACTION_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.String(),
    "ex_date": pl.Date(),
    "action": pl.String(),
    "value": pl.Float64(),
    "source": pl.String(),
    "ingested_at": pl.Datetime("us"),
    "source_available_at": pl.Datetime("us", "UTC"),
}

CORPORATE_ACTION_COVERAGE_SCHEMA: dict[str, pl.DataType] = {
    "market": pl.String(),
    "mic": pl.String(),
    "symbol": pl.String(),
    "source": pl.String(),
    "coverage_start": pl.Date(),
    "coverage_end": pl.Date(),
    "source_available_at": pl.Datetime("us", "UTC"),
    "source_valid_until": pl.Datetime("us", "UTC"),
    "coverage_status": pl.String(),
    "coverage_schema_version": pl.Int8(),
    "coverage_basis": pl.String(),
}

MACRO_INDICATOR_CONFIG = {
    "dxy": {"source": "yfinance", "ticker": "^DXY"},
    "treasury_10y": {"source": "yfinance", "ticker": "^TNX"},
    "tips_yield": {"source": "fred", "series": "DFII10"},
    "breakeven_inflation": {"source": "fred", "series": "T10YIE"},
    "vix": {"source": "yfinance", "ticker": "^VIX"},
    "gld": {"source": "yfinance", "ticker": "GLD"},
    "iau": {"source": "yfinance", "ticker": "IAU"},
    "policy_uncertainty": {"source": "fred", "series": "USEPUINDXD"},
}

BRONZE_ARTICLE_COLUMNS = [
    "article_id",
    "source_type",
    "source_name",
    "source_url",
    "title",
    "body",
    "author",
    "published_at",
    "fetched_at",
    "source_metadata",
    "date",
]

SILVER_ARTICLE_COLUMNS = [
    "article_id",
    "ticker",
    "source_type",
    "source_name",
    "published_at",
    "date",
    "sentiment_score",
    "sentiment_label",
    "confidence",
    "event_type",
    "summary",
    "impact_horizon",
    "market_relevance",
    "key_entities",
    "source_metadata",
]

ANALYST_RATING_COLUMNS = [
    "ticker",
    "date",
    "period",
    "strong_buy",
    "buy",
    "hold",
    "sell",
    "strong_sell",
    "consensus_score",
    "consensus_label",
    "price_target_mean",
    "price_target_median",
    "price_target_high",
    "price_target_low",
    "price_target_count",
    "fetched_at",
]

SEC_EXTRACTION_COLUMNS = [
    "article_id",
    "ticker",
    "filing_type",
    "section_type",
    "filing_date",
    "date",
    "risk_sentiment",
    "key_risks",
    "guidance_direction",
    "forward_statements",
    "management_tone",
    "new_vs_repeated",
    "summary",
    "fetched_at",
]

SEC_FINANCIAL_COLUMNS = [
    "ticker",
    "date",
    "filing_type",
    "fiscal_year",
    "fiscal_period",
    "revenue",
    "net_income",
    "operating_income",
    "total_assets",
    "total_liabilities",
    "stockholders_equity",
    "total_debt",
    "cash_and_equivalents",
    "operating_cash_flow",
    "capex",
    "shares_outstanding",
    "eps",
    "roe",
    "roa",
    "debt_to_equity",
    "net_margin",
    "operating_margin",
    "fetched_at",
]

__all__ = [
    "ACTION_COVERAGE_SNAPSHOT_NAME_PREFIX",
    "ANALYST_RATING_COLUMNS",
    "BRONZE_ARTICLE_COLUMNS",
    "CORPORATE_ACTION_COLUMNS",
    "CORPORATE_ACTION_COVERAGE_COLUMNS",
    "CORPORATE_ACTION_COVERAGE_SCHEMA",
    "CORPORATE_ACTION_REQUIRED_COLUMNS",
    "CORPORATE_ACTION_SCHEMA",
    "CORPORATE_ACTION_TYPES",
    "SYNTHETIC_COVERAGE_SOURCE",
    "FEATURE_SCHEMA_VERSION",
    "FORECAST_PROVENANCE_COLUMNS",
    "FORECAST_PROVENANCE_VERSION",
    "INSTRUMENT_DATE_KEY_COLUMNS",
    "INSTRUMENT_IDENTITY_COLUMNS",
    "MACRO_COLUMNS",
    "MACRO_INDICATOR_CONFIG",
    "NEWS_COLUMNS",
    "PREDICTION_SEMANTICS_VERSION",
    "PREDICTION_TARGET_CANDIDATE_EXECUTE",
    "PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE",
    "PRICE_ORIGIN_UNKNOWN_REASONS",
    "PRICE_PROVENANCE_COLUMNS",
    "PRICE_PROVENANCE_KINDS",
    "PRICE_PROVENANCE_SCHEMA",
    "PRICE_PROVENANCE_VERSION",
    "PRICE_SNAPSHOT_NAME_PREFIX",
    "PRICE_SOURCE_PATTERN",
    "SEC_EXTRACTION_COLUMNS",
    "SEC_FINANCIAL_COLUMNS",
    "SILVER_ARTICLE_COLUMNS",
    "SOCIAL_COLUMNS",
    "STANDARD_COLUMNS",
]
