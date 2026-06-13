"""Build a richer static dashboard for GitHub Pages."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl

from equity_lake.core.config import get_settings
from equity_lake.core.paths import DATA_DIR, JPX_EQUITY_DIR, KRX_EQUITY_DIR, LAKE_DIR, LOGS_DIR

MARKET_DATASETS = {
    "us_equity": LAKE_DIR / "us_equity",
    "cn_ashare": LAKE_DIR / "cn_ashare",
    "hk_sg_equity": LAKE_DIR / "hk_sg_equity",
    "jpx_equity": JPX_EQUITY_DIR,
    "krx_equity": KRX_EQUITY_DIR,
    "features": LAKE_DIR / "features",
}

NAV_LINKS = [
    ("Overview", "index.html"),
    ("Datasets", "datasets.html"),
    ("Health", "health.html"),
    ("Updates", "updates.html"),
    ("Config", "config.html"),
]


class DashboardExporter:
    """Export a multi-page static status site and JSON payload."""

    def __init__(self, output_dir: Path | None = None):
        self.settings = get_settings()
        self.output_dir = output_dir or Path(self.settings.dashboard.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(":memory:")

    def build_payload(self) -> dict[str, Any]:
        """Build dashboard data from local artifacts."""
        datasets = [self._summarize_dataset(name, path) for name, path in MARKET_DATASETS.items()]
        available_datasets = [dataset for dataset in datasets if dataset["available"]]
        last_updated = max(
            (dataset["latest_date"] for dataset in available_datasets if dataset["latest_date"]),
            default="No data yet",
        )

        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "project": self.settings.project.model_dump(),
            "summary": {
                "datasets_available": len(available_datasets),
                "tracked_datasets": len(datasets),
                "latest_update": last_updated,
            },
            "schedule": self.settings.schedule.model_dump(),
            "datasets": datasets,
            "health": self._load_health_report(),
            "updates": self._load_updates(),
            "config": self.settings.model_dump(),
        }
        return payload

    def write(self) -> Path:
        """Write JSON payload and HTML pages."""
        payload = self.build_payload()
        data_path = self.output_dir / self.settings.dashboard.data_file
        data_path.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )

        pages = {
            "index.html": self._render_overview(payload),
            "datasets.html": self._render_datasets(payload),
            "health.html": self._render_health(payload),
            "updates.html": self._render_updates(payload),
            "config.html": self._render_config(payload),
        }
        for filename, content in pages.items():
            (self.output_dir / filename).write_text(content, encoding="utf-8")

        return self.output_dir / "index.html"

    def _summarize_dataset(self, name: str, dataset_dir: Path) -> dict[str, Any]:
        if not dataset_dir.exists():
            return {
                "name": name,
                "available": False,
                "rows": 0,
                "symbols": 0,
                "latest_date": None,
                "path": str(dataset_dir),
            }

        query = f"""
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT ticker) AS symbols,
                CAST(MAX(date) AS VARCHAR) AS latest_date
            FROM read_parquet('{dataset_dir}/**/*.parquet', hive_partitioning=1)
        """

        try:
            row = self.connection.execute(query).fetchone()
        except Exception:
            return {
                "name": name,
                "available": False,
                "rows": 0,
                "symbols": 0,
                "latest_date": None,
                "path": str(dataset_dir),
            }

        if row is None:
            return {
                "name": name,
                "available": False,
                "rows": 0,
                "symbols": 0,
                "latest_date": None,
                "path": str(dataset_dir),
            }

        return {
            "name": name,
            "available": True,
            "rows": int(row[0] or 0),
            "symbols": int(row[1] or 0),
            "latest_date": row[2],
            "path": str(dataset_dir),
        }

    def _load_health_report(self) -> dict[str, Any] | None:
        health_path = self.output_dir / "health-report.json"
        if not health_path.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(health_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return {"alerts": ["Health report could not be parsed."], "metrics": {}}

    def _load_updates(self) -> dict[str, Any]:
        update_history_path = DATA_DIR / "update_history.parquet"
        update_rows: list[dict[str, Any]] = []
        if update_history_path.exists():
            frame = pl.read_parquet(update_history_path)
            if not frame.is_empty():
                recent = frame.sort("updated_at", descending=True).head(20)
                update_rows = recent.to_dicts()

        pipeline_results = []
        for result_path in sorted(LOGS_DIR.glob("pipeline_results_*.json"), reverse=True)[:10]:
            try:
                pipeline_results.append(
                    {
                        "name": result_path.name,
                        "content": json.loads(result_path.read_text(encoding="utf-8")),
                    }
                )
            except json.JSONDecodeError:
                continue

        return {
            "history": update_rows,
            "pipeline_results": pipeline_results,
        }

    def _render_page(
        self,
        title: str,
        active_href: str,
        body: str,
        intro: str | None = None,
    ) -> str:
        nav = "".join((f'<a class="nav-link{" active" if href == active_href else ""}" href="{href}">{label}</a>') for label, href in NAV_LINKS)
        intro_html = f'<p class="subtitle">{intro}</p>' if intro else ""
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        --bg: #f4f1ea;
        --panel: rgba(255, 255, 255, 0.86);
        --line: rgba(33, 41, 52, 0.12);
        --text: #1d2733;
        --muted: #66717f;
        --accent: #116466;
        --accent-soft: #d9eeea;
        --warn: #b55d07;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: "Avenir Next", "Segoe UI", sans-serif;
        color: var(--text);
        background:
          radial-gradient(
            circle at top left,
            rgba(17, 100, 102, 0.18),
            transparent 32%
          ),
          linear-gradient(180deg, #f9f7f2 0%, var(--bg) 100%);
      }}
      main {{ max-width: 1120px; margin: 0 auto; padding: 48px 20px 80px; }}
      .hero {{ margin-bottom: 22px; }}
      h1 {{ font-size: clamp(2rem, 5vw, 4rem); line-height: 0.95; margin: 0; }}
      .subtitle {{ color: var(--muted); max-width: 48rem; margin-top: 14px; }}
      .nav {{
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        margin: 24px 0 30px;
      }}
      .nav-link {{
        text-decoration: none;
        color: var(--text);
        padding: 10px 14px;
        border-radius: 999px;
        background: rgba(255,255,255,0.55);
        border: 1px solid var(--line);
      }}
      .nav-link.active {{ background: var(--accent-soft); color: var(--accent); }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 16px;
        margin: 0 0 24px;
      }}
      .card {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 20px;
        backdrop-filter: blur(18px);
        box-shadow: 0 14px 34px rgba(25, 35, 45, 0.07);
      }}
      .eyebrow {{
        color: var(--muted);
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }}
      .metric {{ font-size: 2rem; margin-top: 10px; }}
      table {{ width: 100%; border-collapse: collapse; }}
      th, td {{
        padding: 12px 0;
        text-align: left;
        border-bottom: 1px solid var(--line);
        vertical-align: top;
      }}
      th {{ color: var(--muted); font-weight: 600; }}
      pre {{
        overflow: auto;
        white-space: pre-wrap;
        background: rgba(0, 0, 0, 0.03);
        border-radius: 14px;
        padding: 14px;
      }}
      .pill {{
        display: inline-block;
        padding: 6px 10px;
        border-radius: 999px;
        background: var(--accent-soft);
        color: var(--accent);
        font-size: 0.84rem;
      }}
      .muted {{ color: var(--muted); }}
      .alerts li {{ margin: 0 0 10px; color: var(--warn); }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <div class="pill">Beta static dashboard</div>
        <h1>{title}</h1>
        {intro_html}
      </section>
      <nav class="nav">{nav}</nav>
      {body}
    </main>
  </body>
</html>
"""

    def _render_overview(self, payload: dict[str, Any]) -> str:
        summary_cards = [
            ("Latest update", payload["summary"]["latest_update"]),
            ("Datasets available", str(payload["summary"]["datasets_available"])),
            ("Tracked datasets", str(payload["summary"]["tracked_datasets"])),
            ("Generated", payload["generated_at"]),
        ]
        cards_html = "".join(
            (f'<article class="card"><div class="eyebrow">{label}</div><div class="metric">{value}</div></article>') for label, value in summary_cards
        )
        rows_html = "".join(
            "<tr>"
            f"<td>{dataset['name']}</td>"
            f"<td>{dataset['rows']:,}</td>"
            f"<td>{dataset['symbols']:,}</td>"
            f"<td>{dataset['latest_date'] or 'n/a'}</td>"
            f"<td>{'ready' if dataset['available'] else 'missing'}</td>"
            "</tr>"
            for dataset in payload["datasets"]
        )
        alerts = payload.get("health", {}).get("alerts", []) if payload.get("health") else []
        alerts_html = "".join(f"<li>{alert}</li>" for alert in alerts) or "<li>No health alerts were exported.</li>"
        body = f"""
      <section class="grid">{cards_html}</section>
      <section class="card">
        <div class="eyebrow">Datasets</div>
        <table>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Rows</th>
              <th>Symbols</th>
              <th>Latest date</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </section>
      <section class="grid" style="margin-top: 16px;">
        <article class="card">
          <div class="eyebrow">Automation</div>
          <p class="muted">
            GitHub Actions cron: {payload["schedule"]["cron"]}
            ({payload["schedule"]["timezone"]})
          </p>
        </article>
        <article class="card">
          <div class="eyebrow">Alerts</div>
          <ul class="alerts">{alerts_html}</ul>
        </article>
      </section>
"""
        return self._render_page(
            title=self.settings.dashboard.title,
            active_href="index.html",
            intro=self.settings.dashboard.subtitle,
            body=body,
        )

    def _render_datasets(self, payload: dict[str, Any]) -> str:
        rows_html = "".join(
            "<tr>"
            f"<td>{dataset['name']}</td>"
            f"<td>{dataset['rows']:,}</td>"
            f"<td>{dataset['symbols']:,}</td>"
            f"<td>{dataset['latest_date'] or 'n/a'}</td>"
            f"<td>{dataset['path']}</td>"
            "</tr>"
            for dataset in payload["datasets"]
        )
        body = f"""
      <section class="card">
        <div class="eyebrow">Dataset inventory</div>
        <table>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Rows</th>
              <th>Symbols</th>
              <th>Latest date</th>
              <th>Path</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </section>
"""
        return self._render_page(
            title="Datasets",
            active_href="datasets.html",
            intro="Storage-backed dataset summaries from the current local lake.",
            body=body,
        )

    def _render_health(self, payload: dict[str, Any]) -> str:
        health = payload.get("health") or {"alerts": [], "metrics": {}}
        alerts_html = "".join(f"<li>{alert}</li>" for alert in health.get("alerts", [])) or "<li>No health alerts were exported.</li>"
        metrics_html = json.dumps(health.get("metrics", {}), indent=2, default=str)
        body = f"""
      <section class="grid">
        <article class="card">
          <div class="eyebrow">Alerts</div>
          <ul class="alerts">{alerts_html}</ul>
        </article>
        <article class="card">
          <div class="eyebrow">Metrics JSON</div>
          <pre>{metrics_html}</pre>
        </article>
      </section>
"""
        return self._render_page(
            title="Health",
            active_href="health.html",
            intro=("Freshness, quality, and pipeline health snapshots exported for static hosting."),
            body=body,
        )

    def _render_updates(self, payload: dict[str, Any]) -> str:
        history_rows = payload["updates"]["history"]
        history_html = (
            "".join(
                "<tr>"
                f"<td>{row.get('source', '')}</td>"
                f"<td>{row.get('symbol', '')}</td>"
                f"<td>{row.get('updated_at', '')}</td>"
                f"<td>{row.get('records', 0)}</td>"
                "</tr>"
                for row in history_rows
            )
            or "<tr><td colspan='4'>No update history found.</td></tr>"
        )
        pipeline_html = "".join(
            (
                "<article class='card'>"
                f"<div class='eyebrow'>{item['name']}</div>"
                f"<pre>{json.dumps(item['content'], indent=2, default=str)}</pre>"
                "</article>"
            )
            for item in payload["updates"]["pipeline_results"]
        ) or ("<article class='card'><div class='eyebrow'>Pipeline results</div><p class='muted'>No saved pipeline result files found.</p></article>")
        body = f"""
      <section class="card">
        <div class="eyebrow">Update history</div>
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Symbol</th>
              <th>Updated at</th>
              <th>Records</th>
            </tr>
          </thead>
          <tbody>{history_html}</tbody>
        </table>
      </section>
      <section class="grid" style="margin-top: 16px;">{pipeline_html}</section>
"""
        return self._render_page(
            title="Updates",
            active_href="updates.html",
            intro="Recent update history and saved pipeline result artifacts.",
            body=body,
        )

    def _render_config(self, payload: dict[str, Any]) -> str:
        config_json = json.dumps(payload["config"], indent=2)
        body = f"""
      <section class="card">
        <div class="eyebrow">Settings</div>
        <pre>{config_json}</pre>
      </section>
"""
        return self._render_page(
            title="Config",
            active_href="config.html",
            intro="Rendered application settings used for the current export.",
            body=body,
        )


def build_dashboard(output_dir: Path | None = None) -> Path:
    """Convenience wrapper for CLI and workflows."""
    return DashboardExporter(output_dir=output_dir).write()


def parse_arguments() -> argparse.Namespace:
    """Parse dashboard CLI arguments."""
    parser = argparse.ArgumentParser(description="Build the static Equity Lake dashboard")
    parser.add_argument(
        "command",
        nargs="?",
        default="build",
        choices=["build"],
        help="Dashboard command to run",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory where static dashboard files should be written",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_arguments()
    if args.command == "build":
        build_dashboard(output_dir=args.output_dir)


__all__ = ["DashboardExporter", "build_dashboard", "main", "parse_arguments"]


if __name__ == "__main__":
    main()
