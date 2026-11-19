# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Export-archive options: level and optional contents (archive.py)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout,
)


class ArchiveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export archive")
        lay = QVBoxLayout(self)
        self.light = QRadioButton("Light — only what the figures use")
        self.light.setChecked(True)
        lay.addWidget(self.light)
        note = QLabel("Per image, the planes and region the panels show (plus a margin) as "
                      "uncompressed OME-TIFF with calibration, original metadata and the source "
                      "file's SHA-256. Small; enough to reopen and re-export the figures.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280; margin-left: 22px;")
        lay.addWidget(note)
        self.complete = QRadioButton("Complete — every original raw file, byte-exact")
        lay.addWidget(self.complete)
        note = QLabel("Source data for journals; can be several GB (each file is SHA-256 verified "
                      "after copy).")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280; margin-left: 22px;")
        lay.addWidget(note)
        self.include_exports = QCheckBox("Include the exported figures and their provenance files")
        self.include_report = QCheckBox("Include an integrity report (JSON + HTML)")
        self.include_report.setChecked(True)
        lay.addWidget(self.include_exports)
        lay.addWidget(self.include_report)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def level(self) -> str:
        return "complete" if self.complete.isChecked() else "light"
