# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Replicate-inset dialog: targets with their detected compatibility, the
mode each will get, "Keep linked" (insets.replicate_inset)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .. import insets as I
from ..models import ImageRef, Panel

COMPAT_TEXT = {"same_image": "same image", "same_geometry": "same size and pixel size",
               "different": "different geometry"}
STAGE_TEXT = {"equal": "same stage position", "differ": "stage position differs",
              "unknown": "stage position unknown"}
REGISTRATION_WARNING = "Same region assumes the images are registered (same field)."


def effective_mode(requested: str, compat: str) -> str:
    return "size" if requested == "region" and compat == "different" else requested


def mode_text(requested: str, compat: str) -> str:
    m = effective_mode(requested, compat)
    if m != requested:
        return "same size (fallback: different geometry)"
    return "same region" if m == "region" else "same size"


class ReplicateDialog(QDialog):
    """`target_sets`: {label: [(panel, ref, display name)]}, e.g. the
    selected panels and the parent's split / lock group."""

    def __init__(self, src_ref: ImageRef, target_sets: dict[str, list[tuple[Panel, ImageRef, str]]],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Replicate inset")
        self.resize(640, 380)
        self._src_ref = src_ref
        self._sets = {k: v for k, v in target_sets.items() if v}
        lay = QVBoxLayout(self)
        self.set_buttons: dict[str, QRadioButton] = {}
        if len(self._sets) > 1:
            row = QHBoxLayout()
            group = QButtonGroup(self)
            for k, items in self._sets.items():
                b = QRadioButton(f"{k} ({len(items)})")
                group.addButton(b)
                b.toggled.connect(lambda on: on and self._refresh())
                self.set_buttons[k] = b
                row.addWidget(b)
            next(iter(self.set_buttons.values())).setChecked(True)
            lay.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Panel", "Geometry", "Field", "Will show"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table, 1)
        mrow = QHBoxLayout()
        self.same_region = QRadioButton("Same region (same image pixels)")
        self.same_size = QRadioButton("Same size (same µm, own position)")
        g2 = QButtonGroup(self)
        g2.addButton(self.same_region)
        g2.addButton(self.same_size)
        compat_all = [I.compatibility(src_ref, ref) for items in self._sets.values()
                      for _p, ref, _n in items]
        (self.same_region if any(c != "different" for c in compat_all) else self.same_size).setChecked(True)
        self.same_region.toggled.connect(lambda _on: self._refresh())
        mrow.addWidget(self.same_region)
        mrow.addWidget(self.same_size)
        lay.addLayout(mrow)
        self.keep_linked = QCheckBox("Keep linked (editing one updates the others)")
        self.keep_linked.setChecked(True)
        lay.addWidget(self.keep_linked)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #b45309;")
        lay.addWidget(self.warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = buttons.addButton("Replicate", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._refresh()

    # ------------------------------------------------------------- state
    def current_set(self) -> list[tuple[Panel, ImageRef, str]]:
        for k, b in self.set_buttons.items():
            if b.isChecked():
                return self._sets[k]
        return next(iter(self._sets.values()), [])

    def mode(self) -> str:
        return "region" if self.same_region.isChecked() else "size"

    def link(self) -> bool:
        return self.keep_linked.isChecked()

    def target_ids(self) -> list[str]:
        return [p.id for p, _r, _n in self.current_set()]

    def _refresh(self) -> None:
        rows = self.current_set()
        self.table.setRowCount(0)
        warn_stage = False
        for p, ref, name in rows:
            compat = I.compatibility(self._src_ref, ref)
            stage = I.stage_relation(self._src_ref, ref)
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, text in enumerate((name, COMPAT_TEXT[compat], STAGE_TEXT[stage],
                                      mode_text(self.mode(), compat))):
                self.table.setItem(r, c, QTableWidgetItem(text))
            if effective_mode(self.mode(), compat) == "region" and compat != "same_image" \
                    and stage != "equal":
                warn_stage = True
        self.warning.setText(REGISTRATION_WARNING if warn_stage else "")
        self.ok.setEnabled(bool(rows))

    def rows_text(self) -> list[tuple[str, ...]]:
        return [tuple(self.table.item(r, c).text() for c in range(4))
                for r in range(self.table.rowCount())]


def target_name(index: int, ref: Optional[ImageRef]) -> str:
    return f"Panel {index} — {ref.filename if ref else '?'}"
