# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Session-1 definition of done, through the real main window (offscreen):
import by reference -> correct calibration -> adjust levels + LUT ->
save/reload identical, with one log entry per gesture."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

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
    yield w
    w.close()


def test_import_adjust_save_reload(app, window, workspace, mm_tiff, stk_tc):
    p = workspace.create_project("Smoke")
    window.open_project_by_id(p.project.id)
    ds = create_dataset(window.project, "Ctrl")
    ids = window.import_images(ds.id, [str(mm_tiff), str(stk_tc)])
    assert len(ids) == 2
    assert window.hashq.wait(30000)
    app.processEvents()
    assert all(window.project.images[i].sha256 for i in ids)
    assert set(window._status.values()) == {"ok"}

    # TIFF: calibration shown.
    window.library.select_image(ids[0])
    app.processEvents()
    assert window.ref.id == ids[0]
    assert "0.325 um/px" in window.info_label.text()
    assert not window.pixmap_item.pixmap().isNull()

    # STK: two channels; drag White (many ticks) then release = ONE log entry.
    window.library.select_image(ids[1])
    app.processEvents()
    assert sorted(window._controls) == [0, 1]
    ctrl = window._controls[1]
    n_before = len(window.project.operation_log)
    for v in (5000, 4500, 4000):
        ctrl.white_slider.setValue(v)
    ctrl.white_slider.sliderReleased.emit()
    assert len(window.project.operation_log) == n_before + 1
    entry = window.project.operation_log[-1]
    assert entry.field == "display.channels[1].white" and entry.new_value == 4000

    ctrl.lut_combo.setCurrentIndex(ctrl.lut_combo.findText("magenta"))
    ctrl._on_lut_activated(ctrl.lut_combo.currentIndex())
    window.t_slider.setValue(2)
    window.flush_pending()
    ref = window.project.images[ids[1]]
    assert ref.display.channels[1].lut == "magenta" and ref.display.t == 2

    reloaded = workspace.load_project(p.project.id)
    assert reloaded == window.project
    assert verify_log_chain(reloaded).status == "ok"


def test_missing_source_shows_placeholder(app, window, workspace, mm_tiff):
    p = workspace.create_project("Missing")
    window.open_project_by_id(p.project.id)
    ds = create_dataset(window.project, "A")
    [iid] = window.import_images(ds.id, [str(mm_tiff)])
    window.hashq.wait(30000)
    app.processEvents()
    mm_tiff.unlink()
    window.check_all_sources(quiet=True)
    assert window._status[iid] == "missing"
    window._on_image_selected(iid)
    assert window.placeholder.isVisible()
    assert "not found" in window.placeholder.toPlainText()
