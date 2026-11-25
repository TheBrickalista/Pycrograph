# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Layout tools (§2.8) and auto-layouts, pure and through FigureEditor
(one undo step, one log entry per panel); page setup; preferences."""

from __future__ import annotations

import json

import pytest

from pycrograph import layout as L
from pycrograph import viewport as V
from pycrograph.figures import FigureEditor, create_figure, panel_channel
from pycrograph.logchain import verify_log_chain
from pycrograph.models import PageSetup, Panel
from pycrograph.preferences import (
    Preferences, builtin_presets, load_preferences, preferences_path, save_preferences,
)
from pycrograph.storage import create_dataset, register_image
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff


def P(pid, x, y, w=20.0, h=10.0, **kw) -> Panel:
    return Panel(id=pid, image_id="i", x_mm=x, y_mm=y, width_mm=w, height_mm=h, **kw)


def geom(ps):
    return [(p.x_mm, p.y_mm, p.width_mm, p.height_mm) for p in ps]


# ------------------------------------------------------------------- pure
def test_align_edges_and_centres():
    ps = [P("a", 5, 0, 20, 10), P("b", 30, 7, 10, 20)]
    L.align(ps, "left")
    assert [p.x_mm for p in ps] == [5, 5]
    L.align(ps, "right")
    assert [p.x_mm + p.width_mm for p in ps] == [25, 25]
    L.align(ps, "top")
    assert [p.y_mm for p in ps] == [0, 0]
    L.align(ps, "bottom")
    assert [p.y_mm + p.height_mm for p in ps] == [20, 20]
    L.align(ps, "hcentre")
    assert {p.x_mm + p.width_mm / 2 for p in ps} == {15}
    L.align(ps, "vcentre")
    assert {p.y_mm + p.height_mm / 2 for p in ps} == {10}


def test_distribute():
    ps = [P("a", 0, 0, 10), P("b", 13, 0, 20), P("c", 60, 0, 10)]
    assert L.distribute(ps, "h")
    assert [p.x_mm for p in ps] == [0, 25, 60]            # span 70, widths 40: gaps 15
    assert not L.distribute(ps[:2], "h")
    ps = [P("a", 0, 0, h=5), P("b", 0, 1, h=5), P("c", 0, 30, h=5)]
    L.distribute(ps, "v")
    assert [p.y_mm for p in ps] == [0, 15, 30]


def test_grid_rows_columns_gap_first_fixed():
    ps = [P("a", 3, 4, 20, 10), P("b", 27, 6, 30, 12), P("c", 2.5, 20, 25, 8), P("d", 40, 19, 10, 10)]
    rows = L.rows_of(ps)
    assert [[p.id for p in r] for r in rows] == [["a", "b"], ["c", "d"]]
    L.grid(ps, gap_mm=2)
    assert geom(ps) == [(3, 4, 20, 10), (3 + 25 + 2, 4, 30, 12),
                        (3, 4 + 12 + 2, 25, 8), (30, 18, 10, 10)]


def test_nudge():
    ps = [P("a", 1, 1)]
    L.nudge(ps, 0.1, -1)
    assert (ps[0].x_mm, ps[0].y_mm) == pytest.approx((1.1, 0))


def test_best_snap_closest_wins():
    # left 10.3 -> 10 (0.3), right 30.3 -> 30.1 (0.2): right is closer.
    assert L.best_snap([10.3, 20.3, 30.3], [10.0, 30.1], tol=1.0) == pytest.approx((-0.2, 30.1, 2))
    assert L.best_snap([10.3, 20.3, 30.3], [50.0], tol=1.0) is None
    assert L.best_snap([10.3], [11.3], tol=1.0) == pytest.approx((1.0, 11.3, 0))   # tol inclusive


def test_best_snap_tie_break_is_deterministic():
    """Equal-width panels: three pairs at the same distance; float noise
    (as on CI) or target order must not change the pick: the moving
    panel's left edge, then the lower target."""
    x = 42.0 + 0.4116182572614164                      # value seen on CI-like scales
    values = [x, x + 20.0, x + 40.0]                    # left, centre, right of a 40 mm panel
    for targets in ([0.0, 91.5, 183.0, 42.0, 62.0, 82.0], [82.0, 62.0, 42.0], [62.0, 82.0, 42.0]):
        d, t, i = L.best_snap(values, targets, tol=1.5)
        assert (t, i) == (42.0, 0) and x + d == pytest.approx(42.0)
    # A tie between two targets for the same value: the lower target wins,
    # whatever their order (e.g. 1 mm either side).
    assert L.best_snap([50.0], [51.0, 49.0], tol=2)[1] == 49.0
    assert L.best_snap([50.0], [49.0, 51.0], tol=2)[1] == 49.0
    # Centre beats right on a tie; a strictly closer right still wins.
    assert L.best_snap([0.0, 10.0, 20.0], [10.5, 20.5], tol=1)[2] == 1
    assert L.best_snap([0.0, 10.0, 20.0], [10.5, 20.4], tol=1)[2] == 2


