"""Dashboard IA parity: one registry, two renderers (A1 + A2).

The dashboard presents one information architecture through two renderers — the
Streamlit app and the static HTML exporter. They used to spell the pages
independently and drifted ("Dataset Explorer" vs "Datasets", "Update History" vs
"Updates", plus a page each renderer owned alone).

The owner standardised the vocabulary on 2026-11-07, so
:mod:`equity_lake.dashboard.pages` is now the **only** place a page label, file or
renderer flag lives. This module enforces that:

* neither renderer may hardcode a page label (checked by reading the sources);
* the retired spellings must not come back;
* every registry page must have a renderer on the side that claims it;
* the navigation stays complete, unique and self-marking on every static page.

The deliberate page-set asymmetry survives: ``Config`` is static-only and the
private tracking preview is app-only (loopback-gated). It is recorded in the
registry, not hidden.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import equity_lake
from equity_lake.dashboard.exporter import _STATIC_RENDERERS, NAV_LINKS, DashboardExporter
from equity_lake.dashboard.pages import (
    PAGE_LABELS,
    PAGE_SPECS,
    STATIC_PAGES,
    STREAMLIT_PAGES,
    page_spec,
)

DASHBOARD_DIR = Path(equity_lake.__file__).parent / "dashboard"
STREAMLIT_SOURCE = DASHBOARD_DIR / "streamlit_app.py"
EXPORTER_SOURCE = DASHBOARD_DIR / "exporter.py"

#: Spellings retired by the 2026-11-07 naming decision.
RETIRED_LABELS = ("Dataset Explorer", "Update History")


def _streamlit_tree() -> ast.Module:
    return ast.parse(STREAMLIT_SOURCE.read_text(encoding="utf-8"))


def _selectbox_page_argument() -> ast.expr:
    """The second argument of ``selectbox("Page", ...)`` — must be the registry tuple."""
    for node in ast.walk(_streamlit_tree()):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not (isinstance(function, ast.Attribute) and function.attr == "selectbox"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != "Page":
            continue
        if len(node.args) < 2:  # pragma: no cover - malformed call, reported below
            break
        return node.args[1]
    pytest.fail('could not find st.sidebar.selectbox("Page", ...) in dashboard/streamlit_app.py')


def _streamlit_dispatch_keys() -> set[str]:
    """Page keys compared in the app's dispatch chain (``page == "<key>"``)."""
    keys: set[str] = set()
    for node in ast.walk(_streamlit_tree()):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
            continue
        left, right = node.left, node.comparators[0]
        if isinstance(left, ast.Name) and left.id == "page" and isinstance(right, ast.Constant) and isinstance(right.value, str):
            keys.add(right.value)
    return keys


class TestRegistryIsTheSingleSource:
    def test_streamlit_navigation_comes_from_the_registry(self) -> None:
        argument = _selectbox_page_argument()

        assert isinstance(argument, ast.Name) and argument.id == "PAGE_LABELS", (
            f"the Streamlit page selector must be PAGE_LABELS from dashboard/pages.py, not a literal list: found {ast.dump(argument)[:120]}"
        )

    @pytest.mark.parametrize("source", [STREAMLIT_SOURCE, EXPORTER_SOURCE], ids=["streamlit", "exporter"])
    def test_renderers_do_not_hardcode_page_labels(self, source: Path) -> None:
        text = source.read_text(encoding="utf-8")

        for label in (*RETIRED_LABELS, "Overview", "Datasets", "Health", "Updates", "Config"):
            assert f'"{label}"' not in text and f"'{label}'" not in text, (
                f"{source.name} still spells the page label {label!r} as a literal; it must come from "
                "dashboard/pages.py so the two renderers cannot drift again"
            )

    def test_retired_labels_are_gone_from_the_renderers(self) -> None:
        for source in (STREAMLIT_SOURCE, EXPORTER_SOURCE):
            text = source.read_text(encoding="utf-8")
            for retired in RETIRED_LABELS:
                assert retired not in text, f"{source.name} still contains the retired label {retired!r}"

    def test_registry_internals_are_consistent(self) -> None:
        keys = [spec.key for spec in PAGE_SPECS]
        labels = [spec.label for spec in PAGE_SPECS]
        files = [spec.static_file for spec in STATIC_PAGES]

        assert len(set(keys)) == len(keys), f"duplicate page keys: {keys}"
        assert len(set(labels)) == len(labels), f"duplicate page labels: {labels}"
        assert len(set(files)) == len(files), f"duplicate static files: {files}"
        assert all(str(name).endswith(".html") for name in files), f"static pages must be .html: {files}"
        assert set(STATIC_PAGES).issubset(PAGE_SPECS)
        assert set(STREAMLIT_PAGES).issubset(PAGE_SPECS)
        assert page_spec("datasets").label == "Datasets"
        with pytest.raises(KeyError):
            page_spec("no-such-page")


