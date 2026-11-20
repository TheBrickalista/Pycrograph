# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""scripts/make_lock_screenshots.py still runs against the current UI."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "make_lock_screenshots.py"
NAMES = ["split", "confirm", "depth", "mismatch", "check", "grid"]


def load():
    spec = importlib.util.spec_from_file_location("make_lock_screenshots", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lock_script_window_uses_english_numbers(tmp_path):
    from PySide6.QtCore import QLocale
    mod = load()
    machine = QLocale()
    QLocale.setDefault(QLocale(QLocale.French, QLocale.France))   # a comma locale
    try:
        previous = mod.base.english_locale()
        app, w, _ids = mod.make_window(tmp_path)
        try:
            text = w.figures_tab.gap_spin.text()
            assert "." in text and "," not in text, text
        finally:
            w.close()
            QLocale.setDefault(previous)
    finally:
        QLocale.setDefault(machine)


def test_lock_screenshots_are_generated(tmp_path):
    from PySide6.QtGui import QImage
    assert load().main(["--out", str(tmp_path)]) == 0
    # Sizes depend on platform fonts (a dialog is 400×99 on Linux): only sanity.
    for name in NAMES:
        img = QImage(str(tmp_path / f"locks-{name}.png"))
        assert not img.isNull() and img.width() >= 50 and img.height() >= 50, name
    guide = (ROOT / "docs" / "guide" / "locks.md").read_text()
    for name in NAMES:
        assert f"_static/img/locks-{name}.png" in guide
        assert (ROOT / "docs" / "_static" / "img" / f"locks-{name}.png").is_file()
