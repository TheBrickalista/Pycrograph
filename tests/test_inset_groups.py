# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""A linked inset group is ONE inset repeated: members share page size,
orientation and region (raw rect, or µm size at the same magnification) —
whatever path changed one of them. Positions stay per member."""

from __future__ import annotations

import itertools

import pytest

from pycrograph import consistency as C
from pycrograph import figures as F
from pycrograph import insets as I
from pycrograph import layout as L
from pycrograph import viewport as V
from pycrograph.figures import FigureEditor, create_figure
from pycrograph.storage import create_dataset, register_image, set_image_hash, sha256_file
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff


def tif(path, px=0.325, seed=0):
    return write_metamorph_tiff(path, [reference_like_plane(64, 64, seed=seed)],
                                [metamorph_xml(width=64, height=64, px=px)])


@pytest.fixture
def env(workspace, data_dir):
    files = [tif(data_dir / "a.tif", seed=1), tif(data_dir / "b.tif", seed=2),
             tif(data_dir / "c.tif", px=0.65, seed=3)]
    p = workspace.create_project("Groups")
    ds = create_dataset(p, "A")
    refs = [register_image(p, f, ds.id) for f in files]
    for r in refs:
        set_image_hash(p, r.id, sha256_file(r.path))
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    a, b, c = ed.add_images(refs)
    b.y_mm = c.y_mm = 0
    b.x_mm, c.x_mm = 0, 0
    b.y_mm, c.y_mm = 60, 120
    src = I.create_inset(fig, a, refs[0], (10, 14, 12, 9), "src")
    with ed.gesture("figure.inset.create", "x"):
        fig.panels.append(src)
    counter = itertools.count()
    with ed.gesture("figure.inset.replicate", "Replicate"):
        copies = I.replicate_inset(fig, src, [b, c], p.images, new_id=lambda: f"n{next(counter)}")
        fig.panels.extend(copies)
    cb, cc = copies
    assert (cb.inset_style.region_link, cc.inset_style.region_link) == ("region", "size")
    return p, fig, ed, refs, (a, b, c), (src, cb, cc)


def rect(fig, pid, images):
    q = fig.panel(pid)
    ref = images[q.image_id]
    c = V.effective_crop(q, ref.metadata.shape.X, ref.metadata.shape.Y)
    return (c.x, c.y, c.w, c.h)


def consistent(p, fig):
    assert I.group_invariant_issues(fig, p.images) == []
    assert not [i for i in C.check_figure(p, fig) if i.code == "inset_group"]


def geometry(fig, pid):
    q = fig.panel(pid)
    return (q.width_mm, q.height_mm, q.rotation_deg, q.flip_horizontal, q.flip_vertical)


def one_step(ed, action):
    """Run action; assert it is one undo step; return (after, before) snapshots."""
    before = F.snapshot(ed.figure)
    depth = len(ed.history._undo)
    action()
    assert len(ed.history._undo) == depth + 1
    after = F.snapshot(ed.figure)
    ed.undo()
    assert F.snapshot(ed.figure)["panels"] == before["panels"]
    ed.redo()
    assert F.snapshot(ed.figure)["panels"] == after["panels"]


def test_replicated_copies_are_the_same_inset(env):
    p, fig, ed, refs, parents, (src, cb, cc) = env
    consistent(p, fig)
    assert geometry(fig, cb.id) == geometry(fig, cc.id) == geometry(fig, src.id)
    assert rect(fig, cb.id, p.images) == pytest.approx(rect(fig, src.id, p.images))
    w, h = rect(fig, cc.id, p.images)[2:]
    assert (w * 0.65, h * 0.65) == pytest.approx((12 * 0.325, 9 * 0.325))


def test_repro_set_geometry_resize(env):
    """Repro 1: resizing the source grew its crop; the copy kept the old rect."""
    p, fig, ed, refs, parents, (src, cb, cc) = env
    n = len(p.operation_log)
    one_step(ed, lambda: ed.set_geometry({src.id: (src.x_mm, src.y_mm, src.width_mm * 1.5,
                                                   src.height_mm)}, "figure.panel.resize", "R"))
    consistent(p, fig)
    assert geometry(fig, cb.id)[:2] == pytest.approx(geometry(fig, src.id)[:2])
    assert rect(fig, cb.id, p.images) == pytest.approx(rect(fig, src.id, p.images))
    targets = [e.target_id for e in p.operation_log[n:]]
    assert {src.id, cb.id, cc.id} <= set(targets)


@pytest.mark.parametrize("tool", ["same_width", "same_height", "same_both", "align_magnification"])
def test_layout_tools_on_one_member(env, tool):
    p, fig, ed, refs, (a, b, c), (src, cb, cc) = env
    if tool == "align_magnification":
        fn = lambda ps, r: L.align_magnification(ps, r, reference=fig.panel(a.id))   # noqa: E731
    else:
        mode = tool.split("_")[1]
        fn = lambda ps, r, m=mode: L.same_size(ps, r, fig.panel(a.id), m)            # noqa: E731
    one_step(ed, lambda: ed.layout([a.id, src.id], fn, f"figure.layout.{tool}", tool))
    consistent(p, fig)
    assert geometry(fig, cb.id) == pytest.approx(geometry(fig, src.id))


def test_positions_stay_per_member(env):
    p, fig, ed, refs, (a, b, c), (src, cb, cc) = env
    pos = (fig.panel(cb.id).x_mm, fig.panel(cb.id).y_mm)
    ed.layout([src.id, a.id], lambda ps, r: L.align(ps, "left"), "figure.layout.align_left", "A")
    ed.set_geometry({src.id: (src.x_mm + 3, src.y_mm + 4, src.width_mm, src.height_mm)})
    assert (fig.panel(cb.id).x_mm, fig.panel(cb.id).y_mm) == pos
    consistent(p, fig)


def test_repro_rotation(env):
    """Repro 2: rotating the source pushed its raw rect into a sibling with
    the old orientation / aspect."""
    p, fig, ed, refs, parents, (src, cb, cc) = env
    one_step(ed, lambda: ed.edit([src.id], lambda q, r: F.rotate(q, r, 90)))
    consistent(p, fig)
    assert fig.panel(cb.id).rotation_deg == 90 == fig.panel(cc.id).rotation_deg
    assert rect(fig, cb.id, p.images) == pytest.approx(rect(fig, src.id, p.images))
    # 12 x 9 centred at (16, 18.5), turned about its centre at the same scale.
    assert rect(fig, src.id, p.images) == pytest.approx((11.5, 12.5, 9, 12))


@pytest.mark.parametrize("attr", ["flip_horizontal", "flip_vertical"])
def test_flip_one_member(env, attr):
    p, fig, ed, refs, parents, (src, cb, cc) = env
    one_step(ed, lambda: ed.edit([cc.id], lambda q, r: F.flip(q, r, attr)))   # a "size" member
    consistent(p, fig)
    assert getattr(fig.panel(src.id), attr) and getattr(fig.panel(cb.id), attr)


def test_paste_viewport_onto_member(env):
    p, fig, ed, refs, (a, b, c), (src, cb, cc) = env
    view = V.copy_view(a, 64, 64)                                   # the whole parent's view
    one_step(ed, lambda: ed.edit([cb.id], lambda q, r: F.paste_viewport(q, r, view)))
    consistent(p, fig)
    assert rect(fig, src.id, p.images) == pytest.approx(rect(fig, cb.id, p.images))


def test_live_ticks_are_one_step(env):
    p, fig, ed, refs, parents, (src, cb, cc) = env

    def drag():
        for k in range(3):
            ed.edit([src.id], lambda q, r: V.pan(q, 64, 64, 1, 0.5) or True, key="pan", final=False)
            assert rect(fig, cb.id, p.images) == pytest.approx(rect(fig, src.id, p.images))
        ed.flush()
    one_step(ed, drag)
    consistent(p, fig)
    assert rect(fig, cb.id, p.images)[:2] == pytest.approx((13, 15.5))


@pytest.mark.parametrize("order", ["copy_first", "source_first"])
def test_two_members_in_one_gesture_first_wins(env, order):
    p, fig, ed, refs, parents, (src, cb, cc) = env
    moves = {src.id: (5, 0), cb.id: (0, 7)}
    ids = [cb.id, src.id] if order == "copy_first" else [src.id, cb.id]
    ed.edit(ids, lambda q, r: V.pan(q, 64, 64, *moves[q.id]) or True)
    consistent(p, fig)
    winner = ids[0]
    dx, dy = moves[winner]
    assert rect(fig, src.id, p.images)[:2] == pytest.approx((10 + dx, 14 + dy))


def test_size_group_across_pixel_sizes_stays_consistent(env):
    p, fig, ed, refs, parents, (src, cb, cc) = env
    centre_c = (lambda r: (r[0] + r[2] / 2, r[1] + r[3] / 2))(rect(fig, cc.id, p.images))
    ed.edit([src.id], lambda q, r: V.zoom_by(q, 64, 64, 1.7) or True)
    ed.set_geometry({src.id: (src.x_mm, src.y_mm, 30.0, 18.0)}, "figure.panel.resize", "R")
    ed.edit([src.id], lambda q, r: F.rotate(q, r, 270))
    consistent(p, fig)
    ra, rc = rect(fig, src.id, p.images), rect(fig, cc.id, p.images)
    assert (rc[2] * 0.65, rc[3] * 0.65) == pytest.approx((ra[2] * 0.325, ra[3] * 0.325))
    assert I.physical_per_mm(fig.panel(cc.id), refs[2]) == \
        pytest.approx(I.physical_per_mm(fig.panel(src.id), refs[0]), rel=1e-6)   # crops kept to 1e-6
    assert (rc[0] + rc[2] / 2, rc[1] + rc[3] / 2) == pytest.approx(centre_c, abs=1e-6)


def test_invariant_checker_catches_divergence(env):
    p, fig, ed, refs, parents, (src, cb, cc) = env
    fig.panel(cb.id).width_mm += 3                                   # tampered outside an edit
    [(msg, ids)] = I.group_invariant_issues(fig, p.images)
    assert "panel size or orientation" in msg and set(ids) == {src.id, cb.id, cc.id}
    [i] = [i for i in C.check_figure(p, fig) if i.code == "inset_group"]
    assert i.severity == "warning"
    ed.edit([src.id], lambda q, r: V.pan(q, 64, 64, 1, 0) or True)  # any edit re-syncs
    consistent(p, fig)
