# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Page setup dialog: journal preset (from Preferences), page size,
orientation, number of pages, background, grid and snap. Returns a new
PageSetup; the Figures tab applies it as one logged gesture."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QPushButton, QSpinBox, QVBoxLayout,
)

from ..models import PageSetup
from ..preferences import Preferences


class PageSetupDialog(QDialog):
    def __init__(self, page: PageSetup, prefs: Preferences, min_pages: int = 1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Page setup")
        self._prefs = prefs
        self._preset = page.preset.model_copy(deep=True)
        self._background = page.background
        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.preset_combo = QComboBox()
        names = [p.name for p in prefs.presets]
        if self._preset.name not in names:
            names.insert(0, self._preset.name)          # the figure's own (e.g. a deleted preset)
        self.preset_combo.addItems(names)
        self.preset_combo.setCurrentText(self._preset.name)
        self.preset_combo.activated.connect(self._on_preset)
        form.addRow("Journal preset", self.preset_combo)

        def mm_spin(v: float) -> QDoubleSpinBox:
            sp = QDoubleSpinBox()
            sp.setRange(10.0, 2000.0)
            sp.setDecimals(1)
            sp.setSuffix(" mm")
            sp.setValue(v)
            return sp
        self.width_spin = mm_spin(page.width_mm)
        self.height_spin = mm_spin(page.height_mm)
        form.addRow("Width (portrait)", self.width_spin)
        form.addRow("Height (portrait)", self.height_spin)
        self.orientation = QComboBox()
        self.orientation.addItems(["portrait", "landscape"])
        self.orientation.setCurrentText(page.orientation)
        form.addRow("Orientation", self.orientation)
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(max(1, min_pages), 50)
        self.pages_spin.setValue(max(page.n_pages, min_pages))
        if min_pages > 1:
            self.pages_spin.setToolTip(f"Page {min_pages} holds panels")
        form.addRow("Pages", self.pages_spin)
        self.bg_button = QPushButton()
        self.bg_button.clicked.connect(self._pick_background)
        self._show_background()
        form.addRow("Background", self.bg_button)
        self.grid_spin = QDoubleSpinBox()
        self.grid_spin.setRange(0.1, 50.0)
        self.grid_spin.setDecimals(1)
        self.grid_spin.setSuffix(" mm")
        self.grid_spin.setValue(page.grid_mm)
        form.addRow("Grid", self.grid_spin)
        self.snap_cb = QCheckBox("Snap to grid while dragging")
        self.snap_cb.setChecked(page.snap)
        form.addRow("", self.snap_cb)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _on_preset(self, _i: int) -> None:
        preset = self._prefs.preset(self.preset_combo.currentText())
        if preset is None:
            return
        self._preset = preset.model_copy(deep=True)
        self.width_spin.setValue(preset.width_mm)
        self.height_spin.setValue(preset.max_height_mm)

    def _pick_background(self) -> None:
        c = QColorDialog.getColor(QColor(self._background), self, "Page background")
        if c.isValid():
            self._background = c.name().upper()
            self._show_background()

    def _show_background(self) -> None:
        self.bg_button.setText(self._background)
        self.bg_button.setStyleSheet(f"background: {self._background};")

    def result_page(self) -> PageSetup:
        return PageSetup(preset=self._preset, width_mm=self.width_spin.value(),
                         height_mm=self.height_spin.value(),
                         orientation=self.orientation.currentText(),
                         n_pages=self.pages_spin.value(), background=self._background,
                         grid_mm=self.grid_spin.value(), snap=self.snap_cb.isChecked())
