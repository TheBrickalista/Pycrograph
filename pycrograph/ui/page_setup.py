# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Page setup dialog: journal preset (Publisher ▾ then Size ▾, from the
catalog and the user's presets, or Custom), what that preset says (sizes,
dpi, font, line, journals, source, status), page size, orientation, number
of pages, background, grid and snap. Returns a new PageSetup; the Figures
tab applies it as one logged gesture.

The figure keeps its preset snapshot until a size is picked: opening and
accepting the dialog never replaces it with the catalog's values.
*Custom page (free size)* stores a preset named "Custom" whose width and
maximum height are the page size entered here, as laid out.

Picking a size sets the page to it, orientation included: a preset wider
than tall (a slide) opens in landscape (models.PageSetup.preset_layout), and
the orientation toggle then turns the same paper."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from ..journal_presets import journal_info
from ..models import PageSetup
from ..preferences import Preferences
from .preset_picker import PresetPicker, preset_info_html

CUSTOM_KEY = "__custom__"            # "Custom page (free size)"
FIGURE_KEY = "__figure__"            # the figure's own preset when it is in no list
CUSTOM_NAME = "Custom"


class PageSetupDialog(QDialog):
    def __init__(self, page: PageSetup, prefs: Preferences, min_pages: int = 1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Page setup")
        self._prefs = prefs
        self._figure_page = page.model_copy(deep=True)
        self._figure_preset = page.preset.model_copy(deep=True)
        self._preset = page.preset.model_copy(deep=True)
        self._key = page.preset.name
        self._background = page.background
        lay = QVBoxLayout(self)
        form = QFormLayout()

        extra = [("Custom page (free size)", CUSTOM_KEY)]
        own = page.preset.name
        if own != CUSTOM_NAME and prefs.preset(own) is None:
            extra.insert(0, (f"{own} (this figure)", FIGURE_KEY))    # e.g. a deleted user preset
        self.picker = PresetPicker(prefs, extra)
        if own == CUSTOM_NAME:
            self._key = CUSTOM_KEY
        elif prefs.preset(own) is None:
            self._key = FIGURE_KEY
        self.picker.set_current(self._key)
        self.picker.chosen.connect(self._on_preset)
        form.addRow("Journal preset", self.picker)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setOpenExternalLinks(True)            # the source page, in the user's browser
        self.info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info.setMinimumWidth(420)
        form.addRow("", self.info)

        def mm_spin(v: float) -> QDoubleSpinBox:
            sp = QDoubleSpinBox()
            sp.setRange(10.0, 2000.0)
            sp.setDecimals(1)
            sp.setSuffix(" mm")
            sp.setValue(v)
            return sp
        self.width_spin = mm_spin(page.width_mm)
        self.height_spin = mm_spin(page.height_mm)
        self._exact = {self.width_spin: page.width_mm, self.height_spin: page.height_mm}
        form.addRow("Width (portrait)", self.width_spin)
        form.addRow("Height (portrait)", self.height_spin)
        for sp in (self.width_spin, self.height_spin):      # Custom: the preset is the page
            sp.valueChanged.connect(lambda _v: self._key == CUSTOM_KEY and self._show_info())
        self.orientation = QComboBox()
        self.orientation.addItems(["portrait", "landscape"])
        self.orientation.setCurrentText(page.orientation)
        form.addRow("Orientation", self.orientation)
        self.orientation.currentTextChanged.connect(
            lambda _t: self._key == CUSTOM_KEY and self._show_info())
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
        self._show_info()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _on_preset(self, key: str) -> None:
        self._key = key
        if key == CUSTOM_KEY:
            self._preset = self._preset.model_copy(update={"name": CUSTOM_NAME})
        elif key == FIGURE_KEY:
            self._preset = self._figure_preset.model_copy(deep=True)
            page = self._figure_page
            self._set_size(page.width_mm, page.height_mm, page.orientation)
        else:
            preset = self._prefs.preset(key)
            if preset is None:
                return
            self._preset = preset.model_copy(deep=True)
            self._set_size(*PageSetup.preset_layout(preset))
        self._show_info()

    def _set_size(self, width_mm: float, height_mm: float, orientation: str) -> None:
        """Portrait width / height and orientation, as PageSetup stores them."""
        self.width_spin.setValue(width_mm)
        self.height_spin.setValue(height_mm)
        self._exact = {self.width_spin: width_mm, self.height_spin: height_mm}
        self.orientation.setCurrentText(orientation)

    def _mm(self, spin: QDoubleSpinBox) -> float:
        """The spin's value, or the exact size it was set to (142.875 mm
        shows as 142.9) while the user has not changed it."""
        exact = self._exact.get(spin)
        if exact is not None and abs(spin.value() - round(exact, spin.decimals())) < 1e-9:
            return exact
        return spin.value()

    def portrait_mm(self) -> tuple[float, float]:
        return self._mm(self.width_spin), self._mm(self.height_spin)

    def laid_out_mm(self) -> tuple[float, float]:
        w, h = self.portrait_mm()
        return (h, w) if self.orientation.currentText() == "landscape" else (w, h)

    def _show_info(self) -> None:
        self.info.setText(preset_info_html(self.current_preset(), journal_info(self._preset.name)))

    def current_preset(self):
        """The preset the figure will store (Custom: sized from the page)."""
        if self._key == CUSTOM_KEY:
            w, h = self.laid_out_mm()
            return self._preset.model_copy(update={"name": CUSTOM_NAME,
                                                   "width_mm": w, "max_height_mm": h})
        return self._preset

    def _pick_background(self) -> None:
        c = QColorDialog.getColor(QColor(self._background), self, "Page background")
        if c.isValid():
            self._background = c.name().upper()
            self._show_background()

    def _show_background(self) -> None:
        self.bg_button.setText(self._background)
        self.bg_button.setStyleSheet(f"background: {self._background};")

    def result_page(self) -> PageSetup:
        w, h = self.portrait_mm()
        return PageSetup(preset=self.current_preset(), width_mm=w, height_mm=h,
                         orientation=self.orientation.currentText(),
                         n_pages=self.pages_spin.value(), background=self._background,
                         grid_mm=self.grid_spin.value(), snap=self.snap_cb.isChecked())
