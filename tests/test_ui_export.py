# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Check consistency and export through the real window (offscreen)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pycrograph.export import ExportSettings  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402
from pycrograph.ui.export_dialogs import ConsistencyDialog, ExportDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, workspace, mm_tiff, stk_tc):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.show()
    p = workspace.create_project("Exp")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    ids = w.import_images(ds.id, [str(mm_tiff), str(stk_tc)])
    assert w.hashq.wait(30000)
    app.processEvents()
    panels = w.add_to_figure(ids)
    yield w, ids, panels
    w.close()


def test_check_dialog_selects_panels(app, setup):
    w, ids, (pa, pb) = setup
    tab = w.figures_tab
    pa.scale_bar.visible = True                           # bar on 1 of 2 panels of the row
    issues = tab.check_consistency()
    bar = next(i for i in issues if i.code == "scalebar")
    assert any(i.code == "resolution" for i in issues)
    assert "Consistency:" in w.statusBar().currentMessage()
    dlg = ConsistencyDialog(issues, tab.canvas.select_ids, "check")
    tab.canvas.select_ids([])
    row = next(r for r in range(dlg.list.count()) if dlg.list.item(r).text() == bar.text())
    dlg.list.itemClicked.emit(dlg.list.item(row))
    assert set(tab.canvas.selected_ids()) == set(bar.panel_ids)


def test_export_anyway_is_logged(app, setup, tmp_path):
    w, ids, (pa, pb) = setup
    tab = w.figures_tab
    assert tab.a_export.isEnabled() and tab.a_check.isEnabled()
    n = len(w.project.operation_log)
    assert tab.export_figure(tmp_path / "f.pdf", ExportSettings("pdf"), confirm=lambda i: False) is None
    assert len(w.project.operation_log) == n and not (tmp_path / "f.pdf").exists()
    seen = []
    res = tab.export_figure(tmp_path / "f.pdf", ExportSettings("pdf"),
                            confirm=lambda issues: seen.extend(issues) or True)
    assert res is not None and res.files[0].is_file()
    e = w.project.operation_log[-1]
    assert e.operation == "export.figure"
    assert len(e.new_value["consistency_warnings"]) == len(seen) > 0
    reloaded = w.ws.load_project(w.project.project.id)          # saved with its log entry
    assert reloaded.operation_log[-1].entry_hash == e.entry_hash


def test_export_refused_and_sources(app, setup, tmp_path, monkeypatch):
    w, ids, (pa, pb) = setup
    tab = w.figures_tab
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    ref = w.project.images[ids[1]]
    path = w.ws.resolve_image_path(w.project, ref)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    assert tab.export_figure(tmp_path / "g.svg", ExportSettings("svg"), confirm=lambda i: True) is None
    assert shown and "MODIFIED" in shown[0]
    tab.canvas.select_ids([pa.id])
    res = tab.export_sources(tmp_path / "src")
    assert len(res.files) == 1 and res.files[0].name.endswith("Nuc_ER_ER.ome.tif")


def test_export_dialog_settings(app):
    dlg = ExportDialog(300, 2)
    assert not dlg.dpi.isEnabled() and dlg.info_page.isEnabled()
    dlg.format.setCurrentIndex(dlg.format.findData("tiff"))
    dlg.dpi.setValue(450)
    dlg.resampling.setCurrentIndex(1)
    s = dlg.settings()
    assert (s.format, s.dpi, s.resampling, s.info_page) == ("tiff", 450, "smooth", False)
    assert "2 pages" in dlg.pages_note.text()


def test_export_dialog_smooth_inset_note(app):
    from pycrograph.consistency import SMOOTH_INSET_WARNING
    dlg = ExportDialog(300, 1, low_res_insets=2)
    assert dlg.inset_note.text() == ""                          # PDF: native resolution
    dlg.format.setCurrentIndex(dlg.format.findData("png"))
    assert dlg.inset_note.text() == ""                          # nearest (default)
    dlg.resampling.setCurrentIndex(dlg.resampling.findData("smooth"))
    assert "2 insets below the page dpi" in dlg.inset_note.text()
    assert SMOOTH_INSET_WARNING in dlg.inset_note.text()
    dlg.format.setCurrentIndex(dlg.format.findData("svg"))
    assert dlg.inset_note.text() == ""
    none = ExportDialog(300, 1)
    none.format.setCurrentIndex(none.format.findData("tiff"))
    none.resampling.setCurrentIndex(none.resampling.findData("smooth"))
    assert none.inset_note.text() == ""
