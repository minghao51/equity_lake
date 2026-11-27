"""Helpers for asserting on rendered Typer/Rich CLI output.

Typer renders help through Rich. When Rich believes its output is a terminal it
styles a single option name across several spans, so the rendered text contains
``ESC[1;36m-ESC[0mESC[1;36m-lake-ESC[0m`` where a plain ``--lake`` is expected and
the literal substring never occurs. Typer forces that terminal mode whenever
``GITHUB_ACTIONS``, ``FORCE_COLOR``, or ``PY_COLORS`` is set, so a
``"--flag" in result.stdout`` assertion passes locally and fails on CI. Strip the
escape sequences before asserting on rendered content.
"""

from __future__ import annotations

import re

from click.testing import Result

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI escape sequences from rendered terminal output."""
    return ANSI_RE.sub("", text)


def help_text(result: Result) -> str:
    """Return the ANSI-stripped standard output of a CLI invocation."""
    return strip_ansi(result.stdout)
