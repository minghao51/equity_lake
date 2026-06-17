"""Medallion-layered Hamilton DAG modules.

Layered modules that compose the feature pipeline DAG:

- ``raw_01``      — Bronze: extract OHLCV columns from ``price_data`` input
- ``clean_02``    — Silver: basic transforms (returns)
- ``features_03`` — Gold: technical indicators
- ``enrichments_04`` — Gold: external data joins (sentiment, analyst, SEC, macro)

The :class:`~equity_lake.features.pipeline.FeaturePipeline` driver assembles
all four modules into a single DAG.  Callers request specific output nodes
(e.g. ``["close", "rsi_14"]`` for technicals, ``["enriched_features"]`` for
enrichments) and Hamilton resolves only the required subgraph.
"""
