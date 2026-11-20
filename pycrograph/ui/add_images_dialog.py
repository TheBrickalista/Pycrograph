# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Add Images picker of the Figures tab: the project's image tree (same as the
Library tab, ui/image_tree.py) with search, rating filter, multi-selection
and a preview pane.

- Ctrl/Cmd-click and Shift-click select several images; selecting a dataset
  (or the project) means all its visible images. `chosen_ids()` lists the
  images in TREE ORDER, each once (an image in several datasets is added
  once).
- Missing source files are greyed and cannot be selected (reason in the
  tooltip); changed / modified ones can, with the Library's status suffix.
- Double-click on an image adds it and closes; "Add N images" adds the
  selection.
- "Import from disk…" does not import: it emits `import_requested(dataset
  id)`; the main window runs its import flow (by reference) and calls
  `after_import(ids)`, which refreshes the tree and pre-selects them.
- The dialog size, the expanded datasets and the last dataset used are
  remembered for the session, per project.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QPushButton, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from ..models import Project
from ..storage import search_images
from ..ui_text import plural
from .image_tree import item_data, iter_image_items, metadata_html, populate_image_tree

RATINGS = ["Any ★", "≥ ★", "≥ ★★", "≥ ★★★", "≥ ★★★★", "★★★★★"]

# Session memory, per project id: {"size": QSize, "collapsed": set[dataset id],
# "dataset": dataset id}.
_SESSION: dict[str, dict] = {}


