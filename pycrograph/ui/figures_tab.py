# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Figures tab (design step 1): figure selector, page canvas at journal width,
panels rendered by PanelRenderer; select / move / resize / multi-select /
delete; snapshot undo/redo. All model changes go through
figures.FigureEditor (one undo step per gesture, one log entry per panel,
then save).
"""

from __future__ import annotations

import html
from typing import Callable, Iterable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QSplitter, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from ..figures import (
    FigureEditor, create_figure, next_figure_name, panel_aspect, rename_figure,
)
from ..models import Figure, Panel, Project
from ..render import PanelRenderer, RenderError
from .figure_canvas import FigureCanvas

CANVAS_MAX_SIDE = 1024      # on-screen decimation (integer stride, no interpolation)


class FiguresTab(QWidget):
    status_message = Signal(str)

    def __init__(self, renderer: PanelRenderer, save: Callable[[], None],
                 before_gesture: Optional[Callable[[], None]] = None, parent=None):
        super().__init__(parent)
        self.renderer = renderer
        self._save = save
        self._before_gesture = before_gesture or (lambda: None)
        self.project: Optional[Project] = None
        self._editors: dict[str, FigureEditor] = {}
        self._build()
        self._update_enabled()

    # ================================================================== UI
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Figure"))
        self.figure_combo = QComboBox()
        self.figure_combo.setMinimumWidth(200)
        self.figure_combo.currentIndexChanged.connect(self._on_figure_changed)
        bar.addWidget(self.figure_combo)

        def button(action: QAction) -> QToolButton:
            b = QToolButton()
            b.setDefaultAction(action)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            bar.addWidget(b)
            return b

        def action(text: str, slot, shortcut=None, tip: str = "") -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut is not None:
                keys = shortcut if isinstance(shortcut, list) else [shortcut]
                a.setShortcuts([QKeySequence(k) for k in keys])
                a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            if tip:
                a.setToolTip(tip)
            self.addAction(a)
            return a

        self.a_new = action("New Figure…", self.new_figure)
        self.a_rename = action("Rename…", self.rename_current)
        button(self.a_new)
        button(self.a_rename)
        bar.addSpacing(12)
        self.a_add = action("Add Image…", self.add_image_dialog,
                            tip="Add library images as panels (copies their display settings)")
        self.a_delete = action("Delete", self.delete_selected,
                               [QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)])
        self.a_undo = action("Undo", self.undo, QKeySequence.Undo)
        self.a_redo = action("Redo", self.redo, [QKeySequence.Redo, QKeySequence("Ctrl+Shift+Z")])
        self.a_select_all = action("Select All", self.select_all, QKeySequence.SelectAll)
        for a in (self.a_add, self.a_delete, self.a_undo, self.a_redo):
            button(a)
        bar.addSpacing(12)
        self.a_fit = action("Fit", lambda: self.canvas.fit_pages())
        self.a_zoom_in = action("+", lambda: self.canvas.zoom_by(1.25), QKeySequence.ZoomIn)
        self.a_zoom_out = action("−", lambda: self.canvas.zoom_by(0.8), QKeySequence.ZoomOut)
        for a in (self.a_fit, self.a_zoom_in, self.a_zoom_out):
            button(a)
        bar.addStretch(1)
        self.page_label = QLabel("")
        self.page_label.setStyleSheet("color: #6b7280;")
        bar.addWidget(self.page_label)
        lay.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.canvas = FigureCanvas()
        self.canvas.set_commit_callback(self._on_geometry_commit)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        split.addWidget(self.canvas)
        self.info = QTextBrowser()
        self.info.setMinimumWidth(260)
        split.addWidget(self.info)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([900, 300])
        lay.addWidget(split, 1)

    def _update_enabled(self) -> None:
        has_project = self.project is not None
        fig = self.current_figure()
        ed = self.current_editor()
        sel = bool(self.canvas.selected_ids()) if fig else False
        self.figure_combo.setEnabled(has_project and bool(self.project.figures))
        self.a_new.setEnabled(has_project)
        self.a_rename.setEnabled(fig is not None)
        self.a_add.setEnabled(has_project and bool(self.project.images))
        self.a_delete.setEnabled(sel)
        self.a_select_all.setEnabled(fig is not None)
        self.a_undo.setEnabled(bool(ed and ed.history.can_undo))
        self.a_redo.setEnabled(bool(ed and ed.history.can_redo))
        self.a_undo.setText(f"Undo {ed.history.undo_label}" if ed and ed.history.can_undo else "Undo")
        self.a_redo.setText(f"Redo {ed.history.redo_label}" if ed and ed.history.can_redo else "Redo")

    # ============================================================ project
    def set_project(self, project: Optional[Project]) -> None:
        self.project = project
        self._editors.clear()
        self.renderer.close()
        self._reload_combo()

    def _reload_combo(self, select_id: Optional[str] = None) -> None:
        p = self.project
        keep = select_id or (self.current_figure().id if self.current_figure() else None)
        self.figure_combo.blockSignals(True)
        self.figure_combo.clear()
        if p is not None:
            for f in p.figures:
                if not f.hidden:
                    self.figure_combo.addItem(f.name, f.id)
            if keep is not None:
                i = self.figure_combo.findData(keep)
                if i >= 0:
                    self.figure_combo.setCurrentIndex(i)
        self.figure_combo.blockSignals(False)
        self._on_figure_changed()

    def current_figure(self) -> Optional[Figure]:
        if self.project is None:
            return None
        fid = self.figure_combo.currentData()
        return self.project.figure(fid) if fid else None

    def current_editor(self) -> Optional[FigureEditor]:
        fig = self.current_figure()
        if fig is None:
            return None
        ed = self._editors.get(fig.id)
        if ed is None:
            ed = FigureEditor(self.project, fig, self._save)
            self._editors[fig.id] = ed
        return ed

    def select_figure(self, figure_id: str) -> None:
        i = self.figure_combo.findData(figure_id)
        if i >= 0:
            self.figure_combo.setCurrentIndex(i)

    def _on_figure_changed(self, *_args) -> None:
        self.refresh(selected=set())

    # ============================================================ figures
    def new_figure(self, name: Optional[str] = None) -> Optional[Figure]:
        if self.project is None:
            return None
        if name is None:
            name, ok = QInputDialog.getText(self, "New figure", "Figure name:",
                                            text=next_figure_name(self.project))
            if not ok:
                return None
        self._before_gesture()
        fig = create_figure(self.project, name or next_figure_name(self.project))
        self._save()
        self._reload_combo(fig.id)
        return fig

    def rename_current(self) -> None:
        fig = self.current_figure()
        if fig is None:
            return
        name, ok = QInputDialog.getText(self, "Rename figure", "Name:", text=fig.name)
        if ok and name.strip():
            self._before_gesture()
            rename_figure(self.project, fig, name)
            self._save()
            self._reload_combo(fig.id)

    # ============================================================= panels
    def add_images(self, image_ids: Iterable[str]) -> list[Panel]:
        """'Add to figure': panels with a copy of each image's display
        settings. Creates a figure when the project has none."""
        if self.project is None:
            return []
        refs = [self.project.images[i] for i in image_ids if i in self.project.images]
        if not refs:
            return []
        self._before_gesture()
        if self.current_figure() is None:
            self.new_figure(next_figure_name(self.project))
        ed = self.current_editor()
        panels = ed.add_images(refs)
        self.refresh(selected={p.id for p in panels})
        self.status_message.emit(f"Added {len(panels)} panel(s) to {ed.figure.name}")
        return panels

    def add_image_dialog(self) -> None:
        p = self.project
        if p is None or not p.images:
            return
        refs = sorted(p.images.values(), key=lambda r: r.filename.lower())
        labels = [r.filename for r in refs]
        choice, ok = QInputDialog.getItem(self, "Add image", "Image:", labels, 0, False)
        if ok:
            self.add_images([refs[labels.index(choice)].id])

    def delete_selected(self) -> int:
        ed = self.current_editor()
        ids = self.canvas.selected_ids()
        if ed is None or not ids:
            return 0
        self._before_gesture()
        n = ed.delete_panels(ids)
        self.refresh(selected=set())
        return n

    def select_all(self) -> None:
        self.canvas.select_all()

    def undo(self) -> None:
        ed = self.current_editor()
        if ed is not None:
            self._before_gesture()
            keep = set(self.canvas.selected_ids())
            if ed.undo():
                self.refresh(selected=keep)

    def redo(self) -> None:
        ed = self.current_editor()
        if ed is not None:
            self._before_gesture()
            keep = set(self.canvas.selected_ids())
            if ed.redo():
                self.refresh(selected=keep)

    def _on_geometry_commit(self, changes, operation: str) -> None:
        ed = self.current_editor()
        if ed is None:
            return
        self._before_gesture()
        label = "Resize panel" if operation == "figure.panel.resize" else \
            f"Move {len(changes)} panel(s)"
        ed.set_geometry(changes, operation, label)
        # Items already show the new geometry; rebuilding the scene here would
        # delete the item whose mouse event is being handled. Sync later.
        QTimer.singleShot(0, self._sync_after_geometry)

    def _sync_after_geometry(self) -> None:
        self._update_info()
        self._update_enabled()

    # ============================================================ drawing
    def _pixmap(self, panel: Panel) -> tuple[Optional[QPixmap], str]:
        ref = self.project.images.get(panel.image_id) if self.project else None
        if ref is None:
            return None, "image not in project"
        try:
            rgb = self.renderer.render(ref, panel, max_side=CANVAS_MAX_SIDE)
        except RenderError as exc:
            return None, f"{ref.filename}\n{exc}"
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img), ""

    def _aspect(self, panel: Panel) -> float:
        ref = self.project.images.get(panel.image_id) if self.project else None
        return panel_aspect(panel, ref)

    def refresh(self, selected: Optional[set[str]] = None) -> None:
        fig = self.current_figure()
        if selected is None:
            selected = set(self.canvas.selected_ids())
        self.canvas.set_figure(fig, self._pixmap, self._aspect, selected)
        if fig is not None:
            w, h = fig.page.size_mm
            pages = f" × {fig.page.n_pages} pages" if fig.page.n_pages > 1 else ""
            self.page_label.setText(f"{fig.page.preset.name} · {w:g} × {h:g} mm{pages}")
        else:
            self.page_label.setText("")
        self._update_info()
        self._update_enabled()

    def _on_selection_changed(self, _ids: list) -> None:
        self._update_info()
        self._update_enabled()

    def _update_info(self) -> None:
        fig = self.current_figure()
        if self.project is None:
            self.info.setHtml("<i>No project open.</i>")
            return
        if fig is None:
            self.info.setHtml("<i>No figure yet. Use New Figure…, or “Add to figure” on a library "
                              "image.</i>")
            return
        ids = self.canvas.selected_ids()
        if not ids:
            self.info.setHtml(f"<b>{html.escape(fig.name)}</b><br>{len(fig.panels)} panel(s)."
                              "<br><small>Click, Ctrl-click or drag a rectangle to select.</small>")
            return
        if len(ids) > 1:
            self.info.setHtml(f"<b>{len(ids)} panels selected</b>")
            return
        panel = fig.panel(ids[0])
        if panel is None:
            return
        ref = self.project.images.get(panel.image_id)
        rows = [("Image", html.escape(ref.filename) if ref else "missing"),
                ("Position", f"{panel.x_mm:.1f}, {panel.y_mm:.1f} mm"),
                ("Size", f"{panel.width_mm:.1f} × {panel.height_mm:.1f} mm")]
        if panel.crop is not None:
            c = panel.crop
            rows.append(("Crop (raw px)", f"x {c.x:g}–{c.x + c.w:g}, y {c.y:g}–{c.y + c.h:g}"))
            crop_w = c.h if panel.rotation_deg in (90, 270) else c.w
            ppi = crop_w / (panel.width_mm / 25.4)
            dpi = fig.page.preset.dpi
            warn = "" if ppi >= dpi else f" <span style='color:#b45309'>(&lt; {dpi} dpi: upsampled at export)</span>"
            rows.append(("Effective resolution", f"{ppi:.0f} ppi{warn}"))
        rows.append(("T / Z", f"{panel.t + 1} / {panel.z + 1}"))
        if panel.rotation_deg or panel.flip_horizontal or panel.flip_vertical:
            flips = "+".join(n for n, on in (("H", panel.flip_horizontal), ("V", panel.flip_vertical)) if on)
            rows.append(("Orientation", f"{panel.rotation_deg}°" + (f" flip {flips}" if flips else "")))
        body = "".join(f"<tr><td style='color:#6b7280;padding-right:8px'>{k}</td><td>{v}</td></tr>"
                       for k, v in rows)
        self.info.setHtml(f"<table style='font-size:11px'>{body}</table>")
