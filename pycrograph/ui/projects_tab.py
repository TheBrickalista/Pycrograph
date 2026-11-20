# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Projects tab: projects of the workspace (new / open / archive / restore) and
the figures of the open project. Emits intents; the main window acts.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..models import Project, ProjectMeta
from ..ui_text import plural

ROLE = Qt.UserRole


class ProjectsTab(QWidget):
    new_project_requested = Signal()
    open_project_requested = Signal(str)          # project id
    archive_requested = Signal(str, bool)         # project id, archived (soft-hide)
    export_archive_requested = Signal()           # .pcarchive of the open project
    import_archive_requested = Signal()
    figure_requested = Signal(str)                # figure id (open project)
    new_figure_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._metas: list[ProjectMeta] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Projects")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        top.addWidget(title)
        top.addStretch(1)
        self.show_archived = QCheckBox("Show archived")
        self.show_archived.toggled.connect(lambda _on: self.refresh_requested())
        top.addWidget(self.show_archived)
        self.new_btn = QPushButton("New Project…")
        self.new_btn.clicked.connect(self.new_project_requested)
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(self._open_selected)
        self.archive_btn = QPushButton("Archive")
        self.archive_btn.clicked.connect(self._archive_selected)
        self.export_archive_btn = QPushButton("Export archive…")
        self.export_archive_btn.setToolTip("Write the open project as a .pcarchive (light: what the "
                                           "figures use; complete: every raw file)")
        self.export_archive_btn.clicked.connect(self.export_archive_requested)
        self.export_archive_btn.setEnabled(False)
        self.import_archive_btn = QPushButton("Import archive…")
        self.import_archive_btn.setToolTip("Verify a .pcarchive and import it as a new project")
        self.import_archive_btn.clicked.connect(self.import_archive_requested)
        for b in (self.new_btn, self.open_btn, self.archive_btn, self.export_archive_btn,
                  self.import_archive_btn):
            top.addWidget(b)
        lay.addLayout(top)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Modified", "Created"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._open_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.table, 2)

        ftop = QHBoxLayout()
        self.fig_title = QLabel("Figures")
        self.fig_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        ftop.addWidget(self.fig_title)
        ftop.addStretch(1)
        self.new_fig_btn = QPushButton("New Figure…")
        self.new_fig_btn.clicked.connect(self.new_figure_requested)
        ftop.addWidget(self.new_fig_btn)
        lay.addLayout(ftop)
        self.figures = QListWidget()
        self.figures.itemDoubleClicked.connect(
            lambda it: self.figure_requested.emit(it.data(ROLE)))
        lay.addWidget(self.figures, 1)
        self._lister = None
        self._update_buttons()

    # The main window provides the listing (workspace access stays there).
    def set_lister(self, lister) -> None:
        self._lister = lister

    def refresh_requested(self) -> None:
        if self._lister is not None:
            self.set_projects(self._lister(self.show_archived.isChecked()))

    def set_projects(self, metas: list[ProjectMeta]) -> None:
        self._metas = list(metas)
        self.table.setRowCount(0)
        for m in self._metas:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = QTableWidgetItem(m.name + ("  (archived)" if m.is_archived else ""))
            name.setData(ROLE, m.id)
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(m.modified_utc or ""))
            self.table.setItem(r, 2, QTableWidgetItem(m.created_utc))
        self._update_buttons()

    def set_open_project(self, project: Optional[Project]) -> None:
        self.export_archive_btn.setEnabled(project is not None)
        self.figures.clear()
        self.new_fig_btn.setEnabled(project is not None)
        if project is None:
            self.fig_title.setText("Figures (no project open)")
            return
        self.fig_title.setText(f"Figures of “{project.project.name}”")
        for f in project.figures:
            if f.hidden:
                continue
            it = QListWidgetItem(f"{f.name}  —  {plural(len(f.panels), 'panel')}")
            it.setData(ROLE, f.id)
            self.figures.addItem(it)

    def _selected_meta(self) -> Optional[ProjectMeta]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        pid = self.table.item(rows[0].row(), 0).data(ROLE)
        return next((m for m in self._metas if m.id == pid), None)

    def _update_buttons(self) -> None:
        m = self._selected_meta()
        self.open_btn.setEnabled(m is not None)
        self.archive_btn.setEnabled(m is not None)
        self.archive_btn.setText("Restore" if m is not None and m.is_archived else "Archive")

    def _open_selected(self) -> None:
        m = self._selected_meta()
        if m is not None:
            self.open_project_requested.emit(m.id)

    def _archive_selected(self) -> None:
        m = self._selected_meta()
        if m is not None:
            self.archive_requested.emit(m.id, not m.is_archived)
