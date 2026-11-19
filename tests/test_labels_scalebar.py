# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Label tokens (§2.5) and scale bar geometry (§2.6), without Qt."""

from __future__ import annotations

import pytest

from pycrograph import layout as L
from pycrograph import viewport as V
from pycrograph.figures import panel_from_image
from pycrograph.labels import MISSING, Run, format_time, plain_text, resolve_label
from pycrograph.models import ZProjection
from pycrograph.scalebar import ScaleBarUnavailable, bar_geometry
from pycrograph.storage import create_dataset, register_image


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z):
    p = workspace.create_project("Labels")
    ds = create_dataset(p, "Control")
    one, two, zs = (register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z))
    return p, one, two, zs


def text(label, panel, ref, project=None):
    runs, missing = resolve_label(label, panel, ref, project)
    return plain_text(runs), missing


def test_format_time():
    assert format_time(12.0) == "12 s"
    assert format_time(270.0) == "4.5 min"
    assert format_time(4500.0) == "1.25 h"
    assert format_time(-30.0, "s") == "-30 s"
    assert format_time(90.0, "min") == "1.5 min"
    assert format_time(3900.0, "hh:mm") == "1:05"
    assert format_time(-300.0, "hh:mm") == "-0:05"


def test_channels_token_names_and_colours(env):
    p, one, two, zs = env
    panel = panel_from_image(two)
    two.channel_stains = {1: "anti-Lamin A/C"}
    panel.channels[0].lut, panel.channels[1].lut = "green", "magenta"
    runs, missing = resolve_label("[channels]", panel, two)
    assert missing == []
    names = [r for r in runs if r.text != " / "]
    assert names[1] == Run("anti-Lamin A/C", "#FF00FF")             # stain wins
    assert names[0].color == "#00FF00" and names[0].text            # channel name
    panel.channels[1].visible = False
    assert plain_text(resolve_label("[channels]", panel, two)[0]) == names[0].text
    panel.channels[0].lut = "grey"                                  # white LUT -> label colour
    assert resolve_label("[channels]", panel, two)[0][0].color is None
    panel.channels[0].visible = False
    assert text("[channels]", panel, two) == (MISSING, ["[channels]"])


def test_time_tokens_with_offset(env):
    p, one, two, zs = env
    panel = panel_from_image(two)
    panel.t = 1                                                     # 30 s
    assert text("t = [time]", panel, two) == ("t = 30 s", [])
    panel.time_offset_s = 90.0
    assert text("[time:s]|[time:min]|[time:hh:mm]", panel, two)[0] == "-60 s|-1 min|-0:01"
    # single-plane TIFF: no time stamps -> "?", reported
    assert text("[time]", panel_from_image(one), one) == (MISSING, ["[time]"])


def test_z_tokens(env):
    p, one, two, zs = env
    panel = panel_from_image(zs)
    panel.z = 2
    assert text("[z]", panel, zs)[0] == "3"
    assert text("[z:um]", panel, zs)[0] == "1 µm"                    # 2 x 0.5 um
    panel.z_offset = 0.25
    assert text("[z:um]", panel, zs)[0] == "0.75 µm"
    panel.z_projection = ZProjection(z_start=0, z_end=4)
    assert text("[z]", panel, zs)[0] == "1–5"
    assert text("[z:um]", panel, zs)[0] == "-0.25–1.75 µm"
    one.metadata.calibration.z_step = None
    assert text("[z:um]", panel_from_image(one), one) == (MISSING, ["[z:um]"])


def test_other_tokens_and_literals(env):
    p, one, two, zs = env
    panel = panel_from_image(one)
    one.key_values = {"Cell line": "MCF10A"}
    one.tags = ["ctrl", "rep1"]
    assert text("[image] / [dataset] / [tags] / [kv:Cell line]", panel, one, p)[0] == \
        "Nuc_ER_ER / Control / ctrl, rep1 / MCF10A"
    s = one.metadata.shape
    V.zoom_by(panel, s.X, s.Y, 2.5)
    assert text("[zoom]", panel, one)[0] == "2.5×"
    t, missing = text("[kv:Drug] [tags] [bogus] [time:days]", panel_from_image(two), two, p)
    assert t == "? ? [bogus] [time:days]"                           # unknown brackets literal
    assert missing == ["[kv:Drug]", "[tags]"]
    assert text("[image]", panel, None) == (MISSING, ["[image]"])


def test_scale_bar_length_follows_viewport(env):
    p, one, two, zs = env
    panel = panel_from_image(one)                                   # 64 px in 40 mm, 0.325 um/px
    panel.scale_bar.length = 5.2
    g = bar_geometry(panel, one)
    assert g.length_mm == pytest.approx(5.2 / 0.325 / (64 / 40))    # = 10 mm
    assert g.text == "5.2 µm" and g.axis == "x" and g.note is None
    s = one.metadata.shape
    V.zoom_by(panel, s.X, s.Y, 2.0)
    assert bar_geometry(panel, one).length_mm == pytest.approx(2 * g.length_mm)
    V.resize(panel, s.X, s.Y, 60, 30)                               # constant scale
    assert bar_geometry(panel, one).length_mm == pytest.approx(2 * g.length_mm)
    L.scale_panel(panel, 0.5)                                       # align magnification-like
    assert bar_geometry(panel, one).length_mm == pytest.approx(g.length_mm)
    panel.scale_bar.unit = "nm"
    panel.scale_bar.length = 5200
    assert bar_geometry(panel, one).length_mm == pytest.approx(g.length_mm)
    assert bar_geometry(panel, one).text == "5200 nm"


def test_scale_bar_axis_and_unavailable(env):
    p, one, two, zs = env
    panel = panel_from_image(one)
    cal = one.metadata.calibration
    cal.pixel_size_y = 0.65                                         # non-isotropic
    g0 = bar_geometry(panel, one)
    assert g0.axis == "x" and "X pixel size" in g0.note
    s = one.metadata.shape
    V.set_rotation(panel, s.X, s.Y, 90)                             # bar along raw Y
    g90 = bar_geometry(panel, one)
    assert g90.axis == "y" and "Y pixel size" in g90.note
    assert g90.length_mm == pytest.approx(g0.length_mm / 2)
    cal.pixel_size_x = None
    with pytest.raises(ScaleBarUnavailable, match="no spatial calibration"):
        bar_geometry(panel, one)


def test_auto_layouts_copy_labels(env):
    from pycrograph.models import PanelLabel
    p, one, two, zs = env
    src = panel_from_image(two)
    src.labels = [PanelLabel(text="[kv:Condition]", position="top"),
                  PanelLabel(text="[time:min]", position="bottom-right")]
    for q in L.split_channels(src, two, 0, 0) + L.montage(src, two, "t", [0, 1], 0, 50):
        assert q.labels == src.labels and q.labels is not src.labels
