# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Labels inspector tab (design §2.5–2.6): labels, scale bar and border of
ALL selected panels.

- The label table lists the union of the selected panels' labels, one row
  per (text, position) with the number of panels holding it. Add appends
  the label to every selected panel; Update / Delete act on every selected
  panel holding the selected row's label.
- Tokens ([channels], [time:min], [kv:Key], ...) are typed in the text or
  inserted from the token menu; values that cannot be resolved are drawn
  as "?" and listed here (and in the status bar).
- Scale bar and border settings apply to every selected panel. Panels of
  uncalibrated images are skipped for the scale bar; when no selected
  panel is calibrated the section is greyed with the reason.

Like the Preview tab, this widget never writes the model: everything goes
through FiguresTab (one undo step, one log entry per panel).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..figures import label_key
from ..labels import TOKENS
from ..models import Panel, PanelLabel
from ..scalebar import BAR_UNITS, ScaleBarUnavailable, bar_geometry, unit_label

if TYPE_CHECKING:                                   # pragma: no cover
    from .figures_tab import FiguresTab

POSITIONS = ("top", "bottom", "left", "right", "left-vertical", "right-vertical",
             "top-left", "top-right", "bottom-left", "bottom-right")
BAR_POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left")
AUTO = "auto"
SECTION_STYLE = """
    QFrame#section { background: #f4f4f4; border: 1px solid #d2d2d2; border-radius: 8px; }
"""
WARN = "color: #b45309; font-size: 11px;"


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("section")
    frame.setStyleSheet(SECTION_STYLE)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(10, 8, 10, 10)
    lbl = QLabel(title)
    lbl.setStyleSheet("font-weight: 600; font-size: 12px;")
    lay.addWidget(lbl)
    return frame, lay


class ColorButton(QPushButton):
    """Shows a colour; `allow_auto` adds an 'auto' state (None)."""

    def __init__(self, color: Optional[str], allow_auto: bool = False):
        super().__init__()
        self.allow_auto = allow_auto
        self.set_color(color)

    def set_color(self, color: Optional[str]) -> None:
        self.color = color
        self.setText(color or AUTO)
        self.setStyleSheet(f"background: {color};" if color else "")

    def pick(self) -> Optional[str]:
        c = QColorDialog.getColor(QColor(self.color or "#000000"), self, "Colour")
        return c.name().upper() if c.isValid() else None


