# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Layout tools through the real window (offscreen): toolbar actions, arrow
nudges, alignment guides while dragging, dashed outlines (canvas only),
page setup, editable presets, auto-layouts, multi-panel pan."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph import viewport as V  # noqa: E402
from pycrograph.logchain import verify_log_chain  # noqa: E402
from pycrograph.models import PageSetup  # noqa: E402
from pycrograph.preferences import preferences_path  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, workspace, mm_tiff, stk_tc, stk_z):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.resize(1500, 950)
    w.show()
    p = workspace.create_project("Layout")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    ids = w.import_images(ds.id, [str(mm_tiff), str(stk_tc), str(stk_z)])
    assert w.hashq.wait(30000)
    app.processEvents()
    panels = w.add_to_figure(ids)
    yield w, ids, panels
    w.close()


def _mouse(view, kind, pos: QPoint, button=Qt.LeftButton, buttons=Qt.LeftButton):
    vp = view.viewport()
    QApplication.sendEvent(vp, QMouseEvent(kind, QPointF(pos), QPointF(vp.mapToGlobal(pos)),
                                           button, buttons, Qt.NoModifier))


def key(view, k, mods=Qt.NoModifier):
    QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, k, mods))


def test_toolbar_align_and_grid(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab = w.figures_tab
    pb.y_mm = 13.0                                          # make them differ
    tab.refresh()
    tab.canvas.select_ids([pa.id, pb.id, pz.id])
    assert tab.layout_actions["align_top"].isEnabled()
    n = len(w.project.operation_log)
    tab.layout_actions["align_top"].trigger()
    entries = w.project.operation_log[n:]
    assert [(e.operation, e.target_id) for e in entries] == [("figure.layout.align_top", pb.id)]
    tab.gap_spin.setValue(5.0)
    tab.layout_actions["grid"].trigger()
    f = tab.current_figure()
    xs = [q.x_mm for q in f.panels]
    assert xs[1] == pytest.approx(xs[0] + f.panels[0].width_mm + 5)
    assert json.loads(preferences_path(w.ws.root).read_text())["layout_gap_mm"] == 5.0
    tab.a_undo.trigger()
    tab.a_undo.trigger()
    assert tab.current_figure().panel(pb.id).y_mm == 13.0


def test_arrow_nudges_one_gesture(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    view.select_ids([pa.id, pb.id])
    x0 = {q.id: q.x_mm for q in (pa, pb)}
    n = len(w.project.operation_log)
    for _ in range(3):
        key(view, Qt.Key_Right)
    key(view, Qt.Key_Down, Qt.ShiftModifier)
    assert len(w.project.operation_log) == n                # still one open gesture
    tab.flush()                                              # = 700 ms without a key
    entries = w.project.operation_log[n:]
    assert [e.target_id for e in entries] == [pa.id, pb.id]
    assert all(e.operation == "figure.layout.nudge" for e in entries)
    assert pa.x_mm == pytest.approx(x0[pa.id] + 0.3) and pa.y_mm == pytest.approx(1.0)
    assert view.item_for(pa.id).pos().x() == pytest.approx(pa.x_mm)


def _drag_pz_near_pb(app, w, pb, pz, pz_width: float, view_size=(800, 600)):
    """Put pz below pb with its left edge 1.2 mm right of pb's, then drag it
    0.9 mm left. The canvas gets a FIXED size first: the pixel rounding of
    mapFromScene (and so the real drag delta) depends on the view scale,
    which otherwise differs between platforms."""
    tab, view = w.figures_tab, w.figures_tab.canvas
    tab.set_page(PageSetup(snap=False))
    tab.current_editor().set_geometry({pz.id: (pb.x_mm + 1.2, 60, pz_width, pz_width)})
    tab.refresh()
    view.setFixedSize(*view_size)
    app.processEvents()
    view.fit_pages()
    view.select_ids([])              # (add_to_figure left all three selected = one block)
    item = view.item_for(pz.id)
    start = item.mapToScene(item.rect().center())
    a = view.mapFromScene(start)
    b = view.mapFromScene(start + QPointF(-0.9, 0.0))       # ~0.3 mm right of pb's left edge
    _mouse(view, QEvent.MouseButtonPress, a)
    _mouse(view, QEvent.MouseMove, a + (b - a) * 0.5, Qt.NoButton)
    _mouse(view, QEvent.MouseMove, b, Qt.NoButton)
    vline, _hline = view.figure_scene.guides
    guide = vline.line().x1() if vline.isVisible() else None
    _mouse(view, QEvent.MouseButtonRelease, b, Qt.LeftButton, Qt.NoButton)
    app.processEvents()
    assert not vline.isVisible()                            # guides only while dragging
    return guide, tab.current_figure().panel(pz.id).x_mm


def test_drag_guides_snap_to_other_panel(app, setup):
    w, ids, (pa, pb, pz) = setup
    # pz narrower than pb: only pz's left edge can meet one of pb's edges.
    assert pb.width_mm == 40
    guide, x = _drag_pz_near_pb(app, w, pb, pz, pz_width=30.0)
    assert guide == pytest.approx(pb.x_mm)
    assert x == pytest.approx(pb.x_mm)                       # the snap result itself


@pytest.mark.parametrize("view_size", [(566, 486), (800, 600), (1084, 808)])
def test_drag_guides_tie_breaks_on_left_edge(app, setup, view_size):
    """Same width as pb: pz's left, centre and right are all equally close
    to pb's left, centre and right (a three-way tie whose float distances
    differ only by rounding, at scales that made CI pick pb's right edge).
    The rule picks the left edge at every scale; the position is the same
    for all three."""
    w, ids, (pa, pb, pz) = setup
    guide, x = _drag_pz_near_pb(app, w, pb, pz, pz_width=pb.width_mm, view_size=view_size)
    assert guide == pytest.approx(pb.x_mm)
    assert x == pytest.approx(pb.x_mm)


def _render(scene, rect: QRectF) -> QImage:
    img = QImage(400, 400, QImage.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    scene.render(p, QRectF(0, 0, 400, 400), rect)
    p.end()
    return img


def test_dashed_outline_canvas_only(app, setup):
    w, ids, (pa, pb, pz) = setup
    view = w.figures_tab.canvas
    view.select_ids([pa.id])
    w.figures_tab.inspector.zoom_by(0.5)                    # pa's border now over page background
    view.select_ids([])
    scene = view.figure_scene
    r = QRectF(pa.x_mm - 1, pa.y_mm - 1, pa.width_mm + 2, pa.height_mm + 2)

    def border_has_ink(img: QImage) -> bool:
        k = 400 / r.width()
        y = int((1 + pa.height_mm / 2) * k)                 # left edge, mid height
        x = int(1 * k)
        return any(QColor(img.pixel(xx, y)).lightness() < 250 for xx in range(x - 2, x + 3))
    assert border_has_ink(_render(scene, r))
    scene.set_decorations(False)
    assert not border_has_ink(_render(scene, r))
    assert not any(it.pen().style() != Qt.NoPen for it in scene.page_items)
    scene.set_decorations(True)


def _item(view, panel_id):
    return next(it for it in view.figure_scene.panel_items() if it.panel_id == panel_id)


def _panel_render(scene, item) -> QImage:
    """The panel and 1 mm around it (its outline is drawn on its edge)."""
    return _render(scene, item.mapRectToScene(item.rect()).adjusted(-1, -1, 1, 1))


def _without_outline(scene, item) -> QImage:
    """The same render with this panel's outline forced off (the page border,
    also a decoration, may share the panel's edge)."""
    item.shows_outline = lambda: False
    try:
        return _panel_render(scene, item)
    finally:
        del item.shows_outline


def test_no_outline_by_default_hover_shows_it(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    scene = view.figure_scene
    assert not w.prefs.canvas_outlines_all and w.prefs.canvas_outlines_empty   # defaults
    assert not tab.outlines_btn.isChecked() and tab.outlines_btn.text() == "Outlines"
    view.select_ids([])
    item = _item(view, pb.id)
    assert not item.has_empty_area()                      # full image, panel covered
    assert not item.shows_outline()
    assert _panel_render(scene, item) == _without_outline(scene, item)   # nothing painted
    # Hover: the outline appears, and goes when the mouse leaves.
    centre = view.mapFromScene(item.mapToScene(item.rect().center()))
    _mouse(view, QEvent.MouseMove, centre, Qt.NoButton, Qt.NoButton)
    app.processEvents()
    assert item.hovered and item.shows_outline()
    assert _panel_render(scene, item) != _without_outline(scene, item)
    _mouse(view, QEvent.MouseMove, QPoint(2, 2), Qt.NoButton, Qt.NoButton)   # canvas background
    app.processEvents()
    assert not item.hovered and not item.shows_outline()
    # "All panel outlines" (the button itself): every unselected panel.
    tab.outlines_btn.click()
    assert tab.a_outlines_all.isChecked() and scene.outline_all
    assert item.shows_outline() and _panel_render(scene, item) != _without_outline(scene, item)
    view.select_ids([pb.id])
    assert not item.shows_outline()                       # selection frame instead
    tab.outlines_btn.click()
    assert not scene.outline_all


def test_empty_area_outline_and_its_option(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    scene = view.figure_scene
    view.select_ids([pa.id])
    tab.inspector.zoom_by(0.5)                            # crop beyond the image
    view.select_ids([])
    item = _item(view, pa.id)
    assert item.has_empty_area() and item.shows_outline()
    assert _panel_render(scene, item) != _without_outline(scene, item)
    tab.a_outlines_empty.setChecked(False)                # menu: "Panels with empty area"
    assert not scene.outline_empty and not item.shows_outline()
    assert _panel_render(scene, item) == _without_outline(scene, item)
    assert not _item(view, pb.id).has_empty_area()


def test_outline_options_persist_and_are_not_project_changes(app, setup, workspace):
    from pycrograph.preferences import load_preferences
    from pycrograph.ui.main_window import MainWindow
    w, ids, (pa, pb, pz) = setup
    tab = w.figures_tab
    n_log = len(w.project.operation_log)
    editor = tab.current_editor()
    n_undo = len(editor.history._undo)
    tab.a_outlines_all.setChecked(True)
    tab.a_outlines_empty.setChecked(False)
    assert len(w.project.operation_log) == n_log          # not logged
    assert len(editor.history._undo) == n_undo                     # not undoable
    saved = load_preferences(workspace.root)
    assert saved.canvas_outlines_all and not saved.canvas_outlines_empty
    w2 = MainWindow(workspace, auto_open=False)           # next session
    try:
        t2 = w2.figures_tab
        assert t2.a_outlines_all.isChecked() and t2.outlines_btn.isChecked()
        assert not t2.a_outlines_empty.isChecked()
        sc = t2.canvas.figure_scene
        assert sc.outline_all and not sc.outline_empty
    finally:
        w2.close()


def test_export_has_no_outlines_whatever_the_toggle(app, setup):
    from pycrograph.ui.scene_builder import export_scene
    w, ids, (pa, pb, pz) = setup
    tab = w.figures_tab
    tab.a_outlines_all.setChecked(True)
    view = tab.canvas
    view.select_ids([pa.id])
    tab.inspector.zoom_by(0.5)                            # empty area too
    scene = export_scene(w.project, tab.current_figure(), w.renderer)
    items = scene.panel_items()
    assert items and not any(it.shows_outline() for it in items)
    it = next(i for i in items if i.panel_id == pa.id)
    assert it.has_empty_area()
    before = _panel_render(scene, it)
    scene.outline_all = True                              # even forced: decorations are off
    assert _panel_render(scene, it) == before


def test_page_setup_and_presets(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, prefs_tab = w.figures_tab, w.preferences_tab
    # Built-in rows are read-only: duplicate one, edit the copy; saved.
    row = [p.name for p in w.prefs.presets].index("Nature single column")
    prefs_tab.table.item(row, 1).setText("90")
    assert w.prefs.preset("Nature single column").width_mm == 89.0
    prefs_tab.table.selectRow(row)
    prefs_tab.duplicate_preset()
    mine = "Nature single column (2)"
    row = [p.name for p in w.prefs.presets].index(mine)
    prefs_tab.table.item(row, 1).setText("90")
    assert w.prefs.preset(mine).width_mm == 90.0
    saved = json.loads(preferences_path(w.ws.root).read_text())
    assert [(p["name"], p["width_mm"]) for p in saved["presets"]] == [(mine, 90.0)]
    prefs_tab.table.item(row, 1).setText("-3")               # invalid: refused
    assert w.prefs.preset(mine).width_mm == 90.0
    assert prefs_tab.message.text()
    # Default preset (Custom › the copy) drives new figures.
    prefs_tab.default_picker.choose("Custom", mine)
    assert w.prefs.default_preset == mine
    fig2 = tab.new_figure("Second")
    assert fig2.page.size_mm == (90.0, 170.0)
    # Page setup on the current figure: one figure-level entry.
    n = len(w.project.operation_log)
    assert tab.set_page(PageSetup.from_preset(w.prefs.preset("Nature double column"), n_pages=2))
    [e] = w.project.operation_log[n:]
    assert e.operation == "figure.page.set" and e.target_id == fig2.id
    assert len(tab.canvas.figure_scene.page_items) == 2


def test_auto_layouts(app, setup, monkeypatch):
    w, ids, (pa, pb, pz) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    view.select_ids([pb.id])
    n = len(w.project.operation_log)
    tab.layout_actions["split_channels"].trigger()
    new = w.project.operation_log[n:]
    assert [e.operation for e in new] == ["figure.layout.split_channels"] * 3
    assert set(view.selected_ids()) == {e.target_id for e in new}     # new panels selected
    tab.a_undo.trigger()
    assert len(tab.current_figure().panels) == 3

    view.select_ids([pb.id])
    tab.montage("t", [0, 1, 2])
    f = tab.current_figure()
    assert [q.t for q in f.panels[3:]] == [0, 1, 2]

    view.select_ids([pa.id, pb.id])
    n = len(w.project.operation_log)
    tab.condition_grid()
    added = w.project.operation_log[n:]
    assert len(added) == 3 + 1                                # 2ch + merge, 1ch
    groups = {f.panel(e.target_id).lock_group for e in added}
    assert len(groups) == 1

    # Deselecting leaves no stale inspector widgets (they were shown until
    # deleteLater ran in the event loop).
    from pycrograph.ui.channel_controls import ChannelControls
    view.select_ids([])
    assert tab.inspector.widget().findChildren(ChannelControls) == []

    reloaded = w.ws.load_project(w.project.project.id)
    assert reloaded == w.project and verify_log_chain(reloaded).status == "ok"


def test_multi_pan_uses_each_orientation(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, insp = w.figures_tab, w.figures_tab.inspector
    tab.canvas.select_ids([pb.id])
    insp.zoom_by(2.0)
    insp.rotate(90)
    tab.canvas.select_ids([pa.id])
    insp.zoom_by(2.0)
    tab.canvas.select_ids([pa.id, pb.id])
    a0, b0 = pa.crop.model_copy(), pb.crop.model_copy()
    insp.pan_by_oriented(4, 0)                               # drag right on screen
    assert (pa.crop.x - a0.x, pa.crop.y - a0.y) == pytest.approx((4, 0))
    # pb is rotated 90° cw: screen-right is raw-up.
    assert (pb.crop.x - b0.x, pb.crop.y - b0.y) == pytest.approx((0, -4))
    assert V.orient_vector(pb.crop.x - b0.x, pb.crop.y - b0.y, 90, False, False) == \
        pytest.approx((4, 0))
