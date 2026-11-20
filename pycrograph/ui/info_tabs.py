# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Preferences tab (journal page presets as editable data, default preset
for new figures) and About tab."""

from __future__ import annotations

import html
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..credits import CITATION, copyright_html, to_entities
from ..models import PagePreset
from ..preferences import Preferences, builtin_presets

# (field, header, type)
PRESET_COLUMNS = (("name", "Name", str), ("width_mm", "Width (mm)", float),
                  ("max_height_mm", "Max height (mm)", float), ("dpi", "dpi", int),
                  ("font_family", "Font", str), ("font_size_pt", "Font size (pt)", float),
                  ("min_line_pt", "Min line (pt)", float))


class PreferencesTab(QWidget):
    """Edits Preferences in place and calls `save` after every valid change
    (preferences are app settings, not project data: not logged)."""

    def __init__(self, prefs: Optional[Preferences] = None,
                 save: Optional[Callable[[], None]] = None, parent=None):
        super().__init__(parent)
        self.prefs = prefs or Preferences()
        self._save = save or (lambda: None)
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        title = QLabel("Preferences")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)
        sub = QLabel("Journal page presets (used by Page setup; values from each journal's "
                     "author guidelines)")
        sub.setStyleSheet("font-weight: 600;")
        lay.addWidget(sub)
        self.table = QTableWidget(0, len(PRESET_COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in PRESET_COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.dup_btn = QPushButton("Duplicate")
        self.del_btn = QPushButton("Delete")
        self.restore_btn = QPushButton("Restore built-in presets")
        self.add_btn.clicked.connect(self.add_preset)
        self.dup_btn.clicked.connect(self.duplicate_preset)
        self.del_btn.clicked.connect(self.delete_preset)
        self.restore_btn.clicked.connect(self.restore_builtins)
        for b in (self.add_btn, self.dup_btn, self.del_btn, self.restore_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Default preset for new figures"))
        self.default_combo = QComboBox()
        self.default_combo.activated.connect(self._on_default)
        drow.addWidget(self.default_combo)
        drow.addStretch(1)
        lay.addLayout(drow)
        self.message = QLabel("")
        self.message.setStyleSheet("color: #b45309;")
        lay.addWidget(self.message)
        note = QLabel("Changing a preset does not change existing figures: apply it with "
                      "Figures › Page setup….")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280;")
        lay.addWidget(note)
        self.reload()

    def reload(self) -> None:
        self._loading = True
        self.table.setRowCount(0)
        for preset in self.prefs.presets:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, (field, _h, _t) in enumerate(PRESET_COLUMNS):
                v = getattr(preset, field)
                self.table.setItem(r, c, QTableWidgetItem(f"{v:g}" if isinstance(v, float) else str(v)))
        self.default_combo.clear()
        self.default_combo.addItems([p.name for p in self.prefs.presets])
        self.default_combo.setCurrentText(self.prefs.default().name)
        self._loading = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        r, c = item.row(), item.column()
        field, header, typ = PRESET_COLUMNS[c]
        preset = self.prefs.presets[r]
        old_name = preset.name
        try:
            value = typ(item.text().strip().replace(",", "."))
            updated = PagePreset.model_validate({**preset.model_dump(), field: value})
        except (ValueError, TypeError) as exc:
            self.message.setText(f"{header}: invalid value ({str(exc).splitlines()[0]})")
            self.reload()
            return
        self.prefs.presets[r] = updated
        errors = self.prefs.validate_presets()
        if errors:
            self.prefs.presets[r] = preset
            self.message.setText("; ".join(errors))
            self.reload()
            return
        if field == "name" and self.prefs.default_preset == old_name:
            self.prefs.default_preset = updated.name
        self.message.setText("")
        self._commit()

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _unique(self, base: str) -> str:
        names = {p.name for p in self.prefs.presets}
        name, n = base, 2
        while name in names:
            name = f"{base} ({n})"
            n += 1
        return name

    def add_preset(self) -> None:
        self.prefs.presets.append(PagePreset(name=self._unique("New preset")))
        self._commit()

    def duplicate_preset(self) -> None:
        r = self._selected_row()
        if r >= 0:
            src = self.prefs.presets[r]
            self.prefs.presets.insert(r + 1, src.model_copy(update={"name": self._unique(src.name)}))
            self._commit()

    def delete_preset(self) -> None:
        r = self._selected_row()
        if r >= 0 and len(self.prefs.presets) > 1:
            removed = self.prefs.presets.pop(r)
            if self.prefs.default_preset == removed.name:
                self.prefs.default_preset = self.prefs.presets[0].name
            self._commit()

    def restore_builtins(self) -> None:
        names = {p.name for p in self.prefs.presets}
        for p in builtin_presets():
            if p.name in names:
                i = next(i for i, q in enumerate(self.prefs.presets) if q.name == p.name)
                self.prefs.presets[i] = p
            else:
                self.prefs.presets.append(p)
        self._commit()

    def _on_default(self, _i: int) -> None:
        self.prefs.default_preset = self.default_combo.currentText()
        self._commit()

    def _commit(self) -> None:
        self._save()
        self.reload()


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
    cite = (f"{CITATION}. Pycrograph (version {__version__}) [Computer software]. "
            "https://github.com/TheBrickalista/Pycrograph")
    return (f"<h2>Pycrograph {html.escape(__version__)}</h2>"
            "<p>Publication figures from fluorescence microscopy images — non-destructive, "
            "provenance-tracked. Sister app of PysternBlot.</p>"
            f"<p>{copyright_html()}</p>"
            "<p><b>Licence</b>: GNU General Public License v3.0 only (GPL-3.0-only).</p>"
            f"<p><b>Citation</b>: {to_entities(cite)}<br>"
            "<small>See CITATION.cff in the repository.</small></p>")