def test_parse_indices():
    assert L.parse_indices("1-3, 5", 6) == [0, 1, 2, 4]
    assert L.parse_indices("3-1 9", 4) == [2, 1, 0]
    with pytest.raises(ValueError):
        L.parse_indices("a", 3)


# --------------------------------------------------------- with real images
@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z, data_dir):
    coarse = write_metamorph_tiff(data_dir / "coarse.tif", [reference_like_plane(64, 64)],
                                  [metamorph_xml(width=64, height=64, px=0.65)])
    p = workspace.create_project("Layout")
    ds = create_dataset(p, "A")
    refs = [register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z, coarse)]
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    panels = ed.add_images(refs)
    return p, ed, refs, panels


def refs_of(p):
    return p.images


def test_layout_gesture_one_entry_per_panel(env):
    p, ed, _refs, panels = env
    ids = [q.id for q in panels]
    bottoms = {q.id: q.y_mm + q.height_mm for q in panels}
    target = max(bottoms.values())
    should_move = {pid for pid, b in bottoms.items() if b != target}
    assert should_move                                        # heights differ
    n, depth = len(p.operation_log), len(ed.history._undo)
    ed.layout(ids, lambda ps, _r: L.align(ps, "bottom"), "figure.layout.align_bottom", "Align")
    entries = p.operation_log[n:]
    assert [e.target_id for e in entries] == [pid for pid in ids if pid in should_move]
    assert {(e.operation, e.field) for e in entries} == {("figure.layout.align_bottom", "y_mm")}
    assert len(ed.history._undo) == depth + 1                 # one undo step
    ed.undo()
    assert {q.id: q.y_mm + q.height_mm for q in ed.figure.panels} == bottoms


def test_same_size(env):
    p, ed, (one, two, zs, coarse), (pa, pb, pz, pc) = env
    crop_b = pb.crop.model_copy()
    ed.layout([pa.id, pb.id], lambda ps, r: L.same_size(ps, r, ps[0], "height"),
              "figure.layout.same_height", "h")
    assert pb.height_mm == pytest.approx(pa.height_mm) and pb.crop == crop_b   # scaled, crop kept
    ed.layout([pa.id, pb.id], lambda ps, r: L.same_size(ps, r, ps[0], "both"),
              "figure.layout.same_both", "both")
    assert (pb.width_mm, pb.height_mm) == pytest.approx((pa.width_mm, pa.height_mm))
    s = two.metadata.shape
    ow, oh = V.oriented_wh(pb.crop.w, pb.crop.h, pb.rotation_deg)
    assert ow / oh == pytest.approx(pb.width_mm / pb.height_mm)                # never stretched
    assert V.zoom_percent(pb, s.X, s.Y) > 0


def test_align_magnification(env):
    p, ed, (one, two, zs, coarse), (pa, pb, pz, pc) = env
    # coarse: 0.65 um/px, same 64 px, same 40 mm panel -> twice the um per mm.
    crop_c = pc.crop.model_copy()
    assert L.um_per_mm(pc, coarse) == pytest.approx(2 * L.um_per_mm(pa, one))
    ed.layout([pa.id, pc.id], lambda ps, r: L.align_magnification(ps, r),
              "figure.layout.align_magnification", "mag")
    assert L.um_per_mm(pc, coarse) == pytest.approx(L.um_per_mm(pa, one))
    assert pc.width_mm == pytest.approx(2 * pa.width_mm) and pc.crop == crop_c  # size, not crop
    e = p.operation_log[-1]
    assert e.target_id == pc.id and e.field == "width_mm,height_mm"
    # Uncalibrated -> skipped; nm calibration converted.
    coarse.metadata.calibration.pixel_size_x = None
    assert L.align_magnification([pa, pc], refs_of(p)) == [pc.id]
    coarse.metadata.calibration.pixel_size_x = 650.0
    coarse.metadata.calibration.unit = "nm"
    assert L.um_per_mm(pc, coarse) == pytest.approx(L.um_per_mm(pa, one))


