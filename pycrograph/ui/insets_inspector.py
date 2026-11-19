# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Insets & Shapes inspector tab (design §2.7).

Insets: "Create inset" puts the canvas in draw mode on the selected panel;
the rectangle drawn becomes a new inset (2× magnification, right of the
parent). For selected insets: colour, width, rectangle on the parent,
matching border, follow T/Z, follow rendering, detach. The rectangle on the
parent is edited on the canvas (select the parent, drag the rectangle or its
corner).

Shapes: draw an arrow / line / rectangle / ellipse on the selected panel;
the list selects shapes (as does clicking them on the canvas once the panel
is selected); colour, stroke and arrowhead (page points) apply to the
selected shapes and to the next one drawn; delete; copy / paste shapes
between panels (same raw-pixel coordinates).

Like the other inspector tabs, nothing here writes the model: every change
goes through FiguresTab (one undo step, one log entry per panel).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .. import insets as I
from ..models import InsetStyle, Panel
from .labels_inspector import ColorButton, _section

if TYPE_CHECKING:                                   # pragma: no cover
    from .figures_tab import FiguresTab

SHAPE_KINDS = ("arrow", "line", "rect", "ellipse")


class InsetsInspector(QScrollArea):
    def __init__(self, tab: "FiguresTab"):
        super().__init__()
        self.tab = tab
        self.setWidgetResizable(True)
        self.panels: list[Panel] = []
        self._loading = False
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)
        self.empty = QLabel("Select one or more panels.")
        self.empty.setStyleSheet("color: #6b7280;")
        lay.addWidget(self.empty)
        self.sections = QWidget()
        sl = QVBoxLayout(self.sections)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self._build_insets())
        sl.addWidget(self._build_shapes())
        lay.addWidget(self.sections)
        lay.addStretch(1)
        self.setWidget(body)
        self.set_panels([])

    # ================================================================ build
    def _build_insets(self) -> QFrame:
        frame, lay = _section("Insets")
        self.inset_info = QLabel("")
        self.inset_info.setWordWrap(True)
        lay.addWidget(self.inset_info)
        self.create_btn = QPushButton("Create inset…")
        self.create_btn.setToolTip("Drag a rectangle on the selected panel: it becomes a new "
                                   "panel at 2× magnification, linked to this one")
        self.create_btn.clicked.connect(self.tab.start_create_inset)
        lay.addWidget(self.create_btn)
        self.style_box = QWidget()
        form = QFormLayout(self.style_box)
        form.setContentsMargins(0, 0, 0, 0)
        self.inset_color = ColorButton("#FFFF00")
        self.inset_color.clicked.connect(self._pick_inset_color)
        form.addRow("Colour", self.inset_color)
        self.inset_width = QDoubleSpinBox()
        self.inset_width.setRange(0.1, 10.0)
        self.inset_width.setDecimals(2)
        self.inset_width.setSuffix(" pt")
        self.inset_width.setKeyboardTracking(False)
        self.inset_width.valueChanged.connect(lambda v: self._style(width_pt=float(v)))
        form.addRow("Width", self.inset_width)
        self.flags: dict[str, QCheckBox] = {}
        for key, text in (("show_on_parent", "Rectangle on the parent"),
                          ("border_on_inset", "Matching border on the inset"),
                          ("follow_parent_tz", "Follow the parent's T / Z"),
                          ("follow_parent_rendering", "Follow the parent's channels")):
            cb = QCheckBox(text)
            cb.clicked.connect(lambda v, k=key: self._style(**{k: bool(v)}))
            self.flags[key] = cb
            form.addRow(cb)
        self.detach_btn = QPushButton("Make ordinary panel")
        self.detach_btn.clicked.connect(self.tab.detach_insets)
        form.addRow(self.detach_btn)
        lay.addWidget(self.style_box)
        return frame

    def _build_shapes(self) -> QFrame:
        frame, lay = _section("Shapes")
        row = QHBoxLayout()
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(SHAPE_KINDS)
        self.draw_btn = QPushButton("Draw…")
        self.draw_btn.setToolTip("Drag on the selected panel (Esc cancels)")
        self.draw_btn.clicked.connect(lambda: self.tab.start_draw_shape(self.kind_combo.currentText()))
        row.addWidget(self.kind_combo)
        row.addWidget(self.draw_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.shape_list = QListWidget()
        self.shape_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.shape_list.setMaximumHeight(120)
        self.shape_list.itemSelectionChanged.connect(self._on_list_selection)
        lay.addWidget(self.shape_list)
        form = QFormLayout()
        self.shape_color = ColorButton("#FFFFFF")
        self.shape_color.clicked.connect(self._pick_shape_color)
        form.addRow("Colour", self.shape_color)
        self.shape_width = QDoubleSpinBox()
        self.shape_width.setRange(0.1, 20.0)
        self.shape_width.setDecimals(2)
        self.shape_width.setValue(1.0)
        self.shape_width.setSuffix(" pt")
        self.shape_width.setKeyboardTracking(False)
        self.shape_width.valueChanged.connect(lambda v: self._shape(width_pt=float(v)))
        form.addRow("Stroke", self.shape_width)
        self.head_size = QDoubleSpinBox()
        self.head_size.setRange(0.5, 50.0)
        self.head_size.setDecimals(1)
        self.head_size.setValue(5.0)
        self.head_size.setSuffix(" pt")
        self.head_size.setKeyboardTracking(False)
        self.head_size.valueChanged.connect(lambda v: self._shape(head_size_pt=float(v)))
        form.addRow("Arrowhead", self.head_size)
        lay.addLayout(form)
        brow = QHBoxLayout()
        self.shape_buttons = {}
        for key, text, slot in (("delete", "Delete", self.tab.delete_selected_shapes),
                                ("copy", "Copy", self.tab.copy_shapes),
                                ("paste", "Paste", self.tab.paste_shapes)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, s_=slot: s_())
            self.shape_buttons[key] = b
            brow.addWidget(b)
        brow.addStretch(1)
        lay.addLayout(brow)
        note = QLabel("Shapes are stored in image pixels (they follow crop, zoom and rotation); "
                      "stroke and arrowhead are in page points.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay.addWidget(note)
        return frame

    # ============================================================ display
    def set_panels(self, panels: list[Panel]) -> None:
        self.panels = list(panels)
        has = bool(self.panels)
        self.empty.setVisible(not has)
        self.sections.setVisible(has)
        if has:
            self.refresh()

    def refresh(self) -> None:
        self.panels = self.tab.selected_panels()     # live objects (undo replaces them)
        if not self.panels:
            self.empty.setVisible(True)
            self.sections.setVisible(False)
            return
        self._loading = True
        fig = self.tab.current_figure()
        P = self.panels
        single = len(P) == 1
        self.create_btn.setEnabled(single and self.tab.ref(P[0]) is not None)
        self.draw_btn.setEnabled(single)
        insets = [p for p in P if p.inset_of and p.inset_style]
        lines = []
        for p in P:
            kids = I.insets_of(fig, p.id) if fig else []
            if kids:
                lines.append(f"Parent of {len(kids)} inset(s): " + ", ".join(
                    f"{k + 1} ({c.inset_style.color if c.inset_style else '?'})"
                    for k, c in enumerate(kids)))
        for p in insets:
            parent = fig.panel(p.inset_of) if fig else None
            ref = self.tab.ref(parent) if parent else None
            lines.append(f"Inset {I.inset_number(fig, p)} of "
                         f"{ref.filename if ref else 'a missing panel'}")
        self.inset_info.setText("\n".join(dict.fromkeys(lines)) or "No inset in the selection.")
        self.style_box.setEnabled(bool(insets))
        st = insets[0].inset_style if insets else InsetStyle()
        self.inset_color.set_color(st.color)
        self.inset_width.blockSignals(True)
        self.inset_width.setValue(st.width_pt)
        self.inset_width.blockSignals(False)
        for key, cb in self.flags.items():
            vals = {getattr(p.inset_style, key) for p in insets} or {getattr(st, key)}
            cb.setTristate(False)
            if len(vals) > 1:
                cb.setCheckState(Qt.PartiallyChecked)
            else:
                cb.setChecked(vals.pop())
        # shapes
        self.shape_list.blockSignals(True)
        self.shape_list.clear()
        if single:
            chosen = self.tab.selected_shapes.get(P[0].id, set())
            for k, sh in enumerate(P[0].shapes, start=1):
                it = QListWidgetItem(f"{k}. {sh.kind}  {sh.color}  {sh.width_pt:g} pt")
                it.setData(Qt.UserRole, sh.id)
                self.shape_list.addItem(it)
                it.setSelected(sh.id in chosen)
            first = next((sh for sh in P[0].shapes if sh.id in chosen), None)
            if first is not None:
                self.shape_color.set_color(first.color)
                for w, v in ((self.shape_width, first.width_pt), (self.head_size, first.head_size_pt)):
                    w.blockSignals(True)
                    w.setValue(v)
                    w.blockSignals(False)
        self.shape_list.setEnabled(single)
        self.shape_list.blockSignals(False)
        self.shape_buttons["paste"].setEnabled(bool(self.tab.clip_shapes))
        self._loading = False

    # ============================================================ actions
    def shape_style(self) -> dict:
        return {"color": self.shape_color.color or "#FFFFFF", "width_pt": self.shape_width.value(),
                "head_size_pt": self.head_size.value()}

    def _style(self, **fields) -> None:
        if not self._loading:
            self.tab.set_inset_style(**fields)

    def _shape(self, **fields) -> None:
        if not self._loading:
            self.tab.update_selected_shapes(**fields)

    def _pick_inset_color(self) -> None:
        c = self.inset_color.pick()
        if c:
            self._style(color=c)

    def _pick_shape_color(self) -> None:
        c = self.shape_color.pick()
        if c:
            self.shape_color.set_color(c)
            self._shape(color=c)

    def _on_list_selection(self) -> None:
        if self._loading or len(self.panels) != 1:
            return
        ids = [it.data(Qt.UserRole) for it in self.shape_list.selectedItems()]
        self.tab.select_shapes(self.panels[0].id, ids)
