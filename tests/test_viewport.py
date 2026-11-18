# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Viewport = crop (§2.4): zoom, pan, resize at constant scale, rotation,
uniform scale (never stretched), orientation mapping."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from pycrograph import viewport as V
from pycrograph.display import apply_orientation
from pycrograph.models import Crop, Panel

ORIENTATIONS = list(itertools.product((0, 90, 180, 270), (False, True), (False, True)))


def panel(w=40.0, h=30.0, crop=(0, 0, 64, 48), rot=0, fh=False, fv=False) -> Panel:
    return Panel(id="p", image_id="i", width_mm=w, height_mm=h, crop=Crop(x=crop[0], y=crop[1],
                 w=crop[2], h=crop[3]), rotation_deg=rot, flip_horizontal=fh, flip_vertical=fv)


@pytest.mark.parametrize("rot,fh,fv", ORIENTATIONS)
def test_orient_point_matches_apply_orientation(rot, fh, fv):
    W, H = 5, 3
    ids = np.arange(W * H).reshape(H, W)
    out = apply_orientation(ids[..., None].repeat(3, -1), rot, fh, fv)[..., 0]
    for y in range(H):
        for x in range(W):
            u, v = V.orient_point(x + 0.5, y + 0.5, W, H, rot, fh, fv)
            assert out[int(v), int(u)] == ids[y, x]


@pytest.mark.parametrize("rot,fh,fv", ORIENTATIONS)
def test_unorient_vector_is_inverse(rot, fh, fv):
    for du, dv in ((1, 0), (0, 1), (3, -2)):
        a, b = V.orient_vector(du, dv, rot, fh, fv)
        assert V.unorient_vector(a, b, rot, fh, fv) == pytest.approx((du, dv))
    # and it agrees with the point mapping
    p0 = V.orient_point(1, 1, 10, 8, rot, fh, fv)
    p1 = V.orient_point(4, -1, 10, 8, rot, fh, fv)
    assert V.orient_vector(3, -2, rot, fh, fv) == pytest.approx((p1[0] - p0[0], p1[1] - p0[1]))


def test_fit_zoom_and_pan():
    p = panel()                                   # 64 x 48 image in a 40 x 30 mm panel
    assert V.zoom_percent(p, 64, 48) == pytest.approx(100)
    V.zoom_by(p, 64, 48, 2.0)
    assert (p.crop.x, p.crop.y, p.crop.w, p.crop.h) == pytest.approx((16, 12, 32, 24))
    assert V.zoom_percent(p, 64, 48) == pytest.approx(200)
    V.pan(p, 64, 48, -20, 5)
    assert (p.crop.x, p.crop.y) == pytest.approx((-4, 17))           # may leave the image
    V.set_zoom_percent(p, 64, 48, 50)
    assert p.crop.w == pytest.approx(128) and V.visible_fraction(p, 64, 48) < 1
    V.fit(p, 64, 48)
    assert (p.crop.x, p.crop.y, p.crop.w, p.crop.h) == pytest.approx((0, 0, 64, 48))
    V.set_zoom_percent(p, 64, 48, 1e9)          # clamped: smallest side 2 px, aspect kept
    assert min(p.crop.w, p.crop.h) == pytest.approx(V.MIN_CROP_PX)
    assert p.crop.w / p.crop.h == pytest.approx(40 / 30)


def test_fit_contains_whole_image_in_other_aspect():
    p = panel(w=40, h=40)
    V.fit(p, 64, 48)
    assert (p.crop.w, p.crop.h) == pytest.approx((64, 64))
    assert p.crop.y == pytest.approx(-8)
    x, y, w, h = V.image_rect_mm(p, 64, 48, (0, 0, 64, 48))
    assert (x, y, w, h) == pytest.approx((0, 5, 40, 30))               # letterboxed, uniform


def test_resize_keeps_centre_and_scale():
    p = panel()
    ppm = V.px_per_mm(p, 64, 48)
    V.resize(p, 64, 48, 60, 30)
    assert (p.width_mm, p.height_mm) == (60, 30)
    assert V.px_per_mm(p, 64, 48) == pytest.approx(ppm)
    assert V.crop_centre(p.crop) == pytest.approx((32, 24))
    assert (p.crop.x, p.crop.w) == pytest.approx((-16, 96))
    # The image is drawn at the same size, centred; background each side.
    assert V.image_rect_mm(p, 64, 48, (0, 0, 64, 48)) == pytest.approx((10, 0, 40, 30))


def test_rotation_turns_crop_about_centre():
    p = panel(crop=(10, 4, 40, 30))
    ppm = V.px_per_mm(p, 64, 48)
    V.set_rotation(p, 64, 48, 90)
    assert (p.width_mm, p.height_mm) == (40, 30)                        # page layout unchanged
    assert (p.crop.w, p.crop.h) == pytest.approx((30, 40))
    assert V.crop_centre(p.crop) == pytest.approx((30, 19))
    assert V.px_per_mm(p, 64, 48) == pytest.approx(ppm)
    ow, oh = V.oriented_wh(p.crop.w, p.crop.h, 90)
    assert ow / oh == pytest.approx(40 / 30)


@pytest.mark.parametrize("rot,fh,fv", ORIENTATIONS)
def test_image_rect_never_stretched(rot, fh, fv):
    p = panel(crop=(-10, 6, 50, 30), rot=rot, fh=fh, fv=fv)
    V.normalize(p, 64, 48)
    s = V.mm_per_px(p, 64, 48)
    bounds = (0, 6, 40, 36)                         # the on-image part of the crop
    x, y, w, h = V.image_rect_mm(p, 64, 48, bounds)
    ow, oh = V.oriented_wh(40, 30, rot)
    assert (w, h) == pytest.approx((ow * s, oh * s))                    # uniform scale
    assert x >= -1e-9 and y >= -1e-9
    assert x + w <= p.width_mm + 1e-9 and y + h <= p.height_mm + 1e-9


def test_copy_paste_view():
    a = panel(crop=(8, 8, 20, 15), rot=90, fh=True)
    view = V.copy_view(a, 64, 48)
    b = panel(w=20, h=40)
    V.paste_view(b, view)
    assert (b.rotation_deg, b.flip_horizontal) == (90, True)
    assert V.crop_centre(b.crop) == pytest.approx(V.crop_centre(a.crop))
    assert V.px_per_mm(b, 64, 48) == pytest.approx(V.px_per_mm(a, 64, 48))


def test_none_crop_is_whole_image():
    p = Panel(id="p", image_id="i", width_mm=32, height_mm=24)
    assert V.effective_crop(p, 64, 48) == Crop(x=0, y=0, w=64, h=48)
    assert V.zoom_percent(p, 64, 48) == pytest.approx(100)