class LabelsInspector(QScrollArea):
    def __init__(self, tab: "FiguresTab"):
        super().__init__()
        self.tab = tab
        self.setWidgetResizable(True)
        self.panels: list[Panel] = []
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
        sl.addWidget(self._build_labels())
        sl.addWidget(self._build_scale_bar())
        sl.addWidget(self._build_border())
        lay.addWidget(self.sections)
        lay.addStretch(1)
        self.setWidget(body)
        self._loading = False
        self.set_panels([])

    # ================================================================ build
    def _build_labels(self) -> QFrame:
        frame, lay = _section("Labels")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Text", "Position", "Panels"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMaximumHeight(150)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        lay.addWidget(self.table)

        form = QFormLayout()
        row = QHBoxLayout()
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("Text, with tokens: [channels], [time:min], [kv:Key]…")
        row.addWidget(self.text_edit, 1)
        self.token_combo = QComboBox()
        self.token_combo.addItem("Token…")
        self.token_combo.addItems(TOKENS)
        self.token_combo.activated.connect(self._insert_token)
        row.addWidget(self.token_combo)
        form.addRow("Text", row)
        self.position_combo = QComboBox()
        self.position_combo.addItems(POSITIONS)
        form.addRow("Position", self.position_combo)
        frow = QHBoxLayout()
        self.family_edit = QLineEdit()
        self.family_edit.setPlaceholderText("page font")
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(0.0, 72.0)
        self.size_spin.setDecimals(1)
        self.size_spin.setSpecialValueText("page size")
        self.size_spin.setSuffix(" pt")
        frow.addWidget(self.family_edit, 1)
        frow.addWidget(self.size_spin)
        form.addRow("Font", frow)
        crow = QHBoxLayout()
        self.color_btn = ColorButton(None, allow_auto=True)
        self.color_btn.clicked.connect(lambda: self.color_btn.set_color(self.color_btn.pick()
                                                                         or self.color_btn.color))
        self.color_auto = QPushButton("Auto")
        self.color_auto.setToolTip("Black outside the panel, white inside")
        self.color_auto.clicked.connect(lambda: self.color_btn.set_color(None))
        crow.addWidget(self.color_btn, 1)
        crow.addWidget(self.color_auto)
        form.addRow("Colour", crow)
        lay.addLayout(form)

        brow = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.update_btn = QPushButton("Update")
        self.delete_btn = QPushButton("Delete")
        self.kv_btn = QPushButton("Labels from key–value…")
        self.add_btn.clicked.connect(lambda: self.tab.add_label(**self.editor_fields()))
        self.update_btn.clicked.connect(self._update_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.kv_btn.clicked.connect(self.tab.labels_from_kv_dialog)
        for b in (self.add_btn, self.update_btn, self.delete_btn):
            brow.addWidget(b)
        brow.addStretch(1)
        lay.addLayout(brow)
        lay.addWidget(self.kv_btn)
        self.missing_label = QLabel("")
        self.missing_label.setWordWrap(True)
        self.missing_label.setStyleSheet(WARN)
        lay.addWidget(self.missing_label)
        return frame

    def _build_scale_bar(self) -> QFrame:
        frame, lay = _section("Scale bar")
        self.bar_frame = frame
        self.bar_reason = QLabel("")
        self.bar_reason.setWordWrap(True)
        self.bar_reason.setStyleSheet(WARN)
        lay.addWidget(self.bar_reason)
        self.bar_widgets = QWidget()
        form = QFormLayout(self.bar_widgets)
        form.setContentsMargins(0, 0, 0, 0)
        self.bar_visible = QCheckBox("Show scale bar")
        self.bar_visible.clicked.connect(lambda v: self._bar(visible=bool(v)))
        form.addRow(self.bar_visible)
        lrow = QHBoxLayout()
        self.bar_length = QDoubleSpinBox()
        self.bar_length.setRange(0.01, 100000.0)
        self.bar_length.setDecimals(2)
        self.bar_length.setKeyboardTracking(False)
        self.bar_length.valueChanged.connect(lambda v: self._bar(length=float(v)))
        self.bar_unit = QComboBox()
        for u in BAR_UNITS:
            self.bar_unit.addItem(unit_label(u), u)
        self.bar_unit.activated.connect(lambda _i: self._bar(unit=self.bar_unit.currentData()))
        lrow.addWidget(self.bar_length, 1)
        lrow.addWidget(self.bar_unit)
        form.addRow("Length", lrow)
        self.bar_position = QComboBox()
        self.bar_position.addItems(BAR_POSITIONS)
        self.bar_position.activated.connect(
            lambda _i: self._bar(position=self.bar_position.currentText()))
        form.addRow("Position", self.bar_position)
        self.bar_color = ColorButton("#FFFFFF")
        self.bar_color.clicked.connect(self._pick_bar_color)
        form.addRow("Colour", self.bar_color)
        self.bar_thickness = QDoubleSpinBox()
        self.bar_thickness.setRange(0.1, 20.0)
        self.bar_thickness.setDecimals(2)
        self.bar_thickness.setSuffix(" pt")
        self.bar_thickness.setKeyboardTracking(False)
        self.bar_thickness.valueChanged.connect(lambda v: self._bar(thickness_pt=float(v)))
        form.addRow("Thickness", self.bar_thickness)
        trow = QHBoxLayout()
        self.bar_show_text = QCheckBox("Text")
        self.bar_show_text.clicked.connect(lambda v: self._bar(show_label=bool(v)))
        self.bar_font = QDoubleSpinBox()
        self.bar_font.setRange(0.0, 72.0)
        self.bar_font.setDecimals(1)
        self.bar_font.setSpecialValueText("page size")
        self.bar_font.setSuffix(" pt")
        self.bar_font.setKeyboardTracking(False)
        self.bar_font.valueChanged.connect(lambda v: self._bar(font_size_pt=float(v) or None))
        self.bar_text_pos = QComboBox()
        self.bar_text_pos.addItems(["below", "above"])
        self.bar_text_pos.activated.connect(
            lambda _i: self._bar(label_position=self.bar_text_pos.currentText()))
        trow.addWidget(self.bar_show_text)
        trow.addWidget(self.bar_font)
        trow.addWidget(self.bar_text_pos)
        form.addRow("Text", trow)
        lay.addWidget(self.bar_widgets)
        self.bar_note = QLabel("")
        self.bar_note.setWordWrap(True)
        self.bar_note.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay.addWidget(self.bar_note)
        return frame

    def _build_border(self) -> QFrame:
        frame, lay = _section("Border")
        form = QFormLayout()
        self.border_visible = QCheckBox("Show border (inside the panel edge)")
        self.border_visible.clicked.connect(lambda v: self.tab.set_border(visible=bool(v)))
        form.addRow(self.border_visible)
        self.border_color = ColorButton("#FFFFFF")
        self.border_color.clicked.connect(self._pick_border_color)
        form.addRow("Colour", self.border_color)
        self.border_width = QDoubleSpinBox()
        self.border_width.setRange(0.05, 20.0)
        self.border_width.setDecimals(2)
        self.border_width.setSuffix(" pt")
        self.border_width.setKeyboardTracking(False)
        self.border_width.valueChanged.connect(lambda v: self.tab.set_border(width_pt=float(v)))
        form.addRow("Width", self.border_width)
        lay.addLayout(form)
        self.border_note = QLabel("")
        self.border_note.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay.addWidget(self.border_note)
        return frame

    # ============================================================ display
    def set_panels(self, panels: list[Panel]) -> None:
        self.panels = list(panels)
        has = bool(self.panels)
        self.empty.setVisible(not has)
        self.sections.setVisible(has)
        if has:
            self.refresh()

    def label_rows(self) -> list[tuple[tuple[str, str], int]]:
        """Union of the selected panels' labels: ((text, position), count)."""
        counts: dict[tuple[str, str], int] = {}
        for p in self.panels:
            for key in dict.fromkeys(label_key(lb) for lb in p.labels):
                counts[key] = counts.get(key, 0) + 1
        return list(counts.items())

    def refresh(self) -> None:
        """Re-read the selected panels (after any change)."""
        self._loading = True
        keep = self.selected_key()
        self.table.setRowCount(0)
        n = len(self.panels)
        for (text, pos), count in self.label_rows():
            r = self.table.rowCount()
            self.table.insertRow(r)
            it = QTableWidgetItem(text)
            it.setData(Qt.UserRole, (text, pos))
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(pos))
            self.table.setItem(r, 2, QTableWidgetItem(f"{count}/{n}"))
            if (text, pos) == keep:
                self.table.selectRow(r)
        missing = self.tab.missing_tokens([p.id for p in self.panels])
        self.missing_label.setText(
            "Unresolved, drawn as “?”: " + ", ".join(missing) if missing else "")
        self._refresh_bar()
        self._refresh_border()
        self._loading = False

    def _refresh_bar(self) -> None:
        P = self.panels
        refs = [self.tab.ref(p) for p in P]
        calibrated = [p for p, r in zip(P, refs) if r and r.metadata.calibration.is_calibrated]
        reasons = []
        for p, r in zip(P, refs):
            if r is not None:
                try:
                    bar_geometry(p, r)
                except ScaleBarUnavailable as exc:
                    reasons.append(str(exc))
        self.bar_widgets.setEnabled(bool(calibrated))
        if not calibrated:
            self.bar_reason.setText("Unavailable: " + "; ".join(dict.fromkeys(reasons)))
        elif reasons:
            self.bar_reason.setText(f"{len(P) - len(calibrated)} of {len(P)} panel(s) skipped: "
                                    + "; ".join(dict.fromkeys(reasons)))
        else:
            self.bar_reason.setText("")
        if not calibrated:
            self.bar_note.setText("")
            return
        p0 = calibrated[0]
        b = p0.scale_bar
        widgets = (self.bar_visible, self.bar_length, self.bar_unit, self.bar_position,
                   self.bar_thickness, self.bar_show_text, self.bar_font, self.bar_text_pos)
        for w in widgets:
            w.blockSignals(True)
        self.bar_visible.setChecked(b.visible)
        self.bar_length.setValue(b.length)
        self.bar_unit.setCurrentIndex(max(0, self.bar_unit.findData(b.unit)))
        self.bar_position.setCurrentText(b.position)
        self.bar_color.set_color(b.color)
        self.bar_thickness.setValue(b.thickness_pt)
        self.bar_show_text.setChecked(b.show_label)
        self.bar_font.setValue(b.font_size_pt or 0.0)
        self.bar_text_pos.setCurrentText(b.label_position)
        for w in widgets:
            w.blockSignals(False)
        notes = []
        if len({p.scale_bar.model_dump_json() for p in calibrated}) > 1:
            notes.append("Settings differ between the selected panels (showing the first); "
                         "any change applies to all.")
        geo = bar_geometry(p0, self.tab.ref(p0))
        notes.append(f"{geo.text} = {geo.length_mm:.2f} mm on the page (first panel)")
        if geo.length_mm > p0.width_mm:
            notes.append("longer than the panel")
        if geo.note:
            notes.append(geo.note)
        self.bar_note.setText("\n".join(notes))

    def _refresh_border(self) -> None:
        b = self.panels[0].border
        for w in (self.border_visible, self.border_width):
            w.blockSignals(True)
        self.border_visible.setChecked(b.visible)
        self.border_color.set_color(b.color)
        self.border_width.setValue(b.width_pt)
        for w in (self.border_visible, self.border_width):
            w.blockSignals(False)
        differ = len({p.border.model_dump_json() for p in self.panels}) > 1
        self.border_note.setText("Settings differ between the selected panels (showing the "
                                 "first)." if differ else "")

    # ============================================================ actions
    def editor_fields(self) -> dict:
        size = self.size_spin.value()
        return {"text": self.text_edit.text(), "position": self.position_combo.currentText(),
                "font_size_pt": size or None, "color": self.color_btn.color,
                "font_family": self.family_edit.text().strip() or None}

    def selected_key(self) -> Optional[tuple[str, str]]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.UserRole)

    def _on_row_selected(self) -> None:
        if self._loading:
            return
        key = self.selected_key()
        if key is None:
            return
        lb: Optional[PanelLabel] = next((lb for p in self.panels for lb in p.labels
                                         if label_key(lb) == key), None)
        if lb is None:
            return
        self.text_edit.setText(lb.text)
        self.position_combo.setCurrentText(lb.position)
        self.family_edit.setText(lb.font_family or "")
        self.size_spin.setValue(lb.font_size_pt or 0.0)
        self.color_btn.set_color(lb.color)

    def _update_selected(self) -> None:
        key = self.selected_key()
        if key is not None:
            self.tab.update_label(key, **self.editor_fields())

    def _delete_selected(self) -> None:
        key = self.selected_key()
        if key is not None:
            self.tab.delete_label(key)

    def _insert_token(self, i: int) -> None:
        if i > 0:
            self.text_edit.insert(self.token_combo.itemText(i))
        self.token_combo.setCurrentIndex(0)

    def _bar(self, **fields) -> None:
        if not self._loading:
            self.tab.set_scale_bar(**fields)

    def _pick_bar_color(self) -> None:
        c = self.bar_color.pick()
        if c:
            self._bar(color=c)

    def _pick_border_color(self) -> None:
        c = self.border_color.pick()
        if c:
            self.tab.set_border(color=c)
