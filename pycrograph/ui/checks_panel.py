# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Checks panel (Figures tab inspector): the figure's consistency issues,
grouped by level (Critical / Major / Minor, with counts), kept up to date
while editing. Clicking an issue selects its panels on the canvas.

Levels are never shown by colour alone: every chip and group carries its
icon and its name. The chip colours have their own text colour (white on
red / orange, dark on amber), so they read the same in light and dark mode.
"""

from __future__ import annotations

import html
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from ..consistency import LEVEL_ICON, LEVEL_LABEL, LEVELS, Issue, counts

# (background, text): contrast >= 4.5:1 in both themes (the chip has its own background)
LEVEL_COLOURS = {"critical": ("#b91c1c", "#ffffff"),      # red, white text (6.5:1)
                 "major": ("#c2410c", "#ffffff"),         # orange, white text (5.2:1)
                 "minor": ("#fbbf24", "#1f2937")}         # amber, dark text (9.6:1)


def level_chip_html(level: str, n: Optional[int] = None) -> str:
    bg, fg = LEVEL_COLOURS[level]
    text = f"{LEVEL_ICON[level]} {LEVEL_LABEL[level]}" + (f" {n}" if n is not None else "")
    return (f'<span style="background:{bg}; color:{fg}; padding:1px 6px; '
            f'border-radius:3px;">&nbsp;{html.escape(text)}&nbsp;</span>')


def counts_html(issues: list[Issue]) -> str:
    """Chips for the levels present, or 'No issue'."""
    c = counts(issues)
    chips = [level_chip_html(lv, n) for lv, n in c.items() if n]
    return " ".join(chips) if chips else "✓ No issue"


def counts_text(issues: list[Issue]) -> str:
    """Plain text for the status bar: '⛔ 1 critical · ⚠ 3 major'."""
    c = counts(issues)
    parts = [f"{LEVEL_ICON[lv]} {n} {lv}" for lv, n in c.items() if n]
    return " · ".join(parts) if parts else "✓ no issue"


class ChecksPanel(QWidget):
    """`run(full)` is called by the panel's buttons; the Figures tab calls
    `show_issues` with the result of each (live or full) check."""

    full_check_requested = Signal()
    live_toggled = Signal(bool)

    def __init__(self, select: Callable[[list[str]], None], parent=None):
        super().__init__(parent)
        self._select = select
        self.issues: list[Issue] = []
        self.full = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        self.counts = QLabel()
        self.counts.setTextFormat(Qt.RichText)
        top.addWidget(self.counts, 1)
        self.full_btn = QPushButton("Full check")
        self.full_btn.setToolTip("Also look for saturated pixels in every crop (reads the images)")
        self.full_btn.clicked.connect(self.full_check_requested)
        top.addWidget(self.full_btn)
        lay.addLayout(top)
        self.live_cb = QCheckBox("Check while editing")
        self.live_cb.setChecked(True)
        self.live_cb.setToolTip("Re-check half a second after each change (saturation: Full check)")
        self.live_cb.toggled.connect(self.live_toggled)
        lay.addWidget(self.live_cb)
        self.scope = QLabel()
        self.scope.setStyleSheet("color: #6b7280;")
        lay.addWidget(self.scope)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.itemClicked.connect(self._on_click)
        lay.addWidget(self.tree, 1)
        self.show_issues([], full=False)

    def show_issues(self, issues: list[Issue], full: bool) -> None:
        self.issues, self.full = list(issues), full
        self.counts.setText(counts_html(issues))
        self.scope.setText("Full check (saturation included)" if full else
                           "Live check — saturation only with Full check")
        self.tree.clear()
        bold = QFont()
        bold.setBold(True)
        for lv in LEVELS:
            mine = [i for i in issues if i.level == lv]
            if not mine:
                continue
            bg, fg = LEVEL_COLOURS[lv]
            head = QTreeWidgetItem([f"{LEVEL_ICON[lv]} {LEVEL_LABEL[lv]} ({len(mine)})"])
            head.setFont(0, bold)
            head.setBackground(0, QBrush(QColor(bg)))
            head.setForeground(0, QBrush(QColor(fg)))
            head.setData(0, Qt.UserRole, [p for i in mine for p in i.panel_ids])
            self.tree.addTopLevelItem(head)
            for i in mine:
                it = QTreeWidgetItem([f"{LEVEL_ICON[lv]} {i.message}"])
                it.setToolTip(0, f"{LEVEL_LABEL[lv]} · {i.code} ({i.rule})\n{i.message}")
                it.setData(0, Qt.UserRole, list(i.panel_ids))
                head.addChild(it)
            head.setExpanded(True)

    def level_counts(self) -> dict[str, int]:
        return counts(self.issues)

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        ids = item.data(0, Qt.UserRole) or []
        if ids:
            self._select(list(dict.fromkeys(ids)))
