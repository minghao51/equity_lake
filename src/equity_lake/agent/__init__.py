"""Phase 2C RAG agent over the Equity Lake.

Components (added incrementally per the Phase 2C plan):

- :mod:`agent.index`  — chunk the silver corpus, embed via OpenRouter, store in
  a sqlite-vec vector index.
- :mod:`agent.rag`    — embed a query, KNN-retrieve, answer over DeepSeek with
  citations; refuse-with-citation when no chunk clears the similarity threshold.
- :mod:`agent.tools`  — thin query tools over the lake (DuckDB scans, finding
  cards) for grounding.
- :mod:`agent.eval`   — accuracy + citation-rate eval (hard gate before any
  dashboard/API exposure).

Boundary (``tests/unit/test_import_boundaries.py``): ``agent`` may import
``core`` / ``storage`` / ``ingestion`` / ``findings`` only — never ``cli``,
``pipeline``, or ``dashboard``. Heavy deps (``sqlite_vec``) are lazy-imported.
"""