def test_split_channels(env):
    p, ed, (one, two, zs, coarse), (pa, pb, pz, pc) = env
    n = len(p.operation_log)
    y0 = L.below_all(ed.figure.panels, 0, 2)

    def fn(ps, r):
        ed.figure.panels.extend(L.split_channels(ps[0], r[ps[0].image_id], 0, y0, 2))
    ed.layout([pb.id], fn, "figure.layout.split_channels", "Split")
    new = ed.figure.panels[4:]
    assert len(new) == 3                                      # 2 channels + merge
    assert [[c.visible for c in q.channels] for q in new] == [[True, False], [False, True], [True, True]]
    assert len({q.lock_group for q in new}) == 1 and new[0].lock_group.startswith("split:")
    assert [q.x_mm for q in new] == pytest.approx([0, pb.width_mm + 2, 2 * (pb.width_mm + 2)])
    assert all(q.y_mm == y0 for q in new)
    entries = p.operation_log[n:]
    assert [e.operation for e in entries] == ["figure.layout.split_channels"] * 3
    assert {e.target_id for e in entries} == {q.id for q in new}
    assert L.split_channels(pa, one, 0, 0) and len(L.split_channels(pa, one, 0, 0)) == 1


def test_montage(env):
    _p, _ed, (one, two, zs, coarse), (pa, pb, pz, pc) = env
    row = L.montage(pb, two, "t", [0, 2, 9], 0, 100, 1)
    assert [q.t for q in row] == [0, 2]                        # out-of-range dropped
    assert row[1].x_mm == pytest.approx(pb.width_mm + 1)
    pz.z_projection = None
    col = L.montage(pz, zs, "z", L.parse_indices("1-5", 5), 0, 0, 1, "column")
    assert [q.z for q in col] == [0, 1, 2, 3, 4]
    assert col[1].y_mm == pytest.approx(pz.height_mm + 1) and col[1].x_mm == 0
    assert len({q.lock_group for q in row + col}) == 2


def test_condition_grid(env):
    _p, _ed, (one, two, zs, coarse), (pa, pb, pz, pc) = env
    rows = L.condition_grid([(pb, two), (pa, one)], 0, 50, 2)
    assert len(rows) == 2 and len(rows[0]) == 3              # 2 channels + merge
    assert rows[1][0] is not None and rows[1][1] is None and rows[1][2] is None
    cells = [q for r in rows for q in r if q is not None]
    assert len({q.lock_group for q in cells}) == 1 and cells[0].lock_group.startswith("grid:")
    assert {(q.width_mm, q.height_mm) for q in cells} == {(pb.width_mm, pb.height_mm)}
    assert rows[1][0].y_mm == pytest.approx(50 + pb.height_mm + 2)
    assert rows[0][2].x_mm == pytest.approx(2 * (pb.width_mm + 2))
    assert [c.visible for c in rows[0][2].channels] == [True, True]
    assert panel_channel(rows[1][0], 0).visible


def test_page_setup_logged_and_guarded(env, workspace):
    p, ed, _refs, panels = env
    n = len(p.operation_log)
    page = PageSetup.from_preset(builtin_presets()[0], n_pages=2, grid_mm=0.5)
    ed.set_page(page)
    [e] = p.operation_log[n:]
    assert (e.operation, e.target_type) == ("figure.page.set", "figure")
    assert "page.width_mm" in e.field and e.new_value["page.width_mm"] == 89.0
    panels[0].page = 1
    with pytest.raises(ValueError):
        ed.set_page(PageSetup(n_pages=1))
    reloaded = workspace.load_project(p.project.id)
    assert reloaded.figures[0].page.n_pages == 2
    assert verify_log_chain(reloaded).status == "ok"


# ------------------------------------------------------------ preferences
def test_preferences_round_trip(tmp_path):
    prefs = load_preferences(tmp_path)                        # missing -> defaults
    assert [p.name for p in prefs.presets] == [p.name for p in builtin_presets()]
    d = prefs.default()                                       # from the catalog
    assert (d.name, d.width_mm, d.max_height_mm, d.dpi, d.font_family) == \
        ("Nature double column", 183.0, 170.0, 300, "Arial")
    assert prefs.preset("Nature single column").width_mm == 89.0
    prefs.presets.append(prefs.presets[0].model_copy(update={"name": "Cell 1 col", "width_mm": 85}))
    prefs.default_preset = "Cell 1 col"
    prefs.layout_gap_mm = 3.5
    save_preferences(tmp_path, prefs)
    saved = json.loads(preferences_path(tmp_path).read_text())
    assert [p["name"] for p in saved["presets"]] == ["Cell 1 col"]   # built-ins: never saved
    again = load_preferences(tmp_path)
    assert again == prefs and again.new_page().width_mm == 85
    preferences_path(tmp_path).write_text("{not json")
    assert load_preferences(tmp_path) == Preferences()
    prefs.presets.append(prefs.presets[0])
    assert any("duplicate" in e for e in prefs.validate_presets())
