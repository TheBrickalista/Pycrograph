# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Consistency check (§2.9): one test per rule, negatives included."""

from __future__ import annotations

import numpy as np
import pytest

from pycrograph import consistency as C
from pycrograph import layout as L
from pycrograph import viewport as V
from pycrograph.figures import FigureEditor, create_figure, panel_channel
from pycrograph.insets import create_inset
from pycrograph.models import Panel, ZProjection
from pycrograph.storage import create_dataset, register_image


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z):
    p = workspace.create_project("Check")
    ds = create_dataset(p, "A")
    one, two, zs = (register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z))
    for r in (one, two, zs):
        r.sha256 = "0" * 64
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: None)
    return p, fig, ed, (one, two, zs)


def codes(issues, code=None):
    return [i for i in issues if code is None or i.code == code]


def ok(_ref):
    return "ok"


def run(p, fig, **kw):
    kw.setdefault("source_status", ok)
    return C.check_figure(p, fig, **kw)


def test_rows_columns_groups():
    ps = [Panel(id=f"p{i}", image_id="i", x_mm=x, y_mm=y, width_mm=20, height_mm=10)
          for i, (x, y) in enumerate([(0, 0), (22, 1), (0, 12), (22, 12)])]
    assert [[p.id for p in c] for c in L.columns_of(ps)] == [["p0", "p2"], ["p1", "p3"]]
    assert [[p.id for p in r] for r in L.rows_of(ps)] == [["p0", "p1"], ["p2", "p3"]]


def test_levels_in_row_only_visible_channels(env):
    p, fig, ed, (one, two, zs) = env
    a, b = ed.add_images([one, one])                      # one row, same stain (TxRed)
    for q in (a, b):
        panel_channel(q, 0).lut, panel_channel(q, 0).black, panel_channel(q, 0).white = "grey", 100, 9000
    assert not codes(run(p, fig), "levels")
    panel_channel(b, 0).white = 8000
    panel_channel(b, 0).lut = "green"
    [i] = codes(run(p, fig), "levels")
    assert (i.level, i.rule) == ("major", "levels.row") and set(i.panel_ids) == {a.id, b.id}
    assert "row 1: 5 TxRed (630 nm) shown with different" in i.message
    assert "white 9000 / 8000" in i.message and "lut grey / green" in i.message
    panel_channel(b, 0).visible = False                   # hidden: not compared
    assert not codes(run(p, fig), "levels")


def test_row_of_different_stains_never_compared(env):
    """TxRed (Nuc_ER_ER.tif) and GFP (zstack.stk) share channel index 0."""
    p, fig, ed, (one, two, zs) = env
    a, b = ed.add_images([one, zs])
    panel_channel(a, 0).white, panel_channel(b, 0).white = 9000, 3000
    issues = run(p, fig)
    assert not codes(issues, "levels") and not codes(issues, "channels")


def test_tz_in_row_and_montage_exempt(env):
    p, fig, ed, (one, two, zs) = env
    a, b = ed.add_images([two, zs])
    assert not codes(run(p, fig), "tz")
    a.t = 2
    [i] = codes(run(p, fig), "tz")
    assert "T3 Z1" in i.message and "T1 Z1" in i.message
    a.t = 0
    b.z_projection = ZProjection(z_start=0, z_end=4)
    assert "max Z1–5" in codes(run(p, fig), "tz")[0].message
    # A montage varies T on purpose: no warning within it.
    b.z_projection = None
    fig.panels = [a]
    row = L.montage(a, two, "t", [0, 1, 2], 0, 60)
    fig.panels += row
    assert not [i for i in codes(run(p, fig), "tz") if set(i.panel_ids) & {q.id for q in row}]


def test_scale_bar_partial_row(env):
    p, fig, ed, (one, two, zs) = env
    a, b = ed.add_images([one, two])
    a.scale_bar.visible = True
    [i] = codes(run(p, fig), "scalebar")
    assert "1 of 2" in i.message
    b.scale_bar.visible = True
    assert not codes(run(p, fig), "scalebar")


def test_scale_bar_row_inset_beside_its_parent(env):
    """An inset starts without a bar: beside a parent that shows one, it is
    not reported. Insets in one row are still compared with each other."""
    p, fig, ed, (one, two, zs) = env
    [a] = ed.add_images([one])
    a.scale_bar.visible = True
    i1 = create_inset(fig, a, one, (8, 8, 16, 16), "i1")
    fig.panels.append(i1)
    assert i1.y_mm == a.y_mm and not i1.scale_bar.visible          # same row, no bar
    assert not codes(run(p, fig), "scalebar")
    i2 = create_inset(fig, a, one, (30, 30, 8, 8), "i2")
    i2.x_mm, i2.y_mm = i1.x_mm + i1.width_mm + 2, i1.y_mm         # a row of two insets
    fig.panels.append(i2)
    i2.scale_bar.visible = True
    [i] = codes(run(p, fig), "scalebar")
    assert "scale bar on 1 of 2 insets" in i.message and set(i.panel_ids) == {i1.id, i2.id}


