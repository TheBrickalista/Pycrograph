# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
ChannelControls — one fluorescence channel in the inspector: visibility, LUT,
levels (histogram gates + slider + typed Black/White, range adapted to the
bit depth, as in PysternBlot), gamma, invert, saturation badge, free-text
stain/antibody field.

The widget never touches the model. It emits
    changed(channel_index, field, value, final)
where `final` marks the end of a gesture (slider released, field committed,
checkbox/combo changed). The main window applies every change to the model
and redraws, but logs + saves once per gesture.

Mixed state (figure inspector, several panels selected): set_mixed(fields)
shows the listed fields as undetermined — partially checked box, empty
LUT combo, blank Black/White/Gamma field and hidden slider handle.
Touching such a control emits its value as usual (applied to every
selected panel) and clears the mixed display of that field.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..display import LUT_NAMES, lut_hex
from ..models import ChannelInfo, ChannelSettings, SaturationStats
from .levels_histogram import LevelsHistogramWidget

FRAME_STYLE = """
    QFrame#channelFrame {
        background: #f4f4f4;
        border: 1px solid #d2d2d2;
        border-radius: 8px;
    }
"""
BADGE_WARN = "color: #b45309; font-weight: bold; font-size: 11px;"
BADGE_OK = "color: #15803d; font-size: 11px;"
CUSTOM_LUT = "Custom…"
MIXED_TEXT = "—"
HIDDEN_HANDLE = "QSlider::handle:horizontal { background: transparent; border: none; }"


