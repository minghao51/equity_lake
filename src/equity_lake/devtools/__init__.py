"""Developer tooling.

Run modules directly, e.g. ``uv run python -m equity_lake.devtools.test_data``.
This package intentionally re-exports nothing so importing it stays cheap
(the tool modules pull in numpy/pandas and must be imported lazily).
"""
