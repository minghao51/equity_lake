"""Build a richer static dashboard for GitHub Pages."""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from equity_lake.core.config import get_settings
from equity_lake.core.logging import redact_secrets as _redact_secrets
from equity_lake.core.paths import LOGS_DIR, PIPELINE_RESULTS_GLOB
from equity_lake.dashboard._common import MARKET_DATASETS, load_health_report, load_update_history, summarize_dataset
from equity_lake.dashboard.components import Notice, notice_class
from equity_lake.dashboard.pages import NAV_LINKS, STATIC_PAGES, page_spec

# Update-history rows rendered on the static Updates page.
_EXPORT_HISTORY_LIMIT = 20


def _alerts_html(entries: list[str], empty_text: str) -> str:
    """Render alert entries through the shared state vocabulary.

    An alert is a warning; an empty alert list is a *check that passed*, so it is a
    success notice rather than a warning-coloured "nothing here". State meaning comes
    from :mod:`equity_lake.dashboard.components`, so the static site and the app
    cannot drift into different colour semantics.
    """
    if not entries:
        return f'<li class="{notice_class(Notice.SUCCESS)}">{_escape(empty_text)}</li>'
    return "".join(f'<li class="{notice_class(Notice.WARNING)}">{_escape(entry)}</li>' for entry in entries)


