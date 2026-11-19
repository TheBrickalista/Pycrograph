# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Figure canvas: pages at true size (scene unit = 1 mm), one PanelItem per
panel.

Interaction
-----------
- click / Ctrl- or Shift-click / rubber band (drag on empty page) / Ctrl+A
  select; drag moves every selected panel; the bottom-right handle of a
  selected panel resizes it freely (Shift: keep the aspect ratio). A resize
  extends or shrinks the crop at constant scale (viewport.resize): the
  image is never rescaled or stretched.
- The image is drawn at ONE uniform scale into `image_rect` (panel-local
  mm, from viewport.image_rect_mm), clipped to the panel; where the crop
  runs off the image the page background shows.
- Positions and sizes snap to the page grid when PageSetup.snap is on.
  While dragging, alignment guides appear when an edge or centre of the
  grabbed panel comes near one of another panel or of the page, and the
  move snaps to it (layout.best_snap: closest first; ties -> left/top
  edge, then centre, then right/bottom, then the lower target). The selection moves as one block (same offset).
- Arrow keys nudge the selection by 0.1 mm (Shift: 1 mm): the canvas
  emits `nudge_requested`, the Figures tab applies it as a gesture.
- Labels, scale bar and border are vector child items of each panel
  (ui/panel_overlays.py), rebuilt through FigureScene.overlay_builder when
  the panel changes and re-laid out live while resizing.
- Unselected panels have a faint dashed outline. Outlines, selection,
  handles, guides and the page border are *decorations*: drawn only while
  `FigureScene.decorations` is on (the editor), never in an export.
- Ctrl/Cmd + wheel zooms, middle-drag (or wheel) scrolls, double-click on
  empty space fits the pages.

The canvas never writes the model. On mouse release it compares item
geometry with the panels and hands the differences to `on_commit`
(one call per gesture); the Figures tab turns that into one undo step and
one log entry per panel.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QStyle,
    QStyleOptionGraphicsItem, QWidget,
)

from ..figures import MIN_PANEL_MM, Geometry, snap_value
from ..layout import best_snap
from ..models import Figure, Panel
from .panel_overlays import KIND_KEY

PAGE_GAP_MM = 10.0
HANDLE_PX = 9.0
SELECT_COLOR = QColor("#2563eb")
OUTLINE_COLOR = QColor(107, 114, 128, 140)      # faint dashed outline, unselected panels
GUIDE_COLOR = QColor("#db2777")
GUIDE_PX = 6.0                                  # snap distance to a guide, screen pixels
CANVAS_BG = QColor("#9ca3af")
NUDGE_MM = 0.1
NUDGE_SHIFT_MM = 1.0

# (pixmap or None, message shown when there is no pixmap,
#  where the pixmap goes in panel-local mm: (x, y, w, h) or None = whole panel)
PanelVisual = tuple[Optional[QPixmap], str, Optional[tuple[float, float, float, float]]]
PixmapProvider = Callable[[Panel], PanelVisual]
CommitCallback = Callable[[dict[str, Geometry], str], None]


def page_offset_mm(figure: Figure, page: int) -> float:
    _w, h = figure.page.size_mm
    return page * (h + PAGE_GAP_MM)


