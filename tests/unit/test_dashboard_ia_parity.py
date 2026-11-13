"""Dashboard information-architecture parity guard (A1).

The dashboard renders one information architecture through **two** renderers: the
interactive Streamlit app (`dashboard/streamlit_app.py`) and the published static
HTML exporter (`dashboard/exporter.py`). Nothing compared them, and they had
drifted: two logical pages carry different labels, and each renderer owns one page
the other lacks (Config is static-only; the private tracking preview is
app-only).

This module pins that state instead of pretending it is consistent. It does **not**
decide the naming question — whether the two voices should be standardised is an
open owner decision. What it guarantees is that any *unplanned* change (a page
added to one renderer, a page renamed, a page dropped) fails here and must be
recorded deliberately in :data:`IA_LEDGER`.

No lake, network or filesystem write is involved: the Streamlit page list is read
from source with :mod:`ast` (deliberately, so a refactor to a shared constant
also updates this guard), and the static pages are the real ``NAV_LINKS``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import equity_lake  # type: ignore[import-untyped]
from equity_lake.dashboard.exporter import NAV_LINKS, DashboardExporter  # type: ignore[import-untyped]

STREAMLIT_SOURCE = Path(equity_lake.__file__).parent / "dashboard" / "streamlit_app.py"

#: One row per logical page. ``None`` means that renderer does not expose the page.
#: The label split is TODAY'S state, not an endorsement: it is the open naming
#: decision recorded in the design artifacts under ``assets/svg/``.
IA_LEDGER: dict[str, dict[str, str | None]] = {
    "overview": {"streamlit": "Overview", "static": "Overview"},
    "datasets": {"streamlit": "Dataset Explorer", "static": "Datasets"},
    "health": {"streamlit": "Health", "static": "Health"},
    "updates": {"streamlit": "Update History", "static": "Updates"},
    "config": {"streamlit": None, "static": "Config"},
    "private_tracking": {"streamlit": "Private Tracking Preview", "static": None},
}


def _streamlit_pages() -> list[str]:
    """The labels in ``st.sidebar.selectbox("Page", [...])``, read without importing streamlit."""
    tree = ast.parse(STREAMLIT_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not (isinstance(function, ast.Attribute) and function.attr == "selectbox"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != "Page":
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.List):
            pytest.fail(
                "the Streamlit page selector is no longer a literal list; update the parity guard "
                "(tests/unit/test_dashboard_ia_parity.py) deliberately with the new page source"
            )
        return [element.value for element in node.args[1].elts if isinstance(element, ast.Constant) and isinstance(element.value, str)]
    pytest.fail(
        'could not find st.sidebar.selectbox("Page", [...]) in dashboard/streamlit_app.py; '
        "if the navigation changed shape, update the parity guard deliberately"
    )


def _static_pages() -> list[str]:
    return [label for label, _href in NAV_LINKS]


def _ledger_labels(renderer: str) -> set[str]:
    labels: set[str] = set()
    for row in IA_LEDGER.values():
        label = row[renderer]
        if label is not None:
            labels.add(label)
    return labels


def test_ledger_matches_the_streamlit_pages() -> None:
    actual = set(_streamlit_pages())

    assert actual == _ledger_labels("streamlit"), (
        "the Streamlit page set changed: added {added}, removed {removed}. Record the change in IA_LEDGER "
        "(and the design artifacts) deliberately instead of silently diverging from the static site.".format(
            added=sorted(actual - _ledger_labels("streamlit")),
            removed=sorted(_ledger_labels("streamlit") - actual),
        )
    )


def test_ledger_matches_the_static_pages() -> None:
    actual = set(_static_pages())

    assert actual == _ledger_labels("static"), (
        "the static export page set changed: added {added}, removed {removed}. Record the change in IA_LEDGER deliberately.".format(
            added=sorted(actual - _ledger_labels("static")),
            removed=sorted(_ledger_labels("static") - actual),
        )
    )


def test_every_logical_page_is_rendered_somewhere() -> None:
    orphans = [page for page, row in IA_LEDGER.items() if row["streamlit"] is None and row["static"] is None]

    assert orphans == [], f"logical pages with no renderer: {orphans}"


def test_known_label_divergences_are_exactly_the_recorded_ones() -> None:
    """A rename on either side must fail here, so drift cannot grow silently."""
    divergent = {
        page: (row["streamlit"], row["static"])
        for page, row in IA_LEDGER.items()
        if row["streamlit"] is not None and row["static"] is not None and row["streamlit"] != row["static"]
    }

    assert divergent == {
        "datasets": ("Dataset Explorer", "Datasets"),
        "updates": ("Update History", "Updates"),
    }, (
        "the set of cross-renderer label divergences changed. If the naming decision was taken, shrink "
        "IA_LEDGER and this expectation together; do not add a new divergence without recording it."
    )


def test_renderer_only_pages_are_exactly_the_recorded_ones() -> None:
    streamlit_only = sorted(page for page, row in IA_LEDGER.items() if row["streamlit"] is not None and row["static"] is None)
    static_only = sorted(page for page, row in IA_LEDGER.items() if row["streamlit"] is None and row["static"] is not None)

    assert (streamlit_only, static_only) == (["private_tracking"], ["config"]), (
        f"the renderer-only page sets changed: app-only {streamlit_only}, static-only {static_only}. Add the missing page to the "
        "other renderer or record the asymmetry deliberately."
    )


def test_static_navigation_targets_are_unique_and_page_like() -> None:
    labels = [label for label, _href in NAV_LINKS]
    hrefs = [href for _label, href in NAV_LINKS]

    assert len(set(labels)) == len(labels), f"duplicate nav labels: {labels}"
    assert len(set(hrefs)) == len(hrefs), f"duplicate nav targets: {hrefs}"
    assert all(href.endswith(".html") for href in hrefs), f"nav targets must be static pages: {hrefs}"
    assert len(set(Path(href).stem for href in hrefs)) == len(hrefs), f"nav targets share a stem: {hrefs}"


@pytest.mark.parametrize(("label", "href"), NAV_LINKS)
def test_every_static_page_renders_the_whole_navigation(label: str, href: str) -> None:
    """Each exported page must link every other page, with itself marked active."""
    exporter = object.__new__(DashboardExporter)

    html = exporter._render_page(title=label, active_href=href, body="<p>body</p>")

    for nav_label, nav_href in NAV_LINKS:
        assert f'href="{nav_href}"' in html, f"{href} does not link {nav_href}"
        assert f">{nav_label}</a>" in html, f"{href} drops the {nav_label!r} label"
    assert f'class="nav-link active" href="{href}"' in html, f"{href} does not mark itself active"
    assert html.count('class="nav-link active"') == 1, f"{href} marks more than one page active"


def test_streamlit_labels_are_unique() -> None:
    pages = _streamlit_pages()

    assert len(set(pages)) == len(pages), f"duplicate Streamlit page labels: {pages}"
    assert all(page.strip() == page and page for page in pages), f"blank or padded Streamlit label: {pages}"
