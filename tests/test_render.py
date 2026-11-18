# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""PanelRenderer: crop by slicing, then render_composite; orientation,
Z projection, cache, missing source."""

from __future__ import annotations

import numpy as np
import pytest

from pycrograph.display import apply_orientation, render_composite
from pycrograph.figures import panel_from_image
from pycrograph.models import Crop, ZProjection
from pycrograph.readers import open_image
from pycrograph.render import PanelRenderer, RenderError, crop_bounds
from pycrograph.storage import create_dataset, register_image


@pytest.fixture
def setup(workspace, mm_tiff, stk_tc, stk_z):
    p = workspace.create_project("R")
    ds = create_dataset(p, "A")
    refs = [register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z)]
    renderer = PanelRenderer(lambda ref: workspace.resolve_image_path(p, ref))
    yield p, refs, renderer
    renderer.close()


def test_crop_bounds():
    assert crop_bounds(None, 10, 8) == (0, 0, 10, 8)
    assert crop_bounds(Crop(x=2.5, y=1, w=3, h=4), 10, 8) == (2, 1, 6, 5)
    assert crop_bounds(Crop(x=8, y=6, w=10, h=10), 10, 8) == (8, 6, 10, 8)     # clamped
    assert crop_bounds(Crop(x=50, y=50, w=1, h=1), 10, 8) == (9, 7, 10, 8)     # never empty


def test_crop_equals_slice_of_full_render(setup):
    _p, (a, b, _z), r = setup
    panel = panel_from_image(b)
    panel.channels[1].lut = "magenta"
    panel.t = 1
    full = r.render(b, panel)
    assert full.shape == (24, 32, 3)
    with open_image(b.path) as src:            # memmapped planes: use before close
        planes = [src.get_plane(1, 0, c.index) for c in panel.channels]
        expected = render_composite(planes, panel.channels)
    assert np.array_equal(full, expected)

    panel.crop = Crop(x=4, y=3, w=10, h=7)
    cropped = r.render(b, panel)
    assert np.array_equal(cropped, full[3:10, 4:14])

    panel.rotation_deg = 90
    panel.flip_horizontal = True
    rot = r.render(b, panel)
    assert np.array_equal(rot, apply_orientation(full[3:10, 4:14], 90, True, False))
    assert rot.shape == (10, 7, 3)


def test_hidden_channels_and_max_side(setup):
    _p, (a, _b, _z), r = setup
    panel = panel_from_image(a)
    for c in panel.channels:
        c.visible = False
    out = r.render(a, panel)
    assert out.shape == (64, 64, 3) and not out.any()
    panel.channels[0].visible = True
    small = r.render(a, panel, max_side=16)
    assert small.shape == (16, 16, 3)
    assert np.array_equal(small, r.render(a, panel)[::4, ::4])       # stride, no interpolation


def test_max_projection(setup):
    _p, (_a, _b, z), r = setup
    panel = panel_from_image(z)
    panel.channels[0].black, panel.channels[0].white = 499, 504
    panel.z = 0
    single = r.render(z, panel)
    panel.z_projection = ZProjection(z_start=0, z_end=4)
    proj = r.render(z, panel)
    assert int(single[0, 0, 0]) == round(255 / 5)                    # plane value 500
    assert int(proj[0, 0, 0]) == 255                                 # max = 504


def test_cache_avoids_rereading(setup, monkeypatch):
    _p, (a, _b, _z), r = setup
    panel = panel_from_image(a)
    first = r.render(a, panel)
    assert not first.flags.writeable
    calls = []
    src = r._source(a)
    orig = src.get_plane
    monkeypatch.setattr(src, "get_plane", lambda *args: calls.append(args) or orig(*args))
    panel.x_mm, panel.width_mm = 50, 80                              # page geometry only
    assert r.render(a, panel) is first
    assert calls == []
    panel.channels[0].white -= 1                                     # pixels change
    r.render(a, panel)
    assert calls == [(0, 0, 0)]                                      # one plane read


def test_missing_source(setup):
    _p, (a, _b, _z), r = setup
    panel = panel_from_image(a)
    r.close()
    import os
    os.unlink(a.path)
    with pytest.raises(RenderError, match="not found"):
        r.render(a, panel)
