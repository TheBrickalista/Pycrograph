# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Preview operations on several panels through FigureEditor.edit():
matched by channel index, skipped panels, per-image clamping, live
gestures (many ticks -> one entry per panel), projection, orientation,
copy/paste, image defaults, replace image."""

from __future__ import annotations

import pytest

from pycrograph import figures as F
from pycrograph import viewport as V
from pycrograph.figures import FigureEditor, create_figure, leaf_diff
from pycrograph.logchain import verify_log_chain
from pycrograph.storage import create_dataset, register_image


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z):
    p = workspace.create_project("Prev")
    ds = create_dataset(p, "A")
    one, two, zs = (register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z))
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    pa, pb, pz = ed.add_images([one, two, zs])
    return p, ed, (one, two, zs), (pa, pb, pz)


def new_entries(p, n):
    return p.operation_log[n:]


def test_leaf_diff_paths():
    old = {"x_mm": 1, "channels": [{"index": 0, "white": 5}, {"index": 2, "white": 7}],
           "crop": None, "labels": []}
    new = {"x_mm": 1, "channels": [{"index": 0, "white": 5}, {"index": 2, "white": 9}],
           "crop": {"x": 0}, "labels": [{"text": "a"}]}
    assert leaf_diff(old, new) == [("channels[2].white", 7, 9), ("crop", None, {"x": 0}),
                                   ("labels", [], [{"text": "a"}])]


def test_channel_by_index_skips_and_logs_per_panel(env):
    p, ed, (one, two, _z), (pa, pb, pz) = env
    n = len(p.operation_log)
    applied, skipped = ed.edit([pa.id, pb.id], lambda q, r: F.set_channel_field(q, r, 1, "lut", "#FF00AA"),
                               label="lut")
    assert (applied, skipped) == (1, 1)                    # 1-channel image skipped
    [e] = new_entries(p, n)
    assert (e.target_id, e.field, e.new_value) == (pb.id, "channels[1].lut", "#FF00AA")
    assert e.operation == "figure.panel.set"


def test_levels_clamped_per_image(env):
    p, ed, (one, two, _z), (pa, pb, _pz) = env
    two.metadata.bit_depth = 12                            # 12-bit camera in a 16-bit file
    ed.edit([pa.id, pb.id], lambda q, r: F.set_channel_field(q, r, 0, "white", 60000))
    assert F.panel_channel(pa, 0).white == 60000
    assert F.panel_channel(pb, 0).white == 4095
    ed.edit([pa.id], lambda q, r: F.set_channel_field(q, r, 0, "black", 70000))
    assert F.panel_channel(pa, 0).black == 59999           # below white


def test_live_gesture_one_entry_per_panel_one_undo(env):
    p, ed, _refs, (pa, pb, _pz) = env
    n = len(p.operation_log)
    undo_depth = len(ed.history._undo)
    for v in (5000, 4500, 4000):
        ed.edit([pa.id, pb.id], lambda q, r, v=v: F.set_channel_field(q, r, 0, "white", v),
                key="channels[0].white", final=False)
    assert len(p.operation_log) == n and ed.has_pending     # nothing logged mid-gesture
    ed.flush()
    entries = new_entries(p, n)
    assert [(e.target_id, e.field, e.new_value) for e in entries] == \
        [(pa.id, "channels[0].white", 4000), (pb.id, "channels[0].white", 4000)]
    assert len(ed.history._undo) == undo_depth + 1
    ed.undo()
    assert F.panel_channel(ed.figure.panel(pa.id), 0).white != 4000


def test_other_control_commits_previous_gesture(env):
    p, ed, _refs, (pa, _pb, _pz) = env
    n = len(p.operation_log)
    ed.edit([pa.id], lambda q, r: F.set_channel_field(q, r, 0, "white", 3000), key="w", final=False)
    ed.edit([pa.id], lambda q, r: F.set_channel_field(q, r, 0, "black", 2500), key="b", final=False)
    assert [e.field for e in new_entries(p, n)] == ["channels[0].white"]
    ed.flush()
    assert [e.field for e in new_entries(p, n)] == ["channels[0].white", "channels[0].black"]


def test_tz_and_projection(env):
    p, ed, (one, two, zs), (pa, pb, pz) = env
    n = len(p.operation_log)
    ed.edit([pa.id, pb.id], lambda q, r: F.set_t(q, r, 2))
    assert (pa.t, pb.t) == (0, 2)                          # clamped per image
    assert [e.target_id for e in new_entries(p, n)] == [pb.id]
    applied, skipped = ed.edit([pa.id, pz.id], lambda q, r: F.set_projection(q, r, (4, 1)))
    assert (applied, skipped) == (1, 1)
    assert (pz.z_projection.z_start, pz.z_projection.z_end) == (1, 4)
    assert p.operation_log[-1].field == "z_projection"
    ed.edit([pz.id], lambda q, r: F.set_projection(q, r, None))
    assert pz.z_projection is None


def test_rotate_flip_keep_panel(env):
    _p, ed, _refs, (_pa, pb, _pz) = env
    size = (pb.width_mm, pb.height_mm)
    ed.edit([pb.id], lambda q, r: F.rotate(q, r, 90))
    ed.edit([pb.id], lambda q, r: F.flip(q, r, "flip_vertical"))
    assert (pb.rotation_deg, pb.flip_vertical) == (90, True)
    assert (pb.width_mm, pb.height_mm) == size
    ed.edit([pb.id], lambda q, r: F.rotate(q, r, -180))
    assert pb.rotation_deg == 270


def test_copy_paste_rendering_and_viewport(env):
    _p, ed, (one, two, _z), (pa, pb, pz) = env
    pb.channels[0].lut, pb.channels[0].white, pb.channels[1].gamma = "cyan", 9000, 1.4
    clip = F.rendering_of(pb)
    ed.edit([pa.id, pz.id], lambda q, r: F.paste_rendering(q, r, clip))
    for q in (pa, pz):
        assert (q.channels[0].lut, q.channels[0].white) == ("cyan", 9000)
        assert len(q.channels) == 1                        # channel 1 ignored (absent)
    s = two.metadata.shape
    ed.edit([pb.id], lambda q, r: V.zoom_by(q, s.X, s.Y, 2.0))
    view = V.copy_view(pb, s.X, s.Y)
    ed.edit([pa.id], lambda q, r: F.paste_viewport(q, r, view))
    assert V.crop_centre(pa.crop) == pytest.approx(V.crop_centre(pb.crop))


def test_image_defaults(env, workspace):
    p, ed, (one, two, _z), (_pa, pb, _pz) = env
    pb.channels[1].white = 7777
    pb.t = 1
    n = len(p.operation_log)
    assert F.save_as_image_defaults(p, pb) == 1
    [e] = new_entries(p, n)
    assert (e.operation, e.target_type, e.target_id) == ("image.display.from_panel", "image", two.id)
    assert e.new_value == {"display.channels[1].white": 7777, "display.t": 1}
    assert two.display.channels[1].white == 7777
    assert F.save_as_image_defaults(p, pb) == 0             # nothing changed: no entry
    two.display.channels[1].white = 1234                    # library edit
    ed.edit([pb.id], F.reset_to_image_defaults)
    assert pb.channels[1].white == 1234


def test_replace_image_checks(env, workspace, data_dir):
    p, ed, (one, two, zs), (pa, pb, pz) = env
    errors, _ = F.replace_problems(pa, one, two)
    assert errors and "channel" in errors[0]               # 1 channel -> 2 channels
    errors, warnings = F.replace_problems(pa, one, zs)
    assert not errors and any("size differs" in w for w in warnings)
    pz.scale_bar.visible = True
    assert not F.replace_problems(pz, zs, one)[0]          # calibrated: fine
    cal = one.metadata.calibration
    saved = cal.pixel_size_x
    cal.pixel_size_x = None
    errors, _ = F.replace_problems(pz, zs, one)
    assert errors and "not calibrated" in errors[0]
    cal.pixel_size_x = saved
    pz.scale_bar.visible = False
    # Replace keeps settings, clamps T/Z and levels.
    pa.channels[0].lut, pa.t = "yellow", 0
    n = len(p.operation_log)
    ed.edit([pa.id], lambda q, r: F.replace_image(q, zs) or True,
            operation="figure.panel.replace_image")
    assert pa.image_id == zs.id and pa.channels[0].lut == "yellow"
    e = p.operation_log[-1]
    assert e.operation == "figure.panel.replace_image" and e.field == "image_id"
    assert e.asset_sha256 == zs.sha256 and len(p.operation_log) == n + 1
    reloaded = workspace.load_project(p.project.id)
    assert reloaded == p and verify_log_chain(reloaded).status == "ok"
