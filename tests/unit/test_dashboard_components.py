"""Shared notice/state vocabulary for the two dashboard renderers.

``dashboard/components.py`` owns the *meaning* of a state; each renderer owns the
presentation. These tests keep the two halves honest: every state must map to a real
Streamlit primitive and to a stylesheet rule the static export actually defines, and
the semantic distinctions the design sheet promises (unavailable is not success,
refused is loud, gated is neutral) must survive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import equity_lake
from equity_lake.dashboard.components import (
    Notice,
    css_modifier,
    notice_class,
    render_notice,
    streamlit_method,
)

EXPORTER_SOURCE = Path(equity_lake.__file__).parent / "dashboard" / "exporter.py"

#: The Streamlit primitives a notice may map to.
STREAMLIT_METHODS = {"info", "success", "warning", "error"}


class _FakeStreamlit:
    """Records which primitive was called, without importing streamlit."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def info(self, text: str) -> None:
        self.calls.append(("info", text))

    def success(self, text: str) -> None:
        self.calls.append(("success", text))

    def warning(self, text: str) -> None:
        self.calls.append(("warning", text))

    def error(self, text: str) -> None:
        self.calls.append(("error", text))


def test_the_notice_set_is_closed_and_documented() -> None:
    assert {notice.value for notice in Notice} == {
        "info",
        "success",
        "warning",
        "error",
        "unavailable",
        "partial",
        "refused",
        "gated",
    }


def test_every_notice_maps_to_a_real_streamlit_primitive() -> None:
    for notice in Notice:
        assert streamlit_method(notice) in STREAMLIT_METHODS, (notice, streamlit_method(notice))


def test_every_notice_has_a_stylesheet_rule_in_the_static_export() -> None:
    """A state with no rule would render as an ordinary paragraph on the static site."""
    css = EXPORTER_SOURCE.read_text(encoding="utf-8")

    for notice in Notice:
        modifier = css_modifier(notice)
        if modifier == "warn":
            # Warnings use the stylesheet's default alert rule.
            assert ".alerts li {" in css
        else:
            assert f".alerts li.alert--{modifier}" in css, f"{notice.value} has no .alert--{modifier} rule"
        assert "--ok:" in css and "--error:" in css


def test_unavailable_is_never_rendered_as_a_success() -> None:
    assert streamlit_method(Notice.UNAVAILABLE) != "success"
    assert streamlit_method(Notice.UNAVAILABLE) in {"warning", "info"}


def test_partial_is_never_rendered_as_a_success() -> None:
    assert streamlit_method(Notice.PARTIAL) != "success"


def test_refused_stays_loud_and_gated_stays_neutral() -> None:
    assert streamlit_method(Notice.REFUSED) == "error"
    assert streamlit_method(Notice.GATED) == "info"


def test_notice_class_names_the_modifier() -> None:
    assert notice_class(Notice.UNAVAILABLE) == "alert alert--unavailable"
    assert notice_class(Notice.WARNING) == "alert alert--warn"


def test_render_notice_dispatches_through_the_mapping() -> None:
    streamlit = _FakeStreamlit()

    render_notice(streamlit, Notice.UNAVAILABLE, "no marks for XNAS:AAA")
    render_notice(streamlit, Notice.REFUSED, "the statement file was rejected")
    render_notice(streamlit, Notice.GATED, "private tracking requires loopback binding")

    assert streamlit.calls == [
        ("warning", "no marks for XNAS:AAA"),
        ("error", "the statement file was rejected"),
        ("info", "private tracking requires loopback binding"),
    ]


def test_render_notice_fails_loudly_when_the_primitive_is_missing() -> None:
    class Empty:
        pass

    with pytest.raises(RuntimeError, match="no 'info' method"):
        render_notice(Empty(), Notice.INFO, "text")
