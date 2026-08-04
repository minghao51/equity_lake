"""Streamlit data quality dashboard for Equity Lake.

Provides an interactive dashboard for monitoring data quality,
pipeline health, and dataset status.

Usage:
    equity dashboard serve
    equity dashboard serve --port 8501
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from equity_lake.core.paths import DATA_DIR, LOGS_DIR
from equity_lake.dashboard._queries import (
    MARKET_DATASETS,
    load_health_report,
    load_update_history,
    summarize_dataset,
)


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

        from equity_lake.storage.lake_reader import duckdb_scan_for

        conn = duckdb.connect(":memory:")
        scan = duckdb_scan_for(Path(dataset["path"]))
        query = f"""
            SELECT *
            FROM {scan}
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


def main() -> None:
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

    health = load_health_report([Path("site"), LOGS_DIR])
    updates = load_update_history(DATA_DIR / "update_history.parquet", limit=50)

    # Navigation
    page = st.sidebar.selectbox(
        "Page",
        ["Overview", "Dataset Explorer", "Health", "Update History"],
    )

    if page == "Overview":
        render_overview(datasets, health)
    elif page == "Dataset Explorer":
        render_dataset_detail(datasets)
    elif page == "Health":
        render_health_page(health)
    elif page == "Update History":
        render_update_history(updates)

    # Footer
    st.sidebar.divider()
    st.sidebar.caption(f"Generated at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")


if __name__ == "__main__":
    main()
