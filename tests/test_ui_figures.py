# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Figure editor step 1 through the real main window (offscreen): tabs,
"Add to figure" from the library, select / multi-select / move / resize /
delete with real mouse events, undo/redo, one log entry per gesture per
panel, save/reload identical."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph.logchain import verify_log_chain  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, workspace):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.resize(1400, 900)
    w.show()
    app.processEvents()
    yield w
    w.close()


def _mouse(view, kind, pos: QPoint, button=Qt.LeftButton, buttons=Qt.LeftButton,
           modifiers=Qt.NoModifier):
    vp = view.viewport()
    ev = QMouseEvent(kind, QPointF(pos), QPointF(vp.mapToGlobal(pos)), button, buttons, modifiers)
    QApplication.sendEvent(vp, ev)


def drag(view, start_scene: QPointF, end_scene: QPointF, modifiers=Qt.NoModifier):
    a = view.mapFromScene(start_scene)
    b = view.mapFromScene(end_scene)
    _mouse(view, QEvent.MouseButtonPress, a, modifiers=modifiers)
    for i in range(1, 6):
        _mouse(view, QEvent.MouseMove, a + (b - a) * (i / 5), Qt.NoButton, Qt.LeftButton, modifiers)
    _mouse(view, QEvent.MouseButtonRelease, b, Qt.LeftButton, Qt.NoButton, modifiers)
    QApplication.processEvents()


def click(view, scene_pt: QPointF, modifiers=Qt.NoModifier):
    p = view.mapFromScene(scene_pt)
    _mouse(view, QEvent.MouseButtonPress, p, modifiers=modifiers)
    _mouse(view, QEvent.MouseButtonRelease, p, Qt.LeftButton, Qt.NoButton, modifiers)
    QApplication.processEvents()


def centre(item) -> QPointF:
    return item.mapToScene(item.rect().center())


def test_tabs(window):
    names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert names == ["Home", "Projects", "Library", "Figures", "Preferences", "About"]
    assert window.tabs.currentWidget() is window.home                 # opens on Home
    window.show_about()
    assert window.tabs.currentWidget() is window.about_tab
    assert "GPL-3.0-only" in window.about_tab.text.text()


