# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Left panel: OMERO-style virtual tree (Project › Dataset › Image), search /
rating filter, metadata summary and per-image annotations (tags, rating,
key–value pairs). Emits intents; the main window changes the model.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..models import ImageRef, Project
from ..storage import search_images

from .image_tree import ROLE, STATUS_TEXT, metadata_html, populate_image_tree


class LibraryPanel(QWidget):
    image_selected = Signal(str)                 # image id ("" = none)
    annotation_committed = Signal(str, str, object)   # image id, field, value
    context_action = Signal(str, str, str)       # action, item kind, id

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._thumbs: dict[str, QIcon] = {}
        self._status: dict[str, str] = {}
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self.title = QLabel("No project")
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(self.title)

        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, tag, key/value, channel…")
        self.search.setClearButtonEnabled(True)
        self.min_rating = QComboBox()
        self.min_rating.addItems(["Any ★", "≥ ★", "≥ ★★", "≥ ★★★", "≥ ★★★★", "★★★★★"])
        filt.addWidget(self.search, 1)
        filt.addWidget(self.min_rating)
        lay.addLayout(filt)

        split = QSplitter(Qt.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(40, 40))
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        split.addWidget(self.tree)

        info = QWidget()
        il = QVBoxLayout(info)
        il.setContentsMargins(0, 0, 0, 0)
        self.meta = QTextBrowser()
        self.meta.setOpenExternalLinks(False)
        self.meta.setMinimumHeight(120)
        il.addWidget(self.meta, 1)

        ann = QHBoxLayout()
        ann.addWidget(QLabel("Tags"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma-separated")
        ann.addWidget(self.tags_edit, 1)
        self.rating = QComboBox()
        self.rating.addItems(["☆ (0)", "★", "★★", "★★★", "★★★★", "★★★★★"])
        self.rating.setToolTip("Rating — pick representative images")
        ann.addWidget(self.rating)
        il.addLayout(ann)

        self.kv = QTableWidget(0, 2)
        self.kv.setHorizontalHeaderLabels(["Key", "Value"])
        self.kv.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.kv.verticalHeader().setVisible(False)
        self.kv.setMaximumHeight(130)
        il.addWidget(self.kv)
        kvb = QHBoxLayout()
        self.kv_add = QPushButton("+ key/value")
        self.kv_del = QPushButton("− remove")
        kvb.addWidget(self.kv_add)
        kvb.addWidget(self.kv_del)
        kvb.addStretch(1)
        il.addLayout(kvb)
        split.addWidget(info)
        split.setSizes([380, 300])
        lay.addWidget(split, 1)

        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.min_rating.currentIndexChanged.connect(lambda _i: self.refresh())
        self.tags_edit.editingFinished.connect(self._commit_tags)
        self.rating.activated.connect(self._commit_rating)
        self.kv.itemChanged.connect(lambda _it: self._commit_kv())
        self.kv_add.clicked.connect(self._add_kv_row)
        self.kv_del.clicked.connect(self._del_kv_row)
        self._set_annotation_enabled(False)

    # ------------------------------------------------------------ model in
    def set_project(self, project: Optional[Project]) -> None:
        self._project = project
        self._thumbs.clear()
        self._status.clear()
        self.title.setText(project.project.name if project else "No project")
        self.refresh()

    def set_thumbnail(self, image_id: str, path) -> None:
        pm = QPixmap(str(path))
        if not pm.isNull():
            self._thumbs[image_id] = QIcon(pm)
            for item in self._items_for(image_id):
                item.setIcon(0, self._thumbs[image_id])

    def set_status(self, image_id: str, status: str) -> None:
        self._status[image_id] = status
        ref = self._project.images.get(image_id) if self._project else None
        if ref is None:
            return
        for item in self._items_for(image_id):
            item.setText(0, ref.filename + STATUS_TEXT.get(status, ""))
        if image_id == self.selected_image_id():
            self._show_metadata(ref)

    def refresh(self) -> None:
        keep_img = self.selected_image_id()
        keep_ds = self.selected_dataset_id()
        p = self._project
        visible = ({r.id for r in search_images(p, self.search.text(), self.min_rating.currentIndex())}
                   if p is not None else set())
        filtering = bool(self.search.text().strip()) or self.min_rating.currentIndex() > 0
        populate_image_tree(self.tree, p, visible, self._thumbs, self._status, filtering)
        target = None
        if keep_img:
            target = next(iter(self._items_for(keep_img, dataset_id=keep_ds)), None) or \
                next(iter(self._items_for(keep_img)), None)
        elif keep_ds:
            target = self._dataset_item(keep_ds)
        if target is not None:
            self.tree.setCurrentItem(target)
        else:
            self._on_current_changed(self.tree.currentItem(), None)

    # ----------------------------------------------------------- selection
    def _data(self, item: Optional[QTreeWidgetItem]):
        return item.data(0, ROLE) if item is not None else None

    def selected_image_id(self) -> str:
        d = self._data(self.tree.currentItem())
        return d[1] if d and d[0] == "image" else ""

    def selected_dataset_id(self) -> str:
        d = self._data(self.tree.currentItem())
        if not d:
            return ""
        if d[0] == "dataset":
            return d[1]
        if d[0] == "image":
            return d[2]
        return ""

    def select_image(self, image_id: str) -> None:
        item = next(iter(self._items_for(image_id)), None)
        if item is not None:
            self.tree.setCurrentItem(item)

    def select_dataset(self, dataset_id: str) -> None:
        item = self._dataset_item(dataset_id)
        if item is not None:
            self.tree.setCurrentItem(item)

    def _items_for(self, image_id: str, dataset_id: str | None = None):
        out = []
        root = self.tree.topLevelItem(0)
        if root is None:
            return out
        for i in range(root.childCount()):
            ds = root.child(i)
            for j in range(ds.childCount()):
                d = self._data(ds.child(j))
                if d and d[1] == image_id and (dataset_id is None or d[2] == dataset_id):
                    out.append(ds.child(j))
        return out

    def _dataset_item(self, dataset_id: str):
        root = self.tree.topLevelItem(0)
        if root is None:
            return None
        for i in range(root.childCount()):
            d = self._data(root.child(i))
            if d and d[1] == dataset_id:
                return root.child(i)
        return None

    def _on_current_changed(self, cur, _prev) -> None:
        d = self._data(cur)
        ref = None
        if d and d[0] == "image" and self._project is not None:
            ref = self._project.images.get(d[1])
        self._show_metadata(ref)
        self._show_annotations(ref)
        self.image_selected.emit(ref.id if ref else "")

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        d = self._data(item)
        menu = QMenu(self)
        actions = [("New dataset…", "new_dataset", "", "")]
        if d and d[0] == "dataset":
            actions += [("Import images into this dataset…", "import", "dataset", d[1]),
                        ("Rename dataset…", "rename_dataset", "dataset", d[1]),
                        ("Hide dataset", "hide_dataset", "dataset", d[1])]
        if d and d[0] == "image":
            actions += [("Add to figure", "add_to_figure", "image", d[1]),
                        ("Relink…", "relink", "image", d[1]),
                        ("Verify file (re-hash)", "verify", "image", d[1]),
                        ("Copy into project", "copy_into", "image", d[1]),
                        ("Add to dataset…", "add_to_dataset", "image", d[1]),
                        ("Remove from this dataset", "remove_from_dataset", "image", f"{d[1]}|{d[2]}")]
        for text, act, kind, ident in actions:
            a = menu.addAction(text)
            a.triggered.connect(lambda _c=False, a_=act, k=kind, i=ident: self.context_action.emit(a_, k, i))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ metadata
    def _show_metadata(self, ref: Optional[ImageRef]) -> None:
        self.meta.setHtml(metadata_html(ref, self._status.get(ref.id, "ok") if ref else "ok"))

    # --------------------------------------------------------- annotations
    def _set_annotation_enabled(self, on: bool) -> None:
        for w in (self.tags_edit, self.rating, self.kv, self.kv_add, self.kv_del):
            w.setEnabled(on)

    def _show_annotations(self, ref: Optional[ImageRef]) -> None:
        self._loading = True
        self._set_annotation_enabled(ref is not None)
        self.tags_edit.setText(", ".join(ref.tags) if ref else "")
        self.rating.setCurrentIndex(ref.rating if ref else 0)
        self.kv.setRowCount(0)
        if ref:
            for k, v in ref.key_values.items():
                r = self.kv.rowCount()
                self.kv.insertRow(r)
                self.kv.setItem(r, 0, QTableWidgetItem(k))
                self.kv.setItem(r, 1, QTableWidgetItem(v))
        self._loading = False

    def _commit_tags(self) -> None:
        iid = self.selected_image_id()
        if not iid or self._loading:
            return
        tags = []
        for t in self.tags_edit.text().split(","):
            t = t.strip()
            if t and t not in tags:
                tags.append(t)
        self.annotation_committed.emit(iid, "tags", tags)

    def _commit_rating(self, index: int) -> None:
        iid = self.selected_image_id()
        if iid and not self._loading:
            self.annotation_committed.emit(iid, "rating", int(index))

    def _commit_kv(self) -> None:
        iid = self.selected_image_id()
        if not iid or self._loading:
            return
        kv = {}
        for r in range(self.kv.rowCount()):
            k = self.kv.item(r, 0)
            v = self.kv.item(r, 1)
            key = k.text().strip() if k else ""
            if key:
                kv[key] = v.text().strip() if v else ""
        self.annotation_committed.emit(iid, "key_values", kv)

    def _add_kv_row(self) -> None:
        self._loading = True
        r = self.kv.rowCount()
        self.kv.insertRow(r)
        self.kv.setItem(r, 0, QTableWidgetItem(""))
        self.kv.setItem(r, 1, QTableWidgetItem(""))
        self._loading = False
        self.kv.editItem(self.kv.item(r, 0))

    def _del_kv_row(self) -> None:
        r = self.kv.currentRow()
        if r >= 0:
            self.kv.removeRow(r)
            self._commit_kv()
