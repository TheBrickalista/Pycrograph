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
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QListWidget,
    QListWidgetItem, QSpinBox, QVBoxLayout,
)

from ..consistency import Issue, summary
from ..export import FORMATS, ExportSettings


class ConsistencyDialog(QDialog):
    """The issues of a figure. Clicking one selects its panels on the
    canvas. mode "check": Close; mode "export": Export anyway / Cancel."""

    def __init__(self, issues: list[Issue], select: Callable[[list[str]], None],
                 mode: str = "check", parent=None):
        super().__init__(parent)
        self.issues = issues
        self.setWindowTitle("Consistency check" if mode == "check" else "Export — consistency check")
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        head = QLabel(f"{summary(issues)}." + (" Click an item to select its panels." if issues else ""))
        head.setWordWrap(True)
        lay.addWidget(head)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        for i in issues:
            it = QListWidgetItem(i.text())
            it.setData(Qt.UserRole, list(i.panel_ids))
            it.setToolTip(f"{i.severity} · {i.code}")
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
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class ExportDialog(QDialog):
    def __init__(self, default_dpi: int, n_pages: int, parent=None,
                 initial: Optional[ExportSettings] = None):
        super().__init__(parent)
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
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.format.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        raster = self.format.currentData() in ("tiff", "png")
        self.dpi.setEnabled(raster)
        self.resampling.setEnabled(raster)
        self.info_page.setEnabled(self.format.currentData() == "pdf")

    def settings(self) -> ExportSettings:
        fmt = self.format.currentData()
        return ExportSettings(format=fmt, dpi=self.dpi.value() if fmt in ("tiff", "png") else None,
                              resampling=self.resampling.currentData(),
                              info_page=self.info_page.isChecked() and fmt == "pdf",
                              verify_hashes=self.verify.isChecked())
