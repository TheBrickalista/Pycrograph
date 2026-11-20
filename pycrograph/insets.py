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

Replicated insets (`replicate_inset`) put the same inset on other panels —
the channels of a split row, other conditions — and can stay linked in a
``region_group``. A linked group is ONE inset repeated: its members share
the panel size (width_mm, height_mm) and orientation (rotation, flips) —
only their position on the page is their own — and

- ``region_link == "region"``: every member shows the same raw-pixel
  rectangle. Only between images of compatible geometry (`compatibility`:
  the same image, or the same size and pixel size); it assumes the images
  are registered (same field), which the stage positions can confirm or not.
- ``region_link == "size"``: every member shows a region of the same
  physical size (µm), each at its own position (images of another pixel
  size, or other fields).

`sync_group` (called by FigureEditor at every gesture commit and live tick,
whatever changed the member: an edit, a resize, a layout tool, a rotation)
applies one member's size, orientation and region to the others, exactly:
"region" members get the identical raw rectangle, "size" members a
rectangle of exactly the same µm size around their own centre, at the same
physical magnification (µm per page-mm). A pair that cannot be linked (a
"region" pair across incompatible geometry, or calibrated vs uncalibrated)
is skipped, never forced; `group_invariant_issues` reports it.
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, Literal, Optional, Sequence

from . import viewport as V
from .layout import UNIT_TO_UM, panel_numbers
from .models import Crop, Figure, ImageRef, InsetStyle, Panel, ZProjection

Compatibility = Literal["same_image", "same_geometry", "different"]
LinkMode = Literal["region", "size"]
PIXEL_SIZE_TOL = 1e-6

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


