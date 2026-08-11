"""FastAPI read API over the equity data lake (Phase 2B).

The package ``__init__`` is intentionally dependency-light: importing
``equity_lake.api`` does **not** import FastAPI. Build the ASGI app via
:func:`equity_lake.api.main.create_app` (used by tests and, later, the
``equity api serve`` command / a Dockerfile ``api`` stage).
"""
