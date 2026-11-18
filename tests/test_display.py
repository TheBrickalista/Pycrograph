# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Display pipeline: window -> gamma -> invert -> LUT -> additive composite."""

from __future__ import annotations

import numpy as np
import pytest

from pycrograph.display import (
    apply_orientation, auto_window, compute_saturation_stats, default_luts, lut_hex, lut_rgb,
    make_thumbnail, render_composite, window_curve,
)
from pycrograph.models import ChannelInfo, ChannelSettings


def cs(**kw) -> ChannelSettings:
    kw.setdefault("index", 0)
    return ChannelSettings(**kw)


def test_window_curve_linear():
    c = window_curve(100, 300)
    assert c[0] == 0 and c[100] == 0 and c[300] == 1 and c[65535] == 1
    assert c[200] == pytest.approx(0.5)


def test_window_curve_gamma_matches_pysternblot_convention():
    # out = x ** (1/gamma): gamma 2 brightens mid-tones.
    c = window_curve(0, 100, gamma=2.0)
    assert c[25] == pytest.approx(0.5)


def test_window_curve_invert_and_degenerate():
    c = window_curve(10, 10, invert=True)          # white <= black widened to black + 1
    assert c[10] == 1 and c[11] == 0


def test_composite_additive_and_clipped():
    a = np.array([[0, 1000]], np.uint16)
    b = np.array([[1000, 1000]], np.uint16)
    rgb = render_composite([a, b], [cs(index=0, lut="red", black=0, white=1000),
                                    cs(index=1, lut="green", black=0, white=1000)])
    assert rgb.dtype == np.uint8 and rgb.shape == (1, 2, 3)
    assert rgb[0, 0].tolist() == [0, 255, 0]
    assert rgb[0, 1].tolist() == [255, 255, 0]     # red + green = yellow

    # Two full-white grey channels clip at 255, never wrap.
    rgb = render_composite([b, b], [cs(index=0, black=0, white=1000), cs(index=1, black=0, white=1000)])
    assert rgb.max() == 255


def test_hidden_channel_and_hex_lut():
    a = np.full((2, 2), 500, np.uint16)
    assert render_composite([a], [cs(visible=False)]).max() == 0
    rgb = render_composite([a], [cs(lut="#FF8000", black=0, white=500)])
    assert rgb[0, 0].tolist() == [255, 128, 0]


def test_composite_does_not_touch_raw():
    a = np.arange(16, dtype=np.uint16).reshape(4, 4)
    before = a.copy()
    render_composite([a], [cs(black=0, white=15, gamma=0.5, invert=True)])
    np.testing.assert_array_equal(a, before)


def test_8bit_planes():
    a = np.array([[0, 255]], np.uint8)
    rgb = render_composite([a], [cs(black=0, white=255)])
    assert rgb[0, 1].tolist() == [255, 255, 255]


def test_luts():
    assert lut_rgb("gray") == (1.0, 1.0, 1.0)
    assert lut_hex("magenta") == "#FF00FF"
    with pytest.raises(ValueError):
        lut_rgb("plaid")


def test_default_luts():
    assert default_luts([ChannelInfo(index=0, wavelength_nm=630)]) == ["grey"]
    chans = [ChannelInfo(index=0, wavelength_nm=405), ChannelInfo(index=1, wavelength_nm=488),
             ChannelInfo(index=2, wavelength_nm=630), ChannelInfo(index=3)]
    luts = default_luts(chans)
    assert luts[:3] == ["blue", "green", "red"]
    assert len(set(luts)) == 4


def test_orientation():
    rgb = np.zeros((2, 3, 3), np.uint8)
    rgb[0, 0] = 255                                # top-left marker
    r = apply_orientation(rgb, 90)                 # clockwise: top-left -> top-right
    assert r.shape == (3, 2, 3) and r[0, 1, 0] == 255
    f = apply_orientation(rgb, 0, flip_horizontal=True)
    assert f[0, 2, 0] == 255
    v = apply_orientation(rgb, 0, flip_vertical=True)
    assert v[1, 0, 0] == 255


def test_saturation_solid_vs_specks():
    img = np.zeros((10, 10), np.uint16)
    img[2:6, 2:6] = 65535                          # solid 4x4 block -> 2x2 eroded core
    img[8, 8] = 65535                              # isolated hot pixel
    s = compute_saturation_stats(img, 65535)
    assert s.saturated_count == 17 and s.solid_saturated_count == 4
    assert s.saturated_fraction == pytest.approx(0.17)


def test_saturation_respects_bit_depth():
    img = np.full((4, 4), 4095, np.uint16)         # 12-bit camera in a 16-bit container
    assert compute_saturation_stats(img, 4095).saturated_count == 16
    assert compute_saturation_stats(img, 65535).saturated_count == 0


def test_auto_window():
    rng = np.random.default_rng(1)
    img = rng.integers(2400, 3000, (200, 200)).astype(np.uint16)
    lo, hi = auto_window(img)
    assert 2400 <= lo < hi <= 3000
    assert auto_window(np.full((4, 4), 7, np.uint16)) == (7, 8)


def test_thumbnail_stride():
    rgb = np.zeros((1024, 512, 3), np.uint8)
    assert make_thumbnail(rgb, 256).shape == (256, 128, 3)
