# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""lock_group propagation (design §7.3): a lock shares LUT, black, white,
gamma and invert per channel index — never visibility, T/Z, projection,
crop, orientation or labels. One gesture = one undo step, one log entry
per panel changed. Also the condition grid's shared / own rendering."""

from __future__ import annotations

import pytest

from pycrograph import figures as F
from pycrograph import layout as L
from pycrograph.figures import FigureEditor, create_figure, panel_channel
from pycrograph.logchain import verify_log_chain
from pycrograph.models import Crop, PanelLabel
from pycrograph.storage import create_dataset, register_image


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z):
    p = workspace.create_project("Locks")
    ds = create_dataset(p, "A")
    one, two, zs = (register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z))
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    pa, pb, pz = ed.add_images([one, two, zs])
    return p, ed, (one, two, zs), (pa, pb, pz)


def lock(ed, panels, p):
    ed.layout([q.id for q in panels], lambda ps, _r: F.lock_panels(ps, p.images, "lock:t"),
              "figure.lock.set", "Lock")


def test_lock_syncs_to_first_panel(env):
    p, ed, _refs, (pa, pb, pz) = env
    pa.channels[0].lut, pa.channels[0].white = "cyan", 9000
    pb.channels[0].visible = False
    lock(ed, [pa, pb], p)
    assert (pb.lock_group, pa.lock_group) == ("lock:t", "lock:t")
    assert (panel_channel(pb, 0).lut, panel_channel(pb, 0).white) == ("cyan", 9000)
    assert panel_channel(pb, 0).visible is False                  # never shared
    assert panel_channel(pb, 1).lut != "cyan"                     # pa has no channel 1


def test_edit_propagates_to_group_one_entry_each(env):
    p, ed, (one, two, zs), (pa, pb, pz) = env
    lock(ed, [pa, pb, pz], p)
    zs.metadata.bit_depth = 12                                    # pz clamps at 4095
    n, depth = len(p.operation_log), len(ed.history._undo)
    ed.edit([pa.id], lambda q, r: F.set_channel_field(q, r, 0, "white", 20000),
            lock_values={(0, "white"): 20000})
    assert [panel_channel(q, 0).white for q in (pa, pb, pz)] == [20000, 20000, 4095]
    entries = p.operation_log[n:]
    assert [(e.target_id, e.field) for e in entries] == \
        [(pa.id, "channels[0].white"), (pb.id, "channels[0].white"), (pz.id, "channels[0].white")]
    assert len(ed.history._undo) == depth + 1 and set(ed.last_propagated) == {pb.id, pz.id}
    ed.undo()
    assert panel_channel(ed.figure.panel(pb.id), 0).white != 20000

    # Intended value, not the first panel's clamped copy: edit pz first.
    ed.edit([pz.id, pa.id], lambda q, r: F.set_channel_field(q, r, 0, "white", 30000),
            lock_values={(0, "white"): 30000})
    assert [panel_channel(ed.figure.panel(i), 0).white for i in (pa.id, pb.id, pz.id)] == \
        [30000, 30000, 4095]


def test_never_propagated_fields(env):
    p, ed, (one, two, zs), (pa, pb, pz) = env
    lock(ed, [pa, pb], p)
    before_b = pb.model_dump()
    edits = [lambda q, r: F.set_channel_field(q, r, 0, "visible", False),
             lambda q, r: F.set_t(q, r, 0) and F.set_z(q, r, 0),
             lambda q, r: F.rotate(q, r, 90),
             lambda q, r: F.flip(q, r, "flip_horizontal"),
             lambda q, r: setattr(q, "crop", Crop(x=1, y=1, w=10, h=10)) or True,
             lambda q, r: F.add_label(q, r, PanelLabel(text="A", position="top"))]
    for fn in edits:
        ed.edit([pa.id], fn)
    assert pb.model_dump() == before_b                            # nothing reached pb
    assert pa.rotation_deg == 90 and pa.labels


def test_lacking_channel_skipped_and_unlocked_untouched(env):
    p, ed, (one, two, zs), (pa, pb, pz) = env
    lock(ed, [pa, pb], p)                                         # pz not locked
    ed.edit([pb.id], lambda q, r: F.set_channel_field(q, r, 1, "lut", "yellow"),
            lock_values={(1, "lut"): "yellow"})
    assert len(pa.channels) == 1 and panel_channel(pb, 1).lut == "yellow"
    ed.edit([pb.id], lambda q, r: F.set_channel_field(q, r, 0, "gamma", 1.5))
    assert panel_channel(pa, 0).gamma == 1.5 and panel_channel(pz, 0).gamma == 1.0


def test_split_row_keeps_single_channel_visibility(env):
    p, ed, (one, two, zs), (pa, pb, pz) = env

    def split(ps, r):
        ed.figure.panels.extend(L.split_channels(ps[0], r[ps[0].image_id], 0, 80))
    ed.layout([pb.id], split, "figure.layout.split_channels", "Split")
    c0, c1, merge = ed.figure.panels[3:]
    n = len(p.operation_log)
    for v in (5000, 6000):                                        # live slider on the merge
        ed.edit([merge.id], lambda q, r, v=v: F.set_channel_field(q, r, 1, "white", v),
                key="w", final=False, lock_values={(1, "white"): v})
    ed.flush()
    assert [panel_channel(q, 1).white for q in (c0, c1, merge)] == [6000] * 3
    assert [[c.visible for c in q.channels] for q in (c0, c1, merge)] == \
        [[True, False], [False, True], [True, True]]
    assert [e.target_id for e in p.operation_log[n:]] == [c0.id, c1.id, merge.id]
    assert verify_log_chain(p).status == "ok"


def test_condition_grid_shared_and_own_rendering(env):
    p, ed, (one, two, zs), (pa, pb, pz) = env
    pb.channels[0].white, pb.channels[0].lut = 7000, "cyan"
    pa.channels[0].white, pa.channels[0].gamma = 3000, 2.0
    pa.crop = Crop(x=4, y=4, w=40, h=40)
    zs.metadata.bit_depth = 12
    pz.channels[0].white = 2000
    shared = L.condition_grid([(pb, two), (pa, one), (pz, zs)], 0, 100)
    row_a, row_z = shared[1][0], shared[2][0]
    assert (panel_channel(row_a, 0).white, panel_channel(row_a, 0).lut,
            panel_channel(row_a, 0).gamma) == (7000, "cyan", panel_channel(pb, 0).gamma)
    assert panel_channel(row_z, 0).white == 4095                  # clamped per bit depth
    assert row_a.crop.x != shared[0][0].crop.x                    # crop stays per image
    assert len({q.lock_group for r in shared for q in r if q}) == 1

    own = L.condition_grid([(pb, two), (pa, one)], 0, 200, own_rendering=True)
    assert panel_channel(own[1][0], 0).white == 3000 and panel_channel(own[1][0], 0).gamma == 2.0
    groups = [{q.lock_group for q in r if q} for r in own]
    assert all(len(g) == 1 for g in groups) and groups[0] != groups[1]   # one group per row
