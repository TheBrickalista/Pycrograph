# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Panel overlays: labels, scale bar and border as vector QGraphicsItems in
page millimetres (design §2.5–2.6).

They are CHILD items of the PanelItem, so they follow the panel when it
moves; they are rebuilt from the model whenever the panel changes (and
live during a resize). Every item carries its kind in `data(KIND_KEY)`
("label", "scalebar", "scalebar-text", "border") so the export (step 6)
can write text as text and lines as lines. They are content, not editor
decorations: they are drawn whatever FigureScene.decorations says.

Layout
------
- Outside labels (top, bottom, left, right, left-vertical, right-vertical)
  stack outward from the panel edge, first label nearest the panel,
  LABEL_GAP_MM from the edge and LINE_GAP_MM apart, never overlapping;
  top/bottom are centred on the panel, left/right and the vertical ones
  are centred on its height.
- Inside labels (four corners) stack from the corner inward, INSIDE_MARGIN_MM
  from the panel edge (plus the border width when a border is shown). A
  scale bar in the same corner comes first, nearest the corner.
- The border is drawn INSIDE the panel edge (stroke inset by half its
  width), so it never changes the panel's page size.
- Fonts: family and size from the page preset unless the label overrides
  them; size in points (1 pt = 25.4/72 mm). Text is laid out at a fixed
  pixel size and scaled to millimetres, so metrics are exact at any zoom.

Insets and shapes (§2.7)
------------------------
- On a parent: one numbered rectangle per inset ("inset-rect" /
  "inset-number"), DERIVED from the inset's crop (insets.rect_on_parent).
  When the parent is selected the rectangle can be dragged (move) or
  resized by its corner handle (aspect kept); the edit is reported through
  FigureScene.on_inset_rect and becomes the inset's crop.
- On an inset: the matching border inside its edge and its number in the
  top-left corner.
- Shapes ("shape") are mapped from raw pixels through the crop,
  orientation and scale (viewport.raw_to_panel); stroke and arrowhead in
  page points. With the panel selected a shape can be clicked (select) and
  dragged (FigureScene.on_shape_click / on_shape_moved).
- Shapes and inset rectangles sit in a clipping container ("clip") the size
  of the panel: nothing is drawn outside the panel.