def place(figure: Figure, parent: Panel, w: float, h: float, gap_mm: float,
          extra: Sequence[Panel] = ()) -> tuple[float, float]:
    """Right of the parent, moved down past panels it would overlap, kept on
    the page (shifted left if needed), snapped to the page grid. `extra` =
    panels not yet in the figure that must be avoided too."""
    page_w, page_h = figure.page.size_mm
    x = parent.x_mm + parent.width_mm + gap_mm
    if x + w > page_w:
        x = max(0.0, page_w - w)
    y = parent.y_mm
    others = [(p.x_mm, p.y_mm, p.width_mm, p.height_mm) for p in list(figure.panels) + list(extra)
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


ROW_TOL_MM = 0.5


def _rect(p: Panel) -> Rect:
    return (p.x_mm, p.y_mm, p.width_mm, p.height_mm)


def in_row(figure: Figure, parent: Panel) -> bool:
    """Another ordinary panel on the same page has its top edge level with
    the parent's (within ROW_TOL_MM): the parent is part of a row."""
    return any(q.id != parent.id and q.inset_of is None and q.page == parent.page
               and abs(q.y_mm - parent.y_mm) <= ROW_TOL_MM for q in figure.panels)


def is_below(inset: Panel, parent: Panel) -> bool:
    """The inset sits under its parent (top at or past the parent's bottom,
    overlapping it horizontally)."""
    return (inset.page == parent.page
            and inset.y_mm >= parent.y_mm + parent.height_mm - 1e-6
            and inset.x_mm < parent.x_mm + parent.width_mm
            and parent.x_mm < inset.x_mm + inset.width_mm)


def place_below(figure: Figure, parent: Panel, w: float, h: float, gap_mm: float,
                extra: Sequence[Panel] = (), dx: float = 0.0,
                push: bool = False) -> Optional[tuple[float, float]]:
    """Directly below the parent: left edge at parent.x + dx, top at the
    parent's bottom + gap_mm. With `push`, moved further down past panels it
    would overlap (and snapped to the page grid, like place()). None when
    there is no room (off the page, or an overlap without `push`)."""
    page_w, page_h = figure.page.size_mm
    x, y = parent.x_mm + dx, parent.y_mm + parent.height_mm + gap_mm
    others = [_rect(q) for q in list(figure.panels) + list(extra)
              if q.page == parent.page and q.id != parent.id]
    if push:
        if figure.page.snap:
            g = figure.page.grid_mm
            x, y = -(-x // g) * g, -(-y // g) * g
        moved = True
        while moved:
            moved = False
            for r in others:
                if _overlaps((x, y, w, h), r):
                    y = r[1] + r[3] + gap_mm
                    if figure.page.snap:
                        y = -(-y // figure.page.grid_mm) * figure.page.grid_mm
                    moved = True
    if x < -1e-6 or y < -1e-6 or x + w > page_w + 1e-6 or y + h > page_h + 1e-6:
        return None
    if any(_overlaps((x, y, w, h), r) for r in others):
        return None
    return round(x, 6), round(y, 6)


def create_inset(figure: Figure, parent: Panel, ref: ImageRef, raw_rect: Rect,
                 new_id: str, magnification: float = DEFAULT_MAGNIFICATION,
                 gap_mm: float = 2.0) -> Panel:
    """A new inset of `parent` showing `raw_rect` (raw pixels) at
    `magnification` × the parent's (µm per page-mm divided by it), placed
    right of the parent. Rendering, T/Z and orientation copied from the
    parent; labels, shapes, lock group and scale-bar visibility are not
    copied (the bar's style, unit and length are, hidden, so ticking it on
    matches the parent's). A parent that is part of
    a row (split channels, a lock group laid out in a row…) gets the inset
    directly below it when there is room, so the row stays a row."""
    s = ref.metadata.shape
    ppm = V.px_per_mm(parent, s.X, s.Y) / magnification
    x, y, w, h = raw_rect
    ow, oh = V.oriented_wh(w, h, parent.rotation_deg)
    inset = parent.model_copy(deep=True, update={
        "id": new_id, "inset_of": parent.id, "lock_group": None, "labels": [], "shapes": [],
        "scale_bar": parent.scale_bar.model_copy(update={"visible": False}),
        "inset_style": InsetStyle(color=next_color(figure, parent.id)),
        "width_mm": round(ow / ppm, 6), "height_mm": round(oh / ppm, 6)})
    V.set_view(inset, x + w / 2.0, y + h / 2.0, ppm)
    below = place_below(figure, parent, inset.width_mm, inset.height_mm, gap_mm, push=True) \
        if in_row(figure, parent) else None
    inset.x_mm, inset.y_mm = below or place(figure, parent, inset.width_mm, inset.height_mm, gap_mm)
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


def remap_links(panels: list[Panel], old_to_new: dict[str, str],
                new_group_id: Optional[Callable[[], str]] = None) -> None:
    """After copying a set of panels: an inset whose parent was copied too
    points at the copy; one whose parent was not becomes an ordinary panel.
    Region groups surviving in the copies (see copy_groups_state) get a NEW
    group id, so copies never join the originals' group; a group left with
    one member is cleared."""
    for p in panels:
        if p.inset_of in old_to_new:
            p.inset_of = old_to_new[p.inset_of]
        elif p.inset_of is not None:
            p.inset_of = None
            p.inset_style = None
    renamed: dict[str, str] = {}
    counts: dict[str, int] = {}
    for p in panels:
        g = p.inset_style.region_group if p.inset_style is not None else None
        if g:
            counts[g] = counts.get(g, 0) + 1
    for p in panels:
        g = p.inset_style.region_group if p.inset_style is not None else None
        if not g:
            continue
        if counts[g] < 2:
            p.inset_style = p.inset_style.model_copy(update={"region_group": None})
            continue
        if g not in renamed:
            renamed[g] = f"region:{new_group_id()}" if new_group_id else f"{g}-copy"
        p.inset_style = p.inset_style.model_copy(update={"region_group": renamed[g]})


# ================================================================ replicate
def um_per_px(ref: ImageRef) -> Optional[tuple[float, float]]:
    """(x, y) µm per raw pixel; None if uncalibrated or in an unknown unit."""
    cal = ref.metadata.calibration
    k = UNIT_TO_UM.get(cal.unit)
    if not cal.is_calibrated or k is None:
        return None
    return cal.pixel_size_x * k, cal.pixel_size_y * k


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= PIXEL_SIZE_TOL * max(1.0, abs(a), abs(b))


def compatibility(src_ref: ImageRef, target_ref: ImageRef) -> Compatibility:
    """same_image: the same file. same_geometry: same X / Y size and the same
    pixel size (µm, within 1e-6; two uncalibrated images count as equal).
    different: anything else — only a "size" link is possible."""
    if src_ref.id == target_ref.id:
        return "same_image"
    a, b = src_ref.metadata.shape, target_ref.metadata.shape
    if (a.X, a.Y) != (b.X, b.Y):
        return "different"
    pa, pb = um_per_px(src_ref), um_per_px(target_ref)
    if pa is None and pb is None:
        return "same_geometry"
    if pa is None or pb is None:
        return "different"
    return "same_geometry" if _close(pa[0], pb[0]) and _close(pa[1], pb[1]) else "different"


def stage_position(ref: ImageRef) -> Optional[tuple[float, float]]:
    raw = ref.metadata.raw
    try:
        return float(raw["stage-position-x"]), float(raw["stage-position-y"])
    except (KeyError, TypeError, ValueError):
        return None


def stage_relation(src_ref: ImageRef, target_ref: ImageRef) -> Literal["equal", "differ", "unknown"]:
    """MetaMorph stage-position-x/y: do the two images show the same field?"""
    if src_ref.id == target_ref.id:
        return "equal"
    a, b = stage_position(src_ref), stage_position(target_ref)
    if a is None or b is None:
        return "unknown"
    return "equal" if _close(a[0], b[0]) and _close(a[1], b[1]) else "differ"


def size_comparable(a: ImageRef, b: ImageRef) -> bool:
    """A physical size can be matched only if both images are calibrated, or
    both are not (then in pixels)."""
    return (um_per_px(a) is None) == (um_per_px(b) is None)


def _scale(ref: ImageRef) -> tuple[float, float]:
    """Physical units per pixel: µm when calibrated, else pixels."""
    return um_per_px(ref) or (1.0, 1.0)


def _crop_rect(panel: Panel, ref: ImageRef) -> Rect:
    s = ref.metadata.shape
    c = V.effective_crop(panel, s.X, s.Y)
    return c.x, c.y, c.w, c.h


def _clamp_centre(c: float, size: float, limit: float) -> float:
    if size >= limit:
        return limit / 2.0
    return min(max(c, size / 2.0), limit - size / 2.0)


def size_rect(src_rect: Rect, src_ref: ImageRef, target_ref: ImageRef,
              centre: Optional[tuple[float, float]] = None) -> Rect:
    """A rectangle of the same physical size in the target's pixels, centred
    at `centre` (default: the same fractional position as the source),
    clamped inside the image."""
    x, y, w, h = src_rect
    ss, ts = _scale(src_ref), _scale(target_ref)
    tw, th = w * ss[0] / ts[0], h * ss[1] / ts[1]
    s, t = src_ref.metadata.shape, target_ref.metadata.shape
    if centre is None:
        centre = ((x + w / 2.0) / s.X * t.X, (y + h / 2.0) / s.Y * t.Y)
    cx = _clamp_centre(centre[0], tw, t.X)
    cy = _clamp_centre(centre[1], th, t.Y)
    return cx - tw / 2.0, cy - th / 2.0, tw, th


def _fit_rect(panel: Panel, ref: ImageRef, rect: Rect) -> None:
    """Show `rect` (raw px) in the panel without changing its page size."""
    x, y, w, h = rect
    ow, oh = V.oriented_wh(w, h, panel.rotation_deg)
    V.set_view(panel, x + w / 2.0, y + h / 2.0, max(ow / panel.width_mm, oh / panel.height_mm))


def physical_per_mm(panel: Panel, ref: ImageRef) -> float:
    """Physical units (µm, or px if uncalibrated) per page-mm along X."""
    s = ref.metadata.shape
    return V.px_per_mm(panel, s.X, s.Y) * _scale(ref)[0]


def replicate_inset(figure: Figure, src_inset: Panel, targets: Iterable[Panel],
                    images: dict[str, ImageRef], mode: Optional[LinkMode] = None,
                    link: bool = True, new_id: Callable[[], str] = lambda: "",
                    gap_mm: float = 2.0) -> list[Panel]:
    """The same inset on each target panel (the source's parent and panels
    that are themselves insets are skipped). Region: "region" = the source's
    raw rectangle (only for compatible geometry; incompatible targets fall
    back to "size"), "size" = the same physical size at the same fractional
    centre, clamped (skipped when only one of the two images is calibrated:
    µm cannot be matched to pixels). Default: "region" if compatible, else "size" (per
    target). Same physical magnification, colour, width and flags as the
    source, and the source's scale bar (visible or not; per inset afterwards,
    never propagated). Placement: directly below each target when the source sits below
    its parent or the parents form a row (same vertical gap and left offset as
    the source); otherwise at the source's offset from its parent; place() if
    that position is taken or off the page. With `link`, source and copies share a region_group (the
    source's if it has one) and each copy's region_link is the mode it got.
    The copies are returned, not added to the figure."""
    parent = figure.panel(src_inset.inset_of or "")
    src_ref = images[src_inset.image_id]
    if parent is None or src_inset.inset_style is None:
        raise ValueError("source is not an inset")
    st = src_inset.inset_style
    src_rect = _crop_rect(src_inset, src_ref)
    src_phys = physical_per_mm(src_inset, src_ref)
    dx, dy = src_inset.x_mm - parent.x_mm, src_inset.y_mm - parent.y_mm
    targets = list(targets)
    # Below rule: the source sits below its parent, or the parents form a row
    # (split channels, a lock group in a row): each copy goes directly below
    # its own parent, same vertical gap and left alignment as the source (or
    # left-aligned at gap_mm when the source is elsewhere).
    below: Optional[tuple[float, float]] = None
    if is_below(src_inset, parent):
        below = (dx, src_inset.y_mm - (parent.y_mm + parent.height_mm))
    else:
        parents = [parent] + [t for t in targets if t.inset_of is None and t.id != parent.id]
        if len(parents) > 1 and all(t.page == parent.page and abs(t.y_mm - parent.y_mm) <= ROW_TOL_MM
                                    for t in parents):
            below = (0.0, gap_mm)
    group = st.region_group or (f"region:{new_id()}" if link else None)
    out: list[Panel] = []
    for tgt in targets:
        if tgt.id in (parent.id, src_inset.id) or tgt.inset_of is not None:
            continue
        ref = images.get(tgt.image_id)
        if ref is None:
            continue
        comp = compatibility(src_ref, ref)
        m: LinkMode = mode or ("region" if comp != "different" else "size")
        if m == "region" and comp == "different":
            m = "size"                                   # never a raw rect across geometries
        if m == "size" and not size_comparable(src_ref, ref):
            continue                                     # µm vs pixels: no physical size to match
        rect = src_rect if m == "region" else size_rect(src_rect, src_ref, ref)
        mag = physical_per_mm(tgt, ref) / src_phys
        inset = create_inset(figure, tgt, ref, rect, new_id(), magnification=mag, gap_mm=gap_mm)
        inset.inset_style = st.model_copy(update={
            "region_group": group if link else None, "region_link": m})
        # The SOURCE inset's scale bar (shown or not, same µm length and
        # style): one inset repeated, whatever each target parent shows.
        inset.scale_bar = src_inset.scale_bar.model_copy(deep=True)
        # One inset repeated: the source's page size and orientation, and the
        # region exactly (same raw rect, or the same µm size).
        _match_geometry(inset, src_inset)
        inset.crop = Crop(x=round(rect[0], 6), y=round(rect[1], 6), w=round(rect[2], 6),
                          h=round(rect[3], 6))
        sync_inset(inset, tgt, ref)
        pos = None
        if below:
            pos = place_below(figure, tgt, inset.width_mm, inset.height_mm, below[1], out,
                              dx=below[0])
        else:
            x, y = tgt.x_mm + dx, tgt.y_mm + dy
            page_w, page_h = figure.page.size_mm
            box = (x, y, inset.width_mm, inset.height_mm)
            clash = (x < 0 or y < 0 or x + inset.width_mm > page_w or y + inset.height_mm > page_h
                     or any(_overlaps(box, _rect(q))
                            for q in list(figure.panels) + out if q.page == tgt.page))
            pos = None if clash else (round(x, 6), round(y, 6))
        inset.x_mm, inset.y_mm = pos or place(figure, tgt, inset.width_mm, inset.height_mm,
                                              gap_mm, out)
        out.append(inset)
    if link and out and st.region_group is None:
        src_inset.inset_style = st.model_copy(update={
            "region_group": group, "region_link": mode or "region"})
    return out


def group_members(figure: Figure, group: Optional[str]) -> list[Panel]:
    return [p for p in figure.panels if group and p.inset_style is not None
            and p.inset_style.region_group == group]


def pair_mode(a: Panel, b: Panel, a_ref: ImageRef, b_ref: ImageRef) -> Optional[LinkMode]:
    """How an edit of `a` reaches `b`: "region" only if both are "region"
    members and the geometry is compatible; "size" if either is a "size"
    member; None = never (a "region" link across incompatible geometry)."""
    la, lb = a.inset_style.region_link, b.inset_style.region_link
    if la == "region" and lb == "region":
        return "region" if compatibility(a_ref, b_ref) != "different" else None
    return "size" if size_comparable(a_ref, b_ref) else None


GEOMETRY_KEYS = ("width_mm", "height_mm", "rotation_deg", "flip_horizontal", "flip_vertical")


def _match_geometry(member: Panel, source: Panel) -> None:
    for k in GEOMETRY_KEYS:
        setattr(member, k, getattr(source, k))


def sync_group(figure: Figure, source: Panel, images: dict[str, ImageRef]) -> list[str]:
    """Make every other member of `source`'s group the same inset: its page
    size and orientation, then its region ("region": the identical raw
    rectangle; "size": exactly the same µm size around the member's own
    centre, clamped). Unlinkable pairs (pair_mode None) are left untouched.
    Returns the ids of the members changed."""
    st = source.inset_style
    if st is None or not st.region_group:
        return []
    a_ref = images.get(source.image_id)
    if a_ref is None:
        return []
    rect = _crop_rect(source, a_ref)
    out = []
    for m in group_members(figure, st.region_group):
        b_ref = images.get(m.image_id)
        if m.id == source.id or b_ref is None:
            continue
        mode = pair_mode(source, m, a_ref, b_ref)
        if mode is None:
            continue
        before = m.model_dump()
        _match_geometry(m, source)
        if mode == "region":
            new = rect
        else:
            sx, sy, sw, sh = _crop_rect(m, b_ref)
            new = size_rect(rect, a_ref, b_ref, centre=(sx + sw / 2.0, sy + sh / 2.0))
        m.crop = Crop(x=round(new[0], 6), y=round(new[1], 6), w=round(new[2], 6), h=round(new[3], 6))
        if m.model_dump() != before:
            out.append(m.id)
    return out


def geometry_key(d: dict) -> tuple:
    """What a group shares, from a panel dump: crop + size + orientation."""
    return (d.get("crop"),) + tuple(d.get(k) for k in GEOMETRY_KEYS)


def clear_singleton_groups(figure: Figure) -> list[str]:
    """A group of one is no group: clear it. Returns the ids cleared."""
    counts: dict[str, int] = {}
    for p in figure.panels:
        if p.inset_style is not None and p.inset_style.region_group:
            counts[p.inset_style.region_group] = counts.get(p.inset_style.region_group, 0) + 1
    out = []
    for p in figure.panels:
        g = p.inset_style.region_group if p.inset_style is not None else None
        if g and counts[g] < 2:
            p.inset_style = p.inset_style.model_copy(update={"region_group": None})
            out.append(p.id)
    return out


def copy_groups_state(panels: Sequence[Panel], figure: Figure) -> list[dict]:
    """Dumps for the clipboard: a region group is kept only if ALL its
    members are copied (a partial copy leaves the group)."""
    ids = {p.id for p in panels}
    out = []
    for p in panels:
        d = p.model_dump(mode="json")
        g = p.inset_style.region_group if p.inset_style is not None else None
        if g and not all(m.id in ids for m in group_members(figure, g)):
            d["inset_style"]["region_group"] = None
        out.append(d)
    return out


def group_invariant_issues(figure: Figure, images: dict[str, ImageRef]) -> list[tuple[str, list[str]]]:
    """(message, panel ids) for every linked group that is not one inset
    repeated: members differing in page size or orientation, in physical
    magnification (rel 1e-6), "region" members with different raw
    rectangles, "size" members with different µm sizes, or pairs that
    cannot be linked at all (e.g. after Replace image)."""
    out = []
    groups = dict.fromkeys(p.inset_style.region_group for p in figure.panels
                           if p.inset_style is not None and p.inset_style.region_group)
    for g in groups:
        members = [m for m in group_members(figure, g) if m.image_id in images]
        if len(members) < 2:
            continue
        ref0, m0 = images[members[0].image_id], members[0]
        problems: list[str] = []
        for m in members[1:]:
            ref = images[m.image_id]
            if any(not math.isclose(getattr(m, k), getattr(m0, k), rel_tol=1e-6)
                   if isinstance(getattr(m, k), float) else getattr(m, k) != getattr(m0, k)
                   for k in GEOMETRY_KEYS):
                problems.append("panel size or orientation")
            mode = pair_mode(m0, m, ref0, ref)
            if mode is None:
                problems.append("a link that is not possible (geometry or calibration)")
                continue
            if not math.isclose(physical_per_mm(m, ref), physical_per_mm(m0, ref0), rel_tol=1e-6):
                problems.append("magnification")
            a, b = _crop_rect(m0, ref0), _crop_rect(m, ref)
            if mode == "region":
                if any(abs(x - y) > 1e-6 for x, y in zip(a, b)):
                    problems.append("region")
            else:
                sa, sb = _scale(ref0), _scale(ref)
                if not (math.isclose(a[2] * sa[0], b[2] * sb[0], rel_tol=1e-6)
                        and math.isclose(a[3] * sa[1], b[3] * sb[1], rel_tol=1e-6)):
                    problems.append("region size")
        if problems:
            out.append((f"linked inset group ({panel_numbers(figure, members)}): members differ in "
                        + ", ".join(dict.fromkeys(problems)), [m.id for m in members]))
    return out
