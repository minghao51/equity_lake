# ADR-0003: Polars-first dataframe engine

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled)

## Context

Pandas was the original interchange format across ingestion, validation,
features, and ML. The pipeline is batch, columnar, and local-first; pandas
brought eager-materialization costs and a second type system next to DuckDB
and Delta/Parquet.

## Decision

- Polars is the primary dataframe engine across ingestion, validation,
  feature, and ML stages.
- Pandas appears only at external-library boundaries that require it
  (yfinance, akshare, efinance), converted in and out at the client seam.

## Consequences

- One columnar type system end-to-end; lazy evaluation available for large
  scans.
- Boundary code must own explicit conversions; pandas-only idioms do not
  propagate inward.
- Pointblank (ADR-0007) and the vector backtest engine (ADR-0008) were chosen
  partly because they are Polars-native.
