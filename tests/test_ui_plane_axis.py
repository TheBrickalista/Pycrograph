# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Library viewer: the "Planes are: Time / Z" selector (offscreen)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pycrograph.storage import create_dataset  # noqa: E402
from synth import write_metamorph_planes  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, workspace, data_dir, mm_tiff):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.resize(1400, 900)
    w.show()
    p = workspace.create_project("Axes")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    tl = write_metamorph_planes(data_dir / "timelapse.tif", [1234.5] * 5)
    zs = write_metamorph_planes(data_dir / "zstack.tif", [0.0, 0.5, 1.0, 1.5, 2.0])
    ids = w.import_images(ds.id, [str(tl), str(zs), str(mm_tiff)])
    assert w.hashq.wait(30000)
    w.tabs.setCurrentWidget(w.library_tab)            # the selector lives in the Library viewer
    app.processEvents()
    yield w, ids
    w.close()


def choose(w, axis: str) -> None:
    combo = w.plane_axis_combo
    i = combo.findData(axis)
    combo.setCurrentIndex(i)
    combo.activated.emit(i)                       # what a user pick sends


def test_selector_shown_only_for_generic_plane_axis(app, setup):
    w, (tl, zs, single) = setup
    w.library.select_image(zs)
    assert w.plane_axis_combo.isVisible() and w.plane_axis_combo.currentData() == "Z"
    assert "MetaMorph z-position" in w.plane_axis_combo.toolTip()
    assert "Plane axis" in w.library.meta.toPlainText()
    w.library.select_image(tl)
    assert w.plane_axis_combo.isVisible() and w.plane_axis_combo.currentData() == "T"
    w.library.select_image(single)
    assert not w.plane_axis_combo.isVisible() and not w.plane_axis_label.isVisible()


def test_switch_to_z_updates_sliders_info_and_log(app, setup):
    w, (tl, zs, single) = setup
    w.library.select_image(tl)
    assert w.t_slider.isEnabled() and w.t_slider.maximum() == 4 and not w.z_slider.isEnabled()
    n = len(w.project.operation_log)
    choose(w, "Z")
    ref = w.project.images[tl]
    assert (ref.metadata.shape.T, ref.metadata.shape.Z) == (1, 5)
    assert w.z_slider.isEnabled() and w.z_slider.maximum() == 4 and not w.t_slider.isEnabled()
    assert w.z_label.text().startswith("Z 1/5")
    assert "Z (user override)" in w.library.meta.toPlainText()
    [e] = w.project.operation_log[n:]
    assert (e.operation, e.field, e.old_value, e.new_value) == ("image.set", "plane_axis", "T", "Z")
    saved = w.ws.load_project(w.project.project.id)                      # saved
    assert saved.images[tl].plane_axis_override == "Z"
    assert "planes read as Z" in w.statusBar().currentMessage()
    w.z_slider.setValue(3)                                               # the source reopened
    app.processEvents()
    assert int(w.source.get_plane(0, 3, 0)[0, 0]) == 4


def test_refused_when_in_a_figure_shows_why(app, setup, monkeypatch):
    w, (tl, zs, single) = setup
    w.add_to_figure([zs])
    w.library.select_image(zs)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    n = len(w.project.operation_log)
    choose(w, "T")
    assert shown and "Figure 1" in shown[0] and "not supported" in shown[0]
    assert w.plane_axis_combo.currentData() == "Z"                       # back to the mapping
    assert w.project.images[zs].metadata.shape.Z == 5 and len(w.project.operation_log) == n
