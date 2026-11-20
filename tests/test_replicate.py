# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Replicated insets (§2.7): compatibility, region / size modes, linked
groups propagated in one gesture, orientation, undo / redo, delete / detach /
copy-paste, consistency, migration, light archives."""

from __future__ import annotations

import itertools
import json

import numpy as np
import pytest

from pycrograph import consistency as C
from pycrograph import figures as F
from pycrograph import insets as I
from pycrograph import layout as L
from pycrograph import viewport as V
from pycrograph.figures import FigureEditor, create_figure
from pycrograph.models import PROJECT_FORMAT_VERSION
from pycrograph.scalebar import bar_geometry
from pycrograph.storage import create_dataset, register_image, set_image_hash, sha256_file
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff


def tif(path, n=64, px=0.325, stage=None, seed=0):
    return write_metamorph_tiff(path, [reference_like_plane(n, n, seed=seed)],
                                [metamorph_xml(width=n, height=n, px=px, stage=stage)])


@pytest.fixture
def env(workspace, data_dir, stk_tc):
    files = {
        "gfp": tif(data_dir / "c1.tif", stage=(100.0, 200.0), seed=1),
        "rfp": tif(data_dir / "c2.tif", stage=(100.0, 200.0), seed=2),       # same field
        "other": tif(data_dir / "c3.tif", stage=(900.0, 200.0), seed=3),     # other field
        "nostage": tif(data_dir / "c4.tif", seed=4),
        "coarse": tif(data_dir / "c5.tif", px=0.65, seed=5),                 # other pixel size
        "small": tif(data_dir / "c6.tif", n=32, seed=6),                     # other size
        "stk": stk_tc,
    }
    p = workspace.create_project("Rep")
    ds = create_dataset(p, "A")
    refs = {k: register_image(p, f, ds.id) for k, f in files.items()}
    for r in refs.values():
        set_image_hash(p, r.id, sha256_file(r.path))
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    return p, fig, ed, refs


def add(ed, *refs):
    return ed.add_images(list(refs))


def make_inset(ed, parent, rect, nid="src"):
    ref = ed.project.images[parent.image_id]
    inset = I.create_inset(ed.figure, parent, ref, rect, nid)
    with ed.gesture("figure.inset.create", "Create inset"):
        ed.figure.panels.append(inset)
    return inset


def replicate(ed, src, targets, **kw):
    counter = itertools.count()
    with ed.gesture("figure.inset.replicate", "Replicate inset"):
        copies = I.replicate_inset(ed.figure, src, targets, ed.project.images,
                                   new_id=lambda: f"n{next(counter)}", **kw)
        ed.figure.panels.extend(copies)
    return copies


def rect(panel, ref):
    s = ref.metadata.shape
    c = V.effective_crop(panel, s.X, s.Y)
    return (c.x, c.y, c.w, c.h)


# ----------------------------------------------------------- compatibility
def test_compatibility_and_stage(env):
    p, fig, ed, r = env
    assert I.compatibility(r["gfp"], r["gfp"]) == "same_image"
    assert I.compatibility(r["gfp"], r["rfp"]) == "same_geometry"
    assert I.compatibility(r["gfp"], r["coarse"]) == "different"
    assert I.compatibility(r["gfp"], r["small"]) == "different"
    assert I.stage_relation(r["gfp"], r["rfp"]) == "equal"
    assert I.stage_relation(r["gfp"], r["other"]) == "differ"
    assert I.stage_relation(r["gfp"], r["nostage"]) == "unknown"
    r["rfp"].metadata.calibration.pixel_size_x += 5e-7          # within 1e-6
    assert I.compatibility(r["gfp"], r["rfp"]) == "same_geometry"


# ---------------------------------------------------------- same image
def test_split_channels_identical_rects_linked_move(env):
    p, fig, ed, r = env
    [src_panel] = add(ed, r["stk"])

    def split(ps, refs):
        ed.figure.panels.extend(L.split_channels(ps[0], refs[ps[0].image_id], 0, 50))
    ed.layout([src_panel.id], split, "figure.layout.split_channels", "Split")
    c0, c1, merge = fig.panels[1:4]
    src = make_inset(ed, c0, (4, 4, 12, 9))
    n_undo = len(ed.history._undo)
    copies = replicate(ed, src, [c1, merge, c0, src])            # c0 / src skipped
    assert len(ed.history._undo) == n_undo + 1                   # one step
    assert [c.inset_of for c in copies] == [c1.id, merge.id]
    for c in copies:
        assert rect(c, r["stk"]) == pytest.approx(rect(src, r["stk"]))
        assert c.inset_style.region_link == "region"
        assert c.inset_style.region_group == src.inset_style.region_group is not None
        others = [q for q in fig.panels if q.id != c.id and q.page == c.page]
        assert not any(I._overlaps((c.x_mm, c.y_mm, c.width_mm, c.height_mm),
                                   (q.x_mm, q.y_mm, q.width_mm, q.height_mm)) for q in others)
        assert [ch.visible for ch in c.channels] == \
            [ch.visible for ch in fig.panel(c.inset_of).channels]            # follows its parent
    # A linked move: one gesture, one log entry per member, one undo step.
    n_log, n_undo = len(p.operation_log), len(ed.history._undo)
    ed.edit([src.id], lambda q, ref: V.pan(q, 32, 24, 3, 2) or True, key="pan")
    assert sorted(ed.last_region) == sorted(c.id for c in copies)
    assert [e.target_id for e in p.operation_log[n_log:]] == [src.id] + [c.id for c in copies]
    assert len(ed.history._undo) == n_undo + 1
    for c in copies:
        assert rect(c, r["stk"]) == pytest.approx(rect(src, r["stk"]))
    ed.undo()
    for c in copies:                                              # all back, in one step
        assert rect(fig.panel(c.id), r["stk"]) == pytest.approx((4, 4, 12, 9), abs=1e-6)
    ed.redo()
    assert rect(fig.panel(copies[0].id), r["stk"])[0] == pytest.approx(7)
    ed.undo()
    ed.undo()                                                     # undo the replicate itself
    assert [q.id for q in fig.panels if q.inset_of] == [src.id]
    assert fig.panel(src.id).inset_style.region_group is None


def test_offset_kept_when_free(env):
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["rfp"])
    b.y_mm = 80
    src = make_inset(ed, a, (8, 8, 16, 16))
    [cp] = replicate(ed, src, [b])
    assert (cp.x_mm - b.x_mm, cp.y_mm - b.y_mm) == pytest.approx((src.x_mm - a.x_mm, src.y_mm - a.y_mm))


# ---------------------------------------------------------- same geometry
def test_separate_channel_files_region_allowed(env):
    p, fig, ed, r = env
    a, b, c = add(ed, r["gfp"], r["rfp"], r["other"])
    src = make_inset(ed, a, (10, 20, 16, 12))
    copies = replicate(ed, src, [b, c])
    assert [x.inset_style.region_link for x in copies] == ["region", "region"]
    assert all(rect(x, r["gfp"]) == pytest.approx((10, 20, 16, 12)) for x in copies)
    assert I.stage_relation(r["gfp"], r["other"]) == "differ"   # the UI warns for this one
    # Pixels shown are each file's own.
    s = V.px_per_mm(copies[0], 64, 64)
    assert s == pytest.approx(V.px_per_mm(src, 64, 64))


# --------------------------------------------------------- different geometry
def test_different_pixel_size_falls_back_to_size(env):
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["coarse"])
    src = make_inset(ed, a, (40, 10, 20, 16))
    [cp] = replicate(ed, src, [b], mode="region")               # requested, not possible
    assert cp.inset_style.region_link == "size"
    sx, sy, sw, sh = rect(cp, r["coarse"])
    assert (sw * 0.65, sh * 0.65) == pytest.approx((20 * 0.325, 16 * 0.325))    # same µm
    assert (sx + sw / 2) / 64 == pytest.approx((40 + 10) / 64)                  # same relative centre
    assert I.physical_per_mm(cp, r["coarse"]) == pytest.approx(I.physical_per_mm(src, r["gfp"]))
    assert (cp.width_mm, cp.height_mm) == pytest.approx((src.width_mm, src.height_mm))


def test_size_mode_clamped_at_image_edges(env):
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["small"])                        # 64 px vs 32 px, same px size
    src = make_inset(ed, a, (48, 0, 16, 12))                     # at the right / top edges
    [cp] = replicate(ed, src, [b])
    x, y, w, h = rect(cp, r["small"])
    assert (w, h) == pytest.approx((16, 12))
    assert x >= -1e-6 and x + w <= 32 + 1e-6 and y >= -1e-6 and y + h <= 32 + 1e-6
    # Size-linked edit: the sibling keeps its own centre, gets the new size.
    centre = (x + w / 2, y + h / 2)
    ed.edit([src.id], lambda q, ref: V.zoom_by(q, 64, 64, 2.0) or True)
    x2, y2, w2, h2 = rect(fig.panel(cp.id), r["small"])
    assert (w2, h2) == pytest.approx((8, 6)) and (x2 + w2 / 2, y2 + h2 / 2) == pytest.approx(centre)


def test_region_never_propagated_across_incompatible_geometry(env):
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["coarse"])
    src = make_inset(ed, a, (10, 10, 16, 16))
    [cp] = replicate(ed, src, [b])
    cp.inset_style = cp.inset_style.model_copy(update={"region_link": "region"})   # forced
    before = rect(cp, r["coarse"])
    ed.edit([src.id], lambda q, ref: V.pan(q, 64, 64, 5, 0) or True)
    assert rect(fig.panel(cp.id), r["coarse"]) == pytest.approx(before)
    assert ed.last_region == []


@pytest.mark.parametrize("rot,fh,fv", [(0, False, False), (90, True, False), (180, False, True),
                                       (270, True, True)])
def test_rotated_flipped_parents(env, rot, fh, fv):
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["rfp"])
    for q in (b,):
        q.flip_horizontal, q.flip_vertical = fh, fv
        V.set_rotation(q, 64, 64, rot)
    src = make_inset(ed, a, (10, 20, 16, 12))
    [cp] = replicate(ed, src, [b])
    assert rect(cp, r["rfp"]) == pytest.approx((10, 20, 16, 12))          # same raw region
    # One inset repeated: the SOURCE's orientation and size, whatever the parent's.
    assert (cp.rotation_deg, cp.flip_horizontal, cp.flip_vertical) == \
        (src.rotation_deg, src.flip_horizontal, src.flip_vertical)
    assert (cp.width_mm, cp.height_mm) == (src.width_mm, src.height_mm)
    on_b = I.rect_on_parent(b, cp, r["rfp"])                               # drawn on the rotated parent
    assert on_b == pytest.approx(V.raw_rect_to_panel(b, 64, 64, (10, 20, 16, 12)))


# ---------------------------------------------------- membership changes
def test_delete_detach_and_copy_paste(env):
    p, fig, ed, r = env
    a, b, c = add(ed, r["gfp"], r["rfp"], r["nostage"])
    src = make_inset(ed, a, (10, 10, 16, 16))
    c1, c2 = replicate(ed, src, [b, c])
    g = src.inset_style.region_group
    # Copy the whole group (with parents): a new group for the copies.
    whole = F.pasted_panels(F.copy_panels([a, b, c, src, c1, c2], fig))
    groups = {q.inset_style.region_group for q in whole if q.inset_style}
    assert len(groups) == 1 and g not in groups
    # Partial copy: leaves the group.
    part = F.pasted_panels(F.copy_panels([a, src], fig))
    assert part[1].inset_style.region_group is None
    ed.delete_panels([c2.id])
    assert {fig.panel(x).inset_style.region_group for x in (src.id, c1.id)} == {g}
    ed.delete_panels([c1.id])                                       # group of one: cleared
    assert fig.panel(src.id).inset_style.region_group is None


def test_size_group_consistency_warning(env):
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["coarse"])
    src = make_inset(ed, a, (10, 10, 16, 16))
    [cp] = replicate(ed, src, [b], mode="size")
    assert not [i for i in C.check_figure(p, fig) if i.code == "inset_group"]
    V.zoom_by(cp, 64, 64, 1.5)                                     # tampered outside an edit
    [i] = [i for i in C.check_figure(p, fig) if i.code == "inset_group"]
    assert set(i.panel_ids) == {src.id, cp.id} and "region size" in i.message


# ---------------------------------------------------- migration / archive
def test_migrate_v2_project(workspace, env):
    p, fig, ed, r = env
    a, = add(ed, r["gfp"])
    make_inset(ed, a, (10, 10, 16, 16))
    workspace.save_project(p)
    path = workspace.project_json(p.project.id)
    d = json.loads(path.read_text())
    d["project"]["format_version"] = 2
    for panel in d["figures"][0]["panels"]:
        if panel.get("inset_style"):
            panel["inset_style"].pop("region_group")
            panel["inset_style"].pop("region_link")
    path.write_text(json.dumps(d))
    q = workspace.load_project(p.project.id)
    st = q.figures[0].panels[1].inset_style
    assert (st.region_group, st.region_link) == (None, "region")
    assert q.operation_log[-1].operation == "project.migrate"
    assert (q.operation_log[-1].old_value, q.operation_log[-1].new_value) == \
        (2, PROJECT_FORMAT_VERSION)


def test_light_archive_covers_replicated_regions(workspace, env, tmp_path):
    from pycrograph import archive as A
    from pycrograph.render import PanelRenderer
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["rfp"])
    src = make_inset(ed, a, (2, 2, 12, 12))
    [cp] = replicate(ed, src, [b])
    ed.edit([cp.id], lambda q, ref: True)                           # no-op
    ed.edit([src.id], lambda q, ref: V.pan(q, 64, 64, 40, 40) or True)   # far corner, both move

    def renders(project):
        rr = PanelRenderer(lambda ref: workspace.resolve_image_path(project, ref))
        try:
            return [rr.render(project.images[q.image_id], q) for q in project.figures[0].panels]
        finally:
            rr.close()
    before = renders(p)
    res = A.export_archive(workspace, p, tmp_path / "rep.pcarchive", "light")
    q = A.import_archive(workspace, res.path).project
    after = renders(q)
    assert len(after) == 4 and all(np.array_equal(x, y) for x, y in zip(before, after))


def test_size_needs_both_or_neither_calibrated(env):
    p, fig, ed, r = env
    a, b, c = add(ed, r["gfp"], r["small"], r["coarse"])
    r["small"].metadata.calibration.pixel_size_x = None            # uncalibrated, other size
    src = make_inset(ed, a, (10, 10, 16, 16))
    copies = replicate(ed, src, [b, c])
    assert [fig.panel(x.inset_of).id for x in copies] == [c.id]    # b skipped: µm vs px
    assert not I.size_comparable(r["gfp"], r["small"])
    r["gfp"].metadata.calibration.pixel_size_x = None               # both uncalibrated: px
    assert I.size_comparable(r["gfp"], r["small"])


# --------------------------------------------------------------- placement
def bottom(p):
    return p.y_mm + p.height_mm


def test_create_inset_in_a_row_goes_below(env):
    p, fig, ed, r = env
    a, b, c = add(ed, r["gfp"], r["rfp"], r["other"])            # one row
    assert a.y_mm == b.y_mm == c.y_mm and I.in_row(fig, b)
    src = make_inset(ed, b, (8, 8, 16, 16))
    assert (src.x_mm, src.y_mm) == (b.x_mm, bottom(b) + 2)       # below, not to the right
    second = make_inset(ed, b, (30, 30, 8, 8), "src2")           # stacked under the first
    assert second.x_mm == b.x_mm and second.y_mm >= bottom(src) + 2
    # No room below (row at the page bottom): the old rule, right of the parent.
    page_h = fig.page.size_mm[1]
    for q in (a, b, c):
        q.y_mm = page_h - q.height_mm
    low = I.create_inset(fig, a, r["gfp"], (8, 8, 16, 16), "low")
    assert low.x_mm >= a.x_mm + a.width_mm


def test_single_panel_inset_stays_right(env):
    p, fig, ed, r = env
    [a] = add(ed, r["gfp"])
    assert not I.in_row(fig, a)
    src = make_inset(ed, a, (8, 8, 16, 16))
    assert src.x_mm == a.x_mm + a.width_mm + 2 and src.y_mm == a.y_mm


def test_replicate_below_each_parent_same_gap_and_alignment(env):
    p, fig, ed, r = env
    a, b, c = add(ed, r["gfp"], r["rfp"], r["other"])
    c.height_mm += 7                                             # parents of different heights
    src = make_inset(ed, a, (8, 8, 16, 16))
    src.x_mm += 3                                                # left offset 3, gap 5
    src.y_mm += 3
    assert I.is_below(src, a)
    copies = replicate(ed, src, [b, c], mode="size")
    for cp, parent in zip(copies, (b, c)):
        assert cp.x_mm - parent.x_mm == pytest.approx(src.x_mm - a.x_mm)
        assert cp.y_mm - bottom(parent) == pytest.approx(src.y_mm - bottom(a))


def test_replicate_row_of_parents_source_elsewhere(env):
    """A row of parents with the source NOT below its parent (e.g. a project
    made before this rule): copies still go below each parent, at the gap."""
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["rfp"])
    src = make_inset(ed, a, (8, 8, 16, 16))
    src.x_mm, src.y_mm = 120.0, 0.0                              # off to the right
    assert not I.is_below(src, a)
    [cp] = replicate(ed, src, [b], gap_mm=2.0)
    assert (cp.x_mm, cp.y_mm) == (b.x_mm, bottom(b) + 2.0)


def test_replicate_no_room_below_falls_back(env):
    p, fig, ed, r = env
    a, b = add(ed, r["gfp"], r["rfp"])
    src = make_inset(ed, a, (8, 8, 16, 16))
    page_h = fig.page.size_mm[1]
    a.y_mm = b.y_mm = page_h - b.height_mm                     # a row; nothing fits below
    [cp] = replicate(ed, src, [b])
    assert bottom(cp) <= page_h + 1e-6 and cp.y_mm >= 0
    others = [q for q in fig.panels if q.id != cp.id]
    assert not any(I._overlaps((cp.x_mm, cp.y_mm, cp.width_mm, cp.height_mm),
                               (q.x_mm, q.y_mm, q.width_mm, q.height_mm)) for q in others)


# -------------------------------------------------------------- scale bars
def bar(panel):
    return panel.scale_bar.model_dump()


def test_new_inset_has_parent_bar_style_but_hidden(env):
    p, fig, ed, r = env
    [a] = add(ed, r["gfp"])
    a.scale_bar = a.scale_bar.model_copy(update={
        "visible": True, "length": 5.0, "color": "#FFAA00", "thickness_pt": 3.0,
        "position": "top-left", "font_size_pt": 9.0, "label_position": "above"})
    src = make_inset(ed, a, (8, 8, 16, 16))
    assert src.scale_bar.visible is False                          # not inherited
    assert {**bar(src), "visible": True} == bar(a)                 # style, unit, length kept
    src.scale_bar.visible = True                                   # ticking it on
    g_in, g_par = bar_geometry(src, r["gfp"]), bar_geometry(a, r["gfp"])
    assert g_in.text == g_par.text and src.scale_bar.color == "#FFAA00"
    assert g_in.length_mm == pytest.approx(2 * g_par.length_mm)    # 2× magnification


@pytest.mark.parametrize("shown", [True, False])
def test_replicate_copies_source_bar_not_target_parents(env, shown):
    p, fig, ed, r = env
    a, b, c = add(ed, r["gfp"], r["rfp"], r["coarse"])             # c: 0.65 µm/px
    for q, length, colour in ((b, 20.0, "#00FF00"), (c, 50.0, "#0000FF")):
        q.scale_bar = q.scale_bar.model_copy(update={"visible": True, "length": length,
                                                     "color": colour})
    src = make_inset(ed, a, (8, 8, 16, 16))
    src.scale_bar = src.scale_bar.model_copy(update={"visible": shown, "length": 3.0,
                                                     "color": "#FF00FF"})
    copies = replicate(ed, src, [b, c])
    assert [c_.inset_style.region_link for c_ in copies] == ["region", "size"]
    for cp in copies:
        assert bar(cp) == bar(src)                                 # visible or not, same style
    if shown:
        # Same µm length at the same physical magnification: the same bar on the page,
        # across pixel sizes.
        g0 = bar_geometry(src, r["gfp"])
        for cp, ref in zip(copies, (r["rfp"], r["coarse"])):
            g = bar_geometry(cp, ref)
            assert g.text == g0.text and g.length_mm == pytest.approx(g0.length_mm, rel=1e-6)
    # Per inset afterwards: a linked edit does not propagate the bar.
    n = len(ed.history._undo)
    ed.edit([src.id], lambda q, _r: setattr(q.scale_bar, "length", 7.0) or True, key="bar")
    assert len(ed.history._undo) == n + 1
    assert all(cp.scale_bar.length == 3.0 for cp in copies)
