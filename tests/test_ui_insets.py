# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Insets & shapes through the real window (offscreen), with real mouse
events: draw an inset, drag its rectangle on the parent, follow flags,
draw / select / drag shapes, copy-paste; [time] unit per group."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph import insets as I  # noqa: E402
from pycrograph import viewport as V  # noqa: E402
from pycrograph.logchain import verify_log_chain  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402
from pycrograph.ui.panel_overlays import (  # noqa: E402
    KIND_INSET_BORDER, KIND_INSET_NUMBER, KIND_INSET_RECT, KIND_KEY, KIND_LABEL, KIND_SHAPE, PT_MM,
    ShapeItem,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, workspace, mm_tiff, stk_tc):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.resize(1500, 950)
    w.show()
    p = workspace.create_project("Insets")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    ids = w.import_images(ds.id, [str(mm_tiff), str(stk_tc)])
    assert w.hashq.wait(30000)
    app.processEvents()
    panels = w.add_to_figure(ids)
    view = w.figures_tab.canvas
    view.setFixedSize(900, 800)
    app.processEvents()
    view.fit_pages()
    view.select_ids([])
    yield w, ids, panels
    w.close()


def _mouse(view, kind, scene_pt: QPointF, button=Qt.LeftButton, buttons=Qt.LeftButton, mods=Qt.NoModifier):
    vp = view.viewport()
    pos = view.mapFromScene(scene_pt)
    QApplication.sendEvent(vp, QMouseEvent(kind, QPointF(pos), QPointF(vp.mapToGlobal(pos)),
                                           button, buttons, mods))


def drag(view, a: QPointF, b: QPointF):
    _mouse(view, QEvent.MouseButtonPress, a)
    for k in range(1, 5):
        _mouse(view, QEvent.MouseMove, a + (b - a) * (k / 4), Qt.NoButton)
    _mouse(view, QEvent.MouseButtonRelease, b, Qt.LeftButton, Qt.NoButton)
    QApplication.processEvents()


def kids(item, kind):
    out = []
    for c in item.childItems():
        if c.data(KIND_KEY) == kind:
            out.append(c)
        out += kids(c, kind)
    return out


def test_create_inset_by_drawing_and_edit_its_rect(app, setup):
    w, ids, (pa, pb) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    view.select_ids([pa.id])
    assert tab.start_create_inset()
    item = view.item_for(pa.id)
    n, depth = len(w.project.operation_log), len(tab.current_editor().history._undo)
    drag(view, item.mapToScene(QPointF(10, 10)), item.mapToScene(QPointF(20, 17.5)))
    f = tab.current_figure()
    [inset] = I.insets_of(f, pa.id)
    assert view.selected_ids() == [inset.id]
    [e] = w.project.operation_log[n:]
    assert (e.operation, e.target_id) == ("figure.inset.create", inset.id)
    assert len(tab.current_editor().history._undo) == depth + 1
    s = w.project.images[pa.image_id].metadata.shape
    c = inset.crop                                           # 64 px / 40 mm = 1.6 px/mm
    assert (c.x, c.y, c.w, c.h) == pytest.approx((16, 16, 16, 12), abs=0.5)
    assert V.px_per_mm(inset, s.X, s.Y) == pytest.approx(V.px_per_mm(pa, s.X, s.Y) / 2)
    assert I.in_row(f, pa)                                   # pa, pb side by side
    assert (inset.x_mm, inset.y_mm) == (pa.x_mm, pa.y_mm + pa.height_mm + 2)   # below it

    # Parent shows the numbered rectangle, inset its matching border + number.
    [rect] = kids(view.item_for(pa.id), KIND_INSET_RECT)
    assert rect.pen().color().name().upper() == inset.inset_style.color
    assert [t.toPlainText() for t in kids(rect, KIND_INSET_NUMBER)] == ["1"]
    assert kids(view.item_for(inset.id), KIND_INSET_BORDER)
    assert [t.toPlainText() for t in kids(view.item_for(inset.id), KIND_INSET_NUMBER)] == ["1"]

    # Select the parent, drag the rectangle: the inset's crop moves; one entry, on the inset.
    view.select_ids([pa.id])
    [rect] = kids(view.item_for(pa.id), KIND_INSET_RECT)
    r = rect.rect()
    n = len(w.project.operation_log)
    a = rect.mapToScene(QPointF(r.left(), r.center().y()))       # on the stroke
    drag(view, a, a + QPointF(5, 2.5))
    app.processEvents()
    [e] = w.project.operation_log[n:]
    assert (e.operation, e.target_id) == ("figure.inset.rect", inset.id)
    assert (inset.crop.x, inset.crop.y) == pytest.approx((24, 20), abs=0.6)   # +8, +4 px
    [rect] = kids(view.item_for(pa.id), KIND_INSET_RECT)
    assert rect.rect().left() == pytest.approx(r.left() + 5, abs=0.4)

    # Pan the inset (Preview): the rectangle follows.
    view.select_ids([inset.id])
    tab.inspector.pan_by_oriented(-8, 0)
    [rect] = kids(view.item_for(pa.id), KIND_INSET_RECT)
    assert rect.rect().left() == pytest.approx(r.left(), abs=0.4)
    tab.a_undo.trigger()
    tab.a_undo.trigger()
    tab.a_undo.trigger()
    assert I.insets_of(tab.current_figure(), pa.id) == []
    assert not kids(view.item_for(pa.id), KIND_INSET_RECT)


