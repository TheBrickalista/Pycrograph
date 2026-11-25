# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Preferences tab (journal page presets: the read-only catalog plus the
user's own, editable; default preset for new figures, chosen by Publisher ▾
then Size ▾) and About tab (Legal / Copyright & Credits / Repository, as in
PysternBlot)."""

from __future__ import annotations

import html
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QFontInfo, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..credits import credits_text, repository_text
from ..journal_presets import journal_info
from ..models import PagePreset
from ..preferences import DEFAULT_PRESET, Preferences, is_builtin
from ..selfcheck import CITATION, LICENSE_TXT, NOTICES, resource_path
from .preset_picker import PresetPicker

# (field, header, type): editable for the user's presets
PRESET_COLUMNS = (("name", "Name", str), ("width_mm", "Width (mm)", float),
                  ("max_height_mm", "Max height (mm)", float), ("dpi", "dpi", int),
                  ("font_family", "Font", str), ("font_size_pt", "Font size (pt)", float),
                  ("min_line_pt", "Min line (pt)", float))
INFO_COLUMNS = ("Publisher", "Status")          # read-only, from the catalog
BUILTIN_COLOUR = "#6b7280"


class PreferencesTab(QWidget):
    """Edits Preferences in place and calls `save` after every valid change
    (preferences are app settings, not project data: not logged). Built-in
    presets come from the journal catalog and are read-only here: Duplicate
    one to adapt it."""

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
        sub = QLabel("Journal page presets (used by Page setup)")
        sub.setStyleSheet("font-weight: 600;")
        lay.addWidget(sub)
        expl = QLabel("Grey rows are the built-in journal catalog (read-only; values from each "
                      "journal's author guidelines — verify with the journal). Duplicate one or "
                      "Add a preset to make your own.")
        expl.setWordWrap(True)
        expl.setStyleSheet("color: #6b7280;")
        lay.addWidget(expl)
        self.table = QTableWidget(0, len(PRESET_COLUMNS) + len(INFO_COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in PRESET_COLUMNS] + list(INFO_COLUMNS))
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
        self.add_btn.clicked.connect(self.add_preset)
        self.dup_btn.clicked.connect(self.duplicate_preset)
        self.del_btn.clicked.connect(self.delete_preset)
        for b in (self.add_btn, self.dup_btn, self.del_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Default preset for new figures"))
        self.default_picker = PresetPicker(self.prefs)
        self.default_picker.chosen.connect(self._on_default)
        drow.addWidget(self.default_picker)
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
        grey = QBrush(QColor(BUILTIN_COLOUR))
        for preset in self.prefs.presets:
            r = self.table.rowCount()
            self.table.insertRow(r)
            info = journal_info(preset.name)
            builtin = is_builtin(preset.name)
            cells = [f"{v:g}" if isinstance(v, float) else str(v)
                     for v in (getattr(preset, f) for f, _h, _t in PRESET_COLUMNS)]
            cells += [info.publisher, info.badge] if info else ["", "User"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if builtin or c >= len(PRESET_COLUMNS):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if builtin:
                    item.setForeground(grey)
                    if info:
                        item.setToolTip(info.source)
                self.table.setItem(r, c, item)
        self.default_picker.rebuild(self.prefs)
        self.default_picker.set_current(self.prefs.default().name)
        self._loading = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        r, c = item.row(), item.column()
        if c >= len(PRESET_COLUMNS) or is_builtin(self.prefs.presets[r].name):
            self.reload()
            return
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
        if field == "name" and is_builtin(updated.name):
            self.message.setText(f"“{updated.name}” is a built-in preset name")
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
        """A user copy of the selected preset (built-in or not), at the end."""
        r = self._selected_row()
        if r >= 0:
            src = self.prefs.presets[r]
            self.prefs.presets.append(src.model_copy(update={"name": self._unique(src.name)}))
            self._commit()

    def delete_preset(self) -> None:
        r = self._selected_row()
        if r < 0:
            return
        if is_builtin(self.prefs.presets[r].name):
            self.message.setText("Built-in presets cannot be deleted")
            return
        removed = self.prefs.presets.pop(r)
        if self.prefs.default_preset == removed.name:
            self.prefs.default_preset = DEFAULT_PRESET
        self.message.setText("")
        self._commit()

    def _on_default(self, name: str) -> None:
        if self.prefs.preset(name) is None:
            return
        self.prefs.default_preset = name
        self._commit()

    def _commit(self) -> None:
        self._save()
        self.reload()


def fixed_font() -> QFont:
    """The system's fixed-pitch font, or a known monospace family when the
    platform answers with a proportional one."""
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    if QFontInfo(f).fixedPitch():
        return f
    for family in ("Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"):
        g = QFont(family, f.pointSize())
        g.setStyleHint(QFont.TypeWriter)
        if QFontInfo(g).fixedPitch():
            return g
    f.setStyleHint(QFont.TypeWriter)
    return f


def _resource_text(name: str) -> Optional[str]:
    try:
        return resource_path(name).read_text(encoding="utf-8")
    except OSError:
        return None


class AboutTab(QWidget):
    """As PysternBlot's About tab: a title, then Legal / Copyright & Credits
    / Repository buttons over a read-only text view (Legal first)."""

    LICENSE_MISSING = ("License file not found.\n"
                       "Pycrograph is licensed under the GNU General Public License v3.0 only:\n"
                       "https://www.gnu.org/licenses/gpl-3.0.html")

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        self.title = QLabel(f"<h3>About Pycrograph {html.escape(__version__)}</h3>")
        self.title.setTextFormat(Qt.RichText)
        lay.addWidget(self.title)

        row = QHBoxLayout()
        self.buttons = QButtonGroup(self)
        self.legal_btn = QPushButton("Legal")
        self.credits_btn = QPushButton("Copyright && Credits")        # "&&": a literal "&"
        self.repo_btn = QPushButton("Repository")
        for b, slot in ((self.legal_btn, self.show_legal), (self.credits_btn, self.show_credits),
                        (self.repo_btn, self.show_repository)):
            b.setCheckable(True)
            b.clicked.connect(slot)
            self.buttons.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(fixed_font())                 # licence texts are laid out in columns
        lay.addWidget(self.view, 1)
        self.show_legal()

    def _show(self, button: QPushButton, text: str) -> None:
        button.setChecked(True)
        self.view.setPlainText(text)
        self.view.moveCursor(QTextCursor.Start)

    def show_legal(self) -> None:
        self._show(self.legal_btn, _resource_text(LICENSE_TXT) or self.LICENSE_MISSING)

    def show_credits(self) -> None:
        self._show(self.credits_btn, credits_text(_resource_text(NOTICES)))

    def show_repository(self) -> None:
        self._show(self.repo_btn, repository_text(__version__, _resource_text(CITATION)))
