"""Streamlit data quality dashboard for Equity Lake.

Provides an interactive dashboard for monitoring data quality,
pipeline health, and dataset status.

Usage:
    equity dashboard serve
    equity dashboard serve --port 8501
"""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import pandas as pd

try:
    st: Any = import_module("streamlit")
except ModuleNotFoundError:
    st = None

from equity_lake.dashboard._common import MARKET_DATASETS, load_health_report, load_update_history, summarize_dataset
from equity_lake.storage.lake_reader import duckdb_scan_for

# Update-history rows rendered on the Streamlit page.
_APP_HISTORY_LIMIT = 50


def _load_update_history() -> list[dict[str, Any]]:
    """Load update history from parquet (never fails the page render)."""
    try:
        return load_update_history(_APP_HISTORY_LIMIT)
    except Exception:
        return []


def render_overview(datasets: list[dict[str, Any]], health: dict[str, Any] | None) -> None:
    """Render the overview page."""
    st.title("Equity Lake Dashboard")
    st.caption("Local-first equity data pipeline — Data Quality Dashboard")

    # Summary metrics
    available = [d for d in datasets if d["available"]]
    total_rows = sum(d["rows"] for d in available)
    total_symbols = sum(d["symbols"] for d in available)
    latest_date = max((d["latest_date"] for d in available if d["latest_date"]), default="N/A")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Datasets Available", f"{len(available)}/{len(datasets)}")
    col2.metric("Total Rows", f"{total_rows:,}")
    col3.metric("Total Symbols", f"{total_symbols:,}")
    col4.metric("Latest Update", latest_date)

    st.divider()

    # Dataset table
    st.subheader("Dataset Inventory")
    df = pd.DataFrame(datasets)
    st.dataframe(
        df[["name", "rows", "symbols", "latest_date", "available"]],
        column_config={
            "name": "Dataset",
            "rows": st.column_config.NumberColumn("Rows", format="%d"),
            "symbols": st.column_config.NumberColumn("Symbols", format="%d"),
            "latest_date": "Latest Date",
            "available": st.column_config.CheckboxColumn("Available"),
        },
        hide_index=True,
        use_container_width=True,
    )

    # Health alerts
    if health and health.get("alerts"):
        st.subheader("⚠️ Health Alerts")
        for alert in health["alerts"]:
            st.warning(alert)
    else:
        st.success("No health alerts at this time.")


def render_dataset_detail(datasets: list[dict[str, Any]]) -> None:
    """Render dataset detail page with per-dataset exploration."""
    st.title("Dataset Explorer")

    # Dataset selector
    dataset_names = [d["name"] for d in datasets if d["available"]]
    if not dataset_names:
        st.info("No datasets available. Run the pipeline to generate data.")
        return

    selected = st.selectbox("Select Dataset", dataset_names)

    dataset = next(d for d in datasets if d["name"] == selected)
    st.subheader(f"📊 {selected}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Rows", f"{dataset['rows']:,}")
    col2.metric("Symbols", f"{dataset['symbols']:,}")
    col3.metric("Latest Date", dataset["latest_date"] or "N/A")

    # Sample data
    st.divider()
    st.subheader("Sample Data")
    try:
        import duckdb

        conn = duckdb.connect(":memory:")
        query = f"""
            SELECT *
            FROM {duckdb_scan_for(Path(dataset["path"]))}
            ORDER BY date DESC
            LIMIT 100
        """
        df = conn.execute(query).df()
        st.dataframe(df, hide_index=True, use_container_width=True)
        conn.close()
    except Exception as e:
        st.error(f"Could not load sample data: {e}")


def render_health_page(health: dict[str, Any] | None) -> None:
    """Render the health metrics page."""
    st.title("Pipeline Health")

    if not health:
        st.info("No health report available. Run `equity monitor` to generate one.")
        return

    # Alerts
    alerts = health.get("alerts", [])
    if alerts:
        st.subheader(f"⚠️ {len(alerts)} Alert(s)")
        for alert in alerts:
            st.warning(alert)
    else:
        st.success("✅ All health checks passed")

    # Metrics
    metrics = health.get("metrics", {})
    if metrics:
        st.divider()
        st.subheader("Metrics")
        st.json(metrics)


def render_update_history(updates: list[dict[str, Any]]) -> None:
    """Render the update history page."""
    st.title("Update History")

    if not updates:
        st.info("No update history found. Run the pipeline to generate updates.")
        return

    df = pd.DataFrame(updates)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        height=600,
    )


