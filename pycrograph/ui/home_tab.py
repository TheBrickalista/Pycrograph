# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Home tab: logo, version, licence and big buttons to the main entry points
(new / open project, import images, figures, import archive, about).

Layout after PysternBlot's home tab (``MainWindow._build_home_tab`` /
``_make_home_button``). It only emits intents; the main window acts.

The logo is ``resources/pycrograph_logo.png`` (rendered from the SVG with
rsvg; QtSvg cannot draw its clipPath / blur, so the SVG is never loaded in
Qt), trimmed to its visible part and scaled at the screen's device pixel
ratio so it stays sharp on HiDPI displays. Its wordmark is dark: with a dark
palette the logo sits on a light rounded card.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .. import __version__
from ..selfcheck import logo_path

LOGO_WIDTH = 320                     # logical pixels (buttons visible in 1280×800)
AUTHORS = "Etienne Boulter"          # as in CITATION.cff (test_ui_home checks)
INSTITUTION = "IRCAN, Inserm U1081"
SUBTITLE = ("Publication figures from fluorescence microscopy, with full provenance: "
            "raw files never modified, SHA-256 verification, hash-chained log and "
            "integrity reports.")
SUBTITLE_ALPHA = 0.7
NEEDS_PROJECT = "Open or create a project first."
BUTTON_STYLE = """
    QPushButton {
        background: palette(button);
        border: 1px solid palette(mid);
        border-radius: 10px;
        padding: 12px 18px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton:hover { border: 1px solid palette(dark); }
    QPushButton:pressed { background: palette(midlight); }
"""
CARD_STYLE = "QFrame#logoCard { background: #f4f4f4; border-radius: 16px; }"


