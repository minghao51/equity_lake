"""H00 — the sanitized demo-lake page: one composed static artifact, loopback preview.

The contract is adopted, not proposed: the H00 review
(``docs/reports/20261117-h00-sanitized-demo-lake-page-review.md`` §4-§6) fixed the
allowlist and the owner adopted its recommendations
(``docs/reports/20261121-h00-owner-decisions.md``). This module is the single producer
of that artifact and it is **display-only and offline**:

* **Demo lake only.** Every input path is composed under one demo lake root
  (``data/lake/`` by default). The real-source slice (``data/lake_real/``, ADR-0030
  decision 4), the private ledger, watchlists/tracking, ``.env``, ``logs/`` and the
  pipeline-results files are never opened — they are refused as roots, not filtered
  afterwards (owner decision 7 removes that scope entirely).
* **Allowlist only.** Fields are *named* in :func:`_compose_payload` and serialized
  from there; anything not named does not exist in the artifact. There is no
  settings dump, no dataset path, no alert text, no run metadata, no digest and no
  vendor/attribution text (blockers B1-B4, B6-B10 of the review).
* **Allowlist, then prove it.** :func:`build_sanitized_page` composes every file in
  memory, runs the negative scan over the composed bytes (absolute paths, real-slice
  fingerprints, ledger/private-state names, ``.env`` values, 64-hex digests, raw
  alert/run text, vendor strings, forbidden claims), writes
  ``sanitization-report.json`` *inside* the artifact, and writes nothing if the scan
  finds a hit. Failing closed is the point: a partial artifact is never left behind.
* **Preview on loopback only** (:func:`serve_preview`), reusing the existing
  ``dashboard.streamlit_app.is_loopback_bind_address`` gate. Publishing is not
  implemented here at all: it needs a separate explicit owner approval (owner
  decision 3).

The canonical dashboard (:mod:`equity_lake.dashboard.exporter`, the Streamlit app) is
untouched by this module: it writes one artifact directory and nothing else.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import duckdb

from equity_lake import __version__
from equity_lake.core import paths
from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION
from equity_lake.core.schemas import SYNTHETIC_COVERAGE_SOURCE
from equity_lake.storage.lake_reader import duckdb_scan_for

# ---------------------------------------------------------------------------
# Adopted wording (owner decision 6, review §4.2/§6). These strings are asserted
# verbatim by tests: the banner, the performance disclaimer and the demo notice.
# ---------------------------------------------------------------------------
SYNTHETIC_BANNER: Final = "Synthetic demonstration data — generated offline by `equity demo seed`; not market data"
PERFORMANCE_DISCLAIMER: Final = "Computed on synthetic demo data; not indicative of any real strategy's performance and not investment advice."
DEMO_DATA_ONLY: Final = "demo data only — not investment advice"
STALE_BANNER: Final = "stale — rebuild due"

#: Owner decision 4: rebuilt on demand, staleness threshold 30 days.
STALENESS_THRESHOLD_DAYS: Final = 30

INDEX_FILENAME: Final = "index.html"
PAYLOAD_FILENAME: Final = "payload.json"
REPORT_FILENAME: Final = "sanitization-report.json"
ARTIFACT_FILENAMES: Final = (INDEX_FILENAME, PAYLOAD_FILENAME, REPORT_FILENAME)

ARTIFACT_NAME: Final = "h00-sanitized-demo-lake"
PAYLOAD_SCHEMA_VERSION: Final = 1
REPORT_SCHEMA_VERSION: Final = 1
H00_BUILDER_VERSION: Final = "1"

#: The two verified provenance labels (review §4.2); the coverage label is the
#: generator's own ``source`` and is shared with the feature rows.
DEMO_IDENTITY_PROVENANCE: Final = "current_ticker_config"
DEMO_COVERAGE_SOURCE: Final = SYNTHETIC_COVERAGE_SOURCE
#: ADR-0027 row-bound price-origin vocabulary. A generator-produced demo batch claims
#: ``synthetic`` on every row; a vendor row claims ``real`` (or carries no claim at all
#: in a pre-envelope slice). Only the synthetic claim is publishable here.
DEMO_PRICE_PROVENANCE: Final = "synthetic"

#: Real-row canary values verified in ``data/lake_real`` by the review (§4.3.3); they are
#: constants because the build must never open the real root to "check" for it.
_REAL_ROW_CANARIES: Final = ("185.63999938964844", "370.8699951171875")

#: Directory names that are never a demo lake root, at the root or one level above it: the
#: real slice, the probe artifacts and every private/operator store. Refusal is by name and
#: by containment, so a nested or aliased path fails too.
_FORBIDDEN_ROOT_NAMES: Final = (
    "lake_real",
    "d01",
    "portfolio",
    "watchlists",
    "tracking",
    "logs",
    "models",
    "backups",
    "signals",
    "findings",
    "sample",
    "signal_scans",
    "research",
    "config",
)
#: Whole roots that are never a lake root (equality only: the demo lake lives inside the
#: checkout, so the checkout itself cannot be refused by containment).
_EXACT_REFUSED_ROOTS: Final = ("PROJECT_ROOT", "DATA_DIR", "CONFIG_DIR")
#: Roots whose whole subtree is refused as a lake root.
_SUBTREE_REFUSED_ROOTS: Final = ("REAL_LAKE_DIR", "LOGS_DIR", "MODELS_DIR")

#: The only label columns a demo table may be attested on (identifiers, not values).
_LABEL_COLUMNS: Final = ("identity_provenance", "price_provenance", "source", "action_coverage_source")

_DIGEST_RE: Final = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{64}(?![0-9a-zA-Z])")
#: Shortest ``.env`` value the absence check compares: shorter values are ordinary
#: words that would collide with prose rather than identify a secret.
_ENV_VALUE_MIN_LENGTH: Final = 4
#: Literal/numeric ``.env`` values carry no identifying information and appear in any
#: JSON/HTML page; comparing them would make the scan vacuous. They are excluded by name,
#: never silently by length.
_ENV_LITERAL_VALUES: Final = frozenset({"true", "false", "none", "null"})


class H00BuildError(RuntimeError):
    """The build refused or the sanitization scan failed; nothing was written."""


class H00PreviewError(RuntimeError):
    """The preview refused to bind a non-loopback address, or there is no artifact."""


# ---------------------------------------------------------------------------
# Small value types
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DatasetRow:
    """One demo dataset inventory row — counts and dates only, never a path."""

    name: str
    available: bool
    rows: int
    symbols: int
    latest_date: str | None


@dataclass(frozen=True, slots=True)
class H00Artifact:
    """Where the composed artifact landed and what it claims about itself."""

    directory: Path
    built_at: datetime
    stale: bool
    index_path: Path
    payload_path: Path
    report_path: Path

    @property
    def files(self) -> tuple[Path, ...]:
        return (self.index_path, self.payload_path, self.report_path)


@dataclass(frozen=True, slots=True)
class _BuildContext:
    """Everything the payload composer may look at: one demo root and two instants."""

    root: Path
    artifact_dir: Path
    built_at: datetime
    now: datetime


# ---------------------------------------------------------------------------
# Paths and roots
# ---------------------------------------------------------------------------
def default_artifact_dir() -> Path:
    """The one artifact location: ``data/research/h00`` (resolved at call time)."""
    return paths.DATA_DIR / "research" / "h00"


def _demo_root(lake_dir: Path | str | None, *, allow_sandbox_root: bool = False) -> Path:
    """Resolve and attest the demo lake root; refuse every forbidden root by containment.

    A production build reads only the canonical synthetic lake: an explicit root must resolve
    inside ``paths.LAKE_DIR``. Tests may pass ``allow_sandbox_root=True`` for a throwaway
    ``data/lake`` sandbox; that seam is never reachable from the CLI.
    """
    root = Path(lake_dir).expanduser() if lake_dir is not None else paths.LAKE_DIR
    resolved = root.resolve()
    canonical = paths.LAKE_DIR.resolve()
    if not (resolved == canonical or resolved.is_relative_to(canonical)):
        sandbox = allow_sandbox_root and resolved.name == "lake" and resolved.parent.name == "data"
        if not sandbox:
            raise H00BuildError("refused: a production build reads only the canonical demo lake (data/lake)")
    if any(part in _FORBIDDEN_ROOT_NAMES for part in resolved.parts[-2:]):
        raise H00BuildError("refused: the demo lake root is a private, operator or real-source directory")
    if resolved in {getattr(paths, name).resolve() for name in _EXACT_REFUSED_ROOTS}:
        raise H00BuildError("refused: the demo lake root must be a lake directory, not a project root")
    for blocked in [getattr(paths, name).resolve() for name in _SUBTREE_REFUSED_ROOTS]:
        if resolved == blocked or resolved.is_relative_to(blocked):
            raise H00BuildError("refused: the demo lake root is outside the synthetic demo lake")
    if not resolved.is_dir():
        raise H00BuildError("refused: the demo lake root does not exist")
    return resolved


def _dataset_routes() -> tuple[tuple[str, str], ...]:
    """The allowlisted demo routes: lake-relative paths only, no ``data/<name>/`` artifact."""
    routes: list[tuple[str, str]] = []
    for market in paths.PRICE_MARKETS:
        routes.append((f"01_bronze/market_data/{market}", f"01_bronze/market_data/{market}"))
    for market in paths.PRICE_MARKETS:
        routes.append((f"02_silver/corporate_action_coverage/{market}", f"02_silver/corporate_action_coverage/{market}"))
    routes.append(("03_gold/features", "03_gold/features"))
    routes.append(("04_platinum/predictions", "04_platinum/predictions"))
    return tuple(routes)


def _table_dir(root: Path, relative: str) -> Path:
    """Compose one table path under the attested root (never an absolute input)."""
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise H00BuildError("refused: a table path escaped the demo lake root")
    return candidate


# ---------------------------------------------------------------------------
# Reading the demo lake (counts, columns, labels — never row values)
# ---------------------------------------------------------------------------
def _columns(connection: duckdb.DuckDBPyConnection, table: Path) -> tuple[str, ...]:
    """Column names of one demo table (identifiers only, never row values).

    ``duckdb_scan_for`` is the one place a path becomes a SQL string literal (it escapes
    quotes itself); the only other interpolated fragments below are column identifiers
    taken from this ``DESCRIBE`` and from the fixed tuples ``_symbol_column`` allowlists.
    """
    rows = connection.execute(f"DESCRIBE SELECT * FROM {duckdb_scan_for(table)}").fetchall()  # nosemgrep
    return tuple(str(row[0]) for row in rows)


def _symbol_column(columns: Sequence[str]) -> str | None:
    for candidate in ("ticker", "symbol"):
        if candidate in columns:
            return candidate
    return None


def _as_int(value: Any) -> int | None:
    """Parse one count from a driver row without turning a driver quirk into a crash."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _utc_iso(value: Any) -> str | None:
    """Render a microsecond epoch as an explicit UTC instant (never a session-local offset)."""
    micros = _as_int(value)
    if micros is None:
        return None
    return datetime.fromtimestamp(micros / 1_000_000, tz=UTC).isoformat()


