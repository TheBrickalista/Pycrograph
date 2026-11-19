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
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem

from ..labels import Run, resolve_label
from ..models import Figure, ImageRef, Panel, Project
from ..scalebar import ScaleBarUnavailable, bar_geometry

if TYPE_CHECKING:                                   # pragma: no cover
    from .figure_canvas import PanelItem

KIND_KEY = 1
KIND_LABEL = "label"
KIND_SCALEBAR = "scalebar"
KIND_SCALEBAR_TEXT = "scalebar-text"
KIND_BORDER = "border"
OVERLAY_KINDS = (KIND_LABEL, KIND_SCALEBAR, KIND_SCALEBAR_TEXT, KIND_BORDER)

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


def build_overlays(item: "PanelItem", panel: Panel, ref: Optional[ImageRef],
                   project: Optional[Project], figure: Figure) -> None:
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
    margin = INSIDE_MARGIN_MM + inset

    # ---- corner stacks: next free offset from the corner edge, per corner
    corner_cursor = {c: margin for c in INSIDE}

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
        runs, miss = resolve_label(label.text, panel, ref, project)
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
