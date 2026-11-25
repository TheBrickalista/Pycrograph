# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Consistency-check and export dialogs (design §2.9, §4)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QListWidget,
    QListWidgetItem, QSpinBox, QVBoxLayout,
)

from ..consistency import (
    LEVEL_ICON, LEVEL_LABEL, SMOOTH_INSET_WARNING, Issue, critical_needing_consent, summary,
)
from ..export import FORMATS, ExportSettings
from .checks_panel import LEVEL_COLOURS, counts_html


class ConsistencyDialog(QDialog):
    """The issues of a figure before export. Clicking one selects its
    panels on the canvas. mode "export": Export anyway / Cancel; with
    critical issues (other than missing / modified sources, which refuse
    the export by themselves) "Export anyway" stays disabled until the user
    ticks that they export despite them. mode "check": Close (the Checks
    panel is the usual view)."""

    def __init__(self, issues: list[Issue], select: Callable[[list[str]], None],
                 mode: str = "check", parent=None):
        super().__init__(parent)
        self.issues = issues
        self.setWindowTitle("Consistency check" if mode == "check" else "Export — consistency check")
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        head = QLabel(counts_html(issues) + (" — click an item to select its panels."
                                             if issues else ""))
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        head.setAccessibleName(summary(issues))
        lay.addWidget(head)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        for i in issues:
            it = QListWidgetItem(i.text())
            it.setData(Qt.UserRole, list(i.panel_ids))
            it.setToolTip(f"{LEVEL_LABEL[i.level]} · {i.code} ({i.rule})")
            if i.level == "critical":
                bg, fg = LEVEL_COLOURS["critical"]
                it.setBackground(QColor(bg))
                it.setForeground(QColor(fg))
            self.list.addItem(it)
        self.list.itemClicked.connect(lambda it: select(it.data(Qt.UserRole)))
        lay.addWidget(self.list, 1)
        if mode == "export":
            note = QLabel("Exporting anyway records these warnings in the operation log and in the "
                          "export's provenance file.")
            note.setWordWrap(True)
            note.setStyleSheet("color: #6b7280;")
            lay.addWidget(note)
            buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
            self.export_btn = buttons.addButton("Export anyway", QDialogButtonBox.AcceptRole)
            critical = critical_needing_consent(issues)
            self.consent: Optional[QCheckBox] = None
            if critical:
                self.consent = QCheckBox(
                    f"{LEVEL_ICON['critical']} Export anyway despite "
                    f"{len(critical)} critical issue{'s' if len(critical) > 1 else ''} "
                    "(the figure may be wrong or cut off)")
                self.consent.toggled.connect(self.export_btn.setEnabled)
                self.export_btn.setEnabled(False)
                lay.addWidget(self.consent)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class ExportDialog(QDialog):
    """Format, dpi, resampling and options. `low_res_insets` = how many
    insets are below the page dpi: with a raster format and smooth
    resampling the dialog says that their enlarged pixels get interpolated
    (consistency.SMOOTH_INSET_WARNING; not blocking)."""

    def __init__(self, default_dpi: int, n_pages: int, parent=None,
                 initial: Optional[ExportSettings] = None, low_res_insets: int = 0):
        super().__init__(parent)
        self._low_res_insets = low_res_insets
        self.setWindowTitle("Export figure")
        s = initial or ExportSettings()
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.format = QComboBox()
        for f, text in (("pdf", "PDF (vector, text as text, lossless images)"),
                        ("svg", "SVG (vector, text as text)"),
                        ("tiff", "TIFF (raster, RGB 8-bit)"), ("png", "PNG (raster, RGB 8-bit)")):
            self.format.addItem(text, f)
        self.format.setCurrentIndex(FORMATS.index(s.format))
        form.addRow("Format", self.format)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 2400)
        self.dpi.setSuffix(" dpi")
        self.dpi.setValue(s.dpi or default_dpi)
        form.addRow("Resolution", self.dpi)
        self.resampling = QComboBox()
        self.resampling.addItem("Nearest neighbour (pixels kept sharp)", "nearest")
        self.resampling.addItem("Smooth (bilinear)", "smooth")
        self.resampling.setCurrentIndex(0 if s.resampling == "nearest" else 1)
        form.addRow("Resampling", self.resampling)
        self.info_page = QCheckBox("Append a provenance info page")
        self.info_page.setChecked(s.info_page)
        form.addRow("", self.info_page)
        self.verify = QCheckBox("Re-verify every source file (SHA-256) before exporting")
        self.verify.setChecked(s.verify_hashes)
        form.addRow("", self.verify)
        lay.addLayout(form)
        self.pages_note = QLabel(f"{n_pages} pages: one PDF, or one file per page for the other "
                                 "formats." if n_pages > 1 else "")
        self.pages_note.setStyleSheet("color: #6b7280;")
        lay.addWidget(self.pages_note)
        self.inset_note = QLabel("")
        self.inset_note.setWordWrap(True)
        self.inset_note.setStyleSheet("color: #b45309;")
        lay.addWidget(self.inset_note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.format.currentIndexChanged.connect(self._update)
        self.resampling.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        raster = self.format.currentData() in ("tiff", "png")
        self.dpi.setEnabled(raster)
        self.resampling.setEnabled(raster)
        self.info_page.setEnabled(self.format.currentData() == "pdf")
        warn = raster and self.resampling.currentData() == "smooth" and self._low_res_insets > 0
        n = self._low_res_insets
        self.inset_note.setText(
            f"{LEVEL_ICON['minor']} {n} inset{'s' if n > 1 else ''} below the page dpi: "
            f"{SMOOTH_INSET_WARNING}." if warn else "")
        self.inset_note.setVisible(warn)

    def settings(self) -> ExportSettings:
        fmt = self.format.currentData()
        return ExportSettings(format=fmt, dpi=self.dpi.value() if fmt in ("tiff", "png") else None,
                              resampling=self.resampling.currentData(),
                              info_page=self.info_page.isChecked() and fmt == "pdf",
                              verify_hashes=self.verify.isChecked())