"""

from __future__ import annotations

import html
import math
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsTextItem

from .. import insets as I
from .. import viewport as V
from ..labels import Run, resolve_label
from ..models import Figure, ImageRef, Panel, Project, Shape
from ..scalebar import ScaleBarUnavailable, bar_geometry

if TYPE_CHECKING:                                   # pragma: no cover
    from .figure_canvas import PanelItem

KIND_KEY = 1
KIND_LABEL = "label"
KIND_SCALEBAR = "scalebar"
KIND_SCALEBAR_TEXT = "scalebar-text"
KIND_BORDER = "border"
KIND_INSET_RECT = "inset-rect"
KIND_INSET_NUMBER = "inset-number"
KIND_INSET_BORDER = "inset-border"
KIND_SHAPE = "shape"
KIND_CLIP = "clip"
KIND_DECORATION = "decoration"
ID_KEY = 3                     # data key: shape id / inset id
OVERLAY_KINDS = (KIND_LABEL, KIND_SCALEBAR, KIND_SCALEBAR_TEXT, KIND_BORDER, KIND_INSET_RECT,
                 KIND_INSET_NUMBER, KIND_INSET_BORDER, KIND_SHAPE, KIND_CLIP)
HANDLE_PX = 8.0
SELECT_PEN = QColor("#2563eb")

PT_MM = 25.4 / 72.0
EM_PX = 100                    # layout font size; items are scaled to mm
LABEL_GAP_MM = 0.5             # panel edge -> first outside label
LINE_GAP_MM = 0.2              # between stacked labels
INSIDE_MARGIN_MM = 1.0         # panel edge (inner border edge) -> inside labels / bar
BAR_TEXT_GAP_MM = 0.3          # scale bar -> its text
OUTSIDE_DEFAULT = "#000000"
INSIDE_DEFAULT = "#FFFFFF"
INSIDE = ("top-left", "top-right", "bottom-left", "bottom-right")


def text_item(runs: list[Run], colour: str, family: str, size_pt: float,
              parent: QGraphicsItem, kind: str) -> QGraphicsTextItem:
    """Rich text (one colour per run) scaled so 1 em = size_pt points."""
    item = QGraphicsTextItem(parent)
    item.document().setDocumentMargin(0)
    font = QFont(family)
    font.setPixelSize(EM_PX)
    item.setFont(font)
    item.setDefaultTextColor(QColor(colour))
    body = "".join(
        f'<span style="color:{r.color}">{html.escape(r.text)}</span>' if r.color
        else html.escape(r.text) for r in runs)
    item.setHtml(f'<span style="white-space:pre">{body}</span>')
    item.setScale(size_pt * PT_MM / EM_PX)
    item.setAcceptedMouseButtons(Qt.NoButton)
    item.setAcceptHoverEvents(False)
    item.setData(KIND_KEY, kind)
    return _keep(parent, item)


def _size_mm(item: QGraphicsTextItem) -> tuple[float, float]:
    r = item.boundingRect()
    return r.width() * item.scale(), r.height() * item.scale()


def _keep(item: "PanelItem", child: QGraphicsItem) -> QGraphicsItem:
    """Hold a Python reference to every overlay item: with a Python-subclassed
    parent, PySide does not reliably keep a child alive once its wrapper is
    garbage-collected (the label silently disappears)."""
    refs = getattr(item, "_overlays", None)
    if refs is None:
        refs = item._overlays = []
    refs.append(child)
    return child


def clear_overlays(item: "PanelItem") -> None:
    for child in getattr(item, "_overlays", []):
        child.setParentItem(None)
        if child.scene() is not None:
            child.scene().removeItem(child)
    item._overlays = []


def _scene_scale(item: QGraphicsItem) -> float:
    sc = item.scene()
    views = sc.views() if sc is not None else []
    return views[0].transform().m11() if views else 1.0


def _number_item(n: int, colour: str, preset, parent: QGraphicsItem) -> QGraphicsTextItem:
    t = text_item([Run(str(n))], colour, preset.font_family, preset.font_size_pt, parent,
                  KIND_INSET_NUMBER)
    return t


class InsetRectItem(QGraphicsRectItem):
    """The derived rectangle of one inset on its parent. Interactive only
    when the parent panel is selected: drag = move, corner = resize with
    the aspect ratio kept. Reports through scene.on_inset_rect."""

    def __init__(self, inset_id: str, panel_item: "PanelItem", rect: QRectF, colour: str,
                 width_mm: float, parent: QGraphicsItem):
        super().__init__(rect, parent)
        self.inset_id = inset_id
        self._panel_item = panel_item
        pen = QPen(QColor(colour), width_mm)
        pen.setJoinStyle(Qt.MiterJoin)
        self.setPen(pen)
        self.setBrush(Qt.NoBrush)
        self.setData(KIND_KEY, KIND_INSET_RECT)
        self.setData(ID_KEY, inset_id)
        self._drag: Optional[tuple[str, QPointF, QRectF]] = None
        self.number: Optional[QGraphicsTextItem] = None

    def _handle(self) -> QRectF:
        s = HANDLE_PX / max(_scene_scale(self), 1e-6)
        r = self.rect()
        return QRectF(r.right() - s, r.bottom() - s, s, s)

    def shape(self) -> QPainterPath:              # clickable: the stroke band + handle
        path = QPainterPath()
        r = self.rect()
        tol = 4.0 / max(_scene_scale(self), 1e-6)
        path.addRect(r.adjusted(-tol, -tol, tol, tol))
        inner = r.adjusted(tol, tol, -tol, -tol)
        if inner.isValid():
            hole = QPainterPath()
            hole.addRect(inner)
            path = path.subtracted(hole)
        path.addRect(self._handle())
        return path

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        if self._panel_item.isSelected() and getattr(self.scene(), "decorations", True):
            painter.fillRect(self._handle(), QColor(self.pen().color()))

    def set_rect_live(self, r: QRectF) -> None:
        self.prepareGeometryChange()
        self.setRect(r)
        if self.number is not None:
            self.number.setPos(r.left() + 0.3, r.top() + 0.1)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self._panel_item.isSelected():
            event.ignore()                       # -> the panel (selection / move)
            return
        mode = "resize" if self._handle().contains(event.pos()) else "move"
        self._drag = (mode, event.pos(), QRectF(self.rect()))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        mode, p0, r0 = self._drag
        d = event.pos() - p0
        if mode == "move":
            r = r0.translated(d)
        else:
            k = max((r0.width() + d.x()) / r0.width(), (r0.height() + d.y()) / r0.height(), 0.05)
            r = QRectF(r0.topLeft(), r0.size() * k)
        self.set_rect_live(r)
        cb = getattr(self.scene(), "on_inset_rect", None)
        if cb:
            cb(self.inset_id, self._panel_item.panel_id, self.rect(), False)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return
        self._drag = None
        cb = getattr(self.scene(), "on_inset_rect", None)
        if cb:
            cb(self.inset_id, self._panel_item.panel_id, self.rect(), True)


def shape_path(points_mm: list[tuple[float, float]], kind: str, head_mm: float) -> QPainterPath:
    path = QPainterPath()
    if len(points_mm) < 2:
        return path
    (x0, y0), (x1, y1) = points_mm[0], points_mm[1]
    if kind in ("rect", "ellipse"):
        r = QRectF(QPointF(min(x0, x1), min(y0, y1)), QPointF(max(x0, x1), max(y0, y1)))
        path.addRect(r) if kind == "rect" else path.addEllipse(r)
        return path
    if kind == "line":
        path.moveTo(x0, y0)
        path.lineTo(x1, y1)
        return path
    # arrow: line to the base of a filled head at (x1, y1)
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1e-9:
        return path
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    head = min(head_mm, length)
    bx, by = x1 - ux * head, y1 - uy * head
    half = head * 0.4
    path.moveTo(x0, y0)
    path.lineTo(bx, by)
    path.addPolygon(QPolygonF([QPointF(x1, y1), QPointF(bx - uy * half, by + ux * half),
                               QPointF(bx + uy * half, by - ux * half), QPointF(x1, y1)]))
    path.closeSubpath()
    return path


class ShapeItem(QGraphicsPathItem):
    """A shape on a panel. Interactive when its panel is selected: click
    selects it (Ctrl/Shift adds), drag moves the selected shapes of that
    panel. Reports through scene.on_shape_click / on_shape_moved."""

    def __init__(self, shape: Shape, path: QPainterPath, panel_item: "PanelItem",
                 parent: QGraphicsItem, selected: bool):
        super().__init__(path, parent)
        self.shape_id = shape.id
        self.selected_shape = selected
        self._panel_item = panel_item
        pen = QPen(QColor(shape.color), shape.width_pt * PT_MM)
        pen.setCapStyle(Qt.FlatCap if shape.kind == "arrow" else Qt.RoundCap)
        pen.setJoinStyle(Qt.MiterJoin)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(shape.color)) if shape.kind == "arrow" else Qt.NoBrush)
        self.setData(KIND_KEY, KIND_SHAPE)
        self.setData(ID_KEY, shape.id)
        self._press: Optional[QPointF] = None

    def shape(self) -> QPainterPath:              # easy to hit: a band around the stroke
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.pen().widthF(), 6.0 / max(_scene_scale(self), 1e-6)))
        return stroker.createStroke(self.path()).united(self.path())

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        if (self.selected_shape and self._panel_item.isSelected()
                and getattr(self.scene(), "decorations", True)):
            pen = QPen(SELECT_PEN, 1, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.path().boundingRect())

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self._panel_item.isSelected():
            event.ignore()
            return
        additive = bool(event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))
        if not additive:              # highlight updated here: no overlay rebuild mid-press
            for sib in self.parentItem().childItems():
                if isinstance(sib, ShapeItem) and sib is not self and sib.selected_shape:
                    sib.selected_shape = False
                    sib.update()
        self.selected_shape = True
        self.update()
        cb = getattr(self.scene(), "on_shape_click", None)
        if cb:
            cb(self._panel_item.panel_id, self.shape_id, additive)
        self._press = event.scenePos()
        event.accept()

    def _movers(self) -> list["ShapeItem"]:
        sibs = [c for c in self.parentItem().childItems() if isinstance(c, ShapeItem)]
        return [c for c in sibs if c.selected_shape] or [self]

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        d = event.scenePos() - self._press
        for it in self._movers():
            it.setPos(d)

    def mouseReleaseEvent(self, event) -> None:
        if self._press is None:
            return
        d = event.scenePos() - self._press
        self._press = None
        if abs(d.x()) < 1e-9 and abs(d.y()) < 1e-9:
            return
        cb = getattr(self.scene(), "on_shape_moved", None)
        if cb:
            cb(self._panel_item.panel_id, [it.shape_id for it in self._movers()], d.x(), d.y())


def _clip_container(item: "PanelItem", w: float, h: float) -> QGraphicsRectItem:
    clip = QGraphicsRectItem(QRectF(0, 0, w, h), item)
    clip.setPen(QPen(Qt.NoPen))
    clip.setBrush(Qt.NoBrush)
    clip.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)
    clip.setAcceptedMouseButtons(Qt.NoButton)
    clip.setData(KIND_KEY, KIND_CLIP)
    return _keep(item, clip)


def build_overlays(item: "PanelItem", panel: Panel, ref: Optional[ImageRef],
                   project: Optional[Project], figure: Figure,
                   time_unit: Optional[str] = None,
                   selected_shapes: frozenset = frozenset()) -> None:
    """(Re)create the panel's overlays. Sets item.missing (unresolved label
    tokens) and item.scale_bar_error / item.scale_bar_note."""
    clear_overlays(item)
    preset = figure.page.preset
    w, h = panel.width_mm, panel.height_mm
    missing: list[str] = []
    item.scale_bar_error = None
    item.scale_bar_note = None

    # ---- border (inside the edge)
    inset = 0.0
    if panel.border.visible:
        bw = panel.border.width_pt * PT_MM
        inset = bw
        rect = QGraphicsRectItem(QRectF(bw / 2, bw / 2, max(w - bw, 0.0), max(h - bw, 0.0)), item)
        pen = QPen(QColor(panel.border.color), bw)
        pen.setJoinStyle(Qt.MiterJoin)
        rect.setPen(pen)
        rect.setBrush(Qt.NoBrush)
        rect.setAcceptedMouseButtons(Qt.NoButton)
        rect.setData(KIND_KEY, KIND_BORDER)
        _keep(item, rect)
    # ---- an inset: matching border inside the edge + its number
    style = panel.inset_style if panel.inset_of else None
    if style is not None and style.border_on_inset:
        bw = style.width_pt * PT_MM
        rect = QGraphicsRectItem(QRectF(inset + bw / 2, inset + bw / 2, max(w - 2 * inset - bw, 0.0),
                                        max(h - 2 * inset - bw, 0.0)), item)
        pen = QPen(QColor(style.color), bw)
        pen.setJoinStyle(Qt.MiterJoin)
        rect.setPen(pen)
        rect.setBrush(Qt.NoBrush)
        rect.setAcceptedMouseButtons(Qt.NoButton)
        rect.setData(KIND_KEY, KIND_INSET_BORDER)
        _keep(item, rect)
        inset += bw
    margin = INSIDE_MARGIN_MM + inset

    # ---- corner stacks: next free offset from the corner edge, per corner
    corner_cursor = {c: margin for c in INSIDE}
    if style is not None:
        n = I.inset_number(figure, panel)
        if n:
            num = _number_item(n, style.color, figure.page.preset, item)
            num.setPos(margin, margin)
            corner_cursor["top-left"] += _size_mm(num)[1] + LINE_GAP_MM

    # ---- shapes and inset rectangles, clipped to the panel
    insets = I.insets_of(figure, panel.id) if ref is not None else []
    if (panel.shapes or insets) and ref is not None:
        clip = _clip_container(item, w, h)
        s = ref.metadata.shape
        for sh in panel.shapes:
            pts = [V.raw_to_panel(panel, s.X, s.Y, x, y) for x, y in sh.points]
            si = ShapeItem(sh, shape_path(pts, sh.kind, sh.head_size_pt * PT_MM), item, clip,
                           sh.id in selected_shapes)
            _keep(item, si)
        for k, child in enumerate(insets, start=1):
            r = I.rect_on_parent(panel, child, ref)
            st = child.inset_style
            if r is None or st is None:
                continue
            ri = InsetRectItem(child.id, item, QRectF(*r), st.color, st.width_pt * PT_MM, clip)
            _keep(item, ri)
            ri.number = _number_item(k, st.color, figure.page.preset, ri)
            ri.number.setPos(r[0] + 0.3, r[1] + 0.1)

    # ---- scale bar
    bar = panel.scale_bar
    if bar.visible and ref is not None:
        try:
            geo = bar_geometry(panel, ref)
        except ScaleBarUnavailable as exc:
            item.scale_bar_error = str(exc)
        else:
            item.scale_bar_note = geo.note
            t = bar.thickness_pt * PT_MM
            txt = None
            tw = th = 0.0
            if bar.show_label:
                txt = text_item([Run(geo.text)], bar.color, preset.font_family,
                                bar.font_size_pt or preset.font_size_pt, item, KIND_SCALEBAR_TEXT)
                tw, th = _size_mm(txt)
            block_h = t + ((BAR_TEXT_GAP_MM + th) if txt is not None else 0.0)
            block_w = max(geo.length_mm, tw)
            corner = bar.position
            left = corner.endswith("left")
            top = corner.startswith("top")
            bx = margin + (block_w - geo.length_mm) / 2 if left else \
                w - margin - block_w + (block_w - geo.length_mm) / 2
            by0 = corner_cursor[corner] if top else h - corner_cursor[corner] - block_h
            if txt is not None and bar.label_position == "above":
                text_y, bar_y = by0, by0 + th + BAR_TEXT_GAP_MM
            else:
                bar_y, text_y = by0, by0 + t + BAR_TEXT_GAP_MM
            rect = QGraphicsRectItem(QRectF(bx, bar_y, geo.length_mm, t), item)
            rect.setPen(QPen(Qt.NoPen))
            rect.setBrush(QBrush(QColor(bar.color)))
            rect.setAcceptedMouseButtons(Qt.NoButton)
            rect.setData(KIND_KEY, KIND_SCALEBAR)
            _keep(item, rect)
            if txt is not None:
                txt.setPos(bx + (geo.length_mm - tw) / 2, text_y)
            corner_cursor[corner] += block_h + LINE_GAP_MM

    # ---- labels
    stacks: dict[str, list[tuple[QGraphicsTextItem, float, float]]] = {}
    for label in panel.labels:
        runs, miss = resolve_label(label.text, panel, ref, project, time_unit)
        missing += miss
        if not runs:
            continue
        inside = label.position in INSIDE
        colour = label.color or (INSIDE_DEFAULT if inside else OUTSIDE_DEFAULT)
        ti = text_item(runs, colour, label.font_family or preset.font_family,
                       label.font_size_pt or preset.font_size_pt, item, KIND_LABEL)
        ti.setData(KIND_KEY + 1, label.position)
        lw, lh = _size_mm(ti)
        stacks.setdefault(label.position, []).append((ti, lw, lh))
    _place_labels(stacks, w, h, corner_cursor, margin)
    item.missing = list(dict.fromkeys(missing))


def _place_labels(stacks, w: float, h: float, corner_cursor: dict[str, float],
                  side: float) -> None:
    for pos, items in stacks.items():
        if pos == "top":
            y = -LABEL_GAP_MM
            for ti, lw, lh in items:
                y -= lh
                ti.setPos((w - lw) / 2, y)
                y -= LINE_GAP_MM
        elif pos == "bottom":
            y = h + LABEL_GAP_MM
            for ti, lw, lh in items:
                ti.setPos((w - lw) / 2, y)
                y += lh + LINE_GAP_MM
        elif pos in ("left", "right"):
            total = sum(lh for _t, _w, lh in items) + LINE_GAP_MM * (len(items) - 1)
            y = (h - total) / 2
            for ti, lw, lh in items:
                ti.setPos(-LABEL_GAP_MM - lw if pos == "left" else w + LABEL_GAP_MM, y)
                y += lh + LINE_GAP_MM
        elif pos == "left-vertical":
            x = -LABEL_GAP_MM
            for ti, lw, lh in items:            # rotated -90°: reads bottom to top
                ti.setRotation(-90)
                ti.setPos(x - lh, (h + lw) / 2)
                x -= lh + LINE_GAP_MM
        elif pos == "right-vertical":
            x = w + LABEL_GAP_MM
            for ti, lw, lh in items:            # rotated +90°: reads top to bottom
                ti.setRotation(90)
                ti.setPos(x + lh, (h - lw) / 2)
                x += lh + LINE_GAP_MM
        else:                                   # inside corners
            top = pos.startswith("top")
            left = pos.endswith("left")
            margin = corner_cursor[pos]
            for ti, lw, lh in items:
                x = side if left else w - side - lw
                if top:
                    ti.setPos(x, margin)
                else:
                    ti.setPos(x, h - margin - lh)
                margin += lh + LINE_GAP_MM
            corner_cursor[pos] = margin


def label_rects(item: "PanelItem") -> list[tuple[str, QRectF]]:
    """(position, rect in the panel's coordinates) of every label item:
    for tests and the export."""
    out = []
    for child in item.childItems():
        if child.data(KIND_KEY) == KIND_LABEL:
            out.append((child.data(KIND_KEY + 1),
                        child.mapRectToParent(child.boundingRect())))
    return out
