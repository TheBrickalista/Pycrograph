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
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
    QSplitter, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from .. import figures as F
from .. import insets as I
from .. import layout as L
from .. import viewport as V
from ..figures import FigureEditor, create_figure, next_figure_name, rename_figure
from ..models import Figure, ImageRef, Panel, PanelLabel, Project, Shape
from ..render import PanelRenderer
from .. import consistency as C
from .. import export as X
from ..preferences import Preferences
from ..storage import Workspace, check_image
from ..ui_text import noun, plural
from .figure_canvas import FigureCanvas, PanelItem, PanelVisual
from .insets_inspector import InsetsInspector
from .labels_inspector import LabelsInspector
from .checks_panel import ChecksPanel, counts_text
from .page_setup import PageSetupDialog
from .panel_inspector import PreviewInspector
from .scene_builder import overlay_builder, panel_visual

CANVAS_MAX_SIDE = 1024      # on-screen decimation (integer stride, no interpolation)
IDLE_COMMIT_MS = 700        # same as the library / PysternBlot gesture commit
LIVE_CHECK_MS = 500         # live consistency check, after the last committed gesture


class FiguresTab(QWidget):
    status_message = Signal(str)
    checks_changed = Signal(str)                  # level counts, for the status bar
    image_defaults_changed = Signal(str)          # image id

    def __init__(self, renderer: PanelRenderer, save: Callable[[], None],
                 before_gesture: Optional[Callable[[], None]] = None,
                 prefs: Optional[Preferences] = None,
                 save_prefs: Optional[Callable[[], None]] = None,
                 workspace: Optional[Workspace] = None, parent=None):
        super().__init__(parent)
        self.renderer = renderer
        self.workspace = workspace
        self.prefs = prefs or Preferences()
        self._save_prefs = save_prefs or (lambda: None)
        self._save_project = save
        self._save = self._save_and_check            # every committed gesture saves
        self._before_gesture = before_gesture or (lambda: None)
        self.project: Optional[Project] = None
        self._editors: dict[str, FigureEditor] = {}
        self.clip_rendering: Optional[list[dict]] = None
        self.clip_panels: Optional[list[dict]] = None
        self.clip_shapes: Optional[list[dict]] = None
        # Selected shapes per panel (shape selection lives here, not in the model)
        self.selected_shapes: dict[str, set[str]] = {}
        self._last_missing: tuple = ()
        self.clip_viewport: Optional[dict] = None
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_COMMIT_MS)
        self._idle.timeout.connect(self.end_live)
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(LIVE_CHECK_MS)
        self._check_timer.timeout.connect(self._live_check)
        self.last_issues: list[C.Issue] = []
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
        self.a_add = action("Add Images…", self.add_image_dialog,
                            tip="Pick library images (or import new ones) as panels; each copies "
                                "the image's display settings")
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
        bar.addWidget(self._build_outlines_button())
        bar.addSpacing(12)
        self.a_check = action("Check consistency", lambda: self.check_consistency(show=True),
                              tip="Full check in the Checks panel: levels, T/Z, scale bars, "
                                  "channels, sources, resolution, line widths, saturation — per "
                                  "row, column and lock group")
        self.a_export = action("Export…", self.export_figure_dialog,
                               tip="PDF / SVG / TIFF / PNG with a provenance file")
        self.a_export_sources = action("Export source data…", self.export_sources_dialog,
                                       tip="Raw cropped planes of the selected (or all) panels as "
                                           "OME-TIFF")
        for a in (self.a_check, self.a_export, self.a_export_sources):
            button(a)
        bar.addStretch(1)
        self.page_label = QLabel("")
        self.page_label.setStyleSheet("color: #6b7280;")
        bar.addWidget(self.page_label)
        lay.addLayout(bar)
        lay.addLayout(self._build_layout_bar(action))

        split = QSplitter(Qt.Horizontal)
        self.canvas = FigureCanvas()
        self._apply_outlines()
        self.canvas.set_commit_callback(self._on_geometry_commit)
        self.canvas.set_overlay_builder(self._build_overlays)
        sc = self.canvas.figure_scene
        sc.on_inset_rect = self._on_inset_rect
        sc.on_shape_click = self._on_shape_click
        sc.on_shape_moved = self._on_shape_moved
        # Panel copy/paste: shortcuts on the canvas only, so Ctrl+C / Ctrl+V
        # keep working on text in the inspector fields.
        self.a_copy_panels = QAction("Copy Panels", self.canvas)
        self.a_copy_panels.setShortcut(QKeySequence.Copy)
        self.a_copy_panels.setShortcutContext(Qt.WidgetShortcut)
        self.a_copy_panels.triggered.connect(self.copy_panels)
        self.a_paste_panels = QAction("Paste Panels", self.canvas)
        self.a_paste_panels.setShortcut(QKeySequence.Paste)
        self.a_paste_panels.setShortcutContext(Qt.WidgetShortcut)
        self.a_paste_panels.triggered.connect(self.paste_panels)
        self.canvas.addAction(self.a_copy_panels)
        self.canvas.addAction(self.a_paste_panels)
        self.canvas.nudge_requested.connect(self.nudge)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        split.addWidget(self.canvas)
        self.inspector_tabs = QTabWidget()
        self.inspector = PreviewInspector(self)
        self.info = QTextBrowser()
        self.labels_inspector = LabelsInspector(self)
        self.inspector_tabs.addTab(self.inspector, "Preview")
        self.inspector_tabs.addTab(self.labels_inspector, "Labels")
        self.insets_inspector = InsetsInspector(self)
        self.inspector_tabs.addTab(self.insets_inspector, "Insets && Shapes")
        self.inspector_tabs.addTab(self.info, "Info")
        self.checks = ChecksPanel(self.canvas.select_ids)
        self.checks.full_check_requested.connect(lambda: self.run_checks(full=True))
        self.checks.live_toggled.connect(lambda on: on and self.schedule_check())
        self.inspector_tabs.addTab(self.checks, "Checks")
        self.inspector_tabs.setMinimumWidth(400)
        split.addWidget(self.inspector_tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([850, 420])
        lay.addWidget(split, 1)

    def _build_outlines_button(self) -> QToolButton:
        """**Outlines ▾**: dashed outlines of unselected panels on the canvas
        (a hovered panel always shows one). Clicking toggles "All panel
        outlines"; the menu also has "Panels with empty area" (crop beyond
        the image). Preferences, not project data: not logged, not undoable,
        never exported."""
        self.a_outlines_all = QAction("All panel outlines", self, checkable=True)
        self.a_outlines_all.setChecked(self.prefs.canvas_outlines_all)
        self.a_outlines_all.setToolTip("Dashed outline on every unselected panel (never exported)")
        self.a_outlines_empty = QAction("Panels with empty area", self, checkable=True)
        self.a_outlines_empty.setChecked(self.prefs.canvas_outlines_empty)
        self.a_outlines_empty.setToolTip("Outline panels whose crop extends beyond the image "
                                         "(part of the panel is page background)")
        for a in (self.a_outlines_all, self.a_outlines_empty):
            a.toggled.connect(self._on_outlines_toggled)
        menu = QMenu(self)
        menu.addAction(self.a_outlines_all)
        menu.addAction(self.a_outlines_empty)
        b = QToolButton()
        b.setText("Outlines")
        b.setCheckable(True)                          # mirrors "All panel outlines"
        b.setChecked(self.a_outlines_all.isChecked())
        b.toggled.connect(self.a_outlines_all.setChecked)
        self.a_outlines_all.toggled.connect(b.setChecked)
        b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        b.setMenu(menu)
        b.setPopupMode(QToolButton.MenuButtonPopup)
        b.setToolTip("Panel outlines on the canvas (never exported). Click: all panels; "
                     "arrow: options")
        self.outlines_btn = b
        return b

    def _on_outlines_toggled(self, _on: bool) -> None:
        self.prefs.canvas_outlines_all = self.a_outlines_all.isChecked()
        self.prefs.canvas_outlines_empty = self.a_outlines_empty.isChecked()
        self._save_prefs()
        self._apply_outlines()

    def _apply_outlines(self) -> None:
        if hasattr(self, "canvas"):
            self.canvas.figure_scene.set_outlines(self.prefs.canvas_outlines_all,
                                                  self.prefs.canvas_outlines_empty)

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
            ("condition_grid", "Condition grid (shared rendering)…",
             lambda: self.condition_grid_dialog(own_rendering=False)),
            ("condition_grid_own", "Condition grid (keep each image's rendering)…",
             lambda: self.condition_grid_dialog(own_rendering=True)),
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
        self.a_add.setEnabled(has_project)               # the picker offers Import from disk
        self.a_delete.setEnabled(sel)
        self.a_select_all.setEnabled(fig is not None)
        for a in (self.a_check, self.a_export, self.a_export_sources):
            a.setEnabled(bool(fig and fig.panels and self.workspace is not None))
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
                "condition_grid": 1, "condition_grid_own": 1}
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
              panel_ids: Optional[list[str]] = None, propagate: bool = True,
              lock_values: Optional[dict] = None) -> tuple[int, int]:
        """Apply fn to every selected panel (FigureEditor.edit, which also
        propagates locked channel settings to lock-group members) and
        redraw."""
        ed = self.current_editor()
        if ed is None:
            return 0, 0
        ids = panel_ids if panel_ids is not None else [p.id for p in self.selected_panels()]
        if not ids:
            return 0, 0
        if not ed.has_pending:
            self._before_gesture()
        applied, skipped = ed.edit(ids, fn, operation, label, key=key, final=final,
                                   propagate=propagate, lock_values=lock_values)
        if final:
            self._idle.stop()
        else:
            self._idle.start()
        notes = []
        if skipped:
            notes.append(f"{plural(skipped, 'panel')} skipped" + (f" ({what})" if what else ""))
        if ed.last_lock_skipped:          # say which locked panels lack the channel
            notes.append(F.skipped_note(ed.figure, ed.last_lock_skipped,
                                        len(ed.last_propagated)))
        elif ed.last_propagated:
            notes.append(f"also applied to {plural(len(ed.last_propagated), 'locked panel')}")
        if ed.last_propagated:
            mism = self.mismatch_warning(ids + ed.last_propagated)
            if mism and final:
                notes.append(mism)
        if ed.last_synced and final:
            notes.append(f"{plural(len(ed.last_synced), 'inset')} followed")
        if ed.last_region and final:
            notes.append(self.linked_note(ed.last_region))
        if notes:
            self.status_message.emit(f"{label}: " + "; ".join(notes))
        changed = ids + ed.last_propagated + ed.last_synced + ed.last_region
        self.canvas.update_items(self._visual, self._related_ids(changed, final))
        self.inspector.refresh_values(channels=final)
        if final:
            self.labels_inspector.refresh()
            self._report_missing()
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
        self.status_message.emit(f"Added {plural(len(panels), 'panel')} to {ed.figure.name}")
        return panels

    # Set by the main window: () -> (thumbnails {id: QIcon}, statuses {id: str})
    # of the Library, and its import flow (dataset id -> imported image ids).
    library_state: Optional[Callable[[], tuple[dict, dict]]] = None
    import_handler: Optional[Callable[[str], list[str]]] = None
    # Set by the main window: True while the project is open read-only
    # (another window holds its edit lock); exports are then refused.
    is_read_only: Callable[[], bool] = staticmethod(lambda: False)

    def make_add_images_dialog(self):
        from .add_images_dialog import AddImagesDialog
        thumbs, status = self.library_state() if self.library_state else ({}, {})
        dlg = AddImagesDialog(self.project, thumbs, status, self.library_state, self)

        def do_import(dataset_id: str) -> None:
            if self.import_handler is not None:
                dlg.after_import(self.import_handler(dataset_id) or [])
        dlg.import_requested.connect(do_import)
        return dlg

    def add_image_dialog(self) -> list[Panel]:
        """The Add Images picker; the chosen images become panels in ONE
        gesture (tree order, each image once)."""
        if self.project is None:
            return []
        dlg = self.make_add_images_dialog()
        if dlg.exec() != QDialog.Accepted or not dlg.chosen_ids():
            return []
        return self.add_images(dlg.chosen_ids())

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
            f"Move {plural(len(changes), 'panel')}"
        ed.set_geometry(changes, operation, label)
        followers = list(ed.last_region)
        if followers:
            self.status_message.emit(f"{label}: {self.linked_note(followers)}")
        # Rebuilding the scene here would delete the item whose mouse event
        # is being handled: sync the items (new crop after a resize) later.
        QTimer.singleShot(0, lambda ids=list(changes) + followers: self._sync_after_geometry(
            self._related_ids(ids, False)))

    def _sync_after_geometry(self, ids: list[str]) -> None:
        self.canvas.update_items(self._visual, ids)
        self.labels_inspector.refresh()
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
        if ed.last_region:
            self.status_message.emit(f"{label}: {self.linked_note(ed.last_region)}")
        if added:
            _w, h = ed.figure.page.size_mm
            bottom = max(p.y_mm + p.height_mm for p in ed.figure.panels if p.id in added)
            if bottom > h:
                self.status_message.emit(f"{label}: {plural(len(added), 'panel')} added; {'it extends' if len(added) == 1 else 'they extend'} "
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
            self.status_message.emit(f"Align magnification: {plural(len(skipped), 'panel')} skipped "
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
        warn = self.new_groups_bit_depth_warning()
        if warn:
            self.status_message.emit(f"Montage {axis.upper()}: {warn}")

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

    def new_groups_bit_depth_warning(self) -> str:
        """After an auto-layout (its new panels selected): '⚠ These images
        have different bit depths (…)' for each new lock group that spans
        images of different bit depths, else ''."""
        fig = self.current_figure()
        if fig is None or self.project is None:
            return ""
        groups = dict.fromkeys(p.lock_group for p in self.selected_panels() if p.lock_group)
        warns = [F.bit_depth_warning(fig, F.lock_members(fig, g), self.project.images) for g in groups]
        return "; ".join("⚠ " + w for w in warns if w)

    def condition_grid(self, own_rendering: bool = False,
                       row_label: Optional[str] = "[image]") -> None:
        """Rows = images of the selected panels (figure order), columns =
        each channel then the merge. By default every row takes the first
        row's rendering; own_rendering keeps each image's."""
        gap = self.gap_spin.value()

        def fn(ps, refs):
            # One row per image (first selected panel of each image).
            seen, sources = set(), []
            for p in ps:
                if p.image_id not in seen:
                    seen.add(p.image_id)
                    sources.append((p, refs[p.image_id]))
            x, y = self._origin(self.current_figure(), ps[0].page)
            fig = self.current_figure()
            rows = L.condition_grid(sources, x, y, gap, own_rendering=own_rendering,
                                    row_label=row_label,
                                    label_space_mm=L.label_band_mm(fig.page.preset.font_size_pt))
            self.current_figure().panels.extend(p for row in rows for p in row if p is not None)
        self._layout(fn, "figure.layout.condition_grid", "Condition grid", select_new=True)
        mism = self.mismatch_warning(self.canvas.selected_ids())
        warn = [w for w in ((mism if not own_rendering else ""),
                            self.new_groups_bit_depth_warning()) if w]
        if warn:
            what = "shared" if not own_rendering else "own"
            self.status_message.emit(f"Condition grid ({what} rendering): " + "; ".join(warn))

    def condition_grid_dialog(self, own_rendering: bool = False) -> None:
        sel = self.selected_panels()
        if not sel:
            return
        keys = sorted({k for p in sel for k in (self.ref(p).key_values if self.ref(p) else {})})
        choices = ["[image]"] + [f"[kv:{k}]" for k in keys] + ["(no row labels)"]
        choice, ok = QInputDialog.getItem(self, "Condition grid", "Row labels:", choices, 0, False)
        if ok:
            self.condition_grid(own_rendering, None if choice.startswith("(") else choice)

    # ============================================================== labels
    def add_label(self, text: str, position: str = "top", font_size_pt: Optional[float] = None,
                  color: Optional[str] = None, font_family: Optional[str] = None) -> tuple[int, int]:
        if not text.strip():
            return 0, 0
        label = PanelLabel(text=text, position=position, font_size_pt=font_size_pt,
                           color=color, font_family=font_family)
        return self.apply(lambda p, r: F.add_label(p, r, label), "Add label",
                          what="already labelled", operation="figure.label.add")

    def update_label(self, key: tuple[str, str], **fields) -> tuple[int, int]:
        return self.apply(lambda p, r: F.update_labels(p, r, key, fields), "Edit label",
                          what="label absent", operation="figure.label.set")

    def delete_label(self, key: tuple[str, str]) -> tuple[int, int]:
        return self.apply(lambda p, r: F.delete_labels(p, r, key), "Delete label",
                          what="label absent", operation="figure.label.delete")

    def kv_keys(self) -> list[str]:
        return sorted({k for p in self.selected_panels() if (r := self.ref(p))
                       for k in r.key_values})

    def labels_from_kv(self, key: str, position: str = "top") -> tuple[int, int]:
        """Add [kv:<key>] to every selected panel in one step."""
        return self.add_label(f"[kv:{key}]", position)

    def labels_from_kv_dialog(self) -> None:
        keys = self.kv_keys()
        if not keys:
            QMessageBox.information(self, "Labels from key–value",
                                    "The selected panels' images have no key–value pairs "
                                    "(add them in the Library).")
            return
        key, ok = QInputDialog.getItem(self, "Labels from key–value", "Key:", keys, 0, False)
        if ok:
            self.labels_from_kv(key, self.labels_inspector.position_combo.currentText())

    def set_scale_bar(self, **fields) -> tuple[int, int]:
        return self.apply(lambda p, r: F.set_scale_bar(p, r, fields), "Scale bar",
                          what="no spatial calibration", operation="figure.scalebar.set")

    def set_border(self, **fields) -> tuple[int, int]:
        return self.apply(lambda p, r: F.set_border(p, r, fields), "Border",
                          operation="figure.border.set")

    def missing_tokens(self, panel_ids: Optional[list[str]] = None) -> list[str]:
        items = [it for it in self.canvas.figure_scene.panel_items()
                 if panel_ids is None or it.panel_id in panel_ids]
        return list(dict.fromkeys(t for it in items for t in it.missing))

    def _report_missing(self) -> None:
        """Unresolved tokens are never silent: status bar when they change."""
        missing = tuple(self.missing_tokens())
        if missing and missing != self._last_missing:
            self.status_message.emit(f"Labels: {plural(len(missing), 'value')} not found, drawn as “?”: "
                                     + ", ".join(missing))
        self._last_missing = missing

    def _build_overlays(self, item: PanelItem, panel: Panel) -> None:
        if self.project is None:
            return
        overlay_builder(self.project, self.current_figure,
                        lambda pid: frozenset(self.selected_shapes.get(pid, ())))(item, panel)

    def _related_ids(self, ids: list[str], wide: bool = True) -> list[str]:
        """Panels whose drawing depends on `ids`: their parents (inset
        rectangles) and insets; with `wide`, also their lock-group members
        (shared [time] unit)."""
        fig = self.current_figure()
        if fig is None:
            return ids
        out = list(dict.fromkeys(ids))
        for pid in list(out):
            p = fig.panel(pid)
            if p is None:
                continue
            if p.inset_of:
                out.append(p.inset_of)
            out += [q.id for q in I.insets_of(fig, pid)]
            if wide and p.lock_group:
                out += [q.id for q in F.lock_members(fig, p.lock_group)]
        return list(dict.fromkeys(out))

    @staticmethod
    def linked_note(followers: list[str]) -> str:
        return (f"applies to the {plural(len(followers), 'linked inset')} "
                "(same size, orientation and region; Unlink to change one alone)")

    def mismatch_warning(self, panel_ids: list[str]) -> str:
        """Same channel index, different channel name / wavelength between
        panels sharing rendering (kept for the step-6 consistency check)."""
        fig = self.current_figure()
        if fig is None or self.project is None:
            return ""
        panels = [p for p in fig.panels if p.id in set(panel_ids)]
        mism = F.channel_mismatches(panels, self.project.images)
        return ("⚠ " + "; ".join(m.message() for m in mism)) if mism else ""

    # =============================================================== insets
    # Called with (source inset, {label: [(panel, ref, name)]}) -> (mode, link,
    # target ids) or None; None = ask with ReplicateDialog. Tests set it.
    replicate_prompt: Optional[Callable] = None

    def start_create_inset(self) -> bool:
        """Draw mode on the selected panel(s); the rectangle drawn on one of
        them becomes the inset's region; with several selected, the same
        inset is then offered for the others (Replicate dialog)."""
        sel = [p for p in self.selected_panels() if p.inset_of is None]
        if not sel:
            return False
        self.canvas.start_draw([p.id for p in sel], "rect", self._inset_drawn)
        self.status_message.emit("Create inset: drag a rectangle on "
                                 + ("the panel" if len(sel) == 1 else "one of the panels")
                                 + " (Esc cancels)")
        self._create_targets = [p.id for p in sel]
        return True

    def _inset_drawn(self, panel_id: str, p0, p1) -> None:
        fig = self.current_figure()
        parent = fig.panel(panel_id) if fig else None
        ref = self.ref(parent) if parent else None
        if ref is None:
            return
        s = ref.metadata.shape
        x0, y0 = min(p0[0], p1[0]), min(p0[1], p1[1])
        rect = V.panel_rect_to_raw(parent, s.X, s.Y, (x0, y0, abs(p1[0] - p0[0]), abs(p1[1] - p0[1])))
        others = [i for i in getattr(self, "_create_targets", []) if i != panel_id]
        iid = self.create_inset(panel_id, rect)
        if iid and others:
            self.replicate_inset_dialog(iid, others)

    def create_inset(self, parent_id: str, raw_rect) -> Optional[str]:
        """New inset of `parent_id` showing `raw_rect` (raw px, clipped to
        the image), at 2× the parent's magnification, right of the parent.
        One gesture; the new inset is selected."""
        ed = self.current_editor()
        parent = ed.figure.panel(parent_id) if ed else None
        ref = self.ref(parent) if parent else None
        if ref is None:
            return None
        s = ref.metadata.shape
        x, y, w, h = raw_rect
        x0, y0 = max(0.0, x), max(0.0, y)
        x1, y1 = min(float(s.X), x + w), min(float(s.Y), y + h)
        if x1 - x0 < 1 or y1 - y0 < 1:
            self.status_message.emit("Create inset: the rectangle is outside the image")
            return None
        self.flush()
        self._before_gesture()
        inset = I.create_inset(ed.figure, parent, ref, (x0, y0, x1 - x0, y1 - y0), F.new_id(),
                               gap_mm=self.gap_spin.value())
        with ed.gesture("figure.inset.create", "Create inset"):
            ed.figure.panels.append(inset)
        self.refresh(selected={inset.id})
        self.status_message.emit("Inset created — use Replicate to… to repeat it on other panels")
        return inset.id

    def _on_inset_rect(self, inset_id: str, parent_id: str, rect, final: bool) -> None:
        """The rectangle on the parent was dragged / resized: it becomes the
        inset's crop. Live ticks update the inset only; the final commit is
        deferred (the dragged item lives in the parent's overlays)."""
        r = (rect.x(), rect.y(), rect.width(), rect.height())
        fig = self.current_figure()
        parent = fig.panel(parent_id) if fig else None
        ref = self.ref(parent) if parent else None
        if ref is None:
            return

        def fn(p: Panel, _r: ImageRef) -> bool:
            I.set_from_parent_rect(p, fig.panel(parent_id), ref, r)
            return True
        if not final:
            ed = self.current_editor()
            if not ed.has_pending:
                self._before_gesture()
            ed.edit([inset_id], fn, "figure.inset.rect", "Edit inset region",
                    key="inset.rect", final=False)
            # Linked siblings (and their parents' rectangles) follow live; the
            # dragged rectangle's own parent is not rebuilt until the commit.
            live = [inset_id] + [i for i in self._related_ids(ed.last_region, False)
                                 if i != parent_id]
            self.canvas.update_items(self._visual, live)
            return
        QTimer.singleShot(0, lambda: self.apply(fn, "Edit inset region", key="inset.rect",
                                                operation="figure.inset.rect",
                                                panel_ids=[inset_id]))

    # ---- replicate --------------------------------------------------------
    def replicate_source(self) -> tuple[Optional[Panel], str]:
        """(the inset to replicate, "") from the selection — exactly one
        inset, or else exactly one parent with exactly one inset — or
        (None, why not) for the greyed button's tooltip."""
        fig = self.current_figure()
        sel = self.selected_panels()
        if fig is None or not sel:
            return None, "Select an inset first"
        insets = [p for p in sel if p.inset_of]
        if len(insets) == 1:
            return insets[0], ""
        if len(insets) > 1:
            return None, "Select only one inset to replicate"
        parents = [(p, I.insets_of(fig, p.id)) for p in sel]
        parents = [(p, kids) for p, kids in parents if kids]
        if not parents:
            return None, "Select an inset first"
        if len(parents) > 1:
            return None, "Select only one inset to replicate"
        kids = parents[0][1]
        if len(kids) > 1:
            return None, f"This panel has {len(kids)} insets: select the one to replicate"
        return kids[0], ""

    def replicate_candidates(self, inset_id: Optional[str] = None,
                             target_ids: Optional[list[str]] = None):
        """(source inset, sets). sets: "Selected panels" (the selection, or
        `target_ids`), "All panels of this split / lock group" (the parent's),
        and "Eligible panels" — every panel that can receive the inset, the
        parent's group first, then figure order (the dialog's checklist).
        Entries are (panel, ref, display name)."""
        fig = self.current_figure()
        if fig is None:
            return None, {}
        if inset_id is None:
            src, _why = self.replicate_source()
        else:
            src = fig.panel(inset_id)
        parent = fig.panel(src.inset_of) if src is not None and src.inset_of else None
        if parent is None:
            return None, {}

        def entry(p: Panel):
            return (p, self.ref(p), f"Panel {fig.panels.index(p) + 1} — "
                                    f"{self.ref(p).filename if self.ref(p) else '?'}")

        def ok(p: Optional[Panel]) -> bool:
            return p is not None and p.inset_of is None and p.id != parent.id and self.ref(p) is not None
        ids = target_ids if target_ids is not None else [p.id for p in self.selected_panels()]
        chosen = [p for p in (fig.panel(i) for i in ids) if ok(p)]
        chosen.sort(key=fig.panels.index)
        group = [p for p in F.lock_members(fig, parent.lock_group) if ok(p)]
        eligible = group + [p for p in fig.panels if ok(p) and p not in group]
        return src, {"Selected panels": [entry(p) for p in chosen],
                     "All panels of this split / lock group": [entry(p) for p in group],
                     "Eligible panels": [entry(p) for p in eligible]}

    @staticmethod
    def default_checked(sets: dict) -> list[str]:
        """Pre-checked targets: the selected panels, else the parent's split /
        lock group, else none."""
        for key in ("Selected panels", "All panels of this split / lock group"):
            if sets.get(key):
                return [p.id for p, _r, _n in sets[key]]
        return []

    def make_replicate_dialog(self, src: Panel, sets: dict):
        from .replicate_dialog import ReplicateDialog
        return ReplicateDialog(self.ref(src), sets.get("Eligible panels", []),
                               self.default_checked(sets), self)

    def replicate_inset(self, inset_id: str, target_ids: list[str], mode: Optional[str] = None,
                        link: bool = True) -> list[str]:
        """The same inset on each target (insets.replicate_inset), one gesture."""
        ed = self.current_editor()
        src = ed.figure.panel(inset_id) if ed else None
        if src is None or src.inset_of is None:
            return []
        targets = [ed.figure.panel(i) for i in target_ids if ed.figure.panel(i)]
        self.flush()
        self._before_gesture()
        with ed.gesture("figure.inset.replicate", f"Replicate inset to {plural(len(targets), 'panel')}"):
            copies = I.replicate_inset(ed.figure, src, targets, self.project.images, mode, link,
                                       F.new_id, self.gap_spin.value())
            ed.figure.panels.extend(copies)
        self.refresh(selected={c.id for c in copies} | {src.id})
        modes = [c.inset_style.region_link for c in copies]
        self.status_message.emit(f"Inset replicated to {plural(len(copies), 'panel')}"
                                 + (f" ({modes.count('size')} by size)" if "size" in modes else "")
                                 + (", linked" if link else ""))
        return [c.id for c in copies]

    def replicate_inset_dialog(self, inset_id: Optional[str] = None,
                               target_ids: Optional[list[str]] = None) -> list[str]:
        src, sets = self.replicate_candidates(inset_id, target_ids)
        if src is None:
            self.status_message.emit("Replicate: " + (self.replicate_source()[1]
                                                      or "select one inset"))
            return []
        if not sets.get("Eligible panels"):
            self.status_message.emit("Replicate: no other panel in the figure to replicate to")
            return []
        if self.replicate_prompt is not None:
            answer = self.replicate_prompt(src, sets)
        else:
            dlg = self.make_replicate_dialog(src, sets)
            answer = (dlg.mode(), dlg.link(), dlg.target_ids()) if dlg.exec() == QDialog.Accepted else None
        if not answer:
            return []
        mode, link, ids = answer
        if not ids:
            return []
        return self.replicate_inset(src.id, ids, mode, link)

    def unlink_from_group(self) -> None:
        """Selected insets leave their region group (a group of one is cleared)."""
        def fn(ps, _r):
            for p in ps:
                if p.inset_style is not None and p.inset_style.region_group:
                    p.inset_style = p.inset_style.model_copy(update={"region_group": None})
            I.clear_singleton_groups(self.current_figure())
        self._layout(fn, "figure.inset.unlink", "Unlink inset")

    def set_inset_style(self, **fields) -> tuple[int, int]:
        """Style / follow flags of every selected inset. Turning a follow
        flag on syncs the inset to its parent at once (same gesture)."""
        fig = self.current_figure()

        def fn(p: Panel, r: ImageRef) -> bool:
            if p.inset_of is None or p.inset_style is None:
                return False
            p.inset_style = p.inset_style.model_copy(update=fields)
            parent = fig.panel(p.inset_of)
            if parent is not None:
                I.sync_inset(p, parent, r)
            return True
        return self.apply(fn, "Inset style", what="not an inset", operation="figure.inset.style")

    def detach_insets(self) -> int:
        """Selected insets become ordinary panels (and leave their region
        group; a group of one is cleared) — one gesture."""
        n = [0]

        def fn(ps, _r):
            for p in ps:
                if p.inset_of is not None:
                    p.inset_of, p.inset_style = None, None
                    n[0] += 1
            I.clear_singleton_groups(self.current_figure())
        self._layout(fn, "figure.inset.detach", "Detach inset")
        return n[0]

    # =============================================================== shapes
    def start_draw_shape(self, kind: str) -> bool:
        sel = self.selected_panels()
        if len(sel) != 1:
            return False
        mode = "line" if kind in ("line", "arrow") else "rect"
        self.canvas.start_draw(sel[0].id, mode,
                               lambda pid, p0, p1: self._shape_drawn(pid, kind, p0, p1))
        self.status_message.emit(f"Draw {kind}: drag on the panel (Esc cancels)")
        return True

    def _shape_drawn(self, panel_id: str, kind: str, p0, p1) -> None:
        fig = self.current_figure()
        panel = fig.panel(panel_id) if fig else None
        ref = self.ref(panel) if panel else None
        if ref is None:
            return
        s = ref.metadata.shape
        pts = [V.panel_to_raw(panel, s.X, s.Y, *p0), V.panel_to_raw(panel, s.X, s.Y, *p1)]
        self.add_shape(panel_id, kind, pts, **self.insets_inspector.shape_style())

    def add_shape(self, panel_id: str, kind: str, raw_points, **style) -> Optional[str]:
        shape = Shape(id=F.new_id(), kind=kind,
                      points=[(round(x, 4), round(y, 4)) for x, y in raw_points], **style)
        self.apply(lambda p, r: F.add_shape(p, r, shape), f"Add {kind}",
                   operation="figure.shape.add", panel_ids=[panel_id])
        self.selected_shapes = {panel_id: {shape.id}}
        self.canvas.update_items(self._visual, [panel_id])
        self.insets_inspector.refresh()
        return shape.id

    def _on_shape_click(self, panel_id: str, shape_id: str, additive: bool) -> None:
        cur = self.selected_shapes.get(panel_id, set()) if additive else set()
        self.selected_shapes = {**({k: v for k, v in self.selected_shapes.items()
                                    if additive}), panel_id: cur | {shape_id}}
        self.insets_inspector.refresh()

    def select_shapes(self, panel_id: str, shape_ids) -> None:
        self.selected_shapes = {panel_id: set(shape_ids)}
        self.canvas.update_items(self._visual, [panel_id])
        self.insets_inspector.refresh()

    def _on_shape_moved(self, panel_id: str, shape_ids: list[str], dx_mm: float, dy_mm: float) -> None:
        # Deferred: the dragged ShapeItem is rebuilt by the commit.
        QTimer.singleShot(0, lambda: self.move_shapes(panel_id, shape_ids, dx_mm, dy_mm))

    def move_shapes(self, panel_id: str, shape_ids, dx_mm: float, dy_mm: float) -> None:
        """A page displacement (mm) -> raw pixels through the panel's own
        crop, scale and orientation."""
        fig = self.current_figure()
        panel = fig.panel(panel_id) if fig else None
        ref = self.ref(panel) if panel else None
        if ref is None:
            return
        s = ref.metadata.shape
        ax, ay = V.panel_to_raw(panel, s.X, s.Y, 0.0, 0.0)
        bx, by = V.panel_to_raw(panel, s.X, s.Y, dx_mm, dy_mm)
        self.apply(lambda p, r: F.move_shapes(p, r, shape_ids, bx - ax, by - ay), "Move " + noun(len(shape_ids), "shape"),
                   operation="figure.shape.move", panel_ids=[panel_id])

    def _shape_targets(self) -> dict[str, set[str]]:
        sel = {p.id for p in self.selected_panels()}
        return {k: v for k, v in self.selected_shapes.items() if k in sel and v}

    def update_selected_shapes(self, **fields) -> tuple[int, int]:
        targets = self._shape_targets()
        if not targets:
            return 0, 0
        return self.apply(lambda p, r: F.update_shapes(p, r, targets.get(p.id, ()), fields),
                          "Edit " + noun(sum(len(v) for v in targets.values()), "shape"), operation="figure.shape.set",
                          panel_ids=list(targets))

    def delete_selected_shapes(self) -> tuple[int, int]:
        targets = self._shape_targets()
        if not targets:
            return 0, 0
        out = self.apply(lambda p, r: F.delete_shapes(p, r, targets.get(p.id, ())),
                         "Delete " + noun(sum(len(v) for v in targets.values()), "shape"), operation="figure.shape.delete",
                         panel_ids=list(targets))
        self.selected_shapes = {}
        self.insets_inspector.refresh()
        return out

    def copy_shapes(self) -> int:
        """The selected shapes of the first selected panel (all its shapes
        when none is selected)."""
        sel = self.selected_panels()
        if not sel:
            return 0
        chosen = self.selected_shapes.get(sel[0].id) or {sh.id for sh in sel[0].shapes}
        self.clip_shapes = [sh.model_dump(mode="json") for sh in sel[0].shapes if sh.id in chosen]
        self.status_message.emit(f"{plural(len(self.clip_shapes), 'shape')} copied")
        self.insets_inspector.refresh()
        return len(self.clip_shapes)

    def paste_shapes(self) -> tuple[int, int]:
        """Into every selected panel, same raw coordinates; one gesture."""
        if not self.clip_shapes:
            return 0, 0
        clip = list(self.clip_shapes)
        out = self.apply(lambda p, r: F.paste_shapes(p, r, clip), "Paste " + noun(len(clip), "shape"),
                         operation="figure.shape.paste")
        self.insets_inspector.refresh()
        return out

    # ========================================================= check / export
    # ========================================================== checks
    def _save_and_check(self) -> None:
        self._save_project()
        self.schedule_check()

    def schedule_check(self) -> None:
        """Live mode: re-check LIVE_CHECK_MS after the last committed gesture."""
        if self.checks.live_cb.isChecked():
            self._check_timer.start()

    def _live_check(self) -> None:
        if any(ed.has_pending for ed in self._editors.values()):
            self._check_timer.start()               # a gesture is still open: wait for it
            return
        self.run_checks(full=False)

    def _compute_issues(self, saturation: bool) -> list[C.Issue]:
        fig = self.current_figure()
        if fig is None or self.project is None:
            return []
        ws, project = self.workspace, self.project

        def status(ref) -> str:
            return check_image(ws, project, ref).value if ws is not None else (
                "pending_hash" if ref.sha256 is None else "ok")
        return C.check_figure(project, fig, source_status=status,
                              crop_plane=(lambda p, r, c: self.renderer.crop_plane(r, p, c))
                              if saturation else None)

    def run_checks(self, full: bool = False) -> list[C.Issue]:
        """Check the current figure (sources by size/date; `full` adds
        saturation on each crop) and show the result in the Checks panel,
        on its tab and in the status bar."""
        if full:
            self.flush()
        self._check_timer.stop()
        issues = self._compute_issues(saturation=full)
        self.last_issues = issues
        self.checks.show_issues(issues, full=full)
        n = C.counts(issues)
        marks = " ".join(f"{C.LEVEL_ICON[lv]}{k}" for lv, k in n.items() if k)
        i = self.inspector_tabs.indexOf(self.checks)
        self.inspector_tabs.setTabText(i, f"Checks {marks}" if marks else "Checks ✓")
        self.inspector_tabs.setTabToolTip(i, f"Consistency: {C.summary(issues)}")
        self.checks_changed.emit(("Checks: " + counts_text(issues)) if self.current_figure()
                                 else "")
        return issues

    def check_consistency(self, show: bool = False, saturation: bool = True) -> list[C.Issue]:
        """Run the §2.9 check on the current figure (full by default) and,
        with `show`, bring the Checks panel to the front (non-modal)."""
        issues = self.run_checks(full=saturation)
        self.status_message.emit(f"Consistency: {C.summary(issues)}")
        if show:
            self.inspector_tabs.setCurrentWidget(self.checks)
        return issues

    def export_figure(self, path, settings: "X.ExportSettings",
                      confirm: Optional[Callable[[list[C.Issue]], bool]] = None
                      ) -> Optional["X.ExportResult"]:
        """Consistency check first: if it finds anything, `confirm(issues)`
        (default: the dialog's "Export anyway") decides; the warnings are then
        logged with the export. Missing / modified sources refuse."""
        fig = self.current_figure()
        if fig is None or self.workspace is None or self._refuse_export_read_only():
            return None
        issues = self.check_consistency()
        if issues:
            if confirm is None:
                from .export_dialogs import ConsistencyDialog
                dlg = ConsistencyDialog(issues, self.canvas.select_ids, "export", self)
                ok = dlg.exec() == QDialog.Accepted
            else:
                ok = confirm(issues)
            if not ok:
                self.status_message.emit("Export cancelled")
                return None
        self._before_gesture()
        try:
            result = X.export_figure(self.workspace, self.project, fig, self.renderer, path, settings,
                                     overridden=[i.to_dict() for i in issues] or None)
        except X.ExportRefused as exc:
            QMessageBox.warning(self, "Export refused", str(exc))
            self.status_message.emit(f"Export refused: {exc}")
            return None
        self._save()
        self.status_message.emit(f"Exported {plural(len(result.files), 'file')} + {result.sidecar.name}"
                                 + (f" — {'; '.join(result.warnings)}" if result.warnings else ""))
        return result

    def _refuse_export_read_only(self) -> bool:
        """True, after saying so, when the project is open read-only: an
        export is recorded in the project's log, which cannot be saved."""
        if not self.is_read_only():
            return False
        QMessageBox.information(self, "Read-only",
                                "This project is open read-only, so you cannot export from it. "
                                "Exports are recorded in the project's log.")
        return True

    def export_figure_dialog(self) -> None:
        fig = self.current_figure()
        if fig is None or self._refuse_export_read_only():
            return
        from PySide6.QtWidgets import QFileDialog
        from .export_dialogs import ExportDialog
        dlg = ExportDialog(fig.page.preset.dpi, fig.page.n_pages, self,
                           low_res_insets=len(C.insets_below_dpi(self.project, fig)))
        if dlg.exec() != QDialog.Accepted:
            return
        settings = dlg.settings()
        path, _ = QFileDialog.getSaveFileName(self, "Export figure", f"{fig.name}.{settings.format}",
                                              f"{settings.format.upper()} (*.{settings.format})")
        if path:
            self.export_figure(path, settings)

    def export_sources(self, directory, panel_ids: Optional[list[str]] = None
                       ) -> Optional["X.ExportResult"]:
        """Selected panels (all when none is selected) as OME-TIFF."""
        fig = self.current_figure()
        if fig is None or self.workspace is None or self._refuse_export_read_only():
            return None
        ids = panel_ids or [p.id for p in self.selected_panels()] or [p.id for p in fig.panels]
        self._before_gesture()
        try:
            result = X.export_sources(self.workspace, self.project, fig, self.renderer, ids, directory)
        except X.ExportRefused as exc:
            QMessageBox.warning(self, "Export refused", str(exc))
            return None
        self._save()
        self.status_message.emit(f"Source data: {plural(len(result.files), 'OME-TIFF file')} + "
                                 f"{result.sidecar.name}")
        return result

    def export_sources_dialog(self) -> None:
        if self._refuse_export_read_only():
            return
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Export source data to")
        if d:
            self.export_sources(d)

    # ======================================================== panel clipboard
    def copy_panels(self) -> int:
        sel = self.selected_panels()
        if sel:
            self.clip_panels = F.copy_panels(sel, self.current_figure())
            self.status_message.emit(f"{plural(len(sel), 'panel')} copied")
        return len(sel)

    def paste_panels(self) -> list[str]:
        """Copies of the copied panels (labels, rendering, viewport...), 5 mm
        down-right, not locked; one gesture."""
        ed = self.current_editor()
        if ed is None or not self.clip_panels:
            return []
        self.flush()
        self._before_gesture()
        new = F.pasted_panels(self.clip_panels)
        with ed.gesture("figure.panel.paste", f"Paste {plural(len(new), 'panel')}"):
            ed.figure.panels.extend(new)
        self.refresh(selected={p.id for p in new})
        return [p.id for p in new]

    # ================================================================ locks
    def lock_selection(self, confirm: Optional[Callable[[str], bool]] = None) -> Optional[str]:
        """One lock group for the selection; the others take the first
        panel's locked channel settings — confirmed first when they differ
        (`confirm`: default a dialog)."""
        sel = self.selected_panels()
        if len(sel) < 2:
            return None
        differ = F.locked_settings_differ(sel)
        depth = F.bit_depth_warning(self.current_figure(), sel, self.project.images)
        if differ or depth:                # mixed bit depths: ask even if nothing differs
            ref0 = self.ref(sel[0])
            text = (f"{plural(len(differ), 'panel')} will take the levels/LUTs of panel "
                    f"{self.current_figure().panels.index(sel[0]) + 1} "
                    f"({ref0.filename if ref0 else '?'}). Lock?" if differ
                    else f"Lock these {len(sel)} panels?")
            if depth:
                text += "\n\n⚠ " + depth
            mism = self.mismatch_warning([p.id for p in sel])
            if mism:
                text += "\n\n" + mism
            ok = confirm(text) if confirm else QMessageBox.question(
                self, "Lock selection", text) == QMessageBox.Yes
            if not ok:
                return None
        group = f"lock:{F.new_id()}"
        images = self.project.images
        self._layout(lambda ps, _r: F.lock_panels(ps, images, group), "figure.lock.set",
                     "Lock", min_sel=2)
        self.inspector.set_panels(self.selected_panels())
        warn = [w for w in (self.mismatch_warning([p.id for p in sel]),
                            ("⚠ " + depth) if depth else "") if w]
        if warn:
            self.status_message.emit("Locked: " + "; ".join(warn))
        return group

    def unlock_selection(self) -> None:
        def fn(ps, _r):
            for p in ps:
                p.lock_group = None
        self._layout(fn, "figure.lock.clear", "Unlock")
        self.inspector.set_panels(self.selected_panels())

    def select_lock_group(self) -> None:
        fig = self.current_figure()
        groups = {p.lock_group for p in self.selected_panels() if p.lock_group}
        if fig is not None and groups:
            self.canvas.select_ids(p.id for p in fig.panels if p.lock_group in groups)

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
        # No lock propagation: the levels clamp to the new image's bit depth
        # is not an edit to share with the group.
        self.apply(do, f"Replace image by {new.filename}", operation="figure.panel.replace_image",
                   propagate=False)
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
        if self.project is None:
            return None, "no project", None
        return panel_visual(self.project, self.renderer, panel, CANVAS_MAX_SIDE)

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
        self.labels_inspector.set_panels(self.selected_panels())
        self.insets_inspector.set_panels(self.selected_panels())
        self._report_missing()
        self._update_info()
        self._update_enabled()
        self.schedule_check()

    def _on_selection_changed(self, ids: list) -> None:
        self.flush()                      # a gesture never spans two selections
        self.selected_shapes = {k: v for k, v in self.selected_shapes.items() if k in set(ids)}
        self.inspector.set_panels(self.selected_panels())
        self.labels_inspector.set_panels(self.selected_panels())
        self.insets_inspector.set_panels(self.selected_panels())
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
            self.info.setHtml(f"<b>{html.escape(fig.name)}</b><br>{plural(len(fig.panels), 'panel')}."
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