def _escape(value: Any) -> str:
    """Escape one interpolated value for HTML text/attribute context.

    Every value this module interpolates into the exported pages (alerts,
    dataset names/paths/dates, titles, update rows, JSON blocks) comes from
    local artifacts, but the pages are published as static hosting: text is
    escaped at the sink instead of trusting the artifact.
    """
    return html.escape(str(value), quote=True)


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
            "project": _redact_secrets(self.settings.project.model_dump()),
            "summary": {
                "datasets_available": len(available_datasets),
                "tracked_datasets": len(datasets),
                "latest_update": last_updated,
            },
            "schedule": _redact_secrets(self.settings.schedule.model_dump()),
            "datasets": datasets,
            "health": self._load_health_report(),
            "updates": self._load_updates(),
            "config": _redact_secrets(self.settings.model_dump()),
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

        pages: dict[str, str] = {}
        for spec in STATIC_PAGES:
            filename = spec.static_file
            if filename is None:  # pragma: no cover - STATIC_PAGES already excludes these
                continue
            pages[filename] = _STATIC_RENDERERS[spec.key](self, payload)
        for filename, content in pages.items():
            (self.output_dir / filename).write_text(content, encoding="utf-8")

        return self.output_dir / "index.html"

    def _summarize_dataset(self, name: str, dataset_dir: Path) -> dict[str, Any]:
        return summarize_dataset(self.connection, name, dataset_dir)

    def _load_health_report(self) -> dict[str, Any] | None:
        return load_health_report(self.output_dir)

    def _load_updates(self) -> dict[str, Any]:
        update_rows = load_update_history(_EXPORT_HISTORY_LIMIT)

        pipeline_results = []
        for result_path in sorted(LOGS_DIR.glob(PIPELINE_RESULTS_GLOB), reverse=True)[:10]:
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
        nav = "".join(
            (f'<a class="nav-link{" active" if href == active_href else ""}" href="{_escape(href)}">{_escape(label)}</a>')
            for label, href in NAV_LINKS
        )
        intro_html = f'<p class="subtitle">{_escape(intro)}</p>' if intro else ""
        page_title = _escape(title)
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{page_title}</title>
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
        --ok: #1a7f37;
        --error: #b42318;
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
      .alerts li {{ margin: 0 0 10px; color: var(--warn); overflow-wrap: anywhere; }}
      /* State modifiers come from dashboard/components.py: one rule per Notice, so a
         state can never be rendered unstyled. */
      .alerts li.alert--info {{ color: var(--accent); }}
      .alerts li.alert--ok {{ color: var(--ok); }}
      .alerts li.alert--error {{ color: var(--error); }}
      .alerts li.alert--unavailable,
      .alerts li.alert--partial {{ color: var(--warn); }}
      .alerts li.alert--gated {{ color: var(--muted); }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <div class="pill">Beta static dashboard</div>
        <h1>{page_title}</h1>
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
            (f'<article class="card"><div class="eyebrow">{_escape(label)}</div><div class="metric">{_escape(value)}</div></article>')
            for label, value in summary_cards
        )
        rows_html = "".join(
            "<tr>"
            f"<td>{_escape(dataset['name'])}</td>"
            f"<td>{_escape(format(dataset['rows'], ','))}</td>"
            f"<td>{_escape(format(dataset['symbols'], ','))}</td>"
            f"<td>{_escape(dataset['latest_date'] or 'n/a')}</td>"
            f"<td>{'ready' if dataset['available'] else 'missing'}</td>"
            "</tr>"
            for dataset in payload["datasets"]
        )
        alerts = payload.get("health", {}).get("alerts", []) if payload.get("health") else []
        alerts_html = _alerts_html(list(alerts), "No health alerts were exported.")
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
            GitHub Actions cron (UTC): {_escape(payload["schedule"]["cron"])}
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
            f"<td>{_escape(dataset['name'])}</td>"
            f"<td>{_escape(format(dataset['rows'], ','))}</td>"
            f"<td>{_escape(format(dataset['symbols'], ','))}</td>"
            f"<td>{_escape(dataset['latest_date'] or 'n/a')}</td>"
            f"<td>{_escape(dataset['path'])}</td>"
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
            title=page_spec("datasets").label,
            active_href="datasets.html",
            intro="Storage-backed dataset summaries from the current local lake.",
            body=body,
        )

    def _render_health(self, payload: dict[str, Any]) -> str:
        health = payload.get("health") or {"alerts": [], "metrics": {}}
        alerts_html = _alerts_html(list(health.get("alerts", [])), "No health alerts were exported.")
        metrics_html = _escape(json.dumps(health.get("metrics", {}), indent=2, default=str))
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
            title=page_spec("health").label,
            active_href="health.html",
            intro=("Freshness, quality, and pipeline health snapshots exported for static hosting."),
            body=body,
        )

    def _render_updates(self, payload: dict[str, Any]) -> str:
        history_rows = payload["updates"]["history"]
        history_html = (
            "".join(
                "<tr>"
                f"<td>{_escape(row.get('source', ''))}</td>"
                f"<td>{_escape(row.get('symbol', ''))}</td>"
                f"<td>{_escape(row.get('updated_at', ''))}</td>"
                f"<td>{_escape(row.get('records', 0))}</td>"
                "</tr>"
                for row in history_rows
            )
            or "<tr><td colspan='4'>No update history found.</td></tr>"
        )
        pipeline_html = "".join(
            (
                "<article class='card'>"
                f"<div class='eyebrow'>{_escape(item['name'])}</div>"
                f"<pre>{_escape(json.dumps(item['content'], indent=2, default=str))}</pre>"
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
            title=page_spec("updates").label,
            active_href="updates.html",
            intro="Recent update history and saved pipeline result artifacts.",
            body=body,
        )

    def _render_config(self, payload: dict[str, Any]) -> str:
        config_json = _escape(json.dumps(payload["config"], indent=2))
        body = f"""
      <section class="card">
        <div class="eyebrow">Settings</div>
        <pre>{config_json}</pre>
      </section>
"""
        return self._render_page(
            title=page_spec("config").label,
            active_href="config.html",
            intro="Rendered application settings used for the current export.",
            body=body,
        )


#: Which renderer produces each static page. The page set itself comes from the
#: registry (``dashboard/pages.py``), so a page cannot be exported without a renderer
#: or a renderer silently outlive its page.
_STATIC_RENDERERS: dict[str, Callable[[DashboardExporter, dict[str, Any]], str]] = {
    "overview": DashboardExporter._render_overview,
    "datasets": DashboardExporter._render_datasets,
    "health": DashboardExporter._render_health,
    "updates": DashboardExporter._render_updates,
    "config": DashboardExporter._render_config,
}


def build_dashboard(output_dir: Path | None = None) -> Path:
    """Convenience wrapper for CLI and workflows."""
    return DashboardExporter(output_dir=output_dir).write()


__all__ = ["DashboardExporter", "build_dashboard"]
