# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Archive export / import through the real window (offscreen)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pycrograph.storage import create_dataset  # noqa: E402
from pycrograph.ui.archive_dialog import ArchiveDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_archive_round_trip_in_window(app, workspace, mm_tiff, stk_tc, tmp_path, monkeypatch):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.show()
    try:
        assert not w.projects.export_archive_btn.isEnabled()
        p = workspace.create_project("Window")
        w.open_project_by_id(p.project.id)
        assert w.projects.export_archive_btn.isEnabled() and w.projects.import_archive_btn.isEnabled()
        ds = create_dataset(w.project, "A")
        ids = w.import_images(ds.id, [str(mm_tiff), str(stk_tc)])
        assert w.hashq.wait(30000)
        app.processEvents()
        w.add_to_figure(ids)
        res = w.export_archive(tmp_path / "w.pcarchive", "light", include_report=True)
        assert res is not None and res.path.is_file()
        assert "Archive written (light" in w.statusBar().currentMessage()
        saved = workspace.load_project(p.project.id)
        assert saved.operation_log[-1].operation == "export.archive"     # logged and saved
        imp = w.import_archive(res.path)
        assert imp is not None and w.project.project.id == imp.project.project.id != p.project.id
        assert len(w.project.figures[0].panels) == 2
        names = [w.projects.table.item(r, 0).text() for r in range(w.projects.table.rowCount())]
        assert names.count("Window") == 2
        # A refused import shows why and imports nothing.
        shown = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
        bad = tmp_path / "bad.pcarchive"
        bad.write_bytes(b"nope")
        n = len(workspace.list_projects())
        assert w.import_archive(bad) is None
        assert shown and "nothing imported" in shown[0] and len(workspace.list_projects()) == n
    finally:
        w.close()


def test_archive_dialog(app):
    dlg = ArchiveDialog()
    assert dlg.level() == "light" and dlg.include_report.isChecked()
    dlg.complete.setChecked(True)
    assert dlg.level() == "complete"