class ChannelControls(QFrame):
    changed = Signal(int, str, object, bool)      # index, field, value, final
    stain_committed = Signal(int, str)
    auto_requested = Signal(int)

    def __init__(self, info: ChannelInfo, settings: ChannelSettings, full_scale: int,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("channelFrame")
        self.setStyleSheet(FRAME_STYLE)
        self.index = info.index
        self.full_scale = int(full_scale)
        self._lut = settings.lut
        self.mixed_fields: set[str] = set()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)

        # ---- header: visible + name + LUT
        head = QHBoxLayout()
        self.visible_cb = QCheckBox()
        self.visible_cb.setToolTip("Show this channel in the composite")
        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.name_lbl.setToolTip(self._info_tooltip(info))
        self.lut_combo = QComboBox()
        self.lut_combo.addItems([*LUT_NAMES, CUSTOM_LUT])
        self.lut_combo.setPlaceholderText(MIXED_TEXT)
        self.lut_combo.setToolTip("Colour lookup table (display only)")
        head.addWidget(self.visible_cb)
        head.addWidget(self.name_lbl, 1)
        head.addWidget(self.lut_combo)
        lay.addLayout(head)
        self._info = info

        self.stain_edit = QLineEdit()
        self.stain_edit.setPlaceholderText("Stain / antibody (free text, logged)")
        lay.addWidget(self.stain_edit)

        # ---- histogram
        self.hist = LevelsHistogramWidget()
        lay.addWidget(self.hist)

        # ---- levels
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        self.black_slider = self._slider()
        self.white_slider = self._slider()
        self.black_spin = self._spin()
        self.white_spin = self._spin()
        grid.addWidget(QLabel("Black"), 0, 0)
        grid.addWidget(self.black_slider, 0, 1)
        grid.addWidget(self.black_spin, 0, 2)
        grid.addWidget(QLabel("White"), 1, 0)
        grid.addWidget(self.white_slider, 1, 1)
        grid.addWidget(self.white_spin, 1, 2)
        lay.addLayout(grid)

        row = QHBoxLayout()
        self.auto_btn = QPushButton("Auto")
        self.auto_btn.setToolTip("Black/White at the 0.1 / 99.9 percentiles of the current plane")
        self.full_btn = QPushButton("Full range")
        self.full_btn.setToolTip(f"Black 0, White {self.full_scale}")
        row.addWidget(self.auto_btn)
        row.addWidget(self.full_btn)
        row.addStretch(1)
        row.addWidget(QLabel("Gamma"))
        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(0.10, 5.00)
        self.gamma_spin.setSingleStep(0.05)
        self.gamma_spin.setDecimals(2)
        self.gamma_spin.setKeyboardTracking(False)
        row.addWidget(self.gamma_spin)
        self.invert_cb = QCheckBox("Invert")
        row.addWidget(self.invert_cb)
        lay.addLayout(row)

        self.gamma_badge = QLabel("Non-linear (γ ≠ 1): flagged in the integrity report")
        self.gamma_badge.setStyleSheet(BADGE_WARN)
        self.gamma_badge.setWordWrap(True)
        lay.addWidget(self.gamma_badge)
        self.sat_badge = QLabel()
        self.sat_badge.setWordWrap(True)
        lay.addWidget(self.sat_badge)

        self.set_settings(settings)
        self._connect()

    # ------------------------------------------------------------ building
    def _slider(self) -> QSlider:
        s = QSlider(Qt.Horizontal)
        s.setRange(0, self.full_scale)
        s.setMinimumWidth(140)
        return s

    def _spin(self) -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, self.full_scale)
        s.setKeyboardTracking(False)      # commit typed values on Enter / focus out
        s.setFixedWidth(76)
        return s

    @staticmethod
    def _info_tooltip(info: ChannelInfo) -> str:
        parts = [f"Channel {info.index + 1}: {info.name or '—'}"]
        if info.wavelength_nm:
            parts.append(f"Wavelength: {info.wavelength_nm:g} nm")
        if info.exposure_ms:
            parts.append(f"Exposure: {info.exposure_ms:g} ms")
        return "\n".join(parts)

    def _connect(self) -> None:
        self.visible_cb.clicked.connect(lambda v: self._on_check("visible", v))
        self.visible_cb.toggled.connect(lambda v: self._on_toggled("visible", v))
        self.lut_combo.activated.connect(self._on_lut_activated)
        self.invert_cb.clicked.connect(lambda v: self._on_check("invert", v))
        self.invert_cb.toggled.connect(lambda v: self._on_toggled("invert", v))
        self.gamma_spin.valueChanged.connect(self._on_gamma)

        self.black_slider.valueChanged.connect(lambda v: self._on_level("black", v, False))
        self.white_slider.valueChanged.connect(lambda v: self._on_level("white", v, False))
        self.black_slider.sliderReleased.connect(lambda: self._commit_level("black"))
        self.white_slider.sliderReleased.connect(lambda: self._commit_level("white"))
        self.black_slider.sliderPressed.connect(lambda: self._unmix_level("black"))
        self.white_slider.sliderPressed.connect(lambda: self._unmix_level("white"))
        self.black_spin.valueChanged.connect(lambda v: self._on_level("black", v, True))
        self.white_spin.valueChanged.connect(lambda v: self._on_level("white", v, True))
        self.hist.gate_changed.connect(lambda which, v: self._on_level(which, v, False))
        self.hist.gate_commit.connect(lambda: (self._commit_level("black"), self._commit_level("white")))

        self.auto_btn.clicked.connect(lambda: self.auto_requested.emit(self.index))
        self.full_btn.clicked.connect(self._on_full)
        self.stain_edit.editingFinished.connect(
            lambda: self.stain_committed.emit(self.index, self.stain_edit.text().strip()))

    # ------------------------------------------------------------- setters
    def set_settings(self, s: ChannelSettings) -> None:
        self._clear_mixed_display()
        widgets = (self.visible_cb, self.lut_combo, self.black_slider, self.white_slider,
                   self.black_spin, self.white_spin, self.gamma_spin, self.invert_cb)
        for w in widgets:
            w.blockSignals(True)
        self.visible_cb.setChecked(s.visible)
        self._lut = s.lut
        i = self.lut_combo.findText(s.lut)
        if i < 0:
            self.lut_combo.setItemText(self.lut_combo.count() - 1, s.lut)
            i = self.lut_combo.count() - 1
        self.lut_combo.setCurrentIndex(i)
        self.black_slider.setValue(min(s.black, self.full_scale))
        self.white_slider.setValue(min(s.white, self.full_scale))
        self.black_spin.setValue(min(s.black, self.full_scale))
        self.white_spin.setValue(min(s.white, self.full_scale))
        self.gamma_spin.setValue(s.gamma)
        self.invert_cb.setChecked(s.invert)
        for w in widgets:
            w.blockSignals(False)
        self.hist.set_gates(s.black, s.white)
        self.gamma_badge.setVisible(abs(s.gamma - 1.0) > 1e-9)
        self._update_name_colour()

    # ------------------------------------------------------------ mixed
    def set_mixed(self, fields) -> None:
        """Show `fields` (subset of visible, lut, black, white, gamma,
        invert) as differing between the selected panels."""
        fields = set(fields)
        self.mixed_fields = fields
        if "visible" in fields:
            self._set_check_mixed(self.visible_cb)
        if "invert" in fields:
            self._set_check_mixed(self.invert_cb)
        if "lut" in fields:
            self.lut_combo.blockSignals(True)
            self.lut_combo.setCurrentIndex(-1)
            self.lut_combo.blockSignals(False)
        for which in ("black", "white"):
            if which in fields:
                slider, spin = self._level_widgets(which)
                slider.setStyleSheet(HIDDEN_HANDLE)
                spin.blockSignals(True)
                spin.setMinimum(-1)
                spin.setSpecialValueText(MIXED_TEXT)
                spin.setValue(-1)
                spin.blockSignals(False)
        if "gamma" in fields:
            self.gamma_spin.blockSignals(True)
            self.gamma_spin.setMinimum(0.0)
            self.gamma_spin.setSpecialValueText(MIXED_TEXT)
            self.gamma_spin.setValue(0.0)
            self.gamma_spin.blockSignals(False)
            self.gamma_badge.setVisible(False)

    @staticmethod
    def _set_check_mixed(cb: QCheckBox) -> None:
        cb.blockSignals(True)
        cb.setCheckState(Qt.PartiallyChecked)
        cb.blockSignals(False)

    def _level_widgets(self, which: str):
        return ((self.black_slider, self.black_spin) if which == "black"
                else (self.white_slider, self.white_spin))

    def _unmix_level(self, which: str) -> None:
        if which not in self.mixed_fields:
            return
        self.mixed_fields.discard(which)
        slider, spin = self._level_widgets(which)
        slider.setStyleSheet("")
        spin.blockSignals(True)
        spin.setSpecialValueText("")
        spin.setMinimum(0)
        spin.setValue(slider.value())
        spin.blockSignals(False)

    def _clear_mixed_display(self) -> None:
        for which in ("black", "white"):
            self._unmix_level(which)
        if "gamma" in self.mixed_fields:
            self.gamma_spin.blockSignals(True)
            self.gamma_spin.setSpecialValueText("")
            self.gamma_spin.setMinimum(0.10)
            self.gamma_spin.blockSignals(False)
        for cb in (self.visible_cb, self.invert_cb):
            cb.setTristate(False)
        self.mixed_fields = set()

    def _on_check(self, field: str, checked: bool) -> None:
        # A click on a partially checked box: Qt's next state decides the value.
        if field in self.mixed_fields:
            self.mixed_fields.discard(field)
            cb = self.visible_cb if field == "visible" else self.invert_cb
            cb.setTristate(False)        # setCheckState(Partially) had turned it on
            self.changed.emit(self.index, field, bool(checked), True)

    def _on_toggled(self, field: str, checked: bool) -> None:
        if field not in self.mixed_fields:
            self.changed.emit(self.index, field, bool(checked), True)

    def set_stain(self, text: str) -> None:
        self.stain_edit.setText(text)

    def set_plane(self, plane: np.ndarray, sat: SaturationStats | None) -> None:
        self.hist.set_image(plane, self.full_scale)
        self.set_saturation(sat)

    def set_saturation(self, sat: SaturationStats | None) -> None:
        if sat is None:
            self.sat_badge.setText("")
            return
        if sat.saturated_count:
            self.sat_badge.setStyleSheet(BADGE_WARN)
            self.sat_badge.setText(
                f"⚠ Saturated: {sat.saturated_count} px ({100 * sat.saturated_fraction:.3f} %), "
                f"{sat.solid_saturated_count} in solid regions — at {sat.full_scale}")
        else:
            self.sat_badge.setStyleSheet(BADGE_OK)
            self.sat_badge.setText(f"No clipping in this plane (max {sat.max_value} / {sat.full_scale})")

    def _update_name_colour(self) -> None:
        try:
            colour = lut_hex(self._lut)
        except ValueError:
            colour = "#888888"
        swatch = colour if colour != "#FFFFFF" else "#9CA3AF"
        self.name_lbl.setText(
            f'<span style="color:{swatch}">■</span> {self._info.name or f"Channel {self.index + 1}"}')

    # ----------------------------------------------------------- handlers
    def _on_lut_activated(self, i: int) -> None:
        text = self.lut_combo.itemText(i)
        if i == self.lut_combo.count() - 1:
            start = QColor(self._lut) if self._lut.startswith("#") else QColor("#FFFFFF")
            colour = QColorDialog.getColor(start, self, "Channel colour")
            if not colour.isValid():
                self.set_lut_text(self._lut)
                return
            text = colour.name().upper()
            self.lut_combo.setItemText(i, text)
        self._lut = text
        self.mixed_fields.discard("lut")
        self._update_name_colour()
        self.changed.emit(self.index, "lut", text, True)

    def set_lut_text(self, lut: str) -> None:
        self.lut_combo.blockSignals(True)
        i = self.lut_combo.findText(lut)
        if i < 0:
            i = self.lut_combo.count() - 1
            self.lut_combo.setItemText(i, lut)
        self.lut_combo.setCurrentIndex(i)
        self.lut_combo.blockSignals(False)

    def _on_gamma(self, v: float) -> None:
        if "gamma" in self.mixed_fields:
            self.mixed_fields.discard("gamma")
            v = max(v, 0.10)
            self.gamma_spin.blockSignals(True)
            self.gamma_spin.setSpecialValueText("")
            self.gamma_spin.setMinimum(0.10)
            self.gamma_spin.setValue(v)
            self.gamma_spin.blockSignals(False)
        self.gamma_badge.setVisible(abs(v - 1.0) > 1e-9)
        self.changed.emit(self.index, "gamma", float(round(v, 2)), True)

    def _on_level(self, which: str, value: int, final: bool) -> None:
        value = int(value)
        if which in self.mixed_fields:
            self._unmix_level(which)
            value = max(0, value)
        # A mixed counterpart does not constrain (each panel is clamped).
        black = value if which == "black" else (
            0 if "black" in self.mixed_fields else self.black_spin.value())
        white = value if which == "white" else (
            self.full_scale if "white" in self.mixed_fields else self.white_spin.value())
        if which == "black" and black >= white:
            black = white - 1
        if which == "white" and white <= black:
            white = black + 1
        black = max(0, black)
        white = min(self.full_scale, max(1, white))
        v = black if which == "black" else white
        self._sync_level_widgets(black, white)
        self.changed.emit(self.index, which, v, final)

    def _commit_level(self, which: str) -> None:
        v = self.black_spin.value() if which == "black" else self.white_spin.value()
        self.changed.emit(self.index, which, int(v), True)

    def _on_full(self) -> None:
        self._clear_mixed_display()
        self._sync_level_widgets(0, self.full_scale)
        self.changed.emit(self.index, "black", 0, True)
        self.changed.emit(self.index, "white", self.full_scale, True)

    def _sync_level_widgets(self, black: int, white: int) -> None:
        pairs = []
        if "black" not in self.mixed_fields:
            pairs += [(self.black_slider, black), (self.black_spin, black)]
        if "white" not in self.mixed_fields:
            pairs += [(self.white_slider, white), (self.white_spin, white)]
        for w, v in pairs:
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.hist.set_gates(black, white)