def test_follow_flags_and_style(app, setup):
    w, ids, (pa, pb) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    iid = tab.create_inset(pb.id, (0, 0, 16, 12))
    f = tab.current_figure()
    inset = f.panel(iid)
    view.select_ids([pb.id])
    tab.inspector.t_slider.setValue(2)
    assert inset.t == 2
    assert "inset(s) followed" in w.statusBar().currentMessage()
    view.select_ids([iid])
    insp = tab.insets_inspector
    assert insp.style_box.isEnabled() and insp.flags["follow_parent_tz"].isChecked()
    insp.flags["follow_parent_tz"].click()                    # off
    assert inset.inset_style.follow_parent_tz is False
    view.select_ids([pb.id])
    tab.inspector.t_slider.setValue(0)
    assert inset.t == 2                                        # independent now
    view.select_ids([iid])
    tab.set_inset_style(color="#FF00FF", width_pt=2.0, border_on_inset=False)
    [rect] = kids(view.item_for(pb.id), KIND_INSET_RECT)
    assert rect.pen().color().name().upper() == "#FF00FF"
    assert rect.pen().widthF() == pytest.approx(2.0 * PT_MM)
    assert not kids(view.item_for(iid), KIND_INSET_BORDER)
    tab.set_inset_style(show_on_parent=False)
    assert not kids(view.item_for(pb.id), KIND_INSET_RECT)
    # Copy-paste parent + inset: the link is kept inside the pasted set.
    view.select_ids([pb.id, iid])
    tab.copy_panels()
    new_ids = tab.paste_panels()
    f = tab.current_figure()
    assert f.panel(new_ids[1]).inset_of == new_ids[0]


def test_shapes_draw_select_move_paste(app, setup):
    w, ids, (pa, pb) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    view.select_ids([pa.id])
    tab.insets_inspector.kind_combo.setCurrentText("arrow")
    assert tab.start_draw_shape("arrow")
    item = view.item_for(pa.id)
    drag(view, item.mapToScene(QPointF(5, 5)), item.mapToScene(QPointF(25, 15)))
    [sh] = pa.shapes
    assert sh.kind == "arrow"
    assert sh.points[0] == pytest.approx((8, 8), abs=0.5) and sh.points[1] == pytest.approx((40, 24), abs=0.5)
    [si] = kids(view.item_for(pa.id), KIND_SHAPE)
    assert isinstance(si, ShapeItem) and si.pen().widthF() == pytest.approx(1.0 * PT_MM)
    # Zoom the panel: the arrow follows the image, the stroke stays 1 pt on the page.
    tab.inspector.zoom_by(2.0)
    [si] = kids(view.item_for(pa.id), KIND_SHAPE)
    assert si.pen().widthF() == pytest.approx(1.0 * PT_MM)
    s = w.project.images[pa.image_id].metadata.shape
    start = V.raw_to_panel(pa, s.X, s.Y, *sh.points[0])
    assert si.path().elementAt(0).x == pytest.approx(start[0])
    tab.inspector.fit_viewport()
    # Esc cancels a draw.
    tab.start_draw_shape("line")
    QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not view.drawing
    # Click selects it (panel selected), drag moves it: one entry.
    [si] = kids(view.item_for(pa.id), KIND_SHAPE)
    mid = si.mapToScene(si.path().pointAtPercent(0.3))
    _mouse(view, QEvent.MouseButtonPress, mid)
    _mouse(view, QEvent.MouseButtonRelease, mid, Qt.LeftButton, Qt.NoButton)
    assert tab.selected_shapes == {pa.id: {sh.id}}
    n = len(w.project.operation_log)
    x0 = pa.shapes[0].points[0][0]
    drag(view, mid, mid + QPointF(2.5, 0))
    app.processEvents()
    [e] = w.project.operation_log[n:]
    assert e.operation == "figure.shape.move"
    assert pa.shapes[0].points[0][0] == pytest.approx(x0 + 4, abs=0.3)   # 2.5 mm = 4 px
    # Style, copy / paste into the other panel, delete.
    tab.update_selected_shapes(color="#FF0000", width_pt=2.0, head_size_pt=8.0)
    assert (pa.shapes[0].color, pa.shapes[0].head_size_pt) == ("#FF0000", 8.0)
    tab.copy_shapes()
    view.select_ids([pb.id])
    tab.paste_shapes()
    assert pb.shapes[0].points == pa.shapes[0].points and pb.shapes[0].id != pa.shapes[0].id
    view.select_ids([pa.id])
    tab.select_shapes(pa.id, [pa.shapes[0].id])
    tab.delete_selected_shapes()
    assert pa.shapes == [] and not kids(view.item_for(pa.id), KIND_SHAPE)
    reloaded = w.ws.load_project(w.project.project.id)
    assert reloaded == w.project and verify_log_chain(reloaded).status == "ok"


def test_time_unit_shared_by_montage(app, setup):
    w, ids, (pa, pb) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    view.select_ids([pb.id])
    tab.add_label("[time]", "top-left")
    tab.montage("t", [0, 1, 2])
    f = tab.current_figure()
    row = f.panels[2:]
    texts = [[t.toPlainText() for t in kids(view.item_for(q.id), KIND_LABEL)] for q in row]
    assert texts == [["0 min"], ["0.5 min"], ["1 min"]]
    single = [t.toPlainText() for t in kids(view.item_for(pb.id), KIND_LABEL)]
    assert single == ["0 s"]
    # Lock confirmation names the reference panel; mismatch warned.
    view.select_ids([pa.id, pb.id])
    asked = []
    tab.lock_selection(confirm=lambda text: asked.append(text) or False)
    assert asked and "1 panel(s) will take the levels/LUTs of panel 1" in asked[0]
    assert "channel 1 differs" in asked[0]
    assert pa.lock_group is None                              # declined
