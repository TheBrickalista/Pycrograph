# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Mixed bit depths in a lock (warning, consistency rule; propagation
unchanged) and locked edits that cannot reach panels lacking the channel."""

from __future__ import annotations

import numpy as np
import pytest

from pycrograph import consistency as C
from pycrograph import figures as F
from pycrograph.figures import FigureEditor, create_figure, panel_channel
from pycrograph.storage import create_dataset, register_image
from synth import metamorph_xml, write_metamorph_tiff

DEPTH_TEXT = ("These images have different bit depths (8-bit: panel 2; 16-bit: panels 1, 3). "
              "Equal levels do not mean equal intensities across bit depths or cameras.")


@pytest.fixture
def eight_bit(data_dir):
    plane = (np.arange(64 * 64).reshape(64, 64) % 256).astype(np.uint8)
    return write_metamorph_tiff(data_dir / "camera8.tif", [plane],
                                [metamorph_xml(width=64, height=64, bits=8)])


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, eight_bit):
    p = workspace.create_project("Depths")
    ds = create_dataset(p, "A")
    sixteen, two, eight = (register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, eight_bit))
    assert (sixteen.metadata.bit_depth, eight.metadata.bit_depth) == (16, 8)
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    return p, fig, ed, (sixteen, two, eight)


def lock(ed, panels, p, group="lock:t"):
    ed.layout([q.id for q in panels], lambda ps, _r: F.lock_panels(ps, p.images, group),
              "figure.lock.set", "Lock")


def test_bit_depth_warning_text(env):
    p, fig, ed, (sixteen, two, eight) = env
    a, b, c = ed.add_images([sixteen, eight, sixteen])
    assert F.bit_depth_warning(fig, [a, b, c], p.images) == DEPTH_TEXT
    assert F.bit_depth_warning(fig, [a, c], p.images) == ""


def test_consistency_rule_mixed_depth_lock(env):
    p, fig, ed, (sixteen, two, eight) = env
    a, b, c = ed.add_images([sixteen, eight, sixteen])
    for k, q in enumerate((a, b, c)):
        q.y_mm = 60 * k                                       # no row / column
        q.x_mm = 45 * k
    lock(ed, [a, b, c], p)
    issues = C.check_figure(p, fig)
    [i] = [i for i in issues if i.code == "bit_depth"]
    assert i.level == "major"
    # The 8-bit member holds its own clamped copy of the levels: said here,
    # not reported as a broken lock ("levels").
    assert i.message == ("lock group (manual lock, panels 1–3): mixes 8-bit and 16-bit images; "
                         "levels clamped to each image's range on channel 1")
    assert not [i for i in issues if i.code == "levels"]
    assert set(i.panel_ids) == {a.id, b.id, c.id}               # selects the panels
    lock(ed, [a, c], p, "lock:u")                               # 16-bit only now
    b.lock_group = None
    assert not [i for i in C.check_figure(p, fig) if i.code == "bit_depth"]


def test_propagation_unchanged_typed_value_stays_reference(env):
    p, fig, ed, (sixteen, two, eight) = env
    a, b, c = ed.add_images([sixteen, eight, sixteen])
    lock(ed, [a, b, c], p)
    ed.edit([a.id], lambda q, r: F.set_channel_field(q, r, 0, "white", 4000), key="w",
            lock_values={(0, "white"): 4000})
    assert panel_channel(b, 0).white == 255                     # clamped on the 8-bit image
    assert panel_channel(c, 0).white == 4000                    # not a clamped copy


def test_locked_edit_reports_panels_lacking_the_channel(env):
    p, fig, ed, (sixteen, two, eight) = env
    a, b, c, d = ed.add_images([two, two, sixteen, eight])      # 2, 2, 1, 1 channels
    lock(ed, [a, b, c, d], p)
    n = len(p.operation_log)
    ed.edit([a.id], lambda q, r: F.set_channel_field(q, r, 1, "gamma", 1.5), key="g",
            lock_values={(1, "gamma"): 1.5})
    assert ed.last_propagated == [b.id]
    assert ed.last_lock_skipped == {c.id: {1}, d.id: {1}}
    assert [e.target_id for e in p.operation_log[n:]] == [a.id, b.id]   # nothing logged on c, d
    assert F.skipped_note(fig, ed.last_lock_skipped, len(ed.last_propagated)) == \
        "applied to 1 locked panel; 2 skipped (no channel 2: panels 3–4)"
    # A channel every member has: nothing skipped.
    ed.edit([a.id], lambda q, r: F.set_channel_field(q, r, 0, "gamma", 1.2), key="g0",
            lock_values={(0, "gamma"): 1.2})
    assert ed.last_lock_skipped == {} and sorted(ed.last_propagated) == sorted([b.id, c.id, d.id])


def test_skipped_note_several_channels(env):
    p, fig, ed, (sixteen, two, eight) = env
    a, b, c = ed.add_images([two, sixteen, eight])
    assert F.skipped_note(fig, {b.id: {1, 2}, c.id: {2}}, 1) == \
        "applied to 1 locked panel; 2 skipped (no channel 2: panel 2; no channel 3: panels 2–3)"
    assert F.skipped_note(fig, {}, 3) == ""
