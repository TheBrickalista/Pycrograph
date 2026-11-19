# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Replicating insets through the real window (offscreen)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph import insets as I  # noqa: E402
from pycrograph import viewport as V  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402
from pycrograph.ui.panel_overlays import KIND_INSET_RECT, KIND_KEY  # noqa: E402
from pycrograph.ui.replicate_dialog import REGISTRATION_WARNING, ReplicateDialog  # noqa: E402
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, workspace, stk_tc, data_dir):
    from pycrograph.ui.main_window import MainWindow
    files = [write_metamorph_tiff(data_dir / f"c{i}.tif", [reference_like_plane(64, 64, seed=i)],
                                  [metamorph_xml(width=64, height=64, px=px, stage=st)])
             for i, (px, st) in enumerate([(0.325, (1.0, 2.0)), (0.325, (9.0, 2.0)), (0.65, None)])]
    w = MainWindow(workspace, auto_open=False)
    w.resize(1500, 950)
    w.show()
    p = workspace.create_project("RepUI")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    ids = w.import_images(ds.id, [str(stk_tc)] + [str(f) for f in files])
    assert w.hashq.wait(30000)
    app.processEvents()
    tab = w.figures_tab
    view = tab.canvas
    view.setFixedSize(900, 800)
    yield w, tab, view, ids
    w.close()


def _mouse(view, kind, pt, button=Qt.LeftButton, buttons=Qt.LeftButton):
    vp = view.viewport()
    pos = view.mapFromScene(pt)
    QApplication.sendEvent(vp, QMouseEvent(kind, QPointF(pos), QPointF(vp.mapToGlobal(pos)),
                                           button, buttons, Qt.NoModifier))


def drag(view, a, b):
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


def test_replicate_split_row_and_linked_drag(app, setup):
    w, tab, view, ids = setup
    [src_panel] = w.add_to_figure([ids[0]])
    view.select_ids([src_panel.id])
    tab.split_channels()
    f = tab.current_figure()
    c0, c1, merge = f.panels[1:4]
    iid = tab.create_inset(c0.id, (4, 4, 12, 9))
    insp = tab.insets_inspector
    view.select_ids([iid])
    assert insp.replicate_btn.isEnabled()                       # parent's split group
    asked = []
    tab.replicate_prompt = lambda src, sets: asked.append(sets) or (
        "region", True, [p.id for p, _r, _n in sets["All panels of this split / lock group"]])
    copies = tab.replicate_inset_dialog()
    assert len(copies) == 2 and len(asked[0]["All panels of this split / lock group"]) == 2
    assert w.project.operation_log[-1].operation == "figure.inset.replicate"
    view.select_ids([iid])
    assert "3 insets, same region" in insp.group_label.text() and insp.unlink_btn.isEnabled()
    # Drag the source rectangle on its parent: every copy follows, one step.
    view.fit_pages()
    view.select_ids([c0.id])
    [rect] = kids(view.item_for(c0.id), KIND_INSET_RECT)
    r = rect.rect()
    n_log, n_undo = len(w.project.operation_log), len(tab.current_editor().history._undo)
    a = rect.mapToScene(QPointF(r.left(), r.center().y()))
    drag(view, a, a + QPointF(2.5, 0))
    app.processEvents()
    f = tab.current_figure()
    xs = [V.effective_crop(f.panel(i), 32, 24).x for i in [iid] + copies]
    assert xs[0] > 4 and xs == pytest.approx([xs[0]] * 3)
    targets = [e.target_id for e in w.project.operation_log[n_log:]]
    assert sorted(targets) == sorted([iid] + copies)
    assert len(tab.current_editor().history._undo) == n_undo + 1
    # Every parent's rectangle was redrawn at the new place.
    for cid in copies:
        parent = f.panel(f.panel(cid).inset_of)
        [rr] = kids(view.item_for(parent.id), KIND_INSET_RECT)
        assert rr.rect().left() == pytest.approx(r.left() + 2.5, abs=0.3)
    tab.a_undo.trigger()
    f = tab.current_figure()
    assert all(V.effective_crop(f.panel(i), 32, 24).x == pytest.approx(4) for i in [iid] + copies)
    # Unlink one; detach another: the last one alone loses the group.
    view.select_ids([copies[0]])
    tab.unlink_from_group()
    f = tab.current_figure()
    assert f.panel(copies[0]).inset_style.region_group is None
    assert f.panel(iid).inset_style.region_group is not None
    view.select_ids([copies[1]])
    assert tab.detach_insets() == 1
    f = tab.current_figure()
    assert f.panel(iid).inset_style.region_group is None


