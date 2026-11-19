# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""scripts/make_inset_screenshots.py still runs against the current UI."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_inset_screenshots.py"
NAMES = ["create", "dialog-split", "linked", "dialog-wl", "dialog-cells"]


def load():
    spec = importlib.util.spec_from_file_location("make_inset_screenshots", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_window_uses_english_numbers(tmp_path):
    """Screenshots never show "2,0 mm": the script forces QLocale(English,
    UnitedStates) before building widgets, whatever the machine's locale."""
    from PySide6.QtCore import QLocale
    mod = load()
    machine = QLocale()
    QLocale.setDefault(QLocale(QLocale.French, QLocale.France))   # a comma locale
    try:
        previous = mod.english_locale()
        app, w, _ids = mod.make_window(tmp_path)
        try:
            text = w.figures_tab.gap_spin.text()
            assert "." in text and "," not in text, text
        finally:
            w.close()
            QLocale.setDefault(previous)
        assert QLocale().language() == QLocale.French                 # restored
    finally:
        QLocale.setDefault(machine)


def test_screenshots_are_generated(tmp_path):
    from PySide6.QtGui import QImage
    mod = load()
    assert mod.main(["--out", str(tmp_path)]) == 0
    for name in NAMES:
        img = QImage(str(tmp_path / f"insets-{name}.png"))
        assert not img.isNull() and img.width() > 400 and img.height() > 200, name
    # Every screenshot the guide references exists in the repo.
    guide = (SCRIPT.parent.parent / "docs" / "guide" / "insets.md").read_text()
    for name in NAMES:
        assert f"_static/img/insets-{name}.png" in guide
        assert (SCRIPT.parent.parent / "docs" / "_static" / "img" / f"insets-{name}.png").is_file()
