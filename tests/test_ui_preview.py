# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Preview inspector through the real window (offscreen): edits all selected
panels, mixed states, matching by channel index, one log entry per panel
per gesture, viewport = crop, no stretching, copy/paste, defaults, replace."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pycrograph import viewport as V  # noqa: E402
from pycrograph.figures import panel_channel  # noqa: E402
from pycrograph.logchain import verify_log_chain  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, workspace, mm_tiff, stk_tc, stk_z):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.resize(1400, 900)
    w.show()
    p = workspace.create_project("Prev")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    ids = w.import_images(ds.id, [str(mm_tiff), str(stk_tc), str(stk_z)])
    assert w.hashq.wait(30000)
    app.processEvents()
    panels = w.add_to_figure(ids)
    yield w, ids, panels
    w.close()


def entries_since(w, n):
    return w.project.operation_log[n:]


def test_multi_selection_mixed_and_skips(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, insp = w.figures_tab, w.figures_tab.inspector
    tab.canvas.select_ids([pa.id, pb.id])
    assert [p.id for p in insp.panels] == [pa.id, pb.id]
    assert sorted(insp.controls) == [0, 1]                 # union of channel indices
    c0 = insp.controls[0]
    assert "lut" in c0.mixed_fields                        # grey vs a colour LUT
    assert c0.lut_combo.currentIndex() == -1
    assert c0.black_spin.text() in ("—", "") or "black" not in c0.mixed_fields
    assert not c0.stain_edit.isVisible()

    # Channel 2 exists only in the STK: panel A skipped, status says so.
    n = len(w.project.operation_log)
    c1 = insp.controls[1]
    c1.white_spin.setValue(5000)
    [e] = entries_since(w, n)
    assert (e.target_id, e.field, e.new_value) == (pb.id, "channels[1].white", 5000)
    assert "1 panel(s) skipped" in w.statusBar().currentMessage()

    # Touching a mixed control sets every panel: one entry per panel.
    n = len(w.project.operation_log)
    c0.lut_combo.setCurrentIndex(c0.lut_combo.findText("cyan"))
    c0._on_lut_activated(c0.lut_combo.currentIndex())
    assert [x.target_id for x in entries_since(w, n)] == [pa.id, pb.id]
    assert {panel_channel(q, 0).lut for q in (pa, pb)} == {"cyan"}
    assert "lut" not in insp.controls[0].mixed_fields      # refreshed: now uniform

    # Partially-checked "visible": a click applies to all.
    pb.channels[0].visible = False
    insp.set_panels(tab.selected_panels())
    cb = insp.controls[0].visible_cb
    assert cb.checkState() == Qt.PartiallyChecked
    n = len(w.project.operation_log)
    cb.click()
    assert len({panel_channel(q, 0).visible for q in (pa, pb)}) == 1
    assert len(entries_since(w, n)) == 1                   # only the panel that changed
    assert not insp.controls[0].visible_cb.isTristate()


def test_slider_drag_is_one_gesture(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, insp = w.figures_tab, w.figures_tab.inspector
    tab.canvas.select_ids([pa.id, pb.id])
    ctrl = insp.controls[0]
    n = len(w.project.operation_log)
    ctrl.white_slider.setSliderDown(True)
    for v in (9000, 8500, 8000):
        ctrl.white_slider.setValue(v)
    assert len(w.project.operation_log) == n               # live: not yet logged
    assert tab.canvas.item_for(pa.id)._pixmap is not None
    ctrl.white_slider.setSliderDown(False)
    ctrl.white_slider.sliderReleased.emit()
    got = [(x.target_id, x.field, x.new_value) for x in entries_since(w, n)]
    assert got == [(pa.id, "channels[0].white", 8000), (pb.id, "channels[0].white", 8000)]
    tab.a_undo.trigger()
    f = tab.current_figure()
    assert panel_channel(f.panel(pa.id), 0).white != 8000


def test_viewport_zoom_pan_no_stretch(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, insp = w.figures_tab, w.figures_tab.inspector
    tab.canvas.select_ids([pb.id])
    s = w.project.images[pb.image_id].metadata.shape
    assert insp.zoom_spin.value() == 100
    n = len(w.project.operation_log)
    insp.zoom_spin.setValue(50)                            # zoom out: background around
    [e] = entries_since(w, n)
    assert e.field.startswith("crop.") and pb.crop.w == pytest.approx(2 * s.X)
    item = tab.canvas.item_for(pb.id)
    r = item.image_rect
    assert r.width() / r.height() == pytest.approx(s.X / s.Y)               # never stretched
    assert r.width() == pytest.approx(pb.width_mm / 2)
    assert r.center().x() == pytest.approx(pb.width_mm / 2)

    # Mini-view drag = pan, many ticks -> one entry.
    n = len(w.project.operation_log)
    insp.pan_by_oriented(3, 0, final=False)
    insp.pan_by_oriented(2, 1, final=False)
    insp.end_live()
    [e] = entries_since(w, n)
    assert e.field == "crop.x,crop.y"
    assert e.new_value["crop.x"] - e.old_value["crop.x"] == pytest.approx(5)

    insp.fit_viewport()
    assert V.zoom_percent(pb, s.X, s.Y) == pytest.approx(100)

    # Rotate: same panel on the page, crop turned.
    size = (pb.width_mm, pb.height_mm)
    insp.orient_buttons["cw"].click()
    assert pb.rotation_deg == 90 and (pb.width_mm, pb.height_mm) == size
    # 32 x 24 image turned in a 32:24 panel at the same scale: the crop
    # (24 x 32 raw) meets the image on 24 x 24 px -> a square, uniform scale.
    r = tab.canvas.item_for(pb.id).image_rect
    k = V.mm_per_px(pb, s.X, s.Y)
    assert (r.width(), r.height()) == pytest.approx((24 * k, 24 * k))

    # Mixed zoom across two panels.
    tab.canvas.select_ids([pa.id, pb.id])
    insp.zoom_by(2.0)
    tab.canvas.select_ids([pb.id])
    insp.zoom_by(2.0)
    tab.canvas.select_ids([pa.id, pb.id])
    assert insp.zoom_spin.text() == "—"


def test_tz_projection(app, setup):
    w, ids, (pa, pb, pz) = setup
    tab, insp = w.figures_tab, w.figures_tab.inspector
    tab.canvas.select_ids([pb.id, pz.id])
    assert insp.t_slider.maximum() == 2 and insp.z_slider.maximum() == 4
    n = len(w.project.operation_log)
    insp.t_slider.setValue(2)
    assert [x.target_id for x in entries_since(w, n)] == [pb.id]        # pz has one T
    insp.z_slider.setValue(3)
    assert pz.z == 3 and pb.z == 0
    assert insp.z_label.text() == "Z mixed"
    n = len(w.project.operation_log)
    insp.proj_cb.click()                                   # whole Z range by default
    assert pz.z_projection is not None and (pz.z_projection.z_start, pz.z_projection.z_end) == (0, 4)
    assert pb.z_projection is None
    assert [x.target_id for x in entries_since(w, n)] == [pz.id]
    assert "skipped" in w.statusBar().currentMessage()
    tab.canvas.select_ids([pz.id])
    insp.proj_to.setValue(3)
    assert pz.z_projection.z_end == 2


def test_copy_paste_defaults_replace(app, setup, monkeypatch):
    w, ids, (pa, pb, pz) = setup
    tab, insp = w.figures_tab, w.figures_tab.inspector
    tab.canvas.select_ids([pa.id])
    insp.controls[0].white_spin.setValue(12345)
    insp.zoom_by(2.0)
    insp.buttons["copy_rendering"].click()
    insp.buttons["copy_viewport"].click()
    tab.canvas.select_ids([pz.id])
    assert insp.buttons["paste_rendering"].isEnabled()
    insp.buttons["paste_rendering"].click()
    assert panel_channel(pz, 0).white == 12345
    insp.buttons["paste_viewport"].click()
    sa = w.project.images[pa.image_id].metadata.shape
    sz = w.project.images[pz.image_id].metadata.shape
    assert V.px_per_mm(pz, sz.X, sz.Y) == pytest.approx(V.px_per_mm(pa, sa.X, sa.Y))

    # Save as image defaults -> library; reset brings them back.
    n = len(w.project.operation_log)
    insp.buttons["save_defaults"].click()
    assert w.project.operation_log[-1].operation == "image.display.from_panel"
    assert w.project.images[pz.image_id].display.channels[0].white == 12345
    insp.controls[0].white_spin.setValue(600)
    insp.buttons["reset_defaults"].click()
    assert panel_channel(pz, 0).white == 12345
    assert len(entries_since(w, n)) == 3

    # Replace: 1-channel panel by the 2-channel image is refused.
    tab.canvas.select_ids([pa.id])
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    n = len(w.project.operation_log)
    assert not tab.replace_image(ids[1])
    assert warned and "channel" in warned[0] and len(w.project.operation_log) == n
    # Compatible (1 channel), size differs -> confirmed -> settings kept.
    lut = panel_channel(pa, 0).lut
    assert tab.replace_image(ids[2], confirm=lambda text: "size differs" in text)
    assert pa.image_id == ids[2] and panel_channel(pa, 0).lut == lut
    assert w.project.operation_log[-1].operation == "figure.panel.replace_image"
    tab.a_undo.trigger()
    assert tab.current_figure().panel(pa.id).image_id == ids[0]

    reloaded = w.ws.load_project(w.project.project.id)
    assert reloaded == w.project
    assert verify_log_chain(reloaded).status == "ok"
