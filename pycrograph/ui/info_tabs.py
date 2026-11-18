# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Preferences and About tabs (read-only in design step 1; editable journal
presets come with page setup in step 3)."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

from .. import __version__
from ..models import PagePreset


class PreferencesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        title = QLabel("Preferences")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)
        sub = QLabel("Default page preset for new figures")
        sub.setStyleSheet("font-weight: 600;")
        lay.addWidget(sub)
        form = QFormLayout()
        p = PagePreset()
        for label, value in (("Preset", p.name), ("Width", f"{p.width_mm:g} mm"),
                             ("Maximum height", f"{p.max_height_mm:g} mm"),
                             ("Resolution", f"{p.dpi} dpi"),
                             ("Font", f"{p.font_family} {p.font_size_pt:g} pt"),
                             ("Minimum line width", f"{p.min_line_pt:g} pt")):
            form.addRow(label, QLabel(value))
        lay.addLayout(form)
        note = QLabel("Editable journal presets, default fonts and label defaults come with "
                      "page setup (figure editor, step 3).")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280;")
        lay.addWidget(note)
        lay.addStretch(1)


class AboutTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        self.text = QLabel(about_html())
        self.text.setTextFormat(Qt.RichText)
        self.text.setWordWrap(True)
        self.text.setOpenExternalLinks(True)
        self.text.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lay.addWidget(self.text)
        lay.addStretch(1)


def about_html() -> str:
    cite = (f"Boulter E. Pycrograph (version {__version__}) [Computer software]. "
            "https://github.com/TheBrickalista/Pycrograph")
    return (f"<h2>Pycrograph {html.escape(__version__)}</h2>"
            "<p>Publication figures from fluorescence microscopy images — non-destructive, "
            "provenance-tracked. Sister app of PysternBlot.</p>"
            "<p>Etienne Boulter (IRCAN, Inserm/CNRS/Université Côte d'Azur)</p>"
            "<p><b>Licence</b>: GNU General Public License v3.0 only (GPL-3.0-only).</p>"
            f"<p><b>Citation</b>: {html.escape(cite)}<br>"
            "<small>See CITATION.cff in the repository.</small></p>")