def _trimmed(img: QImage) -> QImage:
    """The image cropped to its non-transparent part (the PNG has a wide
    transparent margin), with a small border."""
    rgba = img.convertToFormat(QImage.Format_RGBA8888)
    arr = np.frombuffer(rgba.constBits(), np.uint8).reshape(
        rgba.height(), rgba.bytesPerLine())[:, :rgba.width() * 4]
    ys, xs = np.nonzero(arr[:, 3::4])
    if not len(xs):
        return img
    pad = max(2, img.width() // 100)
    x0, y0 = max(0, xs.min() - pad), max(0, ys.min() - pad)
    x1, y1 = min(img.width(), xs.max() + 1 + pad), min(img.height(), ys.max() + 1 + pad)
    return rgba.copy(int(x0), int(y0), int(x1 - x0), int(y1 - y0))


def logo_pixmap(width: int, dpr: float) -> QPixmap:
    """The logo `width` logical pixels wide, rendered at `dpr` device pixels
    per logical pixel (null pixmap if the resource is missing)."""
    img = QImage(str(logo_path()))
    if img.isNull():
        return QPixmap()
    dpr = max(1.0, float(dpr))
    pm = QPixmap.fromImage(_trimmed(img).scaledToWidth(round(width * dpr),
                                                       Qt.SmoothTransformation))
    pm.setDevicePixelRatio(dpr)
    return pm


def _font(pixel_size: int, weight=QFont.Normal) -> QFont:
    """A font, not a style sheet: a widget with a style sheet keeps its old
    colours when the palette changes (light ↔ dark)."""
    f = QFont()
    f.setPixelSize(pixel_size)
    f.setWeight(weight)
    return f


def subtitle_color(palette: QPalette) -> QColor:
    """WindowText at SUBTITLE_ALPHA over Window, as an opaque colour: a
    softer text that keeps its contrast in light and dark palettes (Mid /
    PlaceholderText greys are too faint on some dark themes)."""
    fg, bg = palette.color(QPalette.WindowText), palette.color(QPalette.Window)
    a = SUBTITLE_ALPHA
    return QColor.fromRgbF(*(a * f + (1 - a) * b for f, b in
                             ((fg.redF(), bg.redF()), (fg.greenF(), bg.greenF()),
                              (fg.blueF(), bg.blueF()))))


def is_dark(palette: QPalette) -> bool:
    return palette.color(QPalette.Window).lightness() < 128


class HomeTab(QScrollArea):
    new_project_requested = Signal()
    open_project_requested = Signal()          # → Projects tab
    import_images_requested = Signal()         # → the import flow (needs a project)
    figures_requested = Signal()               # → Figures tab (needs a project)
    import_archive_requested = Signal()
    about_requested = Signal()                 # → About tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self._dpr = 0.0
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(14)
        root.addStretch(1)

        # Logo (on a light card when the palette is dark).
        self.logo_card = QFrame()
        self.logo_card.setObjectName("logoCard")
        card = QVBoxLayout(self.logo_card)
        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignCenter)
        card.addWidget(self.logo)
        root.addWidget(self.logo_card, 0, Qt.AlignHCenter)

        self.title = QLabel(f"Version {__version__}")      # the logo shows the name
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setFont(_font(16, QFont.DemiBold))
        root.addWidget(self.title)
        self.subtitle = QLabel(SUBTITLE)
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.setFont(_font(13))
        root.addWidget(self.subtitle)

        self.copyright = QLabel(
            f"<p>Copyright &copy; 2026 {AUTHORS}, {INSTITUTION}</p>"
            "<p>This software is distributed under the <b>GNU General Public License v3 "
            "(GPLv3)</b>.<br>This software is provided without warranty of any kind.</p>")
        self.copyright.setTextFormat(Qt.RichText)
        self.copyright.setAlignment(Qt.AlignCenter)
        self.copyright.setWordWrap(True)
        root.addWidget(self.copyright)

        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch(1)
        self.new_btn = self._big_button("New Project", self.new_project_requested,
                                        "Create a project (with an Unsorted dataset)")
        self.open_btn = self._big_button("Open Project", self.open_project_requested,
                                         "Browse the workspace's projects (Projects tab)")
        self.import_btn = self._big_button("Import Images", self.import_images_requested)
        self.figures_btn = self._big_button("Figures", self.figures_requested)
        self.archive_btn = self._big_button("Import Archive…", self.import_archive_requested,
                                            "Verify a .pcarchive and import it as a new project")
        self.big_buttons = (self.new_btn, self.open_btn, self.import_btn, self.figures_btn,
                            self.archive_btn)
        for b in self.big_buttons:
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        about = QHBoxLayout()
        about.addStretch(1)
        self.about_btn = QPushButton("About / License")
        self.about_btn.clicked.connect(self.about_requested)
        about.addWidget(self.about_btn)
        about.addStretch(1)
        root.addLayout(about)
        root.addStretch(2)
        self.setWidget(container)

        self.set_project_open(False)
        self._apply_theme()

    def _big_button(self, text: str, signal, tip: str = "") -> QPushButton:
        """PysternBlot's home button (``_make_home_button``), palette colours."""
        b = QPushButton(text)
        b.clicked.connect(signal)
        b.setMinimumSize(170, 80)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if tip:
            b.setToolTip(tip)
        return b

    # ------------------------------------------------------------- state
    def set_project_open(self, is_open: bool) -> None:
        """Import Images and Figures need an open project."""
        for b, tip in ((self.import_btn, "Register image files in the open project "
                                         "(by reference, never copied)"),
                       (self.figures_btn, "Open the figure editor")):
            b.setEnabled(is_open)
            b.setToolTip(tip if is_open else NEEDS_PROJECT)

    # ------------------------------------------------------------- theme
    def _apply_theme(self) -> None:
        dark = is_dark(self.palette())
        for b in self.big_buttons:            # palette(...) is resolved when the sheet is set
            b.setStyleSheet("")
            b.setStyleSheet(BUTTON_STYLE)
        self.logo_card.setStyleSheet(CARD_STYLE if dark else "")
        sub = self.subtitle.palette()
        sub.setColor(QPalette.WindowText, subtitle_color(self.palette()))
        self.subtitle.setPalette(sub)
        m = 18 if dark else 0
        self.logo_card.layout().setContentsMargins(m, m, m, m)
        self._update_logo(force=True)

    def _update_logo(self, force: bool = False) -> None:
        dpr = self.devicePixelRatioF()
        if not force and dpr == self._dpr:
            return
        self._dpr = dpr
        pm = logo_pixmap(LOGO_WIDTH, dpr)
        if pm.isNull():
            self.logo.setText("Pycrograph")
        else:
            self.logo.setPixmap(pm)

    def showEvent(self, event) -> None:
        self._update_logo()                         # the screen's ratio is known now
        super().showEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.PaletteChange:
            self._apply_theme()
        elif event.type() == getattr(QEvent.Type, "DevicePixelRatioChange", None):   # Qt ≥ 6.6
            self._update_logo()
        super().changeEvent(event)
