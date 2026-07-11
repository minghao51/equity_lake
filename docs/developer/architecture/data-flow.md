# Data Flow

Equity Lake uses four numbered medallion layers. Source identifiers are routed
by `ingestion/router.py`; destinations are mapped by `ingestion/types.py` and
written as date-partitioned Delta tables whose data files are Parquet.

```mermaid
flowchart LR
    M[Structured market sources] --> V[Router and schema validation]
    U[Unstructured news and social sources] --> V
    S[SEC filings and XBRL] --> V
    V --> B[01 Bronze: raw market, macro, articles]
    B --> P[Bronze-to-Silver processors]
    P --> SI[02 Silver: validated enrichments]
    B --> F[03 Gold: Hamilton features]
    SI -. optional enrichment .-> F
    F --> ML[04 Platinum: predictions and signals]
    B --> Q[DuckDB queries]
    SI --> Q
    F --> Q
    B --> C[Catalog and lineage]
    SI --> C
    F --> C
    ML --> C
    B --> H[Monitoring and freshness checks]
    SI --> H
    F --> H
    ML --> H
```

Price sources are required prerequisites for feature generation. News, social,
macro, analyst, and SEC branches are optional enrichments: their failure is
recorded and the core feature path may continue without them. Bronze-to-Silver
processors are run only when their source branch is selected; their failure
disables that enrichment, not independent core features.