class AddImagesDialog(QDialog):
    import_requested = Signal(str)          # dataset id ("" = let the main window choose)

    def __init__(self, project: Project, thumbs: Optional[dict[str, QIcon]] = None,
                 status: Optional[dict[str, str]] = None,
                 status_provider: Optional[Callable[[], tuple[dict, dict]]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add images to the figure")
        self.project = project
        self._thumbs = thumbs or {}
        self._status = status or {}
        self._provider = status_provider             # refresh thumbs / status after an import
        self._chosen: list[str] = []
        mem = _SESSION.setdefault(project.project.id, {})
        lay = QVBoxLayout(self)

        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, tag, key/value, channel…")
        self.search.setClearButtonEnabled(True)
        self.min_rating = QComboBox()
        self.min_rating.addItems(RATINGS)
        filt.addWidget(self.search, 1)
        filt.addWidget(self.min_rating)
        lay.addLayout(filt)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(40, 40))
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        split.addWidget(self.tree)
        pane = QWidget()
        pl = QVBoxLayout(pane)
        pl.setContentsMargins(6, 0, 0, 0)
        self.preview = QLabel()
        self.preview.setFixedSize(160, 160)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: #2b2b2b; border-radius: 6px;")
        pl.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.info = QTextBrowser()
        pl.addWidget(self.info, 1)
        split.addWidget(pane)
        split.setSizes([420, 300])
        lay.addWidget(split, 1)

        row = QHBoxLayout()
        self.import_btn = QPushButton("Import from disk…")
        self.import_btn.setToolTip("Register files by reference (no copy) into a dataset, then pick them")
        self.import_btn.clicked.connect(self.request_import)
        row.addWidget(self.import_btn)
        row.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.add_btn = self.buttons.addButton("Add 0 images", QDialogButtonBox.AcceptRole)
        self.add_btn.setDefault(True)
        self.buttons.accepted.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        lay.addLayout(row)

        self.search.textChanged.connect(lambda _t: self.refresh())
        self.min_rating.currentIndexChanged.connect(lambda _i: self.refresh())
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._show(cur))
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemCollapsed.connect(self._remember_expansion)
        self.tree.itemExpanded.connect(self._remember_expansion)

        self.resize(mem.get("size") or QSize(820, 560))
        self.refresh()
        last = mem.get("dataset")
        if last:
            ds = self._dataset_item(last)
            if ds is not None:
                self.tree.setCurrentItem(ds, 0, QItemSelectionModel.NoUpdate)
                self.tree.scrollToItem(ds)

    # --------------------------------------------------------------- tree
    def refresh(self, preselect: Optional[list[str]] = None) -> None:
        p = self.project
        visible = {r.id for r in search_images(p, self.search.text(), self.min_rating.currentIndex())}
        filtering = bool(self.search.text().strip()) or self.min_rating.currentIndex() > 0
        populate_image_tree(self.tree, p, visible, self._thumbs, self._status, filtering,
                            disable_missing=True)
        collapsed = _SESSION[p.project.id].get("collapsed", set())
        root = self.tree.topLevelItem(0)
        if root is not None:
            for i in range(root.childCount()):
                ds = root.child(i)
                d = item_data(ds)
                if d and d[1] in collapsed:
                    ds.setExpanded(False)
        if preselect:
            first = None
            for it in iter_image_items(self.tree):
                d = item_data(it)
                if d[1] in preselect and it.flags() & Qt.ItemIsSelectable:
                    it.setSelected(True)
                    first = first or it
            if first is not None:
                self.tree.setCurrentItem(first, 0, QItemSelectionModel.NoUpdate)
                self.tree.scrollToItem(first)
        self._update_buttons()

    def _dataset_item(self, dataset_id: str) -> Optional[QTreeWidgetItem]:
        root = self.tree.topLevelItem(0)
        if root is None:
            return None
        for i in range(root.childCount()):
            d = item_data(root.child(i))
            if d and d[1] == dataset_id:
                return root.child(i)
        return None

    def _remember_expansion(self, item: QTreeWidgetItem) -> None:
        d = item_data(item)
        if not d or d[0] != "dataset":
            return
        collapsed = _SESSION[self.project.project.id].setdefault("collapsed", set())
        if item.isExpanded():
            collapsed.discard(d[1])
        else:
            collapsed.add(d[1])

    # ----------------------------------------------------------- selection
    def selected_image_ids(self) -> list[str]:
        """Selected images — a selected dataset (or the project) counts as
        all its visible, selectable images — in tree order, each once."""
        root = self.tree.topLevelItem(0)
        if root is None:
            return []
        out: list[str] = []
        for it in iter_image_items(self.tree):
            if not it.flags() & Qt.ItemIsSelectable or it.isHidden():
                continue
            ds = it.parent()
            if it.isSelected() or ds.isSelected() or root.isSelected():
                iid = item_data(it)[1]
                if iid not in out:
                    out.append(iid)
        return out

    def current_dataset_id(self) -> str:
        d = item_data(self.tree.currentItem())
        if not d:
            return ""
        return d[1] if d[0] == "dataset" else d[2] if d[0] == "image" else ""

    def chosen_ids(self) -> list[str]:
        return list(self._chosen)

    def _update_buttons(self) -> None:
        n = len(self.selected_image_ids())
        self.add_btn.setText(f"Add {n} image{'s' if n != 1 else ''}")
        self.add_btn.setEnabled(n > 0)

    def _remember(self) -> None:
        mem = _SESSION[self.project.project.id]
        mem["size"] = self.size()
        ds = self.current_dataset_id()
        if ds:
            mem["dataset"] = ds

    def _accept_selection(self) -> None:
        self._chosen = self.selected_image_ids()
        if self._chosen:
            self._remember()
            self.accept()

    def _double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        d = item_data(item)
        if d and d[0] == "image" and item.flags() & Qt.ItemIsSelectable:
            self._chosen = [d[1]]
            self._remember()
            self.accept()

    def reject(self) -> None:
        self._remember()
        super().reject()

    # ------------------------------------------------------------- preview
    def _show(self, item: Optional[QTreeWidgetItem]) -> None:
        d = item_data(item)
        ref = self.project.images.get(d[1]) if d and d[0] == "image" else None
        if ref is None:
            self.preview.clear()
            if d and d[0] == "dataset":
                ds = self.project.dataset(d[1])
                n = len(ds.image_ids) if ds else 0
                self.info.setHtml(f"<b>{ds.name if ds else ''}</b><br>{plural(n, 'image')}. Selecting the "
                                  "dataset adds all its visible images.")
            else:
                self.info.setHtml("<i>Select images or a dataset.</i>")
            return
        icon = self._thumbs.get(ref.id)
        if icon is not None:
            self.preview.setPixmap(icon.pixmap(QSize(156, 156)))
        else:
            self.preview.setText("no thumbnail")
        self.info.setHtml(metadata_html(ref, self._status.get(ref.id, "ok")))

    # -------------------------------------------------------------- import
    def request_import(self) -> None:
        """Ask the main window to import (by reference), into a dataset the
        user picks — the current one offered first."""
        visible = [ds for ds in self.project.datasets if not ds.hidden]
        target = ""
        if len(visible) > 1:
            current = self.current_dataset_id() or _SESSION[self.project.project.id].get("dataset", "")
            names = [ds.name for ds in visible]
            start = next((i for i, ds in enumerate(visible) if ds.id == current), 0)
            choice, ok = QInputDialog.getItem(self, "Import into", "Dataset:", names, start, False)
            if not ok:
                return
            target = visible[names.index(choice)].id
        elif visible:
            target = visible[0].id
        self.import_requested.emit(target)

    def after_import(self, ids: list[str]) -> None:
        """Refresh after the main window imported `ids`; pre-select them."""
        if self._provider is not None:
            self._thumbs, self._status = self._provider()
        self.search.blockSignals(True)
        self.search.clear()                              # make sure they are visible
        self.search.blockSignals(False)
        self.min_rating.blockSignals(True)
        self.min_rating.setCurrentIndex(0)
        self.min_rating.blockSignals(False)
        self.tree.clearSelection()
        self.refresh(preselect=ids)
