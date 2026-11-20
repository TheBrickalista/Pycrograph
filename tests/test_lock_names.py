# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Readable lock group names: the UI and reports name a group by its kind
and its panels, never by its internal id ("split:3f9a…", "grid:…")."""

from __future__ import annotations

import re

import pytest

from pycrograph import consistency as C
from pycrograph import insets as I
from pycrograph import layout as L
from pycrograph import provenance as P
from pycrograph.figures import FigureEditor, create_figure
from pycrograph.storage import create_dataset, register_image

ID_RE = re.compile(r"\b(split|montage|grid|lock|region):[0-9a-z]")


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z):
    p = workspace.create_project("Names")
    ds = create_dataset(p, "A")
    one, two, zs = (register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z))
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    return p, fig, ed, (one, two, zs)


@pytest.mark.parametrize("group,kind", [
    ("split:ab12", "split row"), ("montage:ab12", "montage"), ("grid:ab12", "condition grid"),
    ("grid:ab12:row2", "condition grid"), ("lock:ab12", "manual lock"), ("odd", "manual lock"),
    (None, "manual lock")])
def test_lock_kind(group, kind):
    assert L.lock_kind(group) == kind


def test_panel_numbers(env):
    p, fig, ed, (one, two, zs) = env
    ps = ed.add_images([one, two, zs, one, two, zs, one])
    assert L.panel_numbers(fig, ps[2:3]) == "panel 3"
    assert L.panel_numbers(fig, ps[1:4]) == "panels 2–4"
    assert L.panel_numbers(fig, [ps[0], ps[2], ps[6], ps[4], ps[5]]) == "panels 1, 3, 5–7"
    assert L.panel_numbers(fig, []) == "no panel"


def test_consistency_names_groups_without_ids(env):
    p, fig, ed, (one, two, zs) = env
    [src] = ed.add_images([two])
    split = L.split_channels(src, two, 0, 60)                    # panels 2–4
    fig.panels.extend(split)
    for k, q in enumerate(split):
        q.y_mm = 60 + 50 * k                                     # break the row: lock group only
    split[1].channels[0].white = 999                             # levels differ inside the group
    split[1].channels[1].white = 998
    issues = C.check_figure(p, fig)
    [lv] = [i for i in issues if i.code == "levels"]
    assert lv.message.startswith("lock group (split row, panels 2–4): ")
    assert all(not ID_RE.search(i.message) for i in issues)
    # Every kind, through the same helper.
    for prefix, kind in (("montage", "montage"), ("grid", "condition grid"), ("lock", "manual lock")):
        for q in split:
            q.lock_group = f"{prefix}:9f3a"
        assert L.lock_group_name(fig, f"{prefix}:9f3a") == f"lock group ({kind}, panels 2–4)"


def test_inset_group_message_and_sidecar_without_ids(env):
    p, fig, ed, (one, two, zs) = env
    a, b = ed.add_images([one, one])
    src = I.create_inset(fig, a, one, (8, 8, 16, 16), "s")
    fig.panels.append(src)
    [cp] = I.replicate_inset(fig, src, [b], p.images, "region", True, lambda: "g1")
    fig.panels.append(cp)
    cp.width_mm += 5                                             # break the group on purpose
    issues = C.check_figure(p, fig)
    [g] = [i for i in issues if i.code == "inset_group"]
    assert g.message.startswith("linked inset group (panels 3–4): members differ in ")
    assert not ID_RE.search(g.message)
    a.lock_group = b.lock_group = "split:77aa"
    rec = P.panel_record(p, fig, a, 1)
    assert (rec["lock_group"], rec["lock_kind"]) == ("split:77aa", "split row")
    assert P.panel_record(p, fig, src, 3)["lock_kind"] is None
