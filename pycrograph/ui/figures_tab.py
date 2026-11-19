# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Figures tab: figure selector, page canvas at journal width, panels
rendered by PanelRenderer (select / move / resize / multi-select / delete;
snapshot undo/redo) and the inspector (Preview · Info). A second toolbar
row holds the layout tools (§2.8): align, distribute, grid with gap, same
size, align magnification, page setup and the auto-layouts (split
channels, montage, condition grid). Arrow keys nudge the selection.

All model changes go through figures.FigureEditor: one undo step per
gesture, one log entry per panel, then save. Continuous controls (sliders,
mini-view drag, wheel zoom) apply every tick to the model and the canvas,
and commit when released, after 700 ms without a tick, or when anything
else happens (selection change, another control, undo, tab change).
"""

from __future__ import annotations

import html
from typing import Callable, Iterable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
    QSplitter, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from .. import figures as F
from .. import layout as L
from .. import viewport as V
from ..figures import FigureEditor, create_figure, next_figure_name, rename_figure
from ..models import Figure, ImageRef, Panel, Project
from ..render import PanelRenderer, RenderError, crop_bounds
from ..preferences import Preferences
from .figure_canvas import FigureCanvas, PanelVisual
from .page_setup import PageSetupDialog
from .panel_inspector import PreviewInspector

CANVAS_MAX_SIDE = 1024      # on-screen decimation (integer stride, no interpolation)
IDLE_COMMIT_MS = 700        # same as the library / PysternBlot gesture commit


class FiguresTab(QWidget):
    status_message = Signal(str)
    image_defaults_changed = Signal(str)          # image id

    def __init__(self, renderer: PanelRenderer, save: Callable[[], None],
                 before_gesture: Optional[Callable[[], None]] = None,
                 prefs: Optional[Preferences] = None,
                 save_prefs: Optional[Callable[[], None]] = None, parent=None):
        super().__init__(parent)
        self.renderer = renderer
        self.prefs = prefs or Preferences()
        self._save_prefs = save_prefs or (lambda: None)
        self._save = save
        self._before_gesture = before_gesture or (lambda: None)
        self.project: Optional[Project] = None
        self._editors: dict[str, FigureEditor] = {}
        self.clip_rendering: Optional[list[dict]] = None
        self.clip_viewport: Optional[dict] = None
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_COMMIT_MS)
        self._idle.timeout.connect(self.end_live)
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
        lay.addLayout(self._build_layout_bar(action))

        split = QSplitter(Qt.Horizontal)
        self.canvas = FigureCanvas()
        self.canvas.set_commit_callback(self._on_geometry_commit)
        self.canvas.nudge_requested.connect(self.nudge)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        split.addWidget(self.canvas)
        self.inspector_tabs = QTabWidget()
        self.inspector = PreviewInspector(self)
        self.info = QTextBrowser()
        self.inspector_tabs.addTab(self.inspector, "Preview")
        self.inspector_tabs.addTab(self.info, "Info")
        self.inspector_tabs.setMinimumWidth(400)
        split.addWidget(self.inspector_tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([850, 420])
        lay.addWidget(split, 1)

    def _build_layout_bar(self, action) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Layout"))
        self.layout_actions: dict[str, QAction] = {}

        def menu_button(text: str, items) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setPopupMode(QToolButton.InstantPopup)
            m = QMenu(b)
            for key, label, slot in items:
                if key is None:
                    m.addSeparator()
                    continue
                a = action(label, slot)
                m.addAction(a)
                self.layout_actions[key] = a
            b.setMenu(m)
            bar.addWidget(b)
            return b

        menu_button("Align ▾", [
            ("align_left", "Align left", lambda: self.align("left")),
            ("align_right", "Align right", lambda: self.align("right")),
            ("align_top", "Align top", lambda: self.align("top")),
            ("align_bottom", "Align bottom", lambda: self.align("bottom")),
            ("align_hcentre", "Align horizontal centres", lambda: self.align("hcentre")),
            ("align_vcentre", "Align vertical centres", lambda: self.align("vcentre")),
            (None, "", None),
            ("distribute_h", "Distribute horizontally", lambda: self.distribute("h")),
            ("distribute_v", "Distribute vertically", lambda: self.distribute("v")),
        ])
        a = action("Grid", self.snap_to_grid,
                   tip="Rows and columns from the current positions, gap between cells, "
                       "top-left panel fixed")
        self.layout_actions["grid"] = a
        b = QToolButton()
        b.setDefaultAction(a)
        bar.addWidget(b)
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0, 50.0)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setSingleStep(0.5)
        self.gap_spin.setSuffix(" mm gap")
        self.gap_spin.setValue(self.prefs.layout_gap_mm)
        self.gap_spin.setToolTip("Gap used by Grid and the auto-layouts")
        self.gap_spin.valueChanged.connect(self._on_gap_changed)
        bar.addWidget(self.gap_spin)
        menu_button("Size ▾", [
            ("same_width", "Same width", lambda: self.same_size("width")),
            ("same_height", "Same height", lambda: self.same_size("height")),
            ("same_both", "Same width && height", lambda: self.same_size("both")),
        ])
        for key, text, slot, tip in (
                ("align_magnification", "Align magnification", self.align_magnification,
                 "Resize panels so µm per page-mm equals the top-left panel's "
                 "(equal scale bars look equal); crops unchanged"),
                ("page_setup", "Page setup…", self.page_setup_dialog, "")):
            a = action(text, slot, tip=tip)
            self.layout_actions[key] = a
            b = QToolButton()
            b.setDefaultAction(a)
            bar.addWidget(b)
        menu_button("Auto ▾", [
            ("split_channels", "Split channels", self.split_channels),
            ("montage", "Montage of T or Z…", self.montage_dialog),
            ("condition_grid", "Condition grid", self.condition_grid),
        ])
        bar.addStretch(1)
        return bar

    def _update_enabled(self) -> None:
        has_project = self.project is not None
        fig = self.current_figure()
        ed = self._editors.get(fig.id) if fig else None
        sel = bool(self.canvas.selected_ids()) if fig else False
        self.figure_combo.setEnabled(has_project and bool(self.project.figures))
        self.a_new.setEnabled(has_project)
        self.a_rename.setEnabled(fig is not None)
        self.a_add.setEnabled(has_project and bool(self.project.images))
        self.a_delete.setEnabled(sel)
        self.a_select_all.setEnabled(fig is not None)
        can_undo = bool(ed and (ed.history.can_undo or ed.has_pending))
        self.a_undo.setEnabled(can_undo)
        self.a_redo.setEnabled(bool(ed and ed.history.can_redo))
        self.a_undo.setText(f"Undo {ed.history.undo_label}" if ed and ed.history.can_undo else "Undo")
        self.a_redo.setText(f"Redo {ed.history.redo_label}" if ed and ed.history.can_redo else "Redo")
        n = len(self.canvas.selected_ids()) if fig else 0
        need = {"align_left": 2, "align_right": 2, "align_top": 2, "align_bottom": 2,
                "align_hcentre": 2, "align_vcentre": 2, "distribute_h": 3, "distribute_v": 3,
                "grid": 1, "same_width": 2, "same_height": 2, "same_both": 2,
                "align_magnification": 2, "split_channels": 1, "montage": 1,
                "condition_grid": 1}
        for key, a in getattr(self, "layout_actions", {}).items():
            a.setEnabled(fig is not None and (key == "page_setup" or n >= need.get(key, 1)))

    # ============================================================ project
    def set_project(self, project: Optional[Project]) -> None:
        self.flush()
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

    def ref(self, panel: Panel) -> Optional[ImageRef]:
        return self.project.images.get(panel.image_id) if self.project else None

    def selected_panels(self) -> list[Panel]:
        """Selected panels in figure order (the first is the reference)."""
        fig = self.current_figure()
        if fig is None:
            return []
        ids = set(self.canvas.selected_ids())
        return [p for p in fig.panels if p.id in ids]

    def select_figure(self, figure_id: str) -> None:
        i = self.figure_combo.findData(figure_id)
        if i >= 0:
            self.figure_combo.setCurrentIndex(i)

    def _on_figure_changed(self, *_args) -> None:
        self.flush()
        self.refresh(selected=set())

    # ============================================================ gestures
    def flush(self) -> None:
        """Commit any open continuous gesture of any figure."""
        self._idle.stop()
        for ed in self._editors.values():
            if ed.has_pending:
                ed.flush()
        self._update_enabled()

    def end_live(self) -> None:
        """A continuous gesture ended (release / idle): commit, refresh the
        histograms and the mini-view on what the panels now show."""
        self.flush()
        self.inspector.refresh_values()
        self.inspector.refresh_planes()
        self._update_info()

    def apply(self, fn: Callable[[Panel, ImageRef], bool], label: str, key: Optional[str] = None,
              final: bool = True, what: str = "", operation: str = "figure.panel.set",
              panel_ids: Optional[list[str]] = None) -> tuple[int, int]:
        """Apply fn to every selected panel (FigureEditor.edit) and redraw."""
        ed = self.current_editor()
        if ed is None:
            return 0, 0
        ids = panel_ids if panel_ids is not None else [p.id for p in self.selected_panels()]
        if not ids:
            return 0, 0
        if not ed.has_pending:
            self._before_gesture()
        applied, skipped = ed.edit(ids, fn, operation, label, key=key, final=final)
        if final:
            self._idle.stop()
        else:
            self._idle.start()
        if skipped:
            self.status_message.emit(f"{label}: {skipped} panel(s) skipped"
                                     + (f" ({what})" if what else ""))
        self.canvas.update_items(self._visual, ids)
        self.inspector.refresh_values(channels=final)
        self._update_info()
        self._update_enabled()
        return applied, skipped

    # ============================================================ figures
    def new_figure(self, name: Optional[str] = None) -> Optional[Figure]:
        if self.project is None:
            return None
        if name is None:
            name, ok = QInputDialog.getText(self, "New figure", "Figure name:",
                                            text=next_figure_name(self.project))
            if not ok:
                return None
        self.flush()
        self._before_gesture()
        fig = create_figure(self.project, name or next_figure_name(self.project),
                            page=self.prefs.new_page())
        self._save()
        self._reload_combo(fig.id)
        return fig

    def rename_current(self) -> None:
        fig = self.current_figure()
        if fig is None:
            return
        name, ok = QInputDialog.getText(self, "Rename figure", "Name:", text=fig.name)
        if ok and name.strip():
            self.flush()
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
        self.flush()
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
        self.flush()
        self._before_gesture()
        n = ed.delete_panels(ids)
        self.refresh(selected=set())
        return n

    def select_all(self) -> None:
        self.canvas.select_all()

    def undo(self) -> None:
        ed = self.current_editor()
        if ed is not None:
            self._idle.stop()
            self._before_gesture()
            keep = set(self.canvas.selected_ids())
            if ed.undo():
                self.refresh(selected=keep)

    def redo(self) -> None:
        ed = self.current_editor()
        if ed is not None:
            self._idle.stop()
            self._before_gesture()
            keep = set(self.canvas.selected_ids())
            if ed.redo():
                self.refresh(selected=keep)

    def _on_geometry_commit(self, changes, operation: str) -> None:
        ed = self.current_editor()
        if ed is None:
            return
        self.flush()
        self._before_gesture()
        label = "Resize panel" if operation == "figure.panel.resize" else \
            f"Move {len(changes)} panel(s)"
        ed.set_geometry(changes, operation, label)
        # Rebuilding the scene here would delete the item whose mouse event
        # is being handled: sync the items (new crop after a resize) later.
        QTimer.singleShot(0, lambda ids=list(changes): self._sync_after_geometry(ids))

    def _sync_after_geometry(self, ids: list[str]) -> None:
        self.canvas.update_items(self._visual, ids)
        self.inspector.refresh_values()
        self.inspector.refresh_planes()
        self._update_info()
        self._update_enabled()

    # ============================================================== layout
    def _layout(self, fn, operation: str, label: str, min_sel: int = 1,
                select_new: bool = False):
        """Run one layout gesture on the selection (one undo step, one log
        entry per panel), then redraw."""
        ed = self.current_editor()
        sel = [p.id for p in self.selected_panels()]
        if ed is None or len(sel) < min_sel:
            return None
        self.flush()
        self._before_gesture()
        before = {p.id for p in ed.figure.panels}
        result = ed.layout(sel, fn, operation, label)
        added = {p.id for p in ed.figure.panels} - before
        self.refresh(selected=added if select_new and added else set(sel))
        if added:
            _w, h = ed.figure.page.size_mm
            bottom = max(p.y_mm + p.height_mm for p in ed.figure.panels if p.id in added)
            if bottom > h:
                self.status_message.emit(f"{label}: {len(added)} panel(s) added; they extend "
                                         f"below the page ({bottom:.0f} > {h:g} mm)")
        return result

    def align(self, edge: str) -> None:
        self._layout(lambda ps, _r: L.align(ps, edge), f"figure.layout.align_{edge}",
                     f"Align {edge}", min_sel=2)

    def distribute(self, axis: str) -> None:
        self._layout(lambda ps, _r: L.distribute(ps, axis), f"figure.layout.distribute_{axis}",
                     "Distribute", min_sel=3)

    def snap_to_grid(self) -> None:
        gap = self.gap_spin.value()
        self._layout(lambda ps, _r: L.grid(ps, gap), "figure.layout.grid",
                     f"Grid ({gap:g} mm)")

    def _on_gap_changed(self, v: float) -> None:
        self.prefs.layout_gap_mm = float(v)
        self._save_prefs()

    def same_size(self, mode: str) -> None:
        def fn(ps, refs):
            L.same_size(ps, refs, ps[0], mode)
        self._layout(fn, f"figure.layout.same_{mode}", f"Same {mode}", min_sel=2)

    def align_magnification(self) -> None:
        def fn(ps, refs):
            return L.align_magnification(ps, refs)
        skipped = self._layout(fn, "figure.layout.align_magnification", "Align magnification",
                               min_sel=2)
        if skipped:
            self.status_message.emit(f"Align magnification: {len(skipped)} panel(s) skipped "
                                     "(no spatial calibration)")

    def nudge(self, dx: float, dy: float) -> None:
        """Arrow keys: a burst of presses is one gesture (committed after
        700 ms without a press)."""
        self.apply(lambda p, _r: L.nudge([p], dx, dy), "Nudge", key="nudge", final=False,
                   operation="figure.layout.nudge")

    def page_setup_dialog(self) -> None:
        fig = self.current_figure()
        if fig is None:
            return
        last = max((p.page for p in fig.panels), default=0)
        dlg = PageSetupDialog(fig.page, self.prefs, min_pages=last + 1, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.set_page(dlg.result_page())

    def set_page(self, page) -> bool:
        ed = self.current_editor()
        if ed is None:
            return False
        self.flush()
        self._before_gesture()
        try:
            ed.set_page(page)
        except ValueError as exc:
            QMessageBox.warning(self, "Page setup", str(exc))
            return False
        self.refresh()
        return True

    # ---- auto-layouts ------------------------------------------------------
    def _origin(self, figure: Figure, page: int) -> tuple[float, float]:
        return 0.0, L.below_all(figure.panels, page, self.gap_spin.value())

    def split_channels(self) -> None:
        gap = self.gap_spin.value()

        def fn(ps, refs):
            src = ps[0]
            x, y = self._origin(self.current_figure(), src.page)
            new = L.split_channels(src, refs[src.image_id], x, y, gap)
            self.current_figure().panels.extend(new)
        self._layout(fn, "figure.layout.split_channels", "Split channels", select_new=True)

    def montage(self, axis: str, indices: list[int], direction: str = "row") -> None:
        gap = self.gap_spin.value()

        def fn(ps, refs):
            src = ps[0]
            x, y = self._origin(self.current_figure(), src.page)
            new = L.montage(src, refs[src.image_id], axis, indices, x, y, gap, direction)
            self.current_figure().panels.extend(new)
        self._layout(fn, f"figure.layout.montage_{axis}", f"Montage {axis.upper()}",
                     select_new=True)

    def montage_dialog(self) -> None:
        sel = self.selected_panels()
        if not sel:
            return
        s = self.ref(sel[0]).metadata.shape
        axes = [a for a, n in (("T", s.T), ("Z", s.Z)) if n > 1]
        if not axes:
            QMessageBox.information(self, "Montage", "This image has a single T and Z.")
            return
        axis, ok = QInputDialog.getItem(self, "Montage", "Axis:", axes, 0, False)
        if not ok:
            return
        n = s.T if axis == "T" else s.Z
        text, ok = QInputDialog.getText(self, "Montage", f"{axis} points (1–{n}, e.g. 1-3, 5):",
                                        text=f"1-{n}")
        if not ok:
            return
        try:
            indices = L.parse_indices(text, n)
        except ValueError as exc:
            QMessageBox.warning(self, "Montage", str(exc))
            return
        direction, ok = QInputDialog.getItem(self, "Montage", "Arrange in a:", ["row", "column"],
                                             0, False)
        if ok and indices:
            self.montage(axis.lower(), indices, direction)

    def condition_grid(self) -> None:
        gap = self.gap_spin.value()

        def fn(ps, refs):
            # One row per image (first selected panel of each image).
            seen, sources = set(), []
            for p in ps:
                if p.image_id not in seen:
                    seen.add(p.image_id)
                    sources.append((p, refs[p.image_id]))
            x, y = self._origin(self.current_figure(), ps[0].page)
            rows = L.condition_grid(sources, x, y, gap)
            self.current_figure().panels.extend(p for row in rows for p in row if p is not None)
        self._layout(fn, "figure.layout.condition_grid", "Condition grid", select_new=True)

    # ===================================================== preview actions
    def copy_rendering(self) -> None:
        sel = self.selected_panels()
        if len(sel) == 1:
            self.clip_rendering = F.rendering_of(sel[0])
            self.status_message.emit("Rendering copied")
            self.inspector.buttons["paste_rendering"].setEnabled(True)

    def paste_rendering(self) -> None:
        if self.clip_rendering is not None:
            clip = self.clip_rendering
            self.apply(lambda p, r: F.paste_rendering(p, r, clip), "Paste rendering",
                       what="no matching channel")
            self.inspector.refresh_planes()

    def copy_viewport(self) -> None:
        sel = self.selected_panels()
        if len(sel) == 1:
            s = self.ref(sel[0]).metadata.shape
            self.clip_viewport = V.copy_view(sel[0], s.X, s.Y)
            self.status_message.emit("Viewport copied")
            self.inspector.buttons["paste_viewport"].setEnabled(True)

    def paste_viewport(self) -> None:
        if self.clip_viewport is not None:
            view = dict(self.clip_viewport)
            self.apply(lambda p, r: F.paste_viewport(p, r, view), "Paste viewport")
            self.inspector.refresh_planes()

    def reset_to_defaults(self) -> None:
        self.apply(F.reset_to_image_defaults, "Reset to image defaults")
        self.inspector.set_panels(self.selected_panels())

    def save_as_defaults(self) -> bool:
        sel = self.selected_panels()
        if len(sel) != 1 or self.project is None:
            return False
        self.flush()
        self._before_gesture()
        if F.save_as_image_defaults(self.project, sel[0]):
            self._save()
            self.image_defaults_changed.emit(sel[0].image_id)
            self.status_message.emit(f"Saved as defaults of {self.ref(sel[0]).filename}")
        return True

    def replace_image(self, image_id: str, confirm: Optional[Callable[[str], bool]] = None) -> bool:
        """Show another image in the selected panels, keeping every setting.
        Refused (nothing changed) if any panel is incompatible; warnings are
        confirmed through `confirm` (default: a dialog)."""
        new = self.project.images.get(image_id) if self.project else None
        sel = self.selected_panels()
        if new is None or not sel:
            return False
        errors, warnings = [], []
        for p in sel:
            e, w = F.replace_problems(p, self.ref(p), new)
            errors += e
            warnings += [x for x in w if x not in warnings]
        if errors:
            QMessageBox.warning(self, "Replace image", "Not compatible:\n\n" +
                                "\n".join(dict.fromkeys(errors)))
            return False
        if warnings:
            text = "Replace anyway?\n\n" + "\n".join(warnings)
            ok = confirm(text) if confirm else QMessageBox.question(
                self, "Replace image", text) == QMessageBox.Yes
            if not ok:
                return False

        def do(p: Panel, _r: ImageRef) -> bool:
            F.replace_image(p, new)
            return True
        self.apply(do, f"Replace image by {new.filename}", operation="figure.panel.replace_image")
        self.inspector.set_panels(self.selected_panels())
        return True

    def replace_image_dialog(self) -> None:
        p = self.project
        if p is None or not self.selected_panels():
            return
        refs = sorted(p.images.values(), key=lambda r: r.filename.lower())
        labels = [r.filename for r in refs]
        choice, ok = QInputDialog.getItem(self, "Replace image", "New image:", labels, 0, False)
        if ok:
            self.replace_image(refs[labels.index(choice)].id)

    # ============================================================ drawing
    def _visual(self, panel: Panel) -> PanelVisual:
        ref = self.ref(panel)
        if ref is None:
            return None, "image not in project", None
        try:
            rgb = self.renderer.render(ref, panel, max_side=CANVAS_MAX_SIDE)
        except RenderError as exc:
            return None, f"{ref.filename}\n{exc}", None
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        s = ref.metadata.shape
        rect = V.image_rect_mm(panel, s.X, s.Y, crop_bounds(panel.crop, s.X, s.Y))
        return QPixmap.fromImage(img), "", rect

    def refresh(self, selected: Optional[set[str]] = None) -> None:
        fig = self.current_figure()
        if selected is None:
            selected = set(self.canvas.selected_ids())
        self.canvas.set_figure(fig, self._visual, selected)
        if fig is not None:
            w, h = fig.page.size_mm
            pages = f" × {fig.page.n_pages} pages" if fig.page.n_pages > 1 else ""
            self.page_label.setText(f"{fig.page.preset.name} · {w:g} × {h:g} mm{pages}")
        else:
            self.page_label.setText("")
        self.inspector.set_panels(self.selected_panels())
        self._update_info()
        self._update_enabled()

    def _on_selection_changed(self, _ids: list) -> None:
        self.flush()                      # a gesture never spans two selections
        self.inspector.set_panels(self.selected_panels())
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
        sel = self.selected_panels()
        if not sel:
            self.info.setHtml(f"<b>{html.escape(fig.name)}</b><br>{len(fig.panels)} panel(s)."
                              "<br><small>Click, Ctrl-click or drag a rectangle to select.</small>")
            return
        if len(sel) > 1:
            self.info.setHtml(f"<b>{len(sel)} panels selected</b>")
            return
        panel = sel[0]
        ref = self.ref(panel)
        rows = [("Image", html.escape(ref.filename) if ref else "missing"),
                ("Position", f"{panel.x_mm:.1f}, {panel.y_mm:.1f} mm"),
                ("Size", f"{panel.width_mm:.1f} × {panel.height_mm:.1f} mm")]
        if ref is not None:
            s = ref.metadata.shape
            c = V.effective_crop(panel, s.X, s.Y)
            rows.append(("Crop (raw px)", f"x {c.x:.1f}–{c.x + c.w:.1f}, y {c.y:.1f}–{c.y + c.h:.1f}"))
            rows.append(("Zoom", f"{V.zoom_percent(panel, s.X, s.Y):.0f} %"))
            frac = V.visible_fraction(panel, s.X, s.Y)
            if frac is not None and frac < 0.999:
                rows.append(("On image", f"{100 * frac:.0f} % of the crop (rest: background)"))
            ppi = V.px_per_mm(panel, s.X, s.Y) * 25.4
            dpi = fig.page.preset.dpi
            warn = "" if ppi >= dpi else \
                f" <span style='color:#b45309'>(&lt; {dpi} dpi: upsampled at export)</span>"
            rows.append(("Effective resolution", f"{ppi:.0f} ppi{warn}"))
            cal = ref.metadata.calibration
            if cal.is_calibrated:
                rows.append(("Scale", f"{V.px_per_mm(panel, s.X, s.Y) * cal.pixel_size_x:.2f} "
                                      f"{cal.unit} per page-mm"))
        proj = panel.z_projection
        z = f"max {proj.z_start + 1}–{proj.z_end + 1}" if proj else f"{panel.z + 1}"
        rows.append(("T / Z", f"{panel.t + 1} / {z}"))
        if panel.rotation_deg or panel.flip_horizontal or panel.flip_vertical:
            flips = "+".join(n for n, on in (("H", panel.flip_horizontal), ("V", panel.flip_vertical)) if on)
            rows.append(("Orientation", f"{panel.rotation_deg}°" + (f" flip {flips}" if flips else "")))
        body = "".join(f"<tr><td style='color:#6b7280;padding-right:8px'>{k}</td><td>{v}</td></tr>"
                       for k, v in rows)
        self.info.setHtml(f"<table style='font-size:11px'>{body}</table>")
