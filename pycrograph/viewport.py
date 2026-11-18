# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Viewport = crop (docs/design/figure-editor.md §2.4), pure geometry.

`Panel.crop` is the single source of truth: a rectangle in raw-pixel
coordinates (before rotation/flip). The panel shows that rectangle,
oriented, at ONE uniform scale (never stretched):

    mm_per_px = min(panel_w / crop_w', panel_h / crop_h')   (' = oriented)

Every operation here keeps the oriented crop aspect equal to the panel
aspect, so the crop fills the panel exactly. Where the crop runs off the
image (zoomed out, panned to an edge, panel enlarged) nothing is padded into
the pixels: only the part of the crop inside the image is rendered and the
rest stays page background (`image_rect_mm`).

Zoom % and pan are derived values: zoom 100 % = the whole image fitted in
the panel ("contain"). Resizing a panel keeps the crop centre and the scale
(µm per page-mm), extending or shrinking the crop. Rotation by 90° keeps
the panel and the scale and turns the crop about its centre.
"""

from __future__ import annotations

from typing import Optional

from .models import Crop, Panel

MIN_CROP_PX = 2.0
MIN_ZOOM_PCT = 5.0
MAX_ZOOM_PCT = 20000.0
_ROUND = 6

Rect = tuple[float, float, float, float]       # x, y, w, h


def _r(v: float) -> float:
    return round(float(v), _ROUND)


def oriented_wh(w: float, h: float, rotation_deg: int) -> tuple[float, float]:
    return (h, w) if rotation_deg in (90, 270) else (w, h)


def effective_crop(panel: Panel, width: int, height: int) -> Crop:
    return panel.crop if panel.crop is not None else Crop(x=0, y=0, w=width, h=height)


def crop_centre(crop: Crop) -> tuple[float, float]:
    return crop.x + crop.w / 2.0, crop.y + crop.h / 2.0


# ------------------------------------------------------------- orientation
def orient_point(u: float, v: float, fw: float, fh: float, rotation_deg: int,
                 flip_h: bool, flip_v: bool) -> tuple[float, float]:
    """Point (u, v) of a raw frame fw x fh -> oriented frame. Same order as
    display.apply_orientation: flips first, then clockwise rotation."""
    if flip_h:
        u = fw - u
    if flip_v:
        v = fh - v
    r = rotation_deg % 360
    if r == 90:
        return fh - v, u
    if r == 180:
        return fw - u, fh - v
    if r == 270:
        return v, fw - u
    return u, v


def orient_vector(du: float, dv: float, rotation_deg: int, flip_h: bool,
                  flip_v: bool) -> tuple[float, float]:
    if flip_h:
        du = -du
    if flip_v:
        dv = -dv
    r = rotation_deg % 360
    if r == 90:
        return -dv, du
    if r == 180:
        return -du, -dv
    if r == 270:
        return dv, -du
    return du, dv


def unorient_vector(du: float, dv: float, rotation_deg: int, flip_h: bool,
                    flip_v: bool) -> tuple[float, float]:
    """Inverse of orient_vector: an on-screen displacement -> raw pixels."""
    r = rotation_deg % 360
    if r == 90:
        du, dv = dv, -du
    elif r == 180:
        du, dv = -du, -dv
    elif r == 270:
        du, dv = -dv, du
    if flip_h:
        du = -du
    if flip_v:
        dv = -dv
    return du, dv


def orient_rect(rect: Rect, frame: Rect, rotation_deg: int, flip_h: bool,
                flip_v: bool) -> Rect:
    """A raw rectangle -> the oriented frame of `frame` (both raw)."""
    fx, fy, fw, fh = frame
    x, y, w, h = rect
    pts = [orient_point(px - fx, py - fy, fw, fh, rotation_deg, flip_h, flip_v)
           for px, py in ((x, y), (x + w, y + h))]
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    return min(us), min(vs), max(us) - min(us), max(vs) - min(vs)


# -------------------------------------------------------------- the scale
def mm_per_px(panel: Panel, width: int, height: int) -> float:
    c = effective_crop(panel, width, height)
    ow, oh = oriented_wh(c.w, c.h, panel.rotation_deg)
    return min(panel.width_mm / ow, panel.height_mm / oh)


def px_per_mm(panel: Panel, width: int, height: int) -> float:
    return 1.0 / mm_per_px(panel, width, height)


def image_rect_mm(panel: Panel, width: int, height: int,
                  bounds: tuple[int, int, int, int]) -> Rect:
    """Where the rendered region `bounds` (x0, y0, x1, y1, raw pixels, as
    returned by render.crop_bounds) sits inside the panel, in panel-local
    mm. Uniform scale, centred like the crop; may extend past the panel by
    less than a pixel (callers clip to the panel)."""
    c = effective_crop(panel, width, height)
    s = mm_per_px(panel, width, height)
    ow, oh = oriented_wh(c.w, c.h, panel.rotation_deg)
    ox = (panel.width_mm - ow * s) / 2.0
    oy = (panel.height_mm - oh * s) / 2.0
    x0, y0, x1, y1 = bounds
    u, v, w, h = orient_rect((x0, y0, x1 - x0, y1 - y0), (c.x, c.y, c.w, c.h),
                             panel.rotation_deg, panel.flip_horizontal, panel.flip_vertical)
    return ox + u * s, oy + v * s, w * s, h * s


# ------------------------------------------------------------- operations
def set_view(panel: Panel, cx: float, cy: float, ppm: float) -> None:
    """Crop centred on (cx, cy) raw px, at ppm raw px per mm, filling the
    panel exactly."""
    # Clamp the scale, not each side, so the crop keeps the panel aspect.
    ppm = max(float(ppm), MIN_CROP_PX / min(panel.width_mm, panel.height_mm))
    ow, oh = panel.width_mm * ppm, panel.height_mm * ppm
    w, h = oriented_wh(ow, oh, panel.rotation_deg)        # back to raw (swap is symmetric)
    panel.crop = Crop(x=_r(cx - w / 2.0), y=_r(cy - h / 2.0), w=_r(w), h=_r(h))


def fit_ppm(panel: Panel, width: int, height: int) -> float:
    """Scale at which the whole image fits the panel (zoom 100 %)."""
    ow, oh = oriented_wh(width, height, panel.rotation_deg)
    return max(ow / panel.width_mm, oh / panel.height_mm)


def fit(panel: Panel, width: int, height: int) -> None:
    """Reset the viewport: whole image, centred (background on the sides
    if the panel aspect differs)."""
    set_view(panel, width / 2.0, height / 2.0, fit_ppm(panel, width, height))


def normalize(panel: Panel, width: int, height: int) -> None:
    """Make the crop fill the panel at the current scale (used after a
    paste, a replace or for a project written before step 2)."""
    c = effective_crop(panel, width, height)
    cx, cy = crop_centre(c)
    set_view(panel, cx, cy, px_per_mm(panel, width, height))


def zoom_percent(panel: Panel, width: int, height: int) -> float:
    return 100.0 * fit_ppm(panel, width, height) / px_per_mm(panel, width, height)


def set_zoom_percent(panel: Panel, width: int, height: int, pct: float) -> None:
    pct = min(MAX_ZOOM_PCT, max(MIN_ZOOM_PCT, float(pct)))
    cx, cy = crop_centre(effective_crop(panel, width, height))
    set_view(panel, cx, cy, fit_ppm(panel, width, height) * 100.0 / pct)


def zoom_by(panel: Panel, width: int, height: int, factor: float) -> None:
    set_zoom_percent(panel, width, height, zoom_percent(panel, width, height) * factor)


def pan(panel: Panel, width: int, height: int, dx: float, dy: float) -> None:
    """Move the crop by (dx, dy) raw pixels."""
    c = effective_crop(panel, width, height)
    panel.crop = Crop(x=_r(c.x + dx), y=_r(c.y + dy), w=c.w, h=c.h)


def centre_on(panel: Panel, width: int, height: int, cx: float, cy: float) -> None:
    set_view(panel, cx, cy, px_per_mm(panel, width, height))


def resize(panel: Panel, width: int, height: int, new_w_mm: float, new_h_mm: float) -> None:
    """Change the panel size keeping the crop centre and the scale: the crop
    grows or shrinks, the image is never rescaled or stretched."""
    c = effective_crop(panel, width, height)
    cx, cy = crop_centre(c)
    ppm = px_per_mm(panel, width, height)
    panel.width_mm, panel.height_mm = float(new_w_mm), float(new_h_mm)
    set_view(panel, cx, cy, ppm)


def set_rotation(panel: Panel, width: int, height: int, rotation_deg: int) -> None:
    """Rotate inside the panel: same panel, same scale, crop turned about
    its centre."""
    c = effective_crop(panel, width, height)
    cx, cy = crop_centre(c)
    ppm = px_per_mm(panel, width, height)
    panel.rotation_deg = rotation_deg % 360
    set_view(panel, cx, cy, ppm)


def copy_view(panel: Panel, width: int, height: int) -> dict:
    """What 'Copy viewport' carries: centre, scale, orientation."""
    c = effective_crop(panel, width, height)
    cx, cy = crop_centre(c)
    return {"cx": cx, "cy": cy, "ppm": px_per_mm(panel, width, height),
            "rotation_deg": panel.rotation_deg, "flip_horizontal": panel.flip_horizontal,
            "flip_vertical": panel.flip_vertical}


def paste_view(panel: Panel, view: dict) -> None:
    panel.rotation_deg = view["rotation_deg"]
    panel.flip_horizontal = view["flip_horizontal"]
    panel.flip_vertical = view["flip_vertical"]
    set_view(panel, view["cx"], view["cy"], view["ppm"])


def visible_fraction(panel: Panel, width: int, height: int) -> Optional[float]:
    """Share of the crop that lies on the image (1.0 = no background)."""
    c = effective_crop(panel, width, height)
    iw = max(0.0, min(c.x + c.w, width) - max(c.x, 0.0))
    ih = max(0.0, min(c.y + c.h, height) - max(c.y, 0.0))
    return (iw * ih) / (c.w * c.h)