class TestRenderersCoverTheRegistry:
    def test_every_page_is_rendered_somewhere(self) -> None:
        orphans = [spec.key for spec in PAGE_SPECS if spec.static_file is None and not spec.streamlit]

        assert orphans == [], f"registry pages with no renderer: {orphans}"

    def test_every_static_page_has_a_renderer(self) -> None:
        assert set(_STATIC_RENDERERS) == {spec.key for spec in STATIC_PAGES}, (
            "the static renderer map and the registry's static pages disagree: "
            f"renderers={sorted(_STATIC_RENDERERS)}, pages={sorted(spec.key for spec in STATIC_PAGES)}"
        )

    def test_streamlit_dispatch_covers_exactly_the_registry_pages(self) -> None:
        expected = {spec.key for spec in STREAMLIT_PAGES}

        assert _streamlit_dispatch_keys() == expected, (
            "the Streamlit dispatch chain does not match the registry: "
            f"missing={sorted(expected - _streamlit_dispatch_keys())}, "
            f"extra={sorted(_streamlit_dispatch_keys() - expected)}"
        )

    def test_navigation_labels_match_the_app_labels(self) -> None:
        nav = dict(NAV_LINKS)

        for spec in PAGE_SPECS:
            if spec.static_file is not None and spec.streamlit:
                assert spec.label in nav, f"{spec.key} is exported but missing from the navigation"
                assert spec.label in PAGE_LABELS, f"{spec.key} is in the app but missing from PAGE_LABELS"

    def test_renderer_only_pages_are_recorded_deliberately(self) -> None:
        streamlit_only = sorted(spec.key for spec in PAGE_SPECS if spec.streamlit and spec.static_file is None)
        static_only = sorted(spec.key for spec in PAGE_SPECS if not spec.streamlit and spec.static_file is not None)
        gated = sorted(spec.key for spec in PAGE_SPECS if spec.gated)

        # Deliberate, reviewed asymmetry: Config is static-only; the private surfaces are
        # app-only because they are loopback-gated (ADR-0018). P03's four panels are the
        # private console (spec §5.4) and are never exported.
        assert (streamlit_only, static_only) == (
            ["portfolio", "private_tracking", "readiness", "research", "signals"],
            ["config"],
        )
        assert gated == ["portfolio", "private_tracking", "readiness", "research", "signals"], "every private surface must stay marked gated"


class TestStaticNavigationRendering:
    def test_navigation_targets_are_unique_and_page_like(self) -> None:
        labels = [label for label, _href in NAV_LINKS]
        hrefs = [href for _label, href in NAV_LINKS]

        assert len(set(labels)) == len(labels), f"duplicate nav labels: {labels}"
        assert len(set(hrefs)) == len(hrefs), f"duplicate nav targets: {hrefs}"
        assert len(set(Path(href).stem for href in hrefs)) == len(hrefs), f"nav targets share a stem: {hrefs}"

    @pytest.mark.parametrize(("label", "href"), NAV_LINKS)
    def test_every_static_page_renders_the_whole_navigation(self, label: str, href: str) -> None:
        exporter = object.__new__(DashboardExporter)

        html = exporter._render_page(title=label, active_href=href, body="<p>body</p>")

        for nav_label, nav_href in NAV_LINKS:
            assert f'href="{nav_href}"' in html, f"{href} does not link {nav_href}"
            assert f">{nav_label}</a>" in html, f"{href} drops the {nav_label!r} label"
        assert f'class="nav-link active" href="{href}"' in html, f"{href} does not mark itself active"
        assert html.count('class="nav-link active"') == 1, f"{href} marks more than one page active"
