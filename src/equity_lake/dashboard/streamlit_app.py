"""Streamlit data quality dashboard for Equity Lake.

Provides an interactive dashboard for monitoring data quality,
pipeline health, and dataset status.

Usage:
    equity dashboard serve
    equity dashboard serve --port 8501
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
import streamlit as st

from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    DATA_DIR,
    GOLD_FEATURES_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    LOGS_DIR,
    US_EQUITY_DIR,
)

MARKET_DATASETS = {
    "us_equity": US_EQUITY_DIR,
    "cn_ashare": CN_ASHARE_DIR,
    "hk_sg_equity": HK_SG_EQUITY_DIR,
    "jpx_equity": JPX_EQUITY_DIR,
    "krx_equity": KRX_EQUITY_DIR,
    "features": GOLD_FEATURES_DIR,
}


def _summarize_dataset(conn: Any, name: str, path: Path) -> dict[str, Any]:
    """Summarize a dataset for the dashboard."""
    if not path.exists():
        return {
            "name": name,
            "available": False,
            "rows": 0,
            "symbols": 0,
            "latest_date": None,
            "path": str(path),
        }

    try:
        query = f"""
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT ticker) AS symbols,
                CAST(MAX(date) AS VARCHAR) AS latest_date
            FROM read_parquet('{path}/**/*.parquet', hive_partitioning=1)
        """
        row = conn.execute(query).fetchone()
        if row is None:
            return {
                "name": name,
                "available": False,
                "rows": 0,
                "symbols": 0,
                "latest_date": None,
                "path": str(path),
            }
        return {
            "name": name,
            "available": True,
            "rows": int(row[0] or 0),
            "symbols": int(row[1] or 0),
            "latest_date": row[2],
            "path": str(path),
        }
    except Exception:
        return {
            "name": name,
            "available": False,
            "rows": 0,
            "symbols": 0,
            "latest_date": None,
            "path": str(path),
        }


def _load_health_report() -> dict[str, Any] | None:
    """Load the pipeline health report."""
    health_path = Path("site") / "health-report.json"
    if not health_path.exists():
        # Try logs directory
        health_path = LOGS_DIR / "health-report.json"
    if not health_path.exists():
        return None
    try:
        return cast(dict[str, Any], json.loads(health_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return {"alerts": ["Health report could not be parsed."], "metrics": {}}


def _load_update_history() -> list[dict[str, Any]]:
    """Load update history from parquet."""
    update_history_path = DATA_DIR / "update_history.parquet"
    if not update_history_path.exists():
        return []
    try:
        frame = pd.read_parquet(update_history_path)
        if frame.empty:
            return []
        recent = frame.sort_values("updated_at", ascending=False).head(50)
        return cast(list[dict[str, Any]], recent.to_dict(orient="records"))
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
            FROM read_parquet('{dataset["path"]}/**/*.parquet', hive_partitioning=1)
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
    datasets = [_summarize_dataset(conn, name, path) for name, path in MARKET_DATASETS.items()]
    conn.close()

    health = _load_health_report()
    updates = _load_update_history()

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