class PanelItem(QGraphicsRectItem):
    def __init__(self, panel: Panel, figure: Figure, visual: PanelVisual):
        super().__init__(0, 0, panel.width_mm, panel.height_mm)
        self.panel_id = panel.id
        self.page = panel.page
        self._figure = figure
        self.missing: list[str] = []                 # unresolved label tokens
        self.scale_bar_error: Optional[str] = None
        self.scale_bar_note: Optional[str] = None
        self.set_visual(visual)
        self._resizing = False
        self._press_size: tuple[float, float] = (0.0, 0.0)
        self._press_scene = QPointF()
        # Not ItemIsMovable: the canvas moves the selection itself (one offset
        # for the block, grid + guide snapping), see mouseMoveEvent.
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self._move: Optional[dict] = None
        self.setAcceptHoverEvents(True)
        self.setPos(panel.x_mm, panel.y_mm + page_offset_mm(figure, panel.page))
        self.setPen(Qt.NoPen)

    def set_visual(self, visual: PanelVisual) -> None:
        self._pixmap, self._message, rect = visual
        self._image_rect = QRectF(*rect) if rect is not None else None
        self._image_shift = QPointF()          # live resize: centre-keeping offset
        self.update()

    def sync(self, panel: Panel, visual: PanelVisual) -> None:
        """Follow the model (after an inspector edit) without re-creating."""
        self.prepareGeometryChange()
        self.setRect(0, 0, panel.width_mm, panel.height_mm)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, False)
        self.setPos(panel.x_mm, panel.y_mm + page_offset_mm(self._figure, panel.page))
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.set_visual(visual)
        self.build_overlays(panel)

    def build_overlays(self, panel: Panel) -> None:
        scene = self.scene()
        builder = getattr(scene, "overlay_builder", None)
        if builder is not None:
            builder(self, panel)

    @property
    def image_rect(self) -> Optional[QRectF]:
        return self._image_rect.translated(self._image_shift) if self._image_rect else None

    # ---- geometry in page mm ---------------------------------------------
    def geometry_mm(self) -> Geometry:
        r = self.rect()
        return (round(self.pos().x(), 6),
                round(self.pos().y() - page_offset_mm(self._figure, self.page), 6),
                round(r.width(), 6), round(r.height(), 6))

    @property
    def was_resizing(self) -> bool:
        return self._resizing

    def _scale(self) -> float:
        views = self.scene().views() if self.scene() else []
        return views[0].transform().m11() if views else 1.0

    def _handle_rect(self) -> QRectF:
        s = HANDLE_PX / max(self._scale(), 1e-6)
        r = self.rect()
        return QRectF(r.right() - s, r.bottom() - s, s, s)

    def boundingRect(self) -> QRectF:
        s = (HANDLE_PX + 2) / max(self._scale(), 1e-6)
        return self.rect().adjusted(-s / 2, -s / 2, s / 2, s / 2)

    # ---- painting --------------------------------------------------------
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: Optional[QWidget] = None) -> None:
        r = self.rect()
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.save()
            painter.setClipRect(r)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            target = self.image_rect if self._image_rect is not None else r
            painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
            painter.restore()
        else:
            painter.fillRect(r, QColor("#e5e7eb"))
            painter.setPen(QPen(QColor("#b91c1c"), 0))
            f = QFont()
            f.setPointSizeF(max(r.height() / 12.0, 1.0))
            painter.setFont(f)
            painter.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, self._message or "no image")
        scene = self.scene()
        if not getattr(scene, "decorations", True):
            return
        painter.setBrush(Qt.NoBrush)
        if option.state & QStyle.State_Selected:
            pen = QPen(SELECT_COLOR, 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(r)
            painter.fillRect(self._handle_rect(), SELECT_COLOR)
        else:
            pen = QPen(OUTLINE_COLOR, 1, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(r)

    # ---- interaction -----------------------------------------------------
    def _snap(self, v: float) -> float:
        page = self._figure.page
        return snap_value(v, page.grid_mm) if page.snap else v

    def hoverMoveEvent(self, event) -> None:
        on_handle = self.isSelected() and self._handle_rect().contains(event.pos())
        self.setCursor(Qt.SizeFDiagCursor if on_handle else Qt.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.LeftButton and self.isSelected()
                and self._handle_rect().contains(event.pos())):
            self._resizing = True
            self._press_size = (self.rect().width(), self.rect().height())
            self._press_scene = event.scenePos()
            event.accept()
            return
        self._resizing = False
        super().mousePressEvent(event)           # selection (click / Ctrl-click)
        if event.button() == Qt.LeftButton and isinstance(self.scene(), FigureScene):
            moving = [it for it in self.scene().panel_items() if it.isSelected() or it is self]
            self._move = {"press": event.scenePos(), "start": QPointF(self.pos()),
                          "items": [(it, QPointF(it.pos())) for it in moving]}

    def _move_to(self, scene_pos: QPointF) -> None:
        """Move the block: snap the grabbed panel to guides (else the grid)
        and give every moving panel the same offset."""
        m = self._move
        cand = m["start"] + (scene_pos - m["press"])
        scene: FigureScene = self.scene()
        moving = {id(it) for it, _ in m["items"]}
        tol = GUIDE_PX / max(self._scale(), 1e-6)
        w, h = self.rect().width(), self.rect().height()
        off = page_offset_mm(self._figure, self.page)
        xs, ys = scene.guide_targets(self.page, exclude=moving)
        gx = best_snap([cand.x(), cand.x() + w / 2, cand.x() + w], xs, tol)
        gy = best_snap([cand.y(), cand.y() + h / 2, cand.y() + h], ys, tol)
        x = cand.x() + gx[0] if gx else self._snap(cand.x())
        y = cand.y() + gy[0] if gy else self._snap(cand.y() - off) + off
        delta = QPointF(x, y) - m["start"]
        for it, start in m["items"]:
            it.setPos(start + delta)
        scene.show_guides(gx[1] if gx else None, gy[1] if gy else None, self.page)

    def mouseMoveEvent(self, event) -> None:
        if not self._resizing:
            if self._move is not None and event.buttons() & Qt.LeftButton:
                self._move_to(event.scenePos())
                event.accept()
            return
        d = event.scenePos() - self._press_scene
        w0, h0 = self._press_size
        w = max(MIN_PANEL_MM, self._snap(w0 + d.x()))
        h = max(MIN_PANEL_MM, self._snap(h0 + d.y()))
        if event.modifiers() & Qt.ShiftModifier:          # keep the aspect ratio
            k = max(w / w0, h / h0)
            w, h = max(MIN_PANEL_MM, w0 * k), max(MIN_PANEL_MM, h0 * k)
        self.prepareGeometryChange()
        self.setRect(0, 0, w, h)
        # Constant scale, crop centre kept: the image moves by half the growth.
        self._image_shift = QPointF((w - w0) / 2.0, (h - h0) / 2.0)
        panel = self._figure.panel(self.panel_id)
        if panel is not None:                     # labels follow the moving edge
            self.build_overlays(panel.model_copy(update={"width_mm": w, "height_mm": h}))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        self._move = None
        scene = self.scene()
        if isinstance(scene, FigureScene):
            scene.show_guides(None, None, self.page)
            scene.commit_geometry(resized=self if self._resizing else None)
        self._resizing = False



class FigureScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure: Optional[Figure] = None
        self.on_commit: Optional[CommitCallback] = None
        self._committing = False
        self.decorations = True
        # (PanelItem, Panel) -> builds labels / scale bar / border (set by the tab)
        self.overlay_builder: Optional[Callable[[PanelItem, Panel], None]] = None
        self.page_items: list[QGraphicsRectItem] = []
        self.guides: tuple[Optional[QGraphicsLineItem], Optional[QGraphicsLineItem]] = (None, None)

    def panel_items(self) -> list[PanelItem]:
        return [it for it in self.items() if isinstance(it, PanelItem)]

    def set_decorations(self, on: bool) -> None:
        """Editor decorations (outlines, selection, handles, guides, page
        border) on or off. Off = what an export shows."""
        self.decorations = bool(on)
        for page in self.page_items:
            page.setPen(QPen(QColor("#6b7280"), 0) if on else QPen(Qt.NoPen))
        if not on:
            self.show_guides(None, None, 0)
        self.update()

    def add_guides(self) -> None:
        pen = QPen(GUIDE_COLOR, 1, Qt.DashLine)
        pen.setCosmetic(True)
        lines = []
        for _ in range(2):
            ln = QGraphicsLineItem()
            ln.setPen(pen)
            ln.setZValue(1e6)
            ln.setVisible(False)
            ln.setData(KIND_KEY, "decoration")
            self.addItem(ln)
            lines.append(ln)
        self.guides = (lines[0], lines[1])

    def guide_targets(self, page: int, exclude: set) -> tuple[list[float], list[float]]:
        """Edges and centres of the page and of the non-moving panels on it
        (scene coordinates)."""
        w, h = self.figure.page.size_mm
        off = page_offset_mm(self.figure, page)
        xs, ys = [0.0, w / 2, w], [off, off + h / 2, off + h]
        for it in self.panel_items():
            if id(it) in exclude or it.page != page:
                continue
            r = QRectF(it.pos(), it.rect().size())
            xs += [r.left(), r.center().x(), r.right()]
            ys += [r.top(), r.center().y(), r.bottom()]
        return xs, ys

    def show_guides(self, x: Optional[float], y: Optional[float], page: int) -> None:
        vline, hline = self.guides
        if vline is None or self.figure is None:
            return
        w, h = self.figure.page.size_mm
        off = page_offset_mm(self.figure, page)
        on = self.decorations
        vline.setVisible(on and x is not None)
        hline.setVisible(on and y is not None)
        if x is not None:
            vline.setLine(x, off, x, off + h)
        if y is not None:
            hline.setLine(0, y, w, y)

    def commit_geometry(self, resized: Optional[PanelItem] = None) -> None:
        """Compare items with the model; report changes once per gesture."""
        if self.figure is None or self.on_commit is None or self._committing:
            return
        changes: dict[str, Geometry] = {}
        for it in self.panel_items():
            p = self.figure.panel(it.panel_id)
            if p is None:
                continue
            g = it.geometry_mm()
            if g != (round(p.x_mm, 6), round(p.y_mm, 6), round(p.width_mm, 6), round(p.height_mm, 6)):
                changes[it.panel_id] = g
        if not changes:
            return
        self._committing = True
        try:
            if resized is not None:
                self.on_commit(changes, "figure.panel.resize")
            else:
                self.on_commit(changes, "figure.panel.move")
        finally:
            self._committing = False


class FigureCanvas(QGraphicsView):
    selection_changed = Signal(list)           # selected panel ids
    nudge_requested = Signal(float, float)     # dx, dy in mm

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = FigureScene(self)
        self.setScene(self._scene)
        self._scene.selectionChanged.connect(self._emit_selection)
        self.setBackgroundBrush(QBrush(CANVAS_BG))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                            | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.IntersectsItemShape)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self._panning = False
        self._pan_last = QPointF()
        self._fit_mode = True

    @property
    def figure_scene(self) -> FigureScene:
        return self._scene

    def set_commit_callback(self, cb: CommitCallback) -> None:
        self._scene.on_commit = cb

    def set_overlay_builder(self, builder) -> None:
        self._scene.overlay_builder = builder

    # ---- content ---------------------------------------------------------
    def set_figure(self, figure: Optional[Figure], provider: Optional[PixmapProvider] = None,
                   selected: Optional[set[str]] = None) -> None:
        sc = self._scene
        sc.blockSignals(True)
        sc.clear()
        sc.page_items = []
        sc.guides = (None, None)
        sc.figure = figure
        if figure is not None:
            w, h = figure.page.size_mm
            bg = QColor(figure.page.background)
            for i in range(figure.page.n_pages):
                page = QGraphicsRectItem(0, page_offset_mm(figure, i), w, h)
                page.setBrush(QBrush(bg))
                pen = QPen(QColor("#6b7280"), 0)
                page.setPen(pen)
                page.setZValue(-10)
                page.setData(0, f"page:{i}")
                sc.addItem(page)
                sc.page_items.append(page)
            for z, panel in enumerate(figure.panels):
                item = PanelItem(panel, figure, provider(panel) if provider else (None, "", None))
                item.setZValue(z)
                sc.addItem(item)
                item.build_overlays(panel)
                if selected and panel.id in selected:
                    item.setSelected(True)
            sc.add_guides()
            sc.set_decorations(sc.decorations)
            margin = PAGE_GAP_MM
            total_h = page_offset_mm(figure, figure.page.n_pages - 1) + h
            sc.setSceneRect(-margin, -margin, w + 2 * margin, total_h + 2 * margin)
        sc.blockSignals(False)
        self._emit_selection()
        if self._fit_mode:
            self.fit_pages()

    def update_items(self, provider: PixmapProvider, panel_ids=None) -> bool:
        """Re-sync items with the model in place (inspector edits). Returns
        False when the panel set changed and a full set_figure is needed."""
        fig = self._scene.figure
        if fig is None:
            return False
        items = {it.panel_id: it for it in self._scene.panel_items()}
        if set(items) != {p.id for p in fig.panels}:
            return False
        wanted = set(panel_ids) if panel_ids is not None else set(items)
        for panel in fig.panels:
            if panel.id in wanted:
                items[panel.id].sync(panel, provider(panel))
        return True

    def selected_ids(self) -> list[str]:
        return [it.panel_id for it in self._scene.panel_items() if it.isSelected()]

    def item_for(self, panel_id: str) -> Optional[PanelItem]:
        return next((it for it in self._scene.panel_items() if it.panel_id == panel_id), None)

    def select_ids(self, ids) -> None:
        ids = set(ids)
        self._scene.blockSignals(True)
        for it in self._scene.panel_items():
            it.setSelected(it.panel_id in ids)
        self._scene.blockSignals(False)
        self._emit_selection()

    def select_all(self) -> None:
        self.select_ids(it.panel_id for it in self._scene.panel_items())

    def _emit_selection(self) -> None:
        self.selection_changed.emit(self.selected_ids())

    # ---- view ------------------------------------------------------------
    def fit_pages(self) -> None:
        r = self._scene.sceneRect()
        if r.isValid() and not r.isNull():
            self.resetTransform()
            self.fitInView(r, Qt.KeepAspectRatio)
        self._fit_mode = True

    def zoom_by(self, factor: float) -> None:
        self.scale(factor, factor)
        self._fit_mode = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_pages()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        step = NUDGE_SHIFT_MM if event.modifiers() & Qt.ShiftModifier else NUDGE_MM
        d = {Qt.Key_Left: (-step, 0.0), Qt.Key_Right: (step, 0.0),
             Qt.Key_Up: (0.0, -step), Qt.Key_Down: (0.0, step)}.get(event.key())
        if d is not None and self.selected_ids():
            self.nudge_requested.emit(*d)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            d = event.position() - self._pan_last
            self._pan_last = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            self._fit_mode = False
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if not isinstance(self.itemAt(event.position().toPoint()), PanelItem):
            self.fit_pages()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
