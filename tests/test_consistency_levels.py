# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Consistency check: three levels (critical / major / minor), stains
compared by identity in rows and columns, and the downgrade rules."""

from __future__ import annotations

import re

import pytest

from pycrograph import consistency as C
from pycrograph import layout as L
from pycrograph.figures import FigureEditor, create_figure, lock_panels, panel_channel
from pycrograph.insets import create_inset
from pycrograph.storage import create_dataset, register_image
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff

WAVELENGTHS = (("3 GFP", 520), ("5 TxRed", 630), ("7 Cy5", 670))
CONDITIONS = ("control", "drug", "washout")


def write_wavelength(path, illum, nm, seed):
    """One single-channel MetaMorph file per wavelength (`_w1`, `_w2`…)."""
    return write_metamorph_tiff(path, [reference_like_plane(64, 64, seed)],
                                [metamorph_xml(illum=illum, wavelength=nm, width=64, height=64,
                                               name=path.stem)])


@pytest.fixture
def grid(workspace, data_dir):
    """Rows = conditions, columns = wavelengths, one lock group per
    wavelength, each wavelength shown with its own levels."""
    p = workspace.create_project("Per wavelength")
    ds = create_dataset(p, "A")
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: None)
    cells = {}
    for r, cond in enumerate(CONDITIONS):
        for c, (illum, nm) in enumerate(WAVELENGTHS):
            f = write_wavelength(data_dir / f"{cond}_w{c + 1}{illum.split()[1]}.tif", illum, nm,
                                 seed=3 * r + c)
            ref = register_image(p, f, ds.id)
            ref.sha256 = "0" * 64
            [panel] = ed.add_images([ref])
            panel.x_mm, panel.y_mm = 10 + 42 * c, 10 + 42 * r
            panel.width_mm = panel.height_mm = 40
            cells[cond, c] = panel
    for c in range(len(WAVELENGTHS)):
        column = [cells[cond, c] for cond in CONDITIONS]
        panel_channel(column[0], 0).black, panel_channel(column[0], 0).white = 2500 + 100 * c, 6000 + 900 * c
        lock_panels(column, p.images, f"lock:w{c + 1}")
    return p, fig, ed, cells


def run(p, fig, **kw):
    kw.setdefault("source_status", lambda _r: "ok")
    return C.check_figure(p, fig, **kw)


def rules(issues, *names):
    return [i for i in issues if not names or i.rule in names]


# ------------------------------------------------------------ the reference case
def test_one_lock_group_per_wavelength_is_consistent(grid):
    p, fig, _ed, cells = grid
    issues = run(p, fig)
    assert not [i for i in issues if i.code in ("levels", "channels", "lock_groups", "bit_depth")]
    # The rows (conditions) mix three stains on channel index 0: never compared.
    assert {i.rule for i in issues} <= {"resolution.panel", "font_size"}


def test_same_stain_unlocked_in_a_column_is_major(grid):
    p, fig, _ed, cells = grid
    odd = cells["washout", 1]                       # TxRed, taken out of its lock group
    odd.lock_group = None
    panel_channel(odd, 0).white = 4000
    [i] = rules(run(p, fig), "levels.row")
    assert i.level == "major" and "column 2: 5 TxRed (630 nm)" in i.message
    assert "not locked together" in i.message
    assert set(i.panel_ids) == {cells[c, 1].id for c in CONDITIONS}


def test_different_lock_groups_in_a_column_is_minor_once(grid):
    p, fig, _ed, cells = grid
    a, b, c = (cells[cond, 0] for cond in CONDITIONS)          # GFP column
    lock_panels([c], p.images, "lock:other")                   # 2 lock groups, 1 stain
    panel_channel(c, 0).white = 9999
    issues = run(p, fig)
    [i] = rules(issues, "lock_groups")
    assert i.level == "minor" and "column 1: 2 lock groups show 3 GFP (520 nm)" in i.message
    assert "intended?" in i.message and not rules(issues, "levels.row", "levels.lock")


def test_lock_groups_reported_once_per_row_not_per_channel(workspace, stk_tc):
    p = workspace.create_project("Two channels")
    ref = register_image(p, stk_tc, create_dataset(p, "A").id)
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: None)
    a, b = ed.add_images([ref, ref])                           # one row
    lock_panels([a], p.images, "lock:a")
    lock_panels([b], p.images, "lock:b")
    for idx in (0, 1):
        for q in (a, b):
            panel_channel(q, idx).visible = True
        panel_channel(b, idx).white = 5000 + idx               # defaults are 1000 / 1100
    [i] = rules(run(p, fig), "lock_groups")
    assert "GFP (488 nm), TxRed (561 nm)" in i.message          # both channels, one issue


def test_different_stains_in_a_lock_group_are_critical(workspace, mm_tiff, stk_z):
    p = workspace.create_project("Mixed lock")
    ds = create_dataset(p, "A")
    one, zs = register_image(p, mm_tiff, ds.id), register_image(p, stk_z, ds.id)
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: None)
    a, b = ed.add_images([one, zs])
    lock_panels([a, b], p.images, "lock:x")
    [i] = rules(run(p, fig), "channels.lock")
    assert i.level == "critical" and "TxRed" in i.message and "GFP" in i.message


def test_broken_lock_is_critical(grid):
    p, fig, _ed, cells = grid
    panel_channel(cells["drug", 2], 0).white = 1234             # same bit depth: not clamping
    [i] = rules(run(p, fig), "levels.lock")
    assert i.level == "critical" and "lock broken" in i.message


# ------------------------------------------------------------ scale bars
def test_scale_bar_same_scale_is_minor_other_scale_major(grid):
    p, fig, _ed, cells = grid
    row = [cells["control", c] for c in range(3)]
    row[0].scale_bar.visible = True
    [i] = rules(run(p, fig), "scalebar.same_scale", "scalebar.other_scale")
    assert (i.rule, i.level) == ("scalebar.same_scale", "minor")
    assert "fine if stated in the legend" in i.message
    L.scale_panel(row[2], 1.004)                                # within 1 %: same scale
    assert rules(run(p, fig), "scalebar.same_scale")
    L.scale_panel(row[2], 0.8)                                  # another magnification
    [i] = rules(run(p, fig), "scalebar.same_scale", "scalebar.other_scale")
    assert (i.rule, i.level) == ("scalebar.other_scale", "major")


# ------------------------------------------------------------ insets
def test_inset_following_its_parent_is_not_reported_twice(grid):
    p, fig, _ed, cells = grid
    column = [cells[cond, 1] for cond in CONDITIONS]
    column[2].lock_group = None
    panel_channel(column[2], 0).white = 4000
    insets = []
    for k, parent in enumerate(column):                         # a column of insets beside them
        ins = create_inset(fig, parent, p.images[parent.image_id], (8, 8, 16, 16), f"i{k}")
        ins.x_mm, ins.y_mm = 140, parent.y_mm
        ins.channels = [c.model_copy() for c in parent.channels]
        fig.panels.append(ins)
        insets.append(ins)
    assert len(rules(run(p, fig), "levels.row")) == 1           # the parents' column only


# ------------------------------------------------------------ the level table
def test_every_rule_has_a_level_and_the_docstring_agrees():
    assert set(C.LEVELS) == {"critical", "major", "minor"}
    table = {}
    for line in C.__doc__.splitlines():
        m = re.match(r"\|\s*([a-z_.]+)\s*\|\s*([a-z_]+)\s*\|\s*(critical|major|minor)\s*\|", line)
        if m:
            table[m.group(1)] = (m.group(2), m.group(3))
    assert table == C.RULES
    assert C.REFUSING_RULES <= {r for r, (_c, lv) in C.RULES.items() if lv == "critical"}
    expected = {"critical": {"source.missing", "source.modified", "source.not_in_project", "labels",
                             "off_page", "channels.lock", "levels.lock"},
                "minor": {"resolution.inset", "line_width", "scalebar.same_scale", "lock_groups",
                          "nonlinear", "light_extract", "source.pending_hash"}}
    assert C.RULES["resolution.panel"] == ("resolution", "major")
    for lv, names in expected.items():
        assert {r for r, (_c, x) in C.RULES.items() if x == lv} == names, lv


def test_critical_needing_consent():
    crit = C.Issue("critical", "labels", "m", (), "labels")
    refused = C.Issue("critical", "source", "m", (), "source.missing")
    major = C.Issue("major", "tz", "m", (), "tz")
    assert C.critical_needing_consent([crit, refused, major]) == [crit]


# ------------------------------------------------------------ resolution: panels vs insets
def scale_to_ppi(panel, ref, ppi):
    L.scale_panel(panel, C.effective_ppi(panel, ref) / ppi)
    assert C.effective_ppi(panel, ref) == pytest.approx(ppi)


def test_low_resolution_inset_is_minor_panel_major(workspace, mm_tiff):
    p = workspace.create_project("Insets")
    ref = register_image(p, mm_tiff, create_dataset(p, "A").id)
    ref.sha256 = "0" * 64
    fig = create_figure(p, "F")
    [parent] = FigureEditor(p, fig, lambda: None).add_images([ref])
    scale_to_ppi(parent, ref, 600)                              # the parent is fine
    ins = create_inset(fig, parent, ref, (8, 8, 16, 16), "i1")
    ins.x_mm = parent.x_mm + parent.width_mm + 5
    fig.panels.append(ins)
    scale_to_ppi(ins, ref, 250)                                 # enlarged ×1.2 for 300 dpi
    [i] = rules(run(p, fig), "resolution.inset", "resolution.panel")
    assert (i.rule, i.code, i.level) == ("resolution.inset", "resolution", "minor")
    assert i.message == (f"inset 2 ({ref.filename}): pixels shown enlarged ×1.2 (250 ppi < "
                         "300 dpi); fine for an inset, exported nearest-neighbour")
    assert C.insets_below_dpi(p, fig) == [ins]
    scale_to_ppi(parent, ref, 250)                              # same scale on the parent
    issues = rules(run(p, fig), "resolution.inset", "resolution.panel")
    assert sorted((i.rule, i.level) for i in issues) == [("resolution.inset", "minor"),
                                                         ("resolution.panel", "major")]
    scale_to_ppi(ins, ref, 300)                                 # at the dpi: nothing
    scale_to_ppi(parent, ref, 320)
    assert not rules(run(p, fig), "resolution.inset", "resolution.panel")
    assert C.insets_below_dpi(p, fig) == []
