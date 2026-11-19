# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Replicate-inset dialog: a checklist of every panel that can receive the
inset (the parent's split / lock group first, then figure order), with each
one's detected compatibility and the mode it will get, quick-check buttons,
"Same region" / "Same size" and "Keep linked" (insets.replicate_inset).

Default mode: "Same region" only if every checked target is the same image,
or the same geometry with the same stage position; otherwise "Same size".
It is recomputed when the checks change, until the user clicks a mode.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
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


def region_is_safe(compat: str, stage: str) -> bool:
    """Same pixels are meaningful: the same image, or the same geometry with
    the same stage position."""
    return compat == "same_image" or (compat == "same_geometry" and stage == "equal")


class ReplicateDialog(QDialog):
    """`targets`: [(panel, ref, display name)] in checklist order;
    `checked`: the panel ids pre-checked."""

    def __init__(self, src_ref: ImageRef, targets: list[tuple[Panel, ImageRef, str]],
                 checked: Iterable[str] = (), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Replicate inset")
        self.resize(720, 420)
        self._src_ref = src_ref
        self._targets = list(targets)
        self._info = [(I.compatibility(src_ref, ref), I.stage_relation(src_ref, ref))
                      for _p, ref, _n in self._targets]
        self._user_mode = False                  # a mode radio was clicked: keep it
        checked = set(checked)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Tick the panels that should receive this inset:"))

        quick = QHBoxLayout()
        self.quick_buttons: dict[str, QPushButton] = {}
        for key, text, tip in (
                ("all", "All", "Check every panel"),
                ("none", "None", "Uncheck every panel"),
                ("same_image", "Same image", "Check the panels showing the same file "
                                             "(e.g. split channels)"),
                ("same_geometry", "Same geometry", "Check the panels whose image has the same "
                                                   "size and pixel size (including the same image)")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setAutoDefault(False)                   # Enter replicates, never re-checks
            b.clicked.connect(lambda _c=False, k=key: self.check(k))
            self.quick_buttons[key] = b
            quick.addWidget(b)
        quick.addStretch(1)
        lay.addLayout(quick)

        self.table = QTableWidget(len(self._targets), 4)
        self.table.setHorizontalHeaderLabels(["Panel", "Geometry", "Field", "Will show"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        for r, ((p, _ref, name), (compat, stage)) in enumerate(zip(self._targets, self._info)):
            it = QTableWidgetItem(name)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if p.id in checked else Qt.Unchecked)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(COMPAT_TEXT[compat]))
            self.table.setItem(r, 2, QTableWidgetItem(STAGE_TEXT[stage]))
            self.table.setItem(r, 3, QTableWidgetItem(""))
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table, 1)

        mrow = QHBoxLayout()
        self.same_region = QRadioButton("Same region (same image pixels)")
        self.same_size = QRadioButton("Same size (same µm, own position)")
        g2 = QButtonGroup(self)
        g2.addButton(self.same_region)
        g2.addButton(self.same_size)
        self.same_region.setChecked(True)
        for b in (self.same_region, self.same_size):
            b.clicked.connect(self._mode_clicked)          # user clicks only, not setChecked
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
        self.ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._apply_default_mode()
        self._refresh()
        self.table.setFocus()

    # ------------------------------------------------------------- state
    def mode(self) -> str:
        return "region" if self.same_region.isChecked() else "size"

    def link(self) -> bool:
        return self.keep_linked.isChecked()

    def is_checked(self, row: int) -> bool:
        return self.table.item(row, 0).checkState() == Qt.Checked

    def target_ids(self) -> list[str]:
        """Checked panels, in checklist order."""
        return [p.id for r, (p, _ref, _n) in enumerate(self._targets) if self.is_checked(r)]

    def set_checked(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        self.table.blockSignals(True)
        for r, (p, _ref, _n) in enumerate(self._targets):
            self.table.item(r, 0).setCheckState(Qt.Checked if p.id in ids else Qt.Unchecked)
        self.table.blockSignals(False)
        self._on_checks_changed()

    def check(self, which: str) -> None:
        """Quick checks: "all", "none", "same_image", "same_geometry"."""
        keep = {"all": lambda c: True, "none": lambda c: False,
                "same_image": lambda c: c == "same_image",
                "same_geometry": lambda c: c != "different"}[which]
        self.set_checked(p.id for (p, _r, _n), (c, _s) in zip(self._targets, self._info) if keep(c))

    def default_mode(self) -> Optional[str]:
        """Region only if every checked target is region-safe; None when
        nothing is checked (keep the current mode)."""
        rows = [self._info[r] for r in range(len(self._targets)) if self.is_checked(r)]
        if not rows:
            return None
        return "region" if all(region_is_safe(c, s) for c, s in rows) else "size"

    # ----------------------------------------------------------- updates
    def _mode_clicked(self) -> None:
        self._user_mode = True

    def _apply_default_mode(self) -> None:
        if self._user_mode:
            return
        m = self.default_mode()
        if m is not None:
            (self.same_region if m == "region" else self.same_size).setChecked(True)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._on_checks_changed()

    def _on_checks_changed(self) -> None:
        self._apply_default_mode()
        self._refresh()

    def _refresh(self) -> None:
        warn_stage = False
        self.table.blockSignals(True)
        for r, (compat, stage) in enumerate(self._info):
            on = self.is_checked(r)
            self.table.item(r, 3).setText(mode_text(self.mode(), compat) if on else "—")
            if on and effective_mode(self.mode(), compat) == "region" and compat != "same_image" \
                    and stage != "equal":
                warn_stage = True
        self.table.blockSignals(False)
        self.warning.setText(REGISTRATION_WARNING if warn_stage else "")
        n = len(self.target_ids())
        self.ok.setText(f"Replicate to {n} panel{'s' if n != 1 else ''}")
        self.ok.setEnabled(n > 0)

    def rows_text(self) -> list[tuple[str, ...]]:
        return [tuple(self.table.item(r, c).text() for c in range(4))
                for r in range(self.table.rowCount())]


def target_name(index: int, ref: Optional[ImageRef]) -> str:
    return f"Panel {index} — {ref.filename if ref else '?'}"