def test_channel_mismatch_in_lock_group(env):
    p, fig, ed, (one, two, zs) = env
    a, b = ed.add_images([one, two])
    b.y_mm = 100                                           # not in the same row / column
    b.x_mm = 100
    assert not codes(run(p, fig), "channels")
    a.lock_group = b.lock_group = "lock:x"
    [i] = codes(run(p, fig), "channels")
    assert i.message.startswith("lock group (manual lock, panels 1–2): ") and "TxRed" in i.message
    assert "lock:x" not in i.message


def test_per_panel_rules(env):
    p, fig, ed, (one, two, zs) = env
    [a] = ed.add_images([one])
    issues = run(p, fig)
    assert not codes(issues, "nonlinear") and not codes(issues, "line_width")
    panel_channel(a, 0).gamma = 1.4
    [i] = codes(run(p, fig), "nonlinear")
    assert i.level == "minor" and "disclose in the legend" in i.message
    # 64 px across 40 mm = 41 ppi < 300 dpi
    [i] = codes(run(p, fig), "resolution")
    assert "41 ppi < 300 dpi" in i.message
    s = one.metadata.shape
    L.scale_panel(a, 5.0 / a.width_mm)                     # same 64 px over 5 mm: 325 ppi
    assert V.px_per_mm(a, s.X, s.Y) * 25.4 > 300 and not codes(run(p, fig), "resolution")
    a.border.visible, a.border.width_pt = True, 0.25
    a.scale_bar.visible, a.scale_bar.thickness_pt = True, 0.3
    L.scale_panel(a, 8.0)                                   # back to 40 mm
    [i] = codes(run(p, fig), "line_width")
    assert "border 0.25 pt" in i.message and "scale bar 0.3 pt" in i.message


def test_saturation_in_crop(env):
    p, fig, ed, (one, two, zs) = env
    [a] = ed.add_images([one])
    fs = one.metadata.full_scale
    clean = np.full((8, 8), 100, np.uint16)
    assert not codes(run(p, fig, crop_plane=lambda q, r, c: clean), "saturation")
    sat = clean.copy()
    sat[2:6, 2:6] = fs
    [i] = codes(run(p, fig, crop_plane=lambda q, r, c: sat), "saturation")
    assert "16 px (4 in solid regions)" in i.message
    assert not codes(run(p, fig), "saturation")            # no pixels given: rule skipped


@pytest.mark.parametrize("status,level", [("missing", "critical"), ("modified", "critical"),
                                          ("changed", "major"), ("pending_hash", "minor"),
                                          ("ok", None)])
def test_sources(env, status, level):
    p, fig, ed, (one, two, zs) = env
    ed.add_images([one, one])
    found = codes(run(p, fig, source_status=lambda r: status), "source")
    if level is None:
        assert not found
    else:
        [i] = found                                        # once per image, both panels
        assert i.level == level and i.rule == f"source.{status}" and len(i.panel_ids) == 2


def test_sorted_and_deduplicated(env):
    p, fig, ed, (one, two, zs) = env
    a, b = ed.add_images([one, one])
    a.lock_group = b.lock_group = "lock:y"                 # same row AND lock group
    panel_channel(b, 0).white = 1234
    issues = run(p, fig, source_status=lambda r: "modified")
    assert [i.level for i in issues] == sorted((i.level for i in issues), key=C.LEVEL_ORDER.get)
    [lv] = codes(issues, "levels")                         # row + lock group: reported once
    assert (lv.level, lv.rule) == ("critical", "levels.lock") and "lock broken" in lv.message
    assert C.summary(issues).startswith("2 critical")
    d = codes(issues, "source")[0].to_dict()
    assert set(d) == {"level", "code", "rule", "message", "panel_ids"} and d["level"] == "critical"
    assert d["panel_ids"] == [a.id, b.id]


def test_off_page_scalebar_fit_and_labels(env):
    from pycrograph.models import PanelLabel
    p, fig, ed, (one, two, zs) = env
    [a] = ed.add_images([one])                            # at (0, 0)
    a.x_mm = 10
    assert not codes(run(p, fig), "off_page")
    a.labels = [PanelLabel(text="[image]", position="top"), PanelLabel(text="t=[time]")]
    [i] = codes(run(p, fig), "off_page")
    assert "top labels" in i.message
    a.y_mm = 10                                            # 2 lines of 8 pt ≈ 7.9 mm: fits
    assert not codes(run(p, fig), "off_page")
    a.x_mm = 160
    assert "right" in codes(run(p, fig), "off_page")[0].message
    a.x_mm = 10
    [i] = codes(run(p, fig), "labels")                     # single-plane TIFF: no time
    assert "[time]" in i.message
    a.scale_bar.visible, a.scale_bar.length = True, 30.0   # 30 um = 57.7 mm > 40 mm panel
    [i] = codes(run(p, fig), "scalebar_fit")
    assert "longer than the panel" in i.message
    a.scale_bar.length = 5.0
    assert not codes(run(p, fig), "scalebar_fit")
    one.metadata.calibration.pixel_size_x = None
    assert "not drawn" in codes(run(p, fig), "scalebar_fit")[0].message