def _summarize(connection: duckdb.DuckDBPyConnection, name: str, table: Path, columns: Sequence[str]) -> DatasetRow:
    """Row/symbol/date counts for one demo table; absent means ``available: false``."""
    if not table.is_dir():
        return DatasetRow(name=name, available=False, rows=0, symbols=0, latest_date=None)
    scan = duckdb_scan_for(table)
    symbol_column = _symbol_column(columns)
    symbols_sql = f'COUNT(DISTINCT "{symbol_column}")' if symbol_column else "CAST(NULL AS BIGINT)"
    date_sql = 'CAST(MAX("date") AS VARCHAR)' if "date" in columns else "CAST(NULL AS VARCHAR)"
    try:
        row = connection.execute(  # nosemgrep
            f"SELECT COUNT(*) AS n_rows, {symbols_sql} AS n_symbols, {date_sql} AS latest_date FROM {scan}"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 - an unreadable demo table must fail closed, not summarize as zero
        raise H00BuildError("refused: a demo table could not be read for the inventory") from exc
    if row is None:
        raise H00BuildError("refused: a demo table returned no inventory row")
    rows_count = _as_int(row[0])
    if rows_count is None:
        raise H00BuildError("refused: a demo table row count was not a number")
    symbols_count = _as_int(row[1]) or 0
    latest = row[2]
    return DatasetRow(
        name=name,
        available=True,
        rows=rows_count,
        symbols=symbols_count,
        latest_date=None if latest is None else str(latest),
    )


def _attest_labels(connection: duckdb.DuckDBPyConnection, table: Path, column: str, expected: str) -> None:
    """Require one label column to carry exactly ``expected``; anything else aborts.

    Synthetic attestation (review §4.3.2, ADR-0028 decision 6): no uncertified row is
    published. A missing label column fails too — "no label" is not "synthetic".
    """
    if column not in _LABEL_COLUMNS:
        raise H00BuildError("refused: an unknown label column was requested for attestation")
    try:
        rows = connection.execute(f'SELECT DISTINCT CAST("{column}" AS VARCHAR) AS label FROM {duckdb_scan_for(table)}').fetchall()  # nosemgrep
    except Exception as exc:  # noqa: BLE001 - an unreadable label is a refused attestation
        raise H00BuildError("refused: a provenance label could not be read for attestation") from exc
    labels = {None if row[0] is None else str(row[0]) for row in rows}
    if labels != {expected}:
        raise H00BuildError("refused: a contributing demo table does not carry the synthetic provenance label")


def _attest_price_rows(connection: duckdb.DuckDBPyConnection, table: Path, columns: Sequence[str]) -> None:
    """Attest one Bronze price table as generator-produced before any count is published.

    Two row-bound labels are checked when present (ADR-0027): the identity label every
    demo row carries, and the generator's own ``price_provenance`` claim. A table whose
    rows carry no synthetic claim — a vendor slice, whose envelope columns are null — is
    refused rather than summarized, which is what makes a real row unreachable even if
    the root guard were bypassed. A legacy demo partition with no envelope at all is
    attested on its identity label alone (the review's verified schema).
    """
    if "identity_provenance" in columns:
        _attest_labels(connection, table, "identity_provenance", DEMO_IDENTITY_PROVENANCE)
    if "price_provenance" in columns:
        _attest_labels(connection, table, "price_provenance", DEMO_PRICE_PROVENANCE)


def _select_rows(connection: duckdb.DuckDBPyConnection, table: Path, selects: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """Run one aggregate projection whose aliases and expressions are both allowlisted.

    The expressions are literals written in this module (``MIN("source")`` and friends);
    only the scan target comes from a path, and ``duckdb_scan_for`` escapes it.
    """
    projection = ", ".join(f'{expression} AS "{alias}"' for alias, expression in selects)
    row = connection.execute(f"SELECT {projection} FROM {duckdb_scan_for(table)}").fetchone()  # nosemgrep
    if row is None:
        return {}
    return dict(zip([alias for alias, _ in selects], row, strict=True))


def _coverage_summary(connection: duckdb.DuckDBPyConnection, table: Path, columns: Sequence[str]) -> dict[str, Any] | None:
    """The coverage-proof summary: source, status, window and the validity instant."""
    if not table.is_dir() or "source" not in columns:
        return None
    selects: list[tuple[str, str]] = [
        ("source", 'CAST(MIN("source") AS VARCHAR)'),
        ("n_symbols", 'COUNT(DISTINCT "symbol")'),
    ]
    if "coverage_status" in columns:
        selects.append(("coverage_status", 'CAST(MIN("coverage_status") AS VARCHAR)'))
    if "coverage_start" in columns:
        selects.append(("coverage_start", 'CAST(MIN("coverage_start") AS VARCHAR)'))
    if "coverage_end" in columns:
        selects.append(("coverage_end", 'CAST(MAX("coverage_end") AS VARCHAR)'))
    if "source_valid_until" in columns:
        selects.append(("source_valid_until_us", 'epoch_us(MAX("source_valid_until"))'))
    if "coverage_schema_version" in columns:
        selects.append(("coverage_schema_version", 'MAX("coverage_schema_version")'))
    try:
        values = _select_rows(connection, table, selects)
    except Exception as exc:  # noqa: BLE001 - a coverage proof that cannot be read is refused, not zero
        raise H00BuildError("refused: the coverage proof could not be read") from exc
    if not values:
        return None
    return {
        "source": values.get("source"),
        "coverage_status": values.get("coverage_status"),
        "coverage_start": values.get("coverage_start"),
        "coverage_end": values.get("coverage_end"),
        "source_valid_until": _utc_iso(values.get("source_valid_until_us")),
        "symbols": _as_int(values.get("n_symbols")) or 0,
        "coverage_schema_version": _as_int(values.get("coverage_schema_version")),
    }


def _aggregate_metrics(connection: duckdb.DuckDBPyConnection, features: Path, columns: Sequence[str]) -> dict[str, Any] | None:
    """Aggregate demo-lake counts (owner decision 1: aggregates only, never per symbol)."""
    if not features.is_dir():
        return None
    symbol_column = _symbol_column(columns)
    selects: list[tuple[str, str]] = [("symbol_sessions", "COUNT(*)")]
    if symbol_column:
        selects.append(("n_symbols", f'COUNT(DISTINCT "{symbol_column}")'))
    if "date" in columns:
        selects.append(("n_sessions", 'COUNT(DISTINCT "date")'))
    if "next_day_return" in columns:
        selects.append(("labelled", 'COUNT("next_day_return")'))
    try:
        values = _select_rows(connection, features, selects)
    except Exception as exc:  # noqa: BLE001 - unreadable aggregates are refused, never zero
        raise H00BuildError("refused: the aggregate demo metrics could not be read") from exc
    if not values:
        return None
    rows: list[dict[str, Any]] = []
    for label, key in (
        ("symbol sessions", "symbol_sessions"),
        ("symbols", "n_symbols"),
        ("sessions", "n_sessions"),
        ("labelled symbol sessions", "labelled"),
    ):
        value = _as_int(values.get(key))
        if value is not None:
            rows.append({"label": label, "value": value})
    rows.append({"label": "feature columns", "value": len(columns)})
    return {
        "available": True,
        "aggregate_only": True,
        "disclaimer": PERFORMANCE_DISCLAIMER,
        "metrics": rows,
    }


# ---------------------------------------------------------------------------
# The payload (the allowlist) — the only place a published field is named
# ---------------------------------------------------------------------------
def _compose_payload(context: _BuildContext) -> dict[str, Any]:
    """Compose the allowlisted payload; every published field is named here.

    Nothing outside this function contributes a key to the artifact, which is what
    makes "absent from the allowlist" mean "absent from the page" (ADR-0025 decision 10).
    """
    connection = duckdb.connect(":memory:")
    try:
        datasets: list[dict[str, Any]] = []
        schemas: dict[str, list[str]] = {}
        column_counts: dict[str, int] = {}
        coverage: dict[str, Any] | None = None
        metrics: dict[str, Any] | None = None
        for name, relative in _dataset_routes():
            table = _table_dir(context.root, relative)
            if not table.is_dir():
                # Absent tables are omitted, not listed: a market key the demo lake does
                # not hold would publish internal layout names for no evidence (B1-B10).
                continue
            columns = _columns(connection, table)
            if name.startswith("01_bronze/market_data/"):
                _attest_price_rows(connection, table, columns)
            if name.startswith("02_silver/corporate_action_coverage/"):
                _attest_labels(connection, table, "source", DEMO_COVERAGE_SOURCE)
                if coverage is None:
                    coverage = _coverage_summary(connection, table, columns)
            if name == "03_gold/features":
                _attest_labels(connection, table, "action_coverage_source", DEMO_COVERAGE_SOURCE)
                metrics = _aggregate_metrics(connection, table, columns)
            summarized = _summarize(connection, name, table, columns)
            datasets.append(
                {
                    "name": summarized.name,
                    "available": summarized.available,
                    "rows": summarized.rows,
                    "symbols": summarized.symbols,
                    "latest_date": summarized.latest_date,
                }
            )
            if name.startswith("04_platinum/"):
                # Owner decision 2: fixture predictions are a count and a schema size only —
                # no column list, no model version, no probability value.
                column_counts[name] = len(columns)
            else:
                schemas[name] = list(columns)
    finally:
        connection.close()

    latest_demo_date = max((row["latest_date"] for row in datasets if row["latest_date"]), default=None)
    stale = is_stale(context.built_at, now=context.now)
    return {
        "artifact": ARTIFACT_NAME,
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "provenance": "synthetic",
        "banner": SYNTHETIC_BANNER,
        "disclaimer": PERFORMANCE_DISCLAIMER,
        "demo_data_only": DEMO_DATA_ONLY,
        "built_at": context.built_at.isoformat(),
        "builder": {"name": "equity-lake-h00", "version": H00_BUILDER_VERSION, "package_version": __version__},
        "stale": stale,
        "stale_threshold_days": STALENESS_THRESHOLD_DAYS,
        "stale_banner": STALE_BANNER if stale else None,
        "latest_demo_date": latest_demo_date,
        "provenance_labels": {
            "coverage_source": DEMO_COVERAGE_SOURCE,
            "identity_provenance": DEMO_IDENTITY_PROVENANCE,
            "feature_action_coverage_source": DEMO_COVERAGE_SOURCE,
        },
        "datasets": datasets,
        "coverage": coverage,
        "schemas": {
            "versions": {
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "coverage_schema_version": None if coverage is None else coverage.get("coverage_schema_version"),
            },
            "columns": schemas,
            "column_counts": column_counts,
        },
        "aggregate_metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Staleness (owner decision 4)
# ---------------------------------------------------------------------------
def is_stale(built_at: datetime, *, now: datetime | None = None, threshold_days: int = STALENESS_THRESHOLD_DAYS) -> bool:
    """True when the build instant is older than the adopted staleness threshold."""
    reference = now if now is not None else datetime.now(UTC)
    return reference.astimezone(UTC) - built_at.astimezone(UTC) > timedelta(days=threshold_days)


def last_build_date(built_at: datetime) -> str:
    """The date a stale page shows instead of presenting its numbers as fresh."""
    return built_at.astimezone(UTC).date().isoformat()


# ---------------------------------------------------------------------------
# The negative scan (review §4.3.3) — proven absence, computed over composed bytes
# ---------------------------------------------------------------------------
def _dotenv_path() -> Path:
    """The one local dotenv file, read only for an in-memory absence comparison."""
    return paths.PROJECT_ROOT / ".env"


def _env_values(path: Path | None = None) -> tuple[str, ...]:
    """Values of the local ``.env`` for the absence check. Never logged, never copied."""
    dotenv = path if path is not None else _dotenv_path()
    try:
        text = dotenv.read_text(encoding="utf-8")
    except OSError:
        return ()
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _, _, value = stripped.partition("=")
        candidate = value.strip().strip("'\"")
        if candidate and not candidate.startswith("$") and len(candidate) >= _ENV_VALUE_MIN_LENGTH:
            if candidate.lower() in _ENV_LITERAL_VALUES or candidate.replace(".", "", 1).isdigit():
                continue
            values.append(candidate)
    return tuple(values)


def forbidden_needles(artifact_dir: Path, *, env_path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """The closed set of forbidden classes and their literal needles.

    The report never records a needle — only the class and whether it was clean — so
    ``sanitization-report.json`` can be scanned by the same pass.
    """
    return {
        "paths": (
            str(paths.PROJECT_ROOT),
            str(paths.DATA_DIR),
            str(paths.LAKE_DIR),
            str(Path(artifact_dir).resolve().parent),
            "/home/",
            "/Users/",
            "C:\\",
            "/tmp/",
            "PROJECT_ROOT",
        ),
        "real_slice": ("lake_real", "probe-", "d01/", "/d01", *_REAL_ROW_CANARIES),
        "private_book": (
            "portfolio.sqlite3",
            "marks.json",
            "statement.csv",
            "ledger_events",
            "source_digest",
            "content_digest",
            "data/portfolio",
        ),
        "private_state": ("watchlist", "data/tracking", "private_dir"),
        "secrets": _env_values(env_path),
        "secret_shapes": ("API_KEY", "SECRET_KEY", "PRIVATE_KEY", "TOKEN=", "token=", "password="),
        "alert_text": ("pipeline_results", "health-report.json", "Traceback", "IOException", "DuckDB"),
        "vendor": ("yfinance", "akshare", "efinance", "yahoo", "jpx api", "krx api", "tushare", "polygon.io"),
        "claims": ("real data", "live prices", "actual market data", "real performance", "unadjusted", "live data soon"),
    }


def scan_contents(contents: Mapping[str, str], *groups: Mapping[str, Sequence[str]]) -> dict[str, int]:
    """Count hits per forbidden class over composed file contents (needles never echoed)."""
    hits: dict[str, int] = {}
    for needles in groups:
        for needle_class, literals in needles.items():
            matched = 0
            for content in contents.values():
                haystack = content.lower() if needle_class == "vendor" else content
                matched += sum(haystack.count(literal.lower() if needle_class == "vendor" else literal) for literal in literals)
            if matched:
                hits[needle_class] = hits.get(needle_class, 0) + matched
    digests = sum(len(_DIGEST_RE.findall(content)) for content in contents.values())
    if digests:
        hits["digests"] = digests
    return hits


# ---------------------------------------------------------------------------
# Rendering (one payload, two sinks: JSON and HTML)
# ---------------------------------------------------------------------------
def _e(value: Any) -> str:
    """Escape one interpolated value for HTML text/attribute context."""
    return html.escape("" if value is None else str(value), quote=True)


_STYLE: Final = """
    :root { --bg:#f6f4ef; --panel:#fff; --line:rgba(30,40,50,.12); --text:#1d2733; --muted:#66717f; --accent:#116466; --warn:#b55d07; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:"Avenir Next","Segoe UI",sans-serif; color:var(--text); background:var(--bg); }
    main { max-width:1040px; margin:0 auto; padding:40px 20px 72px; }
    .banner { background:#fff4d6; border:1px solid #e6c15c; border-radius:14px; padding:14px 18px; font-weight:600; }
    .banner + .banner { margin-top:8px; }
    .stale { background:#fdecea; border-color:#e0a9a2; }
    h1 { font-size:clamp(1.6rem,4vw,2.6rem); margin:26px 0 6px; }
    h2 { font-size:1.1rem; margin:32px 0 8px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
    .note { color:var(--muted); max-width:52rem; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:18px; }
    table { width:100%; border-collapse:collapse; }
    th, td { padding:9px 0; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }
    th { color:var(--muted); font-weight:600; }
    pre { overflow:auto; white-space:pre-wrap; background:rgba(0,0,0,.03); border-radius:12px; padding:12px; margin:0; }
    .disclaimer { border-left:3px solid var(--warn); padding-left:12px; color:var(--warn); }
    footer { margin-top:40px; color:var(--muted); font-size:.9rem; }
"""


def _render_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    head = "".join(f"<th>{_e(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _render_html(payload: Mapping[str, Any]) -> str:
    """Render the composed page from the allowlisted payload only."""
    stale = bool(payload.get("stale"))
    built_at = payload.get("built_at")
    banners = [f'<p class="banner">{_e(payload.get("banner"))}</p>', f'<p class="banner">{_e(payload.get("demo_data_only"))}</p>']
    if stale:
        banners.append(
            f'<p class="banner stale">{_e(payload.get("stale_banner"))} — last build {_e(last_build_date(datetime.fromisoformat(str(built_at))))}'
            f" (threshold {_e(payload.get('stale_threshold_days'))} days)</p>"
        )

    dataset_rows = [
        (row["name"], "yes" if row["available"] else "absent", row["rows"], row["symbols"], row["latest_date"] or "—")
        for row in payload.get("datasets", [])
    ]
    sections = [
        "<h2>Demo datasets</h2>",
        '<p class="note">Row counts, symbol counts and demo dates only. '
        "No price value, no per-symbol figure and no stored location appears on this page.</p>",
        _render_table(dataset_rows, ("dataset", "present", "rows", "symbols", "latest demo date")),
    ]

    coverage = payload.get("coverage")
    if isinstance(coverage, Mapping):
        sections.append("<h2>Corporate-action coverage proof</h2>")
        sections.append(
            _render_table(
                [
                    (
                        coverage.get("source"),
                        coverage.get("coverage_status"),
                        coverage.get("coverage_start"),
                        coverage.get("coverage_end"),
                        coverage.get("source_valid_until"),
                        coverage.get("symbols"),
                    )
                ],
                ("source", "status", "from", "to", "valid until", "symbols"),
            )
        )
    else:
        sections.append("<h2>Corporate-action coverage proof</h2>")
        sections.append('<p class="note">Not available in this build: the demo lake carries no coverage proof.</p>')

    labels = payload.get("provenance_labels", {})
    schema_versions = payload.get("schemas", {}).get("versions", {})
    sections.append("<h2>Provenance and schema</h2>")
    sections.append(
        _render_table(
            [
                ("price row identity", labels.get("identity_provenance")),
                ("action coverage source", labels.get("coverage_source")),
                ("feature action coverage", labels.get("feature_action_coverage_source")),
                ("identity schema version", schema_versions.get("identity_schema_version")),
            ],
            ("label", "value"),
        )
    )
    for name, columns in payload.get("schemas", {}).get("columns", {}).items():
        sections.append(f'<p class="note">{_e(name)} — {_e(len(columns))} columns</p>')
        sections.append(f"<pre>{_e(', '.join(columns))}</pre>")
    for name, count in payload.get("schemas", {}).get("column_counts", {}).items():
        sections.append(f'<p class="note">{_e(name)} — {_e(count)} columns (fixture table: column count only, no column list, no value)</p>')

    metrics = payload.get("aggregate_metrics")
    sections.append("<h2>Aggregate synthetic metrics</h2>")
    if isinstance(metrics, Mapping) and metrics.get("available"):
        sections.append('<p class="note">Aggregate counts over the synthetic demo lake. No per-symbol figure is published.</p>')
        sections.append(_render_table([(row["label"], row["value"]) for row in metrics.get("metrics", [])], ("measure", "value")))
    else:
        sections.append('<p class="note">Not available in this build: no aggregate demo figures were computed.</p>')
    sections.append(f'<p class="disclaimer">{_e(payload.get("disclaimer"))}</p>')

    intro = (
        '<p class="note">A sanitized static page composed offline from the local synthetic demonstration lake. '
        "It lists field names, counts, schemas and demo dates; the generator is the source of every value shown.</p>"
    )
    provenance_note = (
        f'<p class="note">Page built at {_e(built_at)} (builder {_e(payload.get("builder", {}).get("version"))}, '
        f"package {_e(payload.get('builder', {}).get('package_version'))}). "
        f"Latest demo date in the inventory: {_e(payload.get('latest_demo_date') or '—')}.</p>"
    )
    footer = (
        f"<p>{_e(payload.get('demo_data_only'))}. This artifact is a local build for loopback review only; "
        "it is not a published page and it certifies nothing about any real market.</p>"
        f"<p>Sanitization: allowlisted fields only, negative scan recorded in {_e(REPORT_FILENAME)}. "
        "No price value is serialized.</p>"
    )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Equity Lake — synthetic demonstration lake</title>
    <style>{_STYLE}</style>
  </head>
  <body>
    <main>
      {"".join(banners)}
      <h1>Equity Lake — synthetic demonstration lake</h1>
      {intro}
      {provenance_note}
      {"".join(sections)}
      <footer>
        {footer}
      </footer>
    </main>
  </body>
</html>
"""


def _render_report(payload: Mapping[str, Any], scan: Mapping[str, Any]) -> dict[str, Any]:
    """The machine-readable report, itself inside the artifact and inside the scan."""
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "artifact": ARTIFACT_NAME,
        "provenance": "synthetic",
        "builder_version": H00_BUILDER_VERSION,
        "build_utc": payload.get("built_at"),
        "stale": bool(payload.get("stale")),
        "files": list(ARTIFACT_FILENAMES),
        "root": "demo_lake_only",
        "labels_attested": dict(payload.get("provenance_labels", {})),
        "negative_scan": dict(scan),
        "passed": True,
    }


# ---------------------------------------------------------------------------
# The one public build function
# ---------------------------------------------------------------------------
def build_sanitized_page(
    *,
    lake_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    allow_sandbox_root: bool = False,
    built_at: datetime | None = None,
    now: datetime | None = None,
) -> H00Artifact:
    """Compose the sanitized demo-lake artifact at ``data/research/h00`` and prove it.

    Args:
        lake_dir: Demo lake root; defaults to the canonical synthetic root. Any
            real-source/private root is refused before a file is read.
        output_dir: Artifact directory; defaults to :func:`default_artifact_dir`.
        built_at: The page build instant (UTC); defaults to now. Injecting an old
            instant is how a stale page is produced/tested (owner decision 4).
        now: The staleness reference instant; defaults to now.

    Raises:
        H00BuildError: a root was refused, a label was not attested, or the negative
            scan found a forbidden class. In every case nothing is written.
    """
    root = _demo_root(lake_dir, allow_sandbox_root=allow_sandbox_root)
    target = Path(output_dir).expanduser() if output_dir is not None else default_artifact_dir()
    reference = now if now is not None else datetime.now(UTC)
    build_instant = built_at if built_at is not None else reference
    context = _BuildContext(root=root, artifact_dir=target, built_at=build_instant, now=reference)

    payload = _compose_payload(context)
    payload["built_at"] = build_instant.astimezone(UTC).isoformat()

    contents: dict[str, str] = {
        PAYLOAD_FILENAME: json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        INDEX_FILENAME: _render_html(payload),
    }
    needles = forbidden_needles(target)
    hits = scan_contents(contents, needles)
    if hits:
        raise H00BuildError(f"sanitization scan failed closed (classes: {', '.join(sorted(hits))}); nothing was written")

    # The report is composed next and scanned by the same pass, so the artifact the
    # operator previews carries its own proof and no file is written before it passes.
    report = _render_report(payload, {"state": "clean", "classes": sorted(needles)})
    contents[REPORT_FILENAME] = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    final_hits = scan_contents(contents, needles)
    if final_hits:
        raise H00BuildError(f"sanitization scan failed closed (classes: {', '.join(sorted(final_hits))}); nothing was written")

    if target.exists():
        unexpected = sorted(path.name for path in target.iterdir() if path.name not in contents)
        if unexpected:
            raise H00BuildError(f"refused: the artifact directory holds files this build cannot vouch for: {unexpected}")
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for filename, content in contents.items():
            path = target / filename
            path.write_text(content, encoding="utf-8")
            written.append(path)
        # The proof is about the artifact that exists, not the frames in memory.
        on_disk = {path.name: path.read_text(encoding="utf-8") for path in sorted(target.iterdir()) if path.is_file()}
        disk_hits = scan_contents(on_disk, needles)
        if disk_hits:
            raise H00BuildError(f"sanitization scan failed closed on the written artifact (classes: {', '.join(sorted(disk_hits))})")
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise

    return H00Artifact(
        directory=target,
        built_at=build_instant.astimezone(UTC),
        stale=bool(payload["stale"]),
        index_path=target / INDEX_FILENAME,
        payload_path=target / PAYLOAD_FILENAME,
        report_path=target / REPORT_FILENAME,
    )


# ---------------------------------------------------------------------------
# Loopback preview (no public server, no publish path)
# ---------------------------------------------------------------------------
def artifact_staleness(artifact_dir: Path | str | None = None, *, now: datetime | None = None) -> tuple[bool, str | None]:
    """Read the artifact's own build instant and report whether it is stale."""
    directory = Path(artifact_dir).expanduser() if artifact_dir is not None else default_artifact_dir()
    payload_path = directory / PAYLOAD_FILENAME
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        built_at = datetime.fromisoformat(str(payload["built_at"]))
    except (OSError, ValueError, KeyError):
        return True, None
    return is_stale(built_at, now=now), last_build_date(built_at)


def serve_preview(host: str = "127.0.0.1", port: int = 8765, *, artifact_dir: Path | str | None = None) -> None:
    """Serve the artifact on a loopback address only (blocking, review-only).

    Reuses the existing P03 gate: a non-loopback bind is refused before a socket is
    opened. There is deliberately no public-server, no upload and no deploy path here.
    """
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from equity_lake.dashboard.streamlit_app import is_loopback_bind_address

    if not is_loopback_bind_address(host):
        raise H00PreviewError("refused: the preview binds a loopback address only (127.0.0.1 or localhost)")

    directory = Path(artifact_dir).expanduser() if artifact_dir is not None else default_artifact_dir()
    if not (directory / INDEX_FILENAME).is_file():
        raise H00PreviewError("refused: no composed artifact to preview; run the H00 build first")

    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    with ThreadingHTTPServer((host, port), handler) as server:
        print(f"H00 sanitized preview on http://{host}:{server.server_port}/ (loopback only; not published)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:  # pragma: no cover - operator Ctrl-C
            print("H00 preview stopped")


__all__ = [
    "ARTIFACT_FILENAMES",
    "DEMO_COVERAGE_SOURCE",
    "DEMO_DATA_ONLY",
    "DEMO_IDENTITY_PROVENANCE",
    "DEMO_PRICE_PROVENANCE",
    "H00Artifact",
    "H00BuildError",
    "H00_BUILDER_VERSION",
    "H00PreviewError",
    "INDEX_FILENAME",
    "PAYLOAD_FILENAME",
    "PERFORMANCE_DISCLAIMER",
    "REPORT_FILENAME",
    "STALENESS_THRESHOLD_DAYS",
    "STALE_BANNER",
    "SYNTHETIC_BANNER",
    "artifact_staleness",
    "build_sanitized_page",
    "default_artifact_dir",
    "forbidden_needles",
    "is_stale",
    "last_build_date",
    "scan_contents",
    "serve_preview",
]