def test_create_inset_on_several_panels(app, setup):
    w, tab, view, ids = setup
    a, b = w.add_to_figure([ids[1], ids[2]])
    view.fit_pages()
    view.select_ids([a.id, b.id])
    tab.replicate_prompt = lambda src, sets: ("region", True,
                                              [p.id for p, _r, _n in sets["Selected panels"]])
    assert tab.start_create_inset()
    item = view.item_for(b.id)                                    # draw on the SECOND panel
    drag(view, item.mapToScene(QPointF(5, 5)), item.mapToScene(QPointF(15, 12)))
    f = tab.current_figure()
    insets = [p for p in f.panels if p.inset_of]
    assert {p.inset_of for p in insets} == {a.id, b.id}
    src = next(p for p in insets if p.inset_of == b.id)
    cp = next(p for p in insets if p.inset_of == a.id)
    assert V.effective_crop(cp, 64, 64) == V.effective_crop(src, 64, 64)
    assert cp.inset_style.region_group == src.inset_style.region_group


def test_dialog_texts(app, setup):
    w, tab, view, ids = setup
    refs = [w.project.images[i] for i in ids[1:]]
    a, b, c = w.add_to_figure(ids[1:])
    names = ["same geometry, other field", "other pixel size"]
    dlg = ReplicateDialog(refs[0], {"Selected panels": [(b, refs[1], names[0]), (c, refs[2], names[1])],
                                    "All panels of this split / lock group": [(b, refs[1], names[0])]})
    rows = dlg.rows_text()
    assert rows[0] == (names[0], "same size and pixel size", "stage position differs", "same region")
    assert rows[1] == (names[1], "different geometry", "stage position unknown",
                       "same size (fallback: different geometry)")
    assert dlg.warning.text() == REGISTRATION_WARNING and dlg.link()
    dlg.same_size.setChecked(True)
    assert dlg.warning.text() == "" and dlg.rows_text()[0][3] == "same size"
    dlg.set_buttons["All panels of this split / lock group"].setChecked(True)
    assert dlg.target_ids() == [b.id]
    assert I.compatibility(refs[0], refs[2]) == "different"


def test_canvas_resize_of_linked_inset(app, setup):
    """Resizing one linked inset with the canvas handle resizes the group."""
    w, tab, view, ids = setup
    a, b = w.add_to_figure([ids[1], ids[2]])
    iid = tab.create_inset(a.id, (8, 8, 16, 12))
    [cid] = tab.replicate_inset(iid, [b.id], "region", True)
    view.fit_pages()
    view.select_ids([iid])
    f = tab.current_figure()
    item = view.item_for(iid)
    w0 = f.panel(iid).width_mm
    n_undo = len(tab.current_editor().history._undo)
    corner = item.mapToScene(item.rect().bottomRight()) - QPointF(0.3, 0.3)
    drag(view, corner, corner + QPointF(6, 0))
    app.processEvents()
    f = tab.current_figure()
    src, cp = f.panel(iid), f.panel(cid)
    assert src.width_mm == pytest.approx(w0 + 6, abs=1.01)
    assert (cp.width_mm, cp.height_mm) == (src.width_mm, src.height_mm)
    assert V.effective_crop(cp, 64, 64) == V.effective_crop(src, 64, 64)
    assert I.group_invariant_issues(f, w.project.images) == []
    assert "applies to the 1 linked inset" in w.statusBar().currentMessage()
    assert len(tab.current_editor().history._undo) == n_undo + 1
    assert view.item_for(cid).rect().width() == pytest.approx(src.width_mm)   # redrawn
    tab.a_undo.trigger()
    f = tab.current_figure()
    assert f.panel(cid).width_mm == pytest.approx(w0)
