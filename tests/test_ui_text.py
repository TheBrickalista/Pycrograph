# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Singular / plural wording (ui_text) and a guard against "panel(s)"."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from pycrograph import consistency as C
from pycrograph.ui_text import noun, plural

PKG = Path(__file__).resolve().parent.parent / "pycrograph"


@pytest.mark.parametrize("n,word,form,expected", [
    (0, "panel", None, "0 panels"), (1, "panel", None, "1 panel"), (2, "panel", None, "2 panels"),
    (1, "other panel", None, "1 other panel"), (3, "copy", "copies", "3 copies"),
    (1, "copy", "copies", "1 copy")])
def test_plural(n, word, form, expected):
    assert plural(n, word, form) == expected


def test_noun():
    assert (noun(1, "channel"), noun(2, "channel"), noun(0, "value")) == ("channel", "channels", "values")


def test_consistency_summary_wording():
    issue = lambda sev: C.Issue(sev, "x", "m", ())          # noqa: E731
    assert C.summary([issue("error"), issue("warning"), issue("warning")]) == "1 error, 2 warnings"
    assert C.summary([issue("info")]) == "1 info"
    assert C.summary([issue("info"), issue("info")]) == "2 info"         # not "infos"
    assert C.summary([]) == "no issue"


def _string_literals(tree: ast.AST):
    """String constants that are not docstrings (f-string parts included)."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node


def test_no_parenthesised_plurals_in_messages():
    """No "panel(s)" / "inset(s)" / "warning(s)"… in any message: use
    ui_text.plural / noun. (Docstrings and comments may still say (s).)"""
    pat = re.compile(r"[A-Za-z]\(s\)")
    found = []
    for path in sorted(PKG.rglob("*.py")):
        for node in _string_literals(ast.parse(path.read_text(), str(path))):
            if pat.search(node.value):
                found.append(f"{path.relative_to(PKG.parent)}:{node.lineno}: {node.value!r}")
    assert not found, "\n".join(found)