def is_loopback_bind_address(address: str | None) -> bool:
    """Accept only an explicit IP loopback address or the localhost hostname."""
    if address is None or not address.strip():
        return False
    normalized = address.strip().lower()
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def render_private_tracking_preview() -> None:
    """Render private tracking observations only when Streamlit is loopback-bound."""
    st.title("Private Tracking Preview")
    try:
        bind_address = st.get_option("server.address")
    except Exception:
        bind_address = None
    if not is_loopback_bind_address(bind_address):
        st.error("Private tracking is disabled unless Streamlit is explicitly bound to a loopback address (127.0.0.1).")
        return

    # Import and load private state only after the fail-closed bind check.
    tracking = import_module("equity_lake.signals.tracking")
    try:
        snapshot = tracking.private_tracking_snapshot()
    except Exception:
        st.error("Private tracking observations could not be loaded; no readiness or signal-health conclusion is available.")
        return

    st.caption(
        "Private local view. Notes and held flags are shown only on this loopback page; SELL is downside watch or an exit candidate, never an order."
    )
    st.caption(f"History: {snapshot.history_status}. Last successful scan: {snapshot.last_successful_scan_at or 'never'}.")
    if snapshot.stale_run_warning:
        st.warning(snapshot.stale_run_warning)

    st.subheader("Private watchlist")
    st.dataframe(
        [
            {
                "market": item.instrument.market,
                "mic": item.instrument.mic,
                "symbol": item.instrument.symbol,
                "held": item.held,
                "groups": ", ".join(item.groups),
                "tags": ", ".join(item.tags),
                "notes": item.notes or "",
                "explicit_price_zone": json.dumps(item.price_zone.model_dump(exclude_none=True) if item.price_zone else {}, sort_keys=True),
            }
            for item in snapshot.watchlist.instruments
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Exact-session readiness")
    if snapshot.history_status == "corrupt":
        st.error("Tracking history or delivery state is corrupt; no healthy/no-signal conclusion is available.")
    if not snapshot.readiness:
        st.info("The private watchlist is empty. Add a canonical instrument with `equity tracking add`.")
    else:
        st.dataframe(
            [
                {
                    "instrument": item.instrument.display_key,
                    "status": item.status,
                    "expected_session": item.expected_session,
                    "last_evaluated_session": item.last_evaluated_session,
                    "detail": item.detail,
                }
                for item in snapshot.readiness
            ],
            hide_index=True,
            use_container_width=True,
        )

    held_by_identity = {item.instrument.tuple: item.held for item in snapshot.watchlist.instruments}
    candidates = [
        outcome.candidate
        for report in snapshot.recent_reports
        for evaluation in report.evaluations
        for outcome in evaluation.rules
        if outcome.candidate is not None
    ]
    st.subheader("Candidate history and conflicts")
    if candidates:
        st.dataframe(
            [
                {
                    "instrument": candidate.instrument.display_key,
                    "session": candidate.session,
                    "candidate_action": candidate.action,
                    "context": (
                        "held exit candidate"
                        if candidate.action == "SELL" and held_by_identity.get(candidate.instrument.tuple, False)
                        else "downside watch"
                        if candidate.action == "SELL"
                        else "upside watch"
                    ),
                    "source": candidate.source,
                    "explanation": candidate.reasoning,
                    "inputs": json.dumps(candidate.model_dump(exclude_none=True), default=str, sort_keys=True),
                }
                for candidate in candidates
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption("ML scores are not calibrated probabilities of profit. Candidate actions are research outputs, not recommendations or orders.")
    else:
        st.info("No actionable candidate was persisted. Missing, stale, or failed evaluations are not neutral results.")
    if snapshot.conflicts:
        st.warning("Conflicting BUY/SELL candidates were observed. Both are shown; no preferred action is selected.")
        for conflict in snapshot.conflicts:
            st.dataframe(
                [
                    {
                        "instrument": item.instrument.display_key,
                        "action": item.action,
                        "source": item.source,
                        "reason": item.reasoning,
                    }
                    for item in conflict
                ],
                hide_index=True,
                use_container_width=True,
            )

    st.subheader("Event inbox and delivery state")
    if snapshot.recent_events:
        st.dataframe(
            [
                {
                    "event_id": item.event.event_id,
                    "instrument": item.event.instrument.display_key,
                    "session": item.event.session,
                    "candidate_action": item.event.action,
                    "source": item.event.source,
                    "delivery": item.status,
                    "attempts": item.attempts,
                    "error_code": item.error_code,
                }
                for item in snapshot.recent_events
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("No durable opportunity events or delivery attempts are recorded.")
    st.caption("Opportunity delivery is disabled unless explicitly enabled. Delivery state does not imply exactly-once receipt.")


def main() -> None:
    if st is None:
        raise RuntimeError("Streamlit is optional; install the dashboard dependency group to run this application.")

    st.set_page_config(
        page_title="Equity Lake",
        page_icon="📊",
        layout="wide",
    )

    # Build dataset summaries
    import duckdb

    conn = duckdb.connect(":memory:")
    datasets = [summarize_dataset(conn, name, path) for name, path in MARKET_DATASETS.items()]
    conn.close()

    health = load_health_report()
    updates = _load_update_history()

    # Navigation
    page = st.sidebar.selectbox(
        "Page",
        ["Overview", "Dataset Explorer", "Health", "Update History", "Private Tracking Preview"],
    )

    if page == "Overview":
        render_overview(datasets, health)
    elif page == "Dataset Explorer":
        render_dataset_detail(datasets)
    elif page == "Health":
        render_health_page(health)
    elif page == "Update History":
        render_update_history(updates)
    elif page == "Private Tracking Preview":
        render_private_tracking_preview()

    # Footer
    st.sidebar.divider()
    st.sidebar.caption(f"Generated at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")


if __name__ == "__main__":
    main()
