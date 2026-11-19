# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Insets (§2.7) and shapes, without Qt: raw <-> panel mapping (all 16
orientations), derived rectangle, editing it back, follow flags, links on
delete / paste, shape operations; step-4 follow-ups (time unit per group,
channel mismatches, lock differences)."""

from __future__ import annotations

import itertools

import pytest

from pycrograph import figures as F
from pycrograph import insets as I
from pycrograph import layout as L
from pycrograph import viewport as V
from pycrograph.figures import FigureEditor, create_figure, panel_from_image
from pycrograph.labels import group_time_unit, plain_text, resolve_label
from pycrograph.models import Crop, Panel, Shape
from pycrograph.storage import create_dataset, register_image

ORIENTATIONS = list(itertools.product((0, 90, 180, 270), (False, True), (False, True)))


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z):
    p = workspace.create_project("Insets")
    ds = create_dataset(p, "A")
    one, two, zs = (register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z))
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    return p, ed, (one, two, zs)


def oriented(rot, fh, fv, crop=(6, 4, 40, 30), w=40.0, h=30.0) -> Panel:
    p = Panel(id="p", image_id="i", width_mm=w, height_mm=h, rotation_deg=rot,
              flip_horizontal=fh, flip_vertical=fv, crop=Crop(x=crop[0], y=crop[1], w=crop[2], h=crop[3]))
    V.normalize(p, 64, 48)
    return p


# ------------------------------------------------------------------ mapping
@pytest.mark.parametrize("rot,fh,fv", ORIENTATIONS)
def test_unorient_point_inverts_orient_point(rot, fh, fv):
    for u, v in ((0, 0), (3.5, 1.25), (10, 7)):
        a, b = V.orient_point(u, v, 10, 7, rot, fh, fv)
        assert V.unorient_point(a, b, 10, 7, rot, fh, fv) == pytest.approx((u, v))


@pytest.mark.parametrize("rot,fh,fv", ORIENTATIONS)
def test_raw_panel_round_trip_and_image_rect(rot, fh, fv):
    p = oriented(rot, fh, fv)
    for x, y in ((6, 4), (20.5, 11), (46, 34), (-3, 50)):
        u, v = V.raw_to_panel(p, 64, 48, x, y)
        assert V.panel_to_raw(p, 64, 48, u, v) == pytest.approx((x, y))
    # The image region maps exactly where the canvas draws the pixmap.
    from pycrograph.render import crop_bounds
    b = crop_bounds(p.crop, 64, 48)
    assert V.raw_rect_to_panel(p, 64, 48, (b[0], b[1], b[2] - b[0], b[3] - b[1])) == \
        pytest.approx(V.image_rect_mm(p, 64, 48, b))
    r = (12.0, 8.0, 10.0, 6.0)
    assert V.panel_rect_to_raw(p, 64, 48, V.raw_rect_to_panel(p, 64, 48, r)) == pytest.approx(r)


# ------------------------------------------------------------------- insets
def test_create_inset_magnification_and_placement(env):
    p, ed, (one, two, zs) = env
    [parent] = ed.add_images([one])                      # 64 px in 40 mm at (0, 0)
    inset = I.create_inset(ed.figure, parent, one, (10, 20, 16, 12), "i1")
    assert inset.inset_of == parent.id and inset.crop == Crop(x=10, y=20, w=16, h=12)
    s = one.metadata.shape
    assert V.px_per_mm(inset, s.X, s.Y) == pytest.approx(V.px_per_mm(parent, s.X, s.Y) / 2)
    assert (inset.width_mm, inset.height_mm) == pytest.approx((20, 15))
    assert (inset.x_mm, inset.y_mm) == (42.0, 0.0)              # right of the parent, gap 2
    assert inset.labels == [] and inset.lock_group is None
    ed.figure.panels.append(inset)
    second = I.create_inset(ed.figure, parent, one, (0, 0, 8, 6), "i2")
    assert second.y_mm >= inset.y_mm + inset.height_mm             # stacked, no overlap
    assert second.inset_style.color != inset.inset_style.color
    ed.figure.panels.append(second)
    assert [I.inset_number(ed.figure, q) for q in (inset, second)] == [1, 2]
    # No room on the right: kept on the page.
    parent.x_mm = 150
    far = I.create_inset(ed.figure, parent, one, (0, 0, 32, 24), "i3")
    assert far.x_mm + far.width_mm <= ed.figure.page.size_mm[0] + 1e-6


@pytest.mark.parametrize("rot,fh,fv", ORIENTATIONS)
def test_rect_on_parent_derived_from_inset_crop(env, rot, fh, fv):
    p, ed, (one, two, zs) = env
    [parent] = ed.add_images([one])
    parent.rotation_deg, parent.flip_horizontal, parent.flip_vertical = rot, fh, fv
    V.fit(parent, 64, 64)
    inset = I.create_inset(ed.figure, parent, one, (10, 20, 16, 12), "i1")
    ed.figure.panels.append(inset)
    r = I.rect_on_parent(parent, inset, one)
    assert r == pytest.approx(V.raw_rect_to_panel(parent, 64, 64, (10, 20, 16, 12)))
    # Pan / zoom the inset: the rectangle follows (it is derived).
    V.pan(inset, 64, 64, 4, -2)
    assert I.rect_on_parent(parent, inset, one) == \
        pytest.approx(V.raw_rect_to_panel(parent, 64, 64, (14, 18, 16, 12)))
    V.zoom_by(inset, 64, 64, 2.0)
    assert I.rect_on_parent(parent, inset, one)[2] == pytest.approx(r[2] / 2)
    inset.inset_style.show_on_parent = False
    assert I.rect_on_parent(parent, inset, one) is None


@pytest.mark.parametrize("prot,pfh,pfv", ORIENTATIONS)
@pytest.mark.parametrize("irot", (0, 90))
def test_editing_rect_writes_inset_crop(env, prot, pfh, pfv, irot):
    p, ed, (one, two, zs) = env
    [parent] = ed.add_images([one])
    parent.rotation_deg, parent.flip_horizontal, parent.flip_vertical = prot, pfh, pfv
    V.fit(parent, 64, 64)
    inset = I.create_inset(ed.figure, parent, one, (10, 20, 16, 12), "i1")
    V.set_rotation(inset, 64, 64, irot)                           # the inset turned on its own
    ed.figure.panels.append(inset)
    size = (inset.width_mm, inset.height_mm)
    r = I.rect_on_parent(parent, inset, one)
    moved = (r[0] + 3.0, r[1] + 1.5, r[2], r[3])
    I.set_from_parent_rect(inset, parent, one, moved)
    assert I.rect_on_parent(parent, inset, one) == pytest.approx(moved)
    assert (inset.width_mm, inset.height_mm) == size              # page layout unchanged
    big = (moved[0], moved[1], moved[2] * 1.5, moved[3] * 1.5)    # corner drag, aspect kept
    I.set_from_parent_rect(inset, parent, one, big)
    assert I.rect_on_parent(parent, inset, one) == pytest.approx(big)


def test_other_image_has_no_rect(env):
    p, ed, (one, two, zs) = env
    [parent] = ed.add_images([one])
    inset = I.create_inset(ed.figure, parent, one, (0, 0, 8, 8), "i1")
    inset.image_id = zs.id
    assert I.rect_on_parent(parent, inset, one) is None


def test_follow_parent_flags_through_edit(env):
    p, ed, (one, two, zs) = env
    [parent] = ed.add_images([two])
    inset = I.create_inset(ed.figure, parent, two, (0, 0, 16, 12), "i1")
    with ed.gesture("figure.inset.create", "x"):
        ed.figure.panels.append(inset)
    n, depth = len(p.operation_log), len(ed.history._undo)
    ed.edit([parent.id], lambda q, r: F.set_t(q, r, 2))
    ed.edit([parent.id], lambda q, r: F.set_channel_field(q, r, 1, "lut", "cyan"))
    assert inset.t == 2 and F.panel_channel(inset, 1).lut == "cyan"
    assert ed.last_synced == [inset.id]
    entries = p.operation_log[n:]
    assert [(e.target_id, e.field) for e in entries] == [
        (parent.id, "t"), (inset.id, "t"), (parent.id, "channels[1].lut"), (inset.id, "channels[1].lut")]
    assert len(ed.history._undo) == depth + 2
    inset.inset_style.follow_parent_tz = False
    inset.inset_style.follow_parent_rendering = False
    ed.edit([parent.id], lambda q, r: F.set_t(q, r, 0))
    ed.edit([parent.id], lambda q, r: F.set_channel_field(q, r, 1, "white", 3000))
    assert inset.t == 2 and F.panel_channel(inset, 1).white != 3000
    # Editing the inset never touches the parent.
    ed.edit([inset.id], lambda q, r: F.set_z(q, r, 0) and V.pan(q, 32, 24, 3, 3) is None)
    assert parent.crop.x == 0


def test_delete_and_paste_keep_rules(env):
    p, ed, (one, two, zs) = env
    [parent, other] = ed.add_images([one, zs])
    inset = I.create_inset(ed.figure, parent, one, (0, 0, 8, 8), "i1")
    with ed.gesture("x", "x"):
        ed.figure.panels.append(inset)
    copies = F.pasted_panels(F.copy_panels([parent, inset]))
    assert copies[1].inset_of == copies[0].id and copies[1].inset_style is not None
    alone = F.pasted_panels(F.copy_panels([inset]))
    assert alone[0].inset_of is None and alone[0].inset_style is None
    ed.delete_panels([inset.id])                                  # rectangle simply gone
    assert I.insets_of(ed.figure, parent.id) == []
    inset2 = I.create_inset(ed.figure, parent, one, (0, 0, 8, 8), "i2")
    with ed.gesture("x", "x"):
        ed.figure.panels.append(inset2)
    ed.delete_panels([parent.id])
    kept = ed.figure.panel("i2")
    assert kept.inset_of is None and kept.inset_style is None     # ordinary panel


# ------------------------------------------------------------------- shapes
def test_shape_operations(env):
    p, ed, (one, two, zs) = env
    [a, b] = ed.add_images([one, zs])
    arrow = Shape(id="s1", kind="arrow", points=[(5, 5), (30, 20)], width_pt=1.5)
    n = len(p.operation_log)
    ed.edit([a.id], lambda q, r: F.add_shape(q, r, arrow), "figure.shape.add")
    ed.edit([a.id], lambda q, r: F.move_shapes(q, r, ["s1"], 2, -1), "figure.shape.move")
    ed.edit([a.id], lambda q, r: F.update_shapes(q, r, ["s1"], {"color": "#FF0000"}))
    assert a.shapes[0].points == [(7, 4), (32, 19)] and a.shapes[0].color == "#FF0000"
    clip = [sh.model_dump(mode="json") for sh in a.shapes]
    ed.edit([b.id], lambda q, r: F.paste_shapes(q, r, clip), "figure.shape.paste")
    assert b.shapes[0].points == a.shapes[0].points and b.shapes[0].id != "s1"
    ed.edit([a.id], lambda q, r: F.delete_shapes(q, r, ["s1"]), "figure.shape.delete")
    assert a.shapes == []
    assert [e.field for e in p.operation_log[n:]] == [
        "shapes", "shapes[0].points[0][0],shapes[0].points[0][1],shapes[0].points[1][0],"
        "shapes[0].points[1][1]", "shapes[0].color", "shapes", "shapes"]


@pytest.mark.parametrize("rot,fh,fv", ORIENTATIONS)
def test_shape_follows_crop_zoom_rotation(rot, fh, fv):
    p = oriented(rot, fh, fv)
    a = V.raw_to_panel(p, 64, 48, 20, 10)
    V.zoom_by(p, 64, 48, 2.0)                                       # about the crop centre
    cx, cy = V.raw_to_panel(p, 64, 48, *V.crop_centre(p.crop))
    b = V.raw_to_panel(p, 64, 48, 20, 10)
    assert (b[0] - cx, b[1] - cy) == pytest.approx((2 * (a[0] - cx), 2 * (a[1] - cy)))


# --------------------------------------------------------- step-4 follow-ups
def test_time_unit_per_group(env):
    p, ed, (one, two, zs) = env
    src = panel_from_image(two)
    src.time_offset_s = 0
    row = L.montage(src, two, "t", [0, 1, 2], 0, 0)               # 0, 30, 60 s
    unit = group_time_unit(row, p.images)
    assert unit == "min"
    texts = [plain_text(resolve_label("[time]", q, two, time_unit=unit)[0]) for q in row]
    assert texts == ["0 min", "0.5 min", "1 min"]
    assert plain_text(resolve_label("[time]", row[1], two)[0]) == "30 s"   # single: unchanged
    assert group_time_unit([panel_from_image(one)], p.images) is None


def test_channel_mismatch_and_lock_difference(env):
    p, ed, (one, two, zs) = env
    a, b = panel_from_image(one), panel_from_image(two)
    mism = F.channel_mismatches([a, b], p.images)
    assert [m.index for m in mism] == [0]
    assert "TxRed" in mism[0].message() and set(mism[0].panel_ids) == {a.id, b.id}
    assert F.channel_mismatches([a, panel_from_image(one)], p.images) == []
    b.channels[0].white = a.channels[0].white + 1
    assert F.locked_settings_differ([a, b]) == [b]
    b.channels[0].white = a.channels[0].white
    b.channels[0].lut, b.channels[0].black, b.channels[0].gamma = a.channels[0].lut, \
        a.channels[0].black, a.channels[0].gamma
    b.channels[1].lut = "cyan"                                     # a has no channel 1
    assert F.locked_settings_differ([a, b]) == []
