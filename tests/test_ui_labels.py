# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Labels, scale bar, border (step 4) and the lock UI through the real
window (offscreen): vector items marked by kind, layout without overlap,
follow move/resize, missing tokens reported, one log entry per panel."""

from __future__ import annotations

import numpy as np
import pytest
import tifffile

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QGraphicsTextItem  # noqa: E402

from pycrograph.figures import panel_channel  # noqa: E402
from pycrograph.logchain import verify_log_chain  # noqa: E402
from pycrograph.scalebar import bar_geometry  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402
from pycrograph.ui.panel_overlays import (  # noqa: E402
    INSIDE_MARGIN_MM, KIND_BORDER, KIND_KEY, KIND_LABEL, KIND_SCALEBAR, KIND_SCALEBAR_TEXT,
    PT_MM, label_rects,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, workspace, mm_tiff, stk_tc, data_dir):
    from pycrograph.ui.main_window import MainWindow
    plain = data_dir / "plain.tif"
    tifffile.imwrite(plain, (np.arange(48 * 64) % 4000).astype(np.uint16).reshape(48, 64))
    w = MainWindow(workspace, auto_open=False)
    w.resize(1500, 950)
    w.show()
    p = workspace.create_project("Labels")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "Control")
    ids = w.import_images(ds.id, [str(mm_tiff), str(stk_tc), str(plain)])
    assert w.hashq.wait(30000)
    app.processEvents()
    panels = w.add_to_figure(ids)
    yield w, ids, panels
    w.close()


def kids(item, kind):
    return [c for c in item.childItems() if c.data(KIND_KEY) == kind]


def test_labels_add_layout_follow_and_log(app, setup):
    w, ids, (pa, pb, pc) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    view.select_ids([pa.id, pb.id])
    n = len(w.project.operation_log)
    tab.add_label("[image]", "top")
    tab.add_label("t = [time]", "top")
    tab.add_label("[channels]", "left-vertical")
    tab.add_label("A", "top-left")
    tab.add_label("B", "top-left")
    entries = w.project.operation_log[n:]
    assert [e.operation for e in entries] == ["figure.label.add"] * 10      # 5 gestures x 2 panels
    assert {e.target_id for e in entries} == {pa.id, pb.id}
    assert [lb.text for lb in pa.labels] == ["[image]", "t = [time]", "[channels]", "A", "B"]

    item = view.item_for(pa.id)
    labels = kids(item, KIND_LABEL)
    assert len(labels) == 5 and all(isinstance(t, QGraphicsTextItem) for t in labels)
    rects = dict()
    for pos, r in label_rects(item):
        rects.setdefault(pos, []).append(r)
    top1, top2 = rects["top"]
    assert top1.bottom() <= 0 and top2.bottom() <= top1.top() + 1e-6      # stacked, no overlap
    assert top1.center().x() == pytest.approx(pa.width_mm / 2)
    [lv] = rects["left-vertical"]
    assert lv.right() <= 0 and lv.height() > lv.width()                   # rotated, outside
    a, b = rects["top-left"]
    assert a.left() == pytest.approx(INSIDE_MARGIN_MM) and a.top() == pytest.approx(INSIDE_MARGIN_MM)
    assert b.top() >= a.bottom()
    # Font size from the page preset: 8 pt.
    assert labels[3].scale() * labels[3].font().pixelSize() == pytest.approx(8 * PT_MM)

    # Unresolved token: "?" + reported in the status bar and the Labels tab.
    assert "[time]" in tab.missing_tokens([pa.id])                        # single-plane TIFF
    assert "?" in labels[1].toPlainText()
    assert "[time]" in w.statusBar().currentMessage()
    assert "[time]" in tab.labels_inspector.missing_label.text()

    # Labels follow a move (children) and a resize (re-laid out).
    tab.current_editor().set_geometry({pa.id: (pa.x_mm + 10, pa.y_mm + 30, 60, 30)},
                                      "figure.panel.resize", "Resize")
    tab._sync_after_geometry([pa.id])
    item = view.item_for(pa.id)
    rects = {pos: r for pos, r in label_rects(item) if pos == "top"}
    assert rects["top"].center().x() == pytest.approx(30)                 # new width / 2
    assert item.mapToScene(label_rects(item)[0][1].topLeft()).x() > pa.x_mm

    # Edit and delete act on every selected panel holding the label.
    n = len(w.project.operation_log)
    tab.update_label(("A", "top-left"), text="A", position="bottom-right", color="#FFFF00")
    assert [e.target_id for e in w.project.operation_log[n:]] == [pa.id, pb.id]
    assert pb.labels[3].position == "bottom-right" and pb.labels[3].color == "#FFFF00"
    tab.delete_label(("B", "top-left"))
    assert all("B" not in [lb.text for lb in q.labels] for q in (pa, pb))
    tab.a_undo.trigger()
    assert "B" in [lb.text for lb in tab.current_figure().panel(pa.id).labels]


def test_labels_from_kv_and_clipboard(app, setup):
    w, ids, (pa, pb, pc) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    w.project.images[ids[0]].key_values = {"Condition": "siCtrl"}
    w.project.images[ids[1]].key_values = {"Condition": "siLMNA"}
    view.select_ids([pa.id, pb.id])
    assert tab.kv_keys() == ["Condition"]
    n = len(w.project.operation_log)
    tab.labels_from_kv("Condition", "top")
    assert len(w.project.operation_log) == n + 2
    texts = [t.toPlainText() for t in kids(view.item_for(pb.id), KIND_LABEL)]
    assert texts == ["siLMNA"]
    # Copy/paste panels keeps labels; pasted panels are new, unlocked.
    pa.lock_group = "x"
    tab.copy_panels()
    new_ids = tab.paste_panels()
    assert len(new_ids) == 2 and set(view.selected_ids()) == set(new_ids)
    f = tab.current_figure()
    copy = f.panel(new_ids[0])
    assert copy.labels == pa.labels and copy.lock_group is None
    assert copy.x_mm == pytest.approx(pa.x_mm + 5)
    assert w.project.operation_log[-1].operation == "figure.panel.paste"


def test_scale_bar_and_border(app, setup):
    w, ids, (pa, pb, pc) = setup
    tab, view = w.figures_tab, w.figures_tab.canvas
    insp = tab.labels_inspector
    # Plain TIFF only: greyed with the reason.
    view.select_ids([pc.id])
    assert not insp.bar_widgets.isEnabled()
    assert "no spatial calibration" in insp.bar_reason.text()
    # Calibrated + uncalibrated: applied to the calibrated one, other skipped.
    view.select_ids([pa.id, pc.id])
    assert insp.bar_widgets.isEnabled() and "skipped" in insp.bar_reason.text()
    n = len(w.project.operation_log)
    tab.set_scale_bar(visible=True, length=5.2, position="bottom-left", label_position="above")
    assert [e.target_id for e in w.project.operation_log[n:]] == [pa.id]
    assert not pc.scale_bar.visible and "skipped" in w.statusBar().currentMessage()
    item = view.item_for(pa.id)
    [bar] = kids(item, KIND_SCALEBAR)
    [txt] = kids(item, KIND_SCALEBAR_TEXT)
    ref = w.project.images[pa.image_id]
    assert bar.rect().width() == pytest.approx(bar_geometry(pa, ref).length_mm)
    assert bar.rect().left() == pytest.approx(INSIDE_MARGIN_MM)
    assert bar.rect().bottom() == pytest.approx(pa.height_mm - INSIDE_MARGIN_MM)
    assert txt.toPlainText() == "5.2 µm" and txt.mapRectToParent(txt.boundingRect()).bottom() <= bar.rect().top()
    # Live with zoom: twice the magnification, twice the bar on the page.
    before = bar.rect().width()
    view.select_ids([pa.id])
    tab.inspector.zoom_by(2.0)
    [bar] = kids(view.item_for(pa.id), KIND_SCALEBAR)
    assert bar.rect().width() == pytest.approx(2 * before)
    assert bar.rect().width() == pytest.approx(bar_geometry(pa, ref).length_mm)
    # A label in the bar's corner stacks above it.
    tab.add_label("x", "bottom-left")
    [(_pos, r)] = [x for x in label_rects(view.item_for(pa.id)) if x[0] == "bottom-left"]
    [bar] = kids(view.item_for(pa.id), KIND_SCALEBAR)
    [txt] = kids(view.item_for(pa.id), KIND_SCALEBAR_TEXT)
    assert r.bottom() <= txt.mapRectToParent(txt.boundingRect()).top() + 1e-6

    # Border inside the edge: the panel's page size is unchanged.
    size = (pa.width_mm, pa.height_mm)
    tab.set_border(visible=True, width_pt=2.0, color="#FF0000")
    [border] = kids(view.item_for(pa.id), KIND_BORDER)
    bw = 2.0 * PT_MM
    outer = border.rect().adjusted(-bw / 2, -bw / 2, bw / 2, bw / 2)
    assert (outer.left(), outer.top()) == pytest.approx((0, 0))
    assert (outer.width(), outer.height()) == pytest.approx(size)
    assert (pa.width_mm, pa.height_mm) == size
    assert view.item_for(pa.id).rect().width() == pytest.approx(size[0])
    # Inside labels move in by the border width.
    tab.add_label("y", "top-right")
    [(_p, r)] = [x for x in label_rects(view.item_for(pa.id)) if x[0] == "top-right"]
    assert r.top() == pytest.approx(INSIDE_MARGIN_MM + bw)
    reloaded = w.ws.load_project(w.project.project.id)
    assert reloaded == w.project and verify_log_chain(reloaded).status == "ok"


def test_lock_ui_and_condition_grid_labels(app, setup):
    w, ids, (pa, pb, pc) = setup
    tab, view, insp = w.figures_tab, w.figures_tab.canvas, w.figures_tab.inspector
    view.select_ids([pa.id, pb.id])
    assert "Not locked" in insp.lock_label.text()
    asked = []
    group = tab.lock_selection(confirm=lambda text: asked.append(text) or True)
    assert asked and "will take the levels/LUTs of panel 1" in asked[0]
    assert group and pa.lock_group == pb.lock_group == group
    assert insp.lock_label.text() == "🔒 Locked: the whole group is selected (2 panels, manual lock)"
    assert group not in insp.lock_label.text()
    # A channel edit through the Preview tab reaches the locked panel.
    view.select_ids([pa.id])
    n = len(w.project.operation_log)
    insp.controls[0].white_spin.setValue(12000)
    assert panel_channel(pb, 0).white == 12000
    assert {e.target_id for e in w.project.operation_log[n:]} == {pa.id, pb.id}
    assert "locked panel" in w.statusBar().currentMessage()
    insp.lock_buttons["select"].click()
    assert set(view.selected_ids()) == {pa.id, pb.id}
    tab.unlock_selection()
    assert pa.lock_group is None and pb.lock_group is None

    # Condition grid: row labels on the first column, column labels on the first row.
    view.select_ids([pb.id, pa.id])
    tab.condition_grid(row_label="[image]")
    f = tab.current_figure()
    new = f.panels[3:]
    firsts = [q for q in new if any(lb.position == "left-vertical" for lb in q.labels)]
    tops = [q for q in new if any(lb.text == "[channels]" for lb in q.labels)]
    assert len(firsts) == 2 and len(tops) == 3                       # 2 rows; 2ch + merge
    texts = {t.toPlainText() for q in firsts for t in kids(view.item_for(q.id), KIND_LABEL)}
    assert {"Nuc_ER_ER", "timelapse 2ch"} <= texts
    # Room was reserved: row labels stay on the page, column labels clear
    # of the panels above the grid.
    above = max(q.y_mm + q.height_mm for q in f.panels[:3])
    for q in new:
        item = view.item_for(q.id)
        for _pos, r in label_rects(item):
            sr = item.mapRectToScene(r)
            assert sr.left() >= -1e-6 and sr.top() >= above
    assert len({q.lock_group for q in new}) == 1


def test_lock_label_kinds_never_ids(app, setup):
    from pycrograph.ui.panel_inspector import lock_text
    w, ids, (pa, pb, pc) = setup
    fig = w.figures_tab.current_figure()
    pa.lock_group = pb.lock_group = pc.lock_group = "split:abc123"
    assert lock_text([pa], fig) == "🔒 Locked with 2 other panel(s) (split row)"
    pc.lock_group = "grid:abc123:row1"
    assert lock_text([pa], fig) == "🔒 Locked with 1 other panel(s) (split row)"
    assert lock_text([pa, pc], fig) == "🔒 Mixed: split row, condition grid"
    pb.lock_group = None
    assert lock_text([pa, pb, pc], fig) == "🔒 Mixed: split row, condition grid + unlocked panels"
    for text in (lock_text([pa], fig), lock_text([pa, pc], fig)):
        assert "abc123" not in text and "split:" not in text and "grid:" not in text
    # Through the real inspector.
    pa.lock_group = pb.lock_group = "lock:zz9"
    pc.lock_group = None
    w.figures_tab.canvas.select_ids([pa.id])
    assert w.figures_tab.inspector.lock_label.text() == "🔒 Locked with 1 other panel(s) (manual lock)"
