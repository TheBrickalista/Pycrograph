# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Main window: top-level tabs Home · Library · Figures · Preferences · About.

The Library tab is the session-1 view (tree on the left, single-image viewer
in the centre, per-channel inspector on the right); settings made there are
the image's defaults, copied into a panel by "Add to figure". The Figures
tab is the figure editor (ui/figures_tab.py).

Model changes follow PysternBlot's rule: the screen updates on every tick of
a continuous control, but one operation-log entry (old -> new) and one save
happen per gesture, so project.json and the hash-chained log never disagree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout,
    QInputDialog, QLabel, QMainWindow, QMessageBox, QProgressDialog, QPushButton,
    QScrollArea, QSlider, QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .. import __version__
from ..display import apply_orientation, auto_window, compute_saturation_stats, render_composite
from ..integrity import build_integrity_report, write_integrity_json
from ..models import ChannelSettings, ImageRef, Project, SaturationStats
from ..readers import IMPORT_FILE_FILTER, ImageSource, UnsupportedImageError, open_image
from ..render import PanelRenderer
from ..storage import (
    HashCancelled, RefStatus, RelinkError, Workspace, add_image_to_dataset, check_image,
    common_prefix_change, copy_into_project, create_dataset, log_operation, register_image,
    relink_by_prefix, relink_image, remove_image_from_dataset, rename_dataset,
    set_dataset_hidden, set_image_annotation, set_image_hash, sha256_file, write_thumbnail,
)
from .channel_controls import ChannelControls
from .figures_tab import FiguresTab
from .home_tab import HomeTab
from .info_tabs import AboutTab, PreferencesTab
from .library_panel import LibraryPanel
from .workers import HashQueue
from .zoomable_graphics_view import ZoomableGraphicsView

IDLE_COMMIT_MS = 700          # same as PysternBlot gesture_commit
SECTION_STYLE = """
    QFrame#section {
        background: #f4f4f4;
        border: 1px solid #d2d2d2;
        border-radius: 8px;
    }
"""


def _format_time(seconds: float) -> str:
    if abs(seconds) >= 120:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.1f} s"