def test_add_move_resize_delete_undo(app, window, workspace, mm_tiff, stk_tc):
    p = workspace.create_project("Figs")
    window.open_project_by_id(p.project.id)
    ds = create_dataset(window.project, "Ctrl")
    ids = window.import_images(ds.id, [str(mm_tiff), str(stk_tc)])
    assert window.hashq.wait(30000)
    app.processEvents()
    proj = window.project

    # Library settings are the defaults copied into the panel.
    window.library.select_image(ids[1])
    app.processEvents()
    ctrl = window._controls[1]
    ctrl.lut_combo.setCurrentIndex(ctrl.lut_combo.findText("magenta"))
    ctrl._on_lut_activated(ctrl.lut_combo.currentIndex())

    window._on_context_action("add_to_figure", "image", ids[0])
    window.add_selected_to_figure()
    tab = window.figures_tab
    assert window.tabs.currentWidget() is tab
    fig = tab.current_figure()
    assert fig is not None and fig.name == "Figure 1" and len(fig.panels) == 2
    pa, pb = fig.panels
    assert pb.channels[1].lut == "magenta"
    assert [c.model_dump() for c in pb.channels] == \
        [c.model_dump() for c in proj.images[ids[1]].display.channels]
    assert fig.page.size_mm == (183.0, 170.0)               # journal width
    view = tab.canvas
    ia, ib = view.item_for(pa.id), view.item_for(pb.id)
    assert ia._pixmap is not None and not ia._pixmap.isNull()
    assert ib._pixmap.width() == 32 and ib._pixmap.height() == 24

    # Click selects one; Ctrl-click adds the second.
    view.fit_pages()
    click(view, centre(ia))
    assert view.selected_ids() == [pa.id] or set(view.selected_ids()) == {pa.id}
    click(view, centre(ib), Qt.ControlModifier)
    assert set(view.selected_ids()) == {pa.id, pb.id}

    # Drag moves both: ONE undo step, one log entry per panel.
    n = len(proj.operation_log)
    old = {q.id: (q.x_mm, q.y_mm) for q in fig.panels}
    drag(view, centre(ia), centre(ia) + QPointF(10, 20))
    moved = proj.operation_log[n:]
    assert [e.operation for e in moved] == ["figure.panel.move"] * 2
    assert {e.target_id for e in moved} == {pa.id, pb.id}
    for q in fig.panels:
        dx, dy = q.x_mm - old[q.id][0], q.y_mm - old[q.id][1]
        assert dx == pytest.approx(10, abs=1.01) and dy == pytest.approx(20, abs=1.01)
        assert q.x_mm == round(q.x_mm)                     # 1 mm grid snap
    assert tab.a_undo.isEnabled()

    # Resize panel A by its handle (wider only): one entry; the crop is
    # extended at constant scale, the image is not stretched.
    click(view, centre(ia))
    assert view.selected_ids() == [pa.id]
    h0, scale0 = pa.height_mm, pa.crop.w / pa.width_mm
    corner = ia.mapToScene(ia.rect().bottomRight()) - QPointF(0.3, 0.3)
    n = len(proj.operation_log)
    drag(view, corner, corner + QPointF(15, 0))
    app.processEvents()
    assert [e.operation for e in proj.operation_log[n:]] == ["figure.panel.resize"]
    assert pa.width_mm == pytest.approx(40 + 15, abs=1.01)
    assert pa.height_mm == pytest.approx(h0)
    assert pa.crop.w / pa.width_mm == pytest.approx(scale0)
    assert pa.crop.x < 0                                    # runs off the image: background
    r = view.item_for(pa.id).image_rect
    assert r.width() == pytest.approx(64 / scale0) and r.height() == pytest.approx(h0)

    # Undo resize, then undo move: back to the first layout. (Undo restores a
    # snapshot: panels are new objects, look them up by id.)
    tab.a_undo.trigger()
    app.processEvents()
    assert tab.current_figure().panel(pa.id).width_mm == pytest.approx(40)
    tab.a_undo.trigger()
    assert {q.id: (q.x_mm, q.y_mm) for q in tab.current_figure().panels} == old
    assert proj.operation_log[-1].operation == "figure.undo"
    tab.a_redo.trigger()
    assert tab.current_figure().panels[0].x_mm != old[pa.id][0] or \
        tab.current_figure().panels[0].y_mm != old[pa.id][1]

    # Select all + Delete key action: one entry per panel; undo restores.
    tab.select_all()
    assert len(view.selected_ids()) == 2
    n = len(proj.operation_log)
    tab.a_delete.trigger()
    assert tab.current_figure().panels == []
    assert [e.operation for e in proj.operation_log[n:]] == ["figure.panel.delete"] * 2
    tab.a_undo.trigger()
    assert len(tab.current_figure().panels) == 2
    assert view.item_for(pa.id) is not None

    # Rubber band on empty page area selects both.
    view.select_ids([])
    f = tab.current_figure()
    right = max(q.x_mm + q.width_mm for q in f.panels) + 3
    bottom = max(q.y_mm + q.height_mm for q in f.panels) + 3
    drag(view, QPointF(right, bottom), QPointF(-1, -1))
    assert set(view.selected_ids()) == {pa.id, pb.id}

    # Projects lists the figure; saved project equals the window's.
    window.tabs.setCurrentWidget(window.projects)
    assert window.projects.figures.count() == 1
    reloaded = workspace.load_project(p.project.id)
    assert reloaded == proj
    assert verify_log_chain(reloaded).status == "ok"


def test_missing_source_panel_placeholder(app, window, workspace, mm_tiff):
    p = workspace.create_project("Missing")
    window.open_project_by_id(p.project.id)
    ds = create_dataset(window.project, "A")
    [iid] = window.import_images(ds.id, [str(mm_tiff)])
    window.hashq.wait(30000)
    app.processEvents()
    window._close_source()
    mm_tiff.unlink()
    [panel] = window.add_to_figure([iid])
    item = window.figures_tab.canvas.item_for(panel.id)
    assert item._pixmap is None and "not found" in item._message
