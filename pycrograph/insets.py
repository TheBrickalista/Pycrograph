# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Dynamic insets (docs/design/figure-editor.md §2.7), pure.

An inset is an ordinary Panel with ``inset_of = parent.id`` and an
``InsetStyle``; the region it magnifies is ITS OWN crop. The rectangle drawn
on the parent is derived from that crop at render time
(`rect_on_parent`), never stored on the parent: pan/zoom/resize of the
inset moves the rectangle, and editing the rectangle writes the inset's
crop (`set_from_parent_rect`). Rotations and flips of either panel go
through viewport.raw_rect_to_panel / panel_rect_to_raw.

Insets of one parent are numbered in figure order (1, 2, ...) and each has
its own colour (rectangle, number and the matching border on the inset).
With follow_parent_tz / follow_parent_rendering the inset takes the parent's
T/Z/projection / channel settings whenever the parent is edited
(`sync_insets`, called by FigureEditor.edit).
"""

from __future__ import annotations

from typing import Optional

from . import viewport as V
from .models import Figure, ImageRef, InsetStyle, Panel, ZProjection

INSET_COLORS = ("#FFFF00", "#00FFFF", "#FF00FF", "#00FF00", "#FF8000", "#FFFFFF")
DEFAULT_MAGNIFICATION = 2.0
Rect = tuple[float, float, float, float]


def insets_of(figure: Figure, parent_id: str) -> list[Panel]:
    return [p for p in figure.panels if p.inset_of == parent_id]


def inset_number(figure: Figure, inset: Panel) -> int:
    """1-based number among its parent's insets (figure order)."""
    siblings = insets_of(figure, inset.inset_of) if inset.inset_of else []
    return next((i + 1 for i, p in enumerate(siblings) if p.id == inset.id), 0)


def next_color(figure: Figure, parent_id: str) -> str:
    used = {p.inset_style.color for p in insets_of(figure, parent_id) if p.inset_style}
    return next((c for c in INSET_COLORS if c not in used),
                INSET_COLORS[len(insets_of(figure, parent_id)) % len(INSET_COLORS)])


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def place(figure: Figure, parent: Panel, w: float, h: float, gap_mm: float) -> tuple[float, float]:
    """Right of the parent, moved down past panels it would overlap, kept on
    the page (shifted left if needed), snapped to the page grid."""
    page_w, page_h = figure.page.size_mm
    x = parent.x_mm + parent.width_mm + gap_mm
    if x + w > page_w:
        x = max(0.0, page_w - w)
    y = parent.y_mm
    others = [(p.x_mm, p.y_mm, p.width_mm, p.height_mm) for p in figure.panels
              if p.page == parent.page and p.id != parent.id]
    moved = True
    while moved:
        moved = False
        for r in others:
            if _overlaps((x, y, w, h), r):
                y = r[1] + r[3] + gap_mm
                moved = True
    if figure.page.snap:
        g = figure.page.grid_mm
        x = min(max(0.0, -(-x // g) * g), max(0.0, page_w - w))       # snap up, stay on page
        y = -(-y // g) * g
    return round(x, 6), round(max(0.0, y), 6)


def create_inset(figure: Figure, parent: Panel, ref: ImageRef, raw_rect: Rect,
                 new_id: str, magnification: float = DEFAULT_MAGNIFICATION,
                 gap_mm: float = 2.0) -> Panel:
    """A new inset of `parent` showing `raw_rect` (raw pixels) at
    `magnification` × the parent's (µm per page-mm divided by it), placed
    right of the parent. Rendering, T/Z and orientation copied from the
    parent; labels, shapes and lock group are not."""
    s = ref.metadata.shape
    ppm = V.px_per_mm(parent, s.X, s.Y) / magnification
    x, y, w, h = raw_rect
    ow, oh = V.oriented_wh(w, h, parent.rotation_deg)
    inset = parent.model_copy(deep=True, update={
        "id": new_id, "inset_of": parent.id, "lock_group": None, "labels": [], "shapes": [],
        "inset_style": InsetStyle(color=next_color(figure, parent.id)),
        "width_mm": round(ow / ppm, 6), "height_mm": round(oh / ppm, 6)})
    V.set_view(inset, x + w / 2.0, y + h / 2.0, ppm)
    inset.x_mm, inset.y_mm = place(figure, parent, inset.width_mm, inset.height_mm, gap_mm)
    return inset


def rect_on_parent(parent: Panel, inset: Panel, ref: ImageRef) -> Optional[Rect]:
    """The inset's region in the parent's panel-local mm (None: not shown,
    or the inset shows another image)."""
    if inset.inset_of != parent.id or inset.image_id != parent.image_id:
        return None
    if inset.inset_style is not None and not inset.inset_style.show_on_parent:
        return None
    s = ref.metadata.shape
    c = V.effective_crop(inset, s.X, s.Y)
    return V.raw_rect_to_panel(parent, s.X, s.Y, (c.x, c.y, c.w, c.h))


def set_from_parent_rect(inset: Panel, parent: Panel, ref: ImageRef, rect_mm: Rect) -> None:
    """Edit of the rectangle on the parent -> the inset's crop: centred on
    the rectangle, scale so the rectangle fits the (unchanged) inset panel."""
    s = ref.metadata.shape
    x, y, w, h = V.panel_rect_to_raw(parent, s.X, s.Y, rect_mm)
    ow, oh = V.oriented_wh(w, h, inset.rotation_deg)
    ppm = max(ow / inset.width_mm, oh / inset.height_mm)
    V.set_view(inset, x + w / 2.0, y + h / 2.0, ppm)


def sync_inset(inset: Panel, parent: Panel, inset_ref: ImageRef) -> None:
    """Apply the follow flags: T/Z/projection and/or channel settings."""
    style = inset.inset_style or InsetStyle()
    s = inset_ref.metadata.shape
    if style.follow_parent_tz:
        inset.t = min(parent.t, s.T - 1)
        inset.z = min(parent.z, s.Z - 1)
        p = parent.z_projection
        inset.z_projection = None if p is None or s.Z < 2 else ZProjection(
            z_start=min(p.z_start, s.Z - 1), z_end=min(p.z_end, s.Z - 1))
    if style.follow_parent_rendering:
        inset.channels = [c.model_copy(deep=True) for c in parent.channels
                          if c.index < s.C]


def sync_insets(figure: Figure, parent: Panel, images: dict[str, ImageRef]) -> list[str]:
    """Sync every inset of `parent`; returns ids of insets changed."""
    changed = []
    for inset in insets_of(figure, parent.id):
        ref = images.get(inset.image_id)
        if ref is None:
            continue
        before = inset.model_dump()
        sync_inset(inset, parent, ref)
        if inset.model_dump() != before:
            changed.append(inset.id)
    return changed


def remap_links(panels: list[Panel], old_to_new: dict[str, str]) -> None:
    """After copying a set of panels: an inset whose parent was copied too
    points at the copy; one whose parent was not becomes an ordinary panel."""
    for p in panels:
        if p.inset_of in old_to_new:
            p.inset_of = old_to_new[p.inset_of]
        elif p.inset_of is not None:
            p.inset_of = None
            p.inset_style = None