class MainWindow(QMainWindow):
    def __init__(self, workspace: Workspace, auto_open: bool = True):
        super().__init__()
        self.ws = workspace
        self.ws.ensure()
        self.project: Optional[Project] = None
        self.ref: Optional[ImageRef] = None
        self.source: Optional[ImageSource] = None
        self._planes: dict[int, np.ndarray] = {}
        self._saturation: dict[int, SaturationStats] = {}
        self._controls: dict[int, ChannelControls] = {}
        self._status: dict[str, str] = {}
        # Pending gesture: field path -> (image_id, old_value, new_value)
        self._pending: dict[str, tuple[str, Any, Any]] = {}

        self.setWindowTitle("Pycrograph")
        self.setMinimumSize(1000, 640)
        self.resize(1400, 860)

        self.hashq = HashQueue(self)
        self.hashq.progress.connect(self._on_hash_progress)
        self.hashq.finished.connect(self._on_hash_finished)
        self.hashq.failed.connect(self._on_hash_failed)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(IDLE_COMMIT_MS)
        self._idle_timer.timeout.connect(self.flush_pending)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(15)
        self._render_timer.timeout.connect(self._render)

        self.renderer = PanelRenderer(lambda ref: self.ws.resolve_image_path(self.project, ref))
        self._build_ui()
        self._build_toolbar()
        self.statusBar().showMessage(f"Pycrograph {__version__} — workspace {self.ws.root}")

        if auto_open:
            projects = self.ws.list_projects()
            if projects:
                self.open_project_by_id(projects[0].id)
        self._update_enabled()

    # ================================================================== UI
    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.home = HomeTab()
        self.home.set_lister(lambda archived: self.ws.list_projects(include_archived=archived))
        self.home.new_project_requested.connect(self.new_project)
        self.home.open_project_requested.connect(self._open_from_home)
        self.home.archive_requested.connect(self._archive_from_home)
        self.home.figure_requested.connect(self.show_figure)
        self.home.new_figure_requested.connect(self._new_figure_from_home)
        self.tabs.addTab(self.home, "Home")

        self.library_tab = self._build_library_tab()
        self.tabs.addTab(self.library_tab, "Library")

        self.figures_tab = FiguresTab(self.renderer, save=self.save,
                                      before_gesture=self.flush_pending)
        self.figures_tab.status_message.connect(lambda m: self.statusBar().showMessage(m, 4000))
        self.tabs.addTab(self.figures_tab, "Figures")

        self.preferences_tab = PreferencesTab()
        self.tabs.addTab(self.preferences_tab, "Preferences")
        self.about_tab = AboutTab()
        self.tabs.addTab(self.about_tab, "About")

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)
        self.home.refresh_requested()
        self.home.set_open_project(None)

    def _on_tab_changed(self, _index: int) -> None:
        self.flush_pending()                    # a gesture never spans two tabs
        w = self.tabs.currentWidget()
        if w is self.home:
            self.home.refresh_requested()
            self.home.set_open_project(self.project)

    def _build_library_tab(self) -> QWidget:
        split = QSplitter(Qt.Horizontal)

        self.library = LibraryPanel()
        self.library.setMinimumWidth(280)
        self.library.image_selected.connect(self._on_image_selected)
        self.library.annotation_committed.connect(self._on_annotation)
        self.library.context_action.connect(self._on_context_action)
        split.addWidget(self.library)

        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(4, 4, 4, 4)
        self.view = ZoomableGraphicsView()
        self.view.setBackgroundBrush(QBrush(QColor("#2b2b2b")))
        self.scene = QGraphicsScene(self)
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setTransformationMode(Qt.FastTransformation)   # show pixels as pixels
        self.scene.addItem(self.pixmap_item)
        self.placeholder = self.scene.addText("")
        self.placeholder.setDefaultTextColor(QColor("#d1d5db"))
        f = QFont()
        f.setPointSize(14)
        self.placeholder.setFont(f)
        self.view.setScene(self.scene)
        cl.addWidget(self.view, 1)

        nav = QHBoxLayout()
        self.t_label = QLabel("T")
        self.t_slider = QSlider(Qt.Horizontal)
        self.z_label = QLabel("Z")
        self.z_slider = QSlider(Qt.Horizontal)
        for s in (self.t_slider, self.z_slider):
            s.setMinimum(0)
            s.valueChanged.connect(self._on_tz_moved)
            s.sliderReleased.connect(self.flush_pending)
        nav.addWidget(self.t_label)
        nav.addWidget(self.t_slider, 1)
        nav.addWidget(self.z_label)
        nav.addWidget(self.z_slider, 1)
        fit = QPushButton("Fit")
        fit.clicked.connect(self.view.fit_scene)
        nav.addWidget(fit)
        cl.addLayout(nav)
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        cl.addWidget(self.info_label)
        split.addWidget(centre)

        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setMinimumWidth(380)
        self.inspector = QWidget()
        self.inspector_layout = QVBoxLayout(self.inspector)
        self.inspector_layout.setContentsMargins(6, 6, 6, 6)
        self.inspector_layout.setSpacing(8)
        self.inspector_scroll.setWidget(self.inspector)
        split.addWidget(self.inspector_scroll)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([320, 700, 400])
        self._show_placeholder("Create a project (New Project…) and import MetaMorph TIFF/STK files.")
        self._rebuild_inspector()
        return split

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setIconSize(QSize(16, 16))
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            return a

        act("New Project…", self.new_project)
        act("Open Project…", self.open_project)
        self.a_archive = act("Archive Project", self.archive_project,
                             "Soft-hide this project (restorable from Open Project…)")
        tb.addSeparator()
        self.a_dataset = act("New Dataset…", self.new_dataset)
        self.a_import = act("Import Images…", lambda: self.import_images(),
                            "Register files by reference (no copy). SHA-256 computed in background.")
        self.a_add_to_figure = act("Add to Figure", self.add_selected_to_figure,
                                   "Add the selected library image to the current figure "
                                   "(copies its display settings)")
        tb.addSeparator()
        self.a_verify = act("Check Sources", self.check_all_sources,
                            "Check every image file (exists, size, date); re-hash the changed ones")
        self.a_integrity = act("Integrity Report…", self.export_integrity)
        tb.addSeparator()
        act("About", self.show_about)

    def _update_enabled(self) -> None:
        has = self.project is not None
        for a in (self.a_archive, self.a_dataset, self.a_import, self.a_verify, self.a_integrity):
            a.setEnabled(has)
        self.a_add_to_figure.setEnabled(has and self.ref is not None)
        self.setWindowTitle(f"Pycrograph — {self.project.project.name}" if has else "Pycrograph")

    def _section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("section")
        frame.setStyleSheet(SECTION_STYLE)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 10)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: 600; font-size: 12px;")
        lay.addWidget(lbl)
        return frame, lay

    def _rebuild_inspector(self) -> None:
        while self.inspector_layout.count():
            item = self.inspector_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._controls.clear()
        ref = self.ref
        if ref is None:
            lbl = QLabel("Select an image in the library.")
            lbl.setStyleSheet("color: #6b7280;")
            self.inspector_layout.addWidget(lbl)
            self.inspector_layout.addStretch(1)
            return

        title = QLabel("Display (non-destructive)")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.inspector_layout.addWidget(title)

        frame, lay = self._section("Orientation")
        row = QHBoxLayout()
        for text, tip, slot in (("⟲", "Rotate 90° counter-clockwise", lambda: self._rotate(-90)),
                                ("⟳", "Rotate 90° clockwise", lambda: self._rotate(90)),
                                ("⇋", "Flip horizontally", lambda: self._flip("flip_horizontal")),
                                ("⇵", "Flip vertically", lambda: self._flip("flip_vertical"))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(36)
            b.clicked.connect(slot)
            row.addWidget(b)
        self.orient_label = QLabel()
        row.addWidget(self.orient_label, 1)
        lay.addLayout(row)
        self.inspector_layout.addWidget(frame)
        self._update_orient_label()

        full_scale = ref.metadata.full_scale
        for info in ref.metadata.channels:
            cs = self._channel_settings(info.index)
            w = ChannelControls(info, cs, full_scale)
            w.set_stain(ref.channel_stains.get(info.index, ""))
            w.changed.connect(self._on_channel_changed)
            w.stain_committed.connect(self._on_stain)
            w.auto_requested.connect(self._on_auto)
            self._controls[info.index] = w
            self.inspector_layout.addWidget(w)
        self.inspector_layout.addStretch(1)

    def _show_placeholder(self, text: str) -> None:
        self.pixmap_item.setPixmap(QPixmap())
        self.placeholder.setPlainText(text)
        self.placeholder.setVisible(True)
        self.scene.setSceneRect(self.placeholder.boundingRect())
        self.view.fit_scene()

    # ============================================================ projects
    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if not ok or not name.strip():
            return
        self._close_project()
        self.project = self.ws.create_project(name)
        create_dataset(self.project, "Unsorted")
        self.ws.save_project(self.project)
        self._after_project_opened()

    def open_project(self) -> None:
        metas = self.ws.list_projects(include_archived=True)
        if not metas:
            QMessageBox.information(self, "Open project", "No project in this workspace yet.")
            return
        labels = [f"{m.name}{'  (archived)' if m.is_archived else ''}  —  {m.modified_utc or m.created_utc}"
                  for m in metas]
        choice, ok = QInputDialog.getItem(self, "Open project", "Project:", labels, 0, False)
        if not ok:
            return
        meta = metas[labels.index(choice)]
        self.open_project_by_id(meta.id)
        if meta.is_archived and self.project is not None:
            if QMessageBox.question(self, "Archived project",
                                    "This project is archived. Restore it?") == QMessageBox.Yes:
                self.ws.set_project_archived(self.project, False)

    def open_project_by_id(self, project_id: str) -> None:
        try:
            project = self.ws.load_project(project_id)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Open project", f"Cannot open project:\n{exc}")
            return
        self._close_project()
        self.project = project
        self._after_project_opened()

    def archive_project(self) -> None:
        if self.project is None:
            return
        if QMessageBox.question(self, "Archive project",
                                f"Archive “{self.project.project.name}”? It is hidden, not deleted, "
                                "and can be restored from Open Project….") != QMessageBox.Yes:
            return
        self.flush_pending()
        self.ws.set_project_archived(self.project, True)
        self._close_project()
        self._update_enabled()
        self.home.refresh_requested()

    def _close_project(self) -> None:
        self.flush_pending()
        self.hashq.cancel_all()
        self._close_source()
        self.project = None
        self.ref = None
        self._status.clear()
        self.figures_tab.set_project(None)
        self.home.set_open_project(None)
        self.library.set_project(None)
        self._rebuild_inspector()
        self._show_placeholder("No project open.")

    def _after_project_opened(self) -> None:
        p = self.project
        self.library.set_project(p)
        self.figures_tab.set_project(p)
        self.home.set_open_project(p)
        self.home.refresh_requested()
        self._update_enabled()
        for ref in p.images.values():
            thumb = self.ws.thumbnail_path(ref.id)
            if thumb.is_file():
                self.library.set_thumbnail(ref.id, thumb)
        self.check_all_sources(quiet=True)
        if not p.images:
            self._show_placeholder("Import images (Import Images…) into a dataset.")

    def save(self) -> None:
        if self.project is not None:
            self.ws.save_project(self.project)

    def _open_from_home(self, project_id: str) -> None:
        if self.project is None or self.project.project.id != project_id:
            self.open_project_by_id(project_id)
        if self.project is not None:
            self.tabs.setCurrentWidget(self.library_tab)

    def _archive_from_home(self, project_id: str, archived: bool) -> None:
        if self.project is not None and self.project.project.id == project_id:
            if archived:
                self.archive_project()
            else:
                self.ws.set_project_archived(self.project, False)
            self.home.refresh_requested()
            return
        try:
            other = self.ws.load_project(project_id)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Archive project", f"Cannot open project:\n{exc}")
            return
        self.ws.set_project_archived(other, archived)
        self.home.refresh_requested()

    # ============================================================= figures
    def show_figure(self, figure_id: str) -> None:
        self.figures_tab.select_figure(figure_id)
        self.tabs.setCurrentWidget(self.figures_tab)

    def _new_figure_from_home(self) -> None:
        fig = self.figures_tab.new_figure()
        if fig is not None:
            self.home.set_open_project(self.project)
            self.show_figure(fig.id)

    def add_to_figure(self, image_ids: list[str]) -> list:
        """Library → figure: one panel per image with a copy of its current
        display settings (pending library edits are committed first)."""
        self.flush_pending()
        panels = self.figures_tab.add_images(image_ids)
        if panels:
            self.tabs.setCurrentWidget(self.figures_tab)
        return panels

    def add_selected_to_figure(self) -> None:
        if self.ref is not None:
            self.add_to_figure([self.ref.id])

    # ============================================================ datasets
    def new_dataset(self) -> None:
        if self.project is None:
            return
        name, ok = QInputDialog.getText(self, "New dataset", "Dataset name (e.g. a condition):")
        if not ok or not name.strip():
            return
        ds = create_dataset(self.project, name)
        self.save()
        self.library.refresh()
        self.library.select_dataset(ds.id)

    def _target_dataset(self) -> Optional[str]:
        p = self.project
        ds_id = self.library.selected_dataset_id()
        if ds_id:
            return ds_id
        visible = [d for d in p.datasets if not d.hidden]
        if len(visible) == 1:
            return visible[0].id
        if not visible:
            ds = create_dataset(p, "Unsorted")
            return ds.id
        names = [d.name for d in visible]
        choice, ok = QInputDialog.getItem(self, "Import into", "Dataset:", names, 0, False)
        return visible[names.index(choice)].id if ok else None

    # ============================================================== import
    def import_images(self, dataset_id: Optional[str] = None, paths: Optional[list[str]] = None) -> list[str]:
        """Register files by reference. Returns registered image ids."""
        if self.project is None:
            return []
        if paths is None:
            paths, _ = QFileDialog.getOpenFileNames(self, "Import images (by reference)", "",
                                                    IMPORT_FILE_FILTER)
            if not paths:
                return []
        dataset_id = dataset_id or self._target_dataset()
        if not dataset_id:
            return []
        ids, errors = [], []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for path in paths:
                try:
                    ref = register_image(self.project, path, dataset_id)
                except (OSError, UnsupportedImageError, ValueError) as exc:
                    errors.append(f"{Path(path).name}: {exc}")
                    continue
                ids.append(ref.id)
                thumb = write_thumbnail(self.ws, self.project, ref)
                if thumb:
                    self.library.set_thumbnail(ref.id, thumb)
        finally:
            QApplication.restoreOverrideCursor()
        self.save()
        self.library.refresh()
        for iid in ids:
            ref = self.project.images[iid]
            if ref.sha256 is None:
                self._set_status(iid, RefStatus.PENDING_HASH.value)
                self.hashq.submit(iid, self.ws.resolve_image_path(self.project, ref))
        if ids:
            self.library.select_image(ids[-1])
        if errors:
            QMessageBox.warning(self, "Import", "Some files were not imported:\n\n" + "\n".join(errors))
        return ids

    # ============================================================= hashing
    def _set_status(self, image_id: str, status: str) -> None:
        self._status[image_id] = status
        self.library.set_status(image_id, status)

    def check_all_sources(self, quiet: bool = False) -> dict[str, str]:
        """Cheap check of every image; queue background hashes where needed."""
        if self.project is None:
            return {}
        counts: dict[str, int] = {}
        for ref in self.project.images.values():
            st = check_image(self.ws, self.project, ref)
            self._set_status(ref.id, st.value)
            counts[st.value] = counts.get(st.value, 0) + 1
            if st in (RefStatus.PENDING_HASH, RefStatus.CHANGED):
                self.hashq.submit(ref.id, self.ws.resolve_image_path(self.project, ref))
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no images"
        self.statusBar().showMessage(f"Sources: {summary}")
        if not quiet and counts.get("missing"):
            QMessageBox.warning(self, "Missing files",
                                f"{counts['missing']} image file(s) not found (drive unplugged or "
                                "renamed?). Right-click an image → Relink….")
        return dict(self._status)

    def _on_hash_progress(self, image_id: str, done, total) -> None:
        ref = self.project.images.get(image_id) if self.project else None
        if ref is not None and total:
            self.statusBar().showMessage(f"Hashing {ref.filename}: {100 * done // total} %")

    def _on_hash_finished(self, image_id: str, path: str, digest: str, size, mtime_ns) -> None:
        p = self.project
        ref = p.images.get(image_id) if p else None
        if ref is None or str(self.ws.resolve_image_path(p, ref)) != path:
            return                                   # project switched or image relinked meanwhile
        self.flush_pending()
        if ref.sha256 is None:
            set_image_hash(p, image_id, digest, size, mtime_ns)
            status = RefStatus.OK
        elif digest == ref.sha256:
            old = ref.mtime_ns
            ref.size_bytes, ref.mtime_ns = int(size), int(mtime_ns)
            log_operation(p, "image.verify", target_type="image", target_id=image_id,
                          asset_sha256=digest, field="mtime_ns", old_value=old, new_value=ref.mtime_ns,
                          note="file date changed, content identical (SHA-256 verified)")
            status = RefStatus.OK
        else:
            log_operation(p, "image.verify", target_type="image", target_id=image_id,
                          asset_sha256=ref.sha256, new_value=digest,
                          note="CONTENT DIFFERS from the imported file (SHA-256 mismatch)")
            status = RefStatus.MODIFIED
            self.statusBar().showMessage(f"⚠ {ref.filename} was modified since import")
        self.save()
        self._set_status(image_id, status.value)
        if not self.hashq.pending:
            self.statusBar().showMessage("All hashes up to date", 4000)

    def _on_hash_failed(self, image_id: str, message: str) -> None:
        ref = self.project.images.get(image_id) if self.project else None
        if ref is not None:
            self.statusBar().showMessage(f"Hash failed for {ref.filename}: {message}")

    def _hash_with_progress(self, path: Path, title: str) -> Optional[str]:
        dlg = QProgressDialog(f"Verifying {path.name}…", "Cancel", 0, 100, self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)

        def progress(done, total):
            dlg.setValue(int(100 * done / total) if total else 100)
            QApplication.processEvents()

        try:
            return sha256_file(path, progress=progress, cancelled=dlg.wasCanceled)
        except HashCancelled:
            return None
        finally:
            dlg.close()

    # ======================================================= context menu
    def _on_context_action(self, action: str, kind: str, ident: str) -> None:
        p = self.project
        if p is None:
            return
        if action == "new_dataset":
            self.new_dataset()
        elif action == "import":
            self.import_images(ident)
        elif action == "rename_dataset":
            ds = p.dataset(ident)
            name, ok = QInputDialog.getText(self, "Rename dataset", "Name:", text=ds.name)
            if ok and name.strip():
                rename_dataset(p, ident, name)
                self.save()
                self.library.refresh()
        elif action == "hide_dataset":
            set_dataset_hidden(p, ident, True)
            self.save()
            self.library.refresh()
        elif action == "relink":
            self.relink(ident)
        elif action == "verify":
            ref = p.images[ident]
            if ref.sha256 is None:
                return
            self._set_status(ident, RefStatus.CHANGED.value)
            self.hashq.submit(ident, self.ws.resolve_image_path(p, ref))
        elif action == "copy_into":
            self.copy_into(ident)
        elif action == "add_to_figure":
            self.add_to_figure([ident])
        elif action == "add_to_dataset":
            names = [d.name for d in p.datasets if not d.hidden]
            choice, ok = QInputDialog.getItem(self, "Add to dataset", "Dataset:", names, 0, False)
            if ok:
                ds = next(d for d in p.datasets if d.name == choice and not d.hidden)
                add_image_to_dataset(p, ident, ds.id)
                self.save()
                self.library.refresh()
        elif action == "remove_from_dataset":
            image_id, ds_id = ident.split("|", 1)
            remove_image_from_dataset(p, image_id, ds_id)
            self.save()
            self.library.refresh()

    def relink(self, image_id: str) -> None:
        p = self.project
        ref = p.images[image_id]
        old_path = ref.path
        start = str(Path(old_path).parent) if Path(old_path).parent.is_dir() else str(Path.home())
        new_path, _ = QFileDialog.getOpenFileName(self, f"Locate {ref.filename}", start, IMPORT_FILE_FILTER)
        if not new_path:
            return
        digest = self._hash_with_progress(Path(new_path), "Relink")
        if digest is None:
            return
        allow_unverified = False
        if ref.sha256 is None:
            if QMessageBox.question(
                    self, "Relink", "This image's hash was never recorded, so the new file cannot "
                    "be verified against the imported one. Relink anyway (recorded as unverified)?"
            ) != QMessageBox.Yes:
                return
            allow_unverified = True
        self.flush_pending()
        try:
            relink_image(p, image_id, new_path, sha256=digest, allow_unverified=allow_unverified)
        except RelinkError as exc:
            QMessageBox.warning(self, "Relink refused", str(exc))
            return
        old_prefix, new_prefix = common_prefix_change(old_path, ref.path)
        others = [r for r in p.images.values() if r.id != image_id and r.path.startswith(old_prefix + "/")
                  and check_image(self.ws, p, r) == RefStatus.MISSING]
        if others and QMessageBox.question(
                self, "Relink others",
                f"{len(others)} other missing image(s) were under\n{old_prefix}\n\n"
                f"Try the same change ({new_prefix}) for them? Each file is verified by SHA-256."
        ) == QMessageBox.Yes:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                done = relink_by_prefix(self.ws, p, old_prefix, new_prefix)
            finally:
                QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"Relinked {len(done) + 1} image(s)")
        self.save()
        self.renderer.invalidate()
        self.figures_tab.refresh()
        self.check_all_sources(quiet=True)
        if self.ref is not None and self.ref.id == image_id:
            self._on_image_selected(image_id)

    def copy_into(self, image_id: str) -> None:
        self.flush_pending()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            copy_into_project(self.ws, self.project, image_id)
        except (OSError, RelinkError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Copy into project", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.save()
        self._set_status(image_id, check_image(self.ws, self.project, self.project.images[image_id]).value)

    # ======================================================== annotations
    def _on_annotation(self, image_id: str, field: str, value) -> None:
        if self.project is None:
            return
        self.flush_pending()
        set_image_annotation(self.project, image_id, field, value)
        self.save()

    def _on_stain(self, index: int, text: str) -> None:
        if self.ref is None:
            return
        stains = dict(self.ref.channel_stains)
        if text:
            stains[index] = text
        else:
            stains.pop(index, None)
        self._on_annotation(self.ref.id, "channel_stains", stains)

    # ============================================================== viewer
    def _close_source(self) -> None:
        if self.source is not None:
            self.source.close()
        self.source = None
        self._planes.clear()
        self._saturation.clear()

    def _on_image_selected(self, image_id: str) -> None:
        self.flush_pending()
        self._close_source()
        self.ref = self.project.images.get(image_id) if (self.project and image_id) else None
        self._update_enabled()
        self._rebuild_inspector()
        ref = self.ref
        if ref is None:
            self.t_slider.setEnabled(False)
            self.z_slider.setEnabled(False)
            self.info_label.setText("")
            self._show_placeholder("Select an image." if self.project and self.project.images
                                   else "Import images (Import Images…) into a dataset.")
            return
        s = ref.metadata.shape
        for slider, n, value in ((self.t_slider, s.T, ref.display.t), (self.z_slider, s.Z, ref.display.z)):
            slider.blockSignals(True)
            slider.setMaximum(max(0, n - 1))
            slider.setValue(min(value, n - 1))
            slider.setEnabled(n > 1)
            slider.blockSignals(False)
        self._update_info_label()
        path = self.ws.resolve_image_path(self.project, ref)
        try:
            self.source = open_image(path)
        except FileNotFoundError:
            self._show_placeholder(f"Source file not found:\n{path}\n\nRight-click the image → Relink….")
            return
        except (OSError, UnsupportedImageError, ValueError) as exc:
            self._show_placeholder(f"Cannot read source:\n{exc}")
            return
        self._load_planes()
        self._render(fit=True)

    def _channel_settings(self, index: int) -> ChannelSettings:
        d = self.ref.display
        cs = next((c for c in d.channels if c.index == index), None)
        if cs is None:
            cs = ChannelSettings(index=index, white=self.ref.metadata.full_scale)
            d.channels.append(cs)
            d.channels.sort(key=lambda c: c.index)
        return cs

    def _load_planes(self) -> None:
        self._planes.clear()
        self._saturation.clear()
        if self.source is None or self.ref is None:
            return
        d = self.ref.display
        fs = self.ref.metadata.full_scale
        for info in self.ref.metadata.channels:
            plane = self.source.get_plane(d.t, d.z, info.index)
            self._planes[info.index] = plane
            sat = compute_saturation_stats(plane, fs)
            self._saturation[info.index] = sat
            ctrl = self._controls.get(info.index)
            if ctrl is not None:
                ctrl.set_plane(plane, sat)

    def _render(self, fit: bool = False) -> None:
        if self.ref is None or not self._planes:
            return
        d = self.ref.display
        settings = [self._channel_settings(i) for i in sorted(self._planes)]
        planes = [self._planes[i] for i in sorted(self._planes)]
        rgb = render_composite(planes, settings)
        rgb = apply_orientation(rgb, d.rotation_deg, d.flip_horizontal, d.flip_vertical)
        self._last_rgb = rgb                       # keep the buffer alive for QImage
        h, w = rgb.shape[:2]
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        self.pixmap_item.setPixmap(QPixmap.fromImage(qimg))
        self.placeholder.setVisible(False)
        self.scene.setSceneRect(0, 0, w, h)
        if fit or self.view.in_fit_mode:
            self.view.fit_scene()

    def _update_info_label(self) -> None:
        ref = self.ref
        m = ref.metadata
        cal = m.calibration
        parts = [f"{m.shape.X} × {m.shape.Y} px", f"{m.dtype} ({m.bit_depth}-bit)"]
        if cal.is_calibrated:
            parts.append(f"{cal.pixel_size_x:g} {cal.unit}/px  →  "
                         f"{m.shape.X * cal.pixel_size_x:.1f} × {m.shape.Y * cal.pixel_size_y:.1f} {cal.unit}")
        else:
            parts.append("not calibrated")
        if m.objective:
            parts.append(m.objective)
        parts.append(ref.reader)
        self.info_label.setText("  ·  ".join(parts))
        t, z = ref.display.t, ref.display.z
        t_txt = f"T {t + 1}/{m.shape.T}"
        if m.time_offsets_s and t < len(m.time_offsets_s):
            t_txt += f"  (t = {_format_time(m.time_offsets_s[t])})"
        self.t_label.setText(t_txt)
        z_txt = f"Z {z + 1}/{m.shape.Z}"
        if cal.z_step and m.shape.Z > 1:
            z_txt += f"  ({z * cal.z_step:.2f} {cal.unit})"
        self.z_label.setText(z_txt)

    # ================================================= changes + gestures
    def _record(self, field_path: str, old: Any, new: Any) -> None:
        if field_path in self._pending:
            iid, first_old, _ = self._pending[field_path]
            self._pending[field_path] = (iid, first_old, new)
        else:
            self._pending[field_path] = (self.ref.id, old, new)

    def flush_pending(self) -> None:
        """Commit every pending gesture: one log entry each, then one save."""
        self._idle_timer.stop()
        if not self._pending or self.project is None:
            self._pending.clear()
            return
        pending, self._pending = self._pending, {}
        touched: dict[str, ImageRef] = {}
        for field_path, (iid, old, new) in pending.items():
            if old == new or iid not in self.project.images:
                continue
            ref = self.project.images[iid]
            log_operation(self.project, "display.set", target_type="image", target_id=iid,
                          asset_sha256=ref.sha256, field=field_path, old_value=old, new_value=new)
            touched[iid] = ref
        if touched:
            self.save()
            # Thumbnails follow the display settings (cheap: one plane per channel).
            for ref in touched.values():
                thumb = write_thumbnail(self.ws, self.project, ref)
                if thumb:
                    self.library.set_thumbnail(ref.id, thumb)

    def _on_channel_changed(self, index: int, field: str, value, final: bool) -> None:
        if self.ref is None:
            return
        cs = self._channel_settings(index)
        old = getattr(cs, field)
        if field == "black":
            value = max(0, min(int(value), cs.white - 1))
        elif field == "white":
            value = min(self.ref.metadata.full_scale, max(int(value), cs.black + 1))
        setattr(cs, field, value)
        self._record(f"display.channels[{index}].{field}", old, value)
        if field == "lut":
            ctrl = self._controls.get(index)
            if ctrl is not None:
                ctrl.set_lut_text(value)
        self._render_timer.start()
        if final:
            self.flush_pending()
        else:
            self._idle_timer.start()

    def _on_auto(self, index: int) -> None:
        plane = self._planes.get(index)
        if plane is None or self.ref is None:
            return
        lo, hi = auto_window(plane)
        fs = self.ref.metadata.full_scale
        hi = min(hi, fs)
        lo = min(lo, hi - 1)
        cs = self._channel_settings(index)
        self._record(f"display.channels[{index}].black", cs.black, lo)
        self._record(f"display.channels[{index}].white", cs.white, hi)
        cs.black, cs.white = lo, hi
        self._controls[index].set_settings(cs)
        self._render()
        self.flush_pending()

    def _on_tz_moved(self, _value: int) -> None:
        if self.ref is None:
            return
        d = self.ref.display
        for axis, slider in (("t", self.t_slider), ("z", self.z_slider)):
            new = slider.value()
            old = getattr(d, axis)
            if new != old:
                setattr(d, axis, new)
                self._record(f"display.{axis}", old, new)
        self._update_info_label()
        self._load_planes()
        self._render_timer.start()
        if not (self.t_slider.isSliderDown() or self.z_slider.isSliderDown()):
            self._idle_timer.start()

    def _rotate(self, delta: int) -> None:
        if self.ref is None:
            return
        d = self.ref.display
        old = d.rotation_deg
        d.rotation_deg = (old + delta) % 360
        self._record("display.rotation_deg", old, d.rotation_deg)
        self._update_orient_label()
        self._render(fit=True)
        self.flush_pending()

    def _flip(self, attr: str) -> None:
        if self.ref is None:
            return
        d = self.ref.display
        old = getattr(d, attr)
        setattr(d, attr, not old)
        self._record(f"display.{attr}", old, not old)
        self._update_orient_label()
        self._render()
        self.flush_pending()

    def _update_orient_label(self) -> None:
        if self.ref is None:
            return
        d = self.ref.display
        flips = [n for n, on in (("H", d.flip_horizontal), ("V", d.flip_vertical)) if on]
        self.orient_label.setText(f"Rotation {d.rotation_deg}°" + (f" · flip {'+'.join(flips)}" if flips else ""))

    # =========================================================== exports
    def export_integrity(self) -> None:
        if self.project is None:
            return
        self.flush_pending()
        default = str(Path.home() / f"{self.project.project.name}_integrity.json")
        path, _ = QFileDialog.getSaveFileName(self, "Integrity report", default, "JSON (*.json)")
        if not path:
            return
        verify = QMessageBox.question(
            self, "Integrity report", "Re-hash every source file now? (slow for large files; "
            "otherwise the size/date check is used)") == QMessageBox.Yes
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            report = build_integrity_report(self.ws, self.project, verify_hashes=verify)
            write_integrity_json(report, path)
            log_operation(self.project, "export.integrity_report", target_type="export",
                          target_id=self.project.project.id, new_value=path,
                          note=f"log chain: {report['log_chain']['status']}; hashes re-verified: {verify}")
            self.save()
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Integrity report written: {path}", 6000)

    def show_about(self) -> None:
        self.tabs.setCurrentWidget(self.about_tab)

    # ============================================================ closing
    def closeEvent(self, event) -> None:
        self.flush_pending()
        self.hashq.cancel_all()
        self._close_source()
        self.renderer.close()
        super().closeEvent(event)
