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
    QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QStyle,
    QStyleOptionGraphicsItem, QWidget,
)

from ..figures import MIN_PANEL_MM, Geometry, snap_value
from ..models import Figure, Panel

PAGE_GAP_MM = 10.0
HANDLE_PX = 9.0
SELECT_COLOR = QColor("#2563eb")
CANVAS_BG = QColor("#9ca3af")

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
        self.set_visual(visual)
        self._resizing = False
        self._press_size: tuple[float, float] = (0.0, 0.0)
        self._press_scene = QPointF()
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                      | QGraphicsItem.ItemSendsGeometryChanges)
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
        if option.state & QStyle.State_Selected:
            pen = QPen(SELECT_COLOR, 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(r)
            painter.fillRect(self._handle_rect(), SELECT_COLOR)

    # ---- interaction -----------------------------------------------------
    def _snap(self, v: float) -> float:
        page = self._figure.page
        return snap_value(v, page.grid_mm) if page.snap else v

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and not self._resizing:
            off = page_offset_mm(self._figure, self.page)
            return QPointF(self._snap(value.x()), self._snap(value.y() - off) + off)
        return super().itemChange(change, value)

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
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._resizing:
            super().mouseMoveEvent(event)
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
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        scene = self.scene()
        if isinstance(scene, FigureScene):
            scene.commit_geometry(resized=self if self._resizing else None)
        self._resizing = False


class FigureScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure: Optional[Figure] = None
        self.on_commit: Optional[CommitCallback] = None
        self._committing = False

    def panel_items(self) -> list[PanelItem]:
        return [it for it in self.items() if isinstance(it, PanelItem)]

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

    # ---- content ---------------------------------------------------------
    def set_figure(self, figure: Optional[Figure], provider: Optional[PixmapProvider] = None,
                   selected: Optional[set[str]] = None) -> None:
        sc = self._scene
        sc.blockSignals(True)
        sc.clear()
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
            for z, panel in enumerate(figure.panels):
                item = PanelItem(panel, figure, provider(panel) if provider else (None, "", None))
                item.setZValue(z)
                sc.addItem(item)
                if selected and panel.id in selected:
                    item.setSelected(True)
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
