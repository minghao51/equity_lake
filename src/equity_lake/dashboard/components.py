"""Shared notice/state vocabulary for the dashboard renderers.

The two renderers cannot share a drawing function — one emits ``st.*`` widgets, the
other static HTML — so they share the **vocabulary** instead: one closed set of
notice kinds and one mapping per renderer. That is what keeps "unavailable",
"partial", "refused" and "gated" meaning the same thing on both surfaces, instead of
becoming a red box on one and an ordinary paragraph on the other.

The state rules these kinds encode are recorded in
``assets/svg/dashboard-component-and-state-sheet.svg``: unavailable is never zero,
partial is never a total, refused means nothing was written, gated means not built.
Presentation stays renderer-local; only the meaning is shared.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class Notice(StrEnum):
    """The closed set of states a dashboard panel can be in."""

    INFO = "info"
    """Neutral information; nothing is wrong."""

    SUCCESS = "success"
    """A positive result that was actually checked (never an empty placeholder)."""

    WARNING = "warning"
    """Something needs attention but the panel is still useful."""

    ERROR = "error"
    """A genuine failure."""

    UNAVAILABLE = "unavailable"
    """Required evidence is missing; never rendered as zero or as a blank cell."""

    PARTIAL = "partial"
    """Some components are complete and some are withheld; a subtotal is not a total."""

    REFUSED = "refused"
    """Input was rejected; nothing was written."""

    GATED = "gated"
    """The surface is planned but not authorized; it must not look interactive."""


#: Which Streamlit primitive expresses each state. ``unavailable``/``partial`` are
#: amber warnings rather than errors — the book is healthy, the evidence is not
#: complete — and ``gated`` is neutral information, not a failure.
_STREAMLIT_METHOD: dict[Notice, str] = {
    Notice.INFO: "info",
    Notice.SUCCESS: "success",
    Notice.WARNING: "warning",
    Notice.ERROR: "error",
    Notice.UNAVAILABLE: "warning",
    Notice.PARTIAL: "warning",
    Notice.REFUSED: "error",
    Notice.GATED: "info",
}

#: The CSS modifier each state gets in the static export. The exporter's stylesheet
#: defines one rule per modifier, so a state cannot be silently unstyled.
_CSS_MODIFIER: dict[Notice, str] = {
    Notice.INFO: "info",
    Notice.SUCCESS: "ok",
    Notice.WARNING: "warn",
    Notice.ERROR: "error",
    Notice.UNAVAILABLE: "unavailable",
    Notice.PARTIAL: "partial",
    Notice.REFUSED: "error",
    Notice.GATED: "gated",
}


def streamlit_method(notice: Notice) -> str:
    """The ``st.*`` method name that expresses a state in the interactive app."""
    return _STREAMLIT_METHOD[notice]


def css_modifier(notice: Notice) -> str:
    """The stylesheet modifier suffix for a state in the static export."""
    return _CSS_MODIFIER[notice]


def notice_class(notice: Notice) -> str:
    """The full class attribute value a static notice element carries."""
    return f"alert alert--{css_modifier(notice)}"


def render_notice(st: Any, notice: Notice, text: str) -> None:
    """Render one notice through the shared mapping (``st`` is injected, never imported)."""
    method = getattr(st, streamlit_method(notice), None)
    if method is None:  # pragma: no cover - a broken Streamlit stand-in, not a data path
        raise RuntimeError(f"Streamlit stand-in has no {streamlit_method(notice)!r} method for notice {notice.value!r}")
    method(text)


__all__ = [
    "Notice",
    "css_modifier",
    "notice_class",
    "render_notice",
    "streamlit_method",
]
