"""The dashboard's single page registry (the one place a page is defined).

Two renderers present one information architecture: the Streamlit app
(:mod:`equity_lake.dashboard.streamlit_app`) and the static HTML exporter
(:mod:`equity_lake.dashboard.exporter`). They used to spell the pages
independently and drifted — "Dataset Explorer" vs "Datasets", "Update History" vs
"Updates", plus one page each renderer owned alone.

The owner standardised the vocabulary on 2026-11-07, so **a label is defined once
here** and both renderers consume it. The *page-set* asymmetry is deliberate and
recorded per page: ``Config`` is static-only, and the private tracking preview is
app-only because it is loopback-gated (ADR-0018).

Adding a page means adding a :class:`PageSpec` here **and** a renderer branch; adding
it anywhere else fails ``tests/unit/test_dashboard_ia_parity.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageSpec:
    """One logical dashboard page and which renderers expose it."""

    key: str
    """Stable machine key used for dispatch; never shown to a reader."""

    label: str
    """The single human label, used by every renderer and by the navigation."""

    static_file: str | None
    """File name in the static export, or ``None`` when the page is not exported."""

    streamlit: bool
    """Whether the interactive app has this page."""

    gated: bool = False
    """A private surface that may only render when the app is loopback-bound."""


PAGE_SPECS: tuple[PageSpec, ...] = (
    PageSpec("overview", "Overview", "index.html", streamlit=True),
    PageSpec("datasets", "Datasets", "datasets.html", streamlit=True),
    PageSpec("health", "Health", "health.html", streamlit=True),
    PageSpec("updates", "Updates", "updates.html", streamlit=True),
    PageSpec("config", "Config", "config.html", streamlit=False),
    PageSpec("private_tracking", "Private Tracking Preview", None, streamlit=True, gated=True),
)

STREAMLIT_PAGES: tuple[PageSpec, ...] = tuple(spec for spec in PAGE_SPECS if spec.streamlit)
STATIC_PAGES: tuple[PageSpec, ...] = tuple(spec for spec in PAGE_SPECS if spec.static_file is not None)
PAGE_LABELS: tuple[str, ...] = tuple(spec.label for spec in STREAMLIT_PAGES)


def _nav_links() -> tuple[tuple[str, str], ...]:
    """``(label, file)`` pairs for the static navigation, in registry order."""
    links: list[tuple[str, str]] = []
    for spec in PAGE_SPECS:
        static_file = spec.static_file
        if static_file is not None:
            links.append((spec.label, static_file))
    return tuple(links)


NAV_LINKS: tuple[tuple[str, str], ...] = _nav_links()

_LABEL_TO_KEY: dict[str, str] = {spec.label: spec.key for spec in STREAMLIT_PAGES}


def page_spec(key: str) -> PageSpec:
    """The spec for a page key; unknown keys raise instead of inventing a page."""
    for spec in PAGE_SPECS:
        if spec.key == key:
            return spec
    raise KeyError(f"unknown dashboard page key {key!r}")


def streamlit_key_for_label(label: str) -> str:
    """The page key a Streamlit selection maps to (the label is the selection)."""
    try:
        return _LABEL_TO_KEY[label]
    except KeyError:
        raise KeyError(f"unknown dashboard page label {label!r}; the label must come from PAGE_LABELS") from None


__all__ = [
    "NAV_LINKS",
    "PAGE_LABELS",
    "PAGE_SPECS",
    "STATIC_PAGES",
    "STREAMLIT_PAGES",
    "PageSpec",
    "page_spec",
    "streamlit_key_for_label",
]
