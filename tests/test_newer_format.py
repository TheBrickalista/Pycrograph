# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""A project written by a newer Pycrograph (format_version above this one's):
listed as "(needs a newer Pycrograph)" with its name from the raw JSON;
opening it is refused, on every load path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from pycrograph import archive as A  # noqa: E402
from pycrograph.models import PROJECT_FORMAT_VERSION  # noqa: E402
from pycrograph.storage import ProjectFormatTooNewError, needs_newer_app  # noqa: E402
from pycrograph.ui.main_window import MainWindow  # noqa: E402

NEWER = PROJECT_FORMAT_VERSION + 1


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _make_newer(workspace, name="From the future", **extra):
    p = workspace.create_project(name)
    path = workspace.project_json(p.project.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["project"].update(format_version=NEWER, app_version="9.0.0",
                           modified_utc="2999-01-01T00:00:00+00:00", **extra)
    data["a_field_this_version_does_not_know"] = {"x": 1}
    path.write_text(json.dumps(data), encoding="utf-8")
    return p.project.id, path


def test_listed_with_its_raw_name(workspace):
    current = workspace.create_project("Current")
    pid, _ = _make_newer(workspace)
    metas = {m.id: m for m in workspace.list_projects()}
    assert set(metas) == {pid, current.project.id}
    m = metas[pid]
    assert m.name == "From the future" and m.format_version == NEWER and needs_newer_app(m)
    assert m.app_version == "9.0.0" and not needs_newer_app(metas[current.project.id])
    assert workspace.list_projects()[0].id == pid                       # most recent first


def test_archived_newer_project_follows_the_archive_filter(workspace):
    pid, _ = _make_newer(workspace, is_archived=True)
    assert pid not in [m.id for m in workspace.list_projects()]
    assert pid in [m.id for m in workspace.list_projects(include_archived=True)]


def test_load_paths_refuse_it(workspace, tmp_path):
    pid, path = _make_newer(workspace)
    with pytest.raises(ProjectFormatTooNewError, match="newer than this"):
        workspace.load_project(pid)
    # Archive import loads through the same function and refuses it too.
    p = workspace.create_project("Archived")
    res = A.export_archive(workspace, p, tmp_path / "p.pcarchive", "light")
    import zipfile
    tampered = tmp_path / "newer.pcarchive"
    with zipfile.ZipFile(res.path) as src, zipfile.ZipFile(tampered, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith("project/project.json"):
                obj = json.loads(data)
                obj["project"]["format_version"] = NEWER
                data = json.dumps(obj).encode()
            dst.writestr(info, data)
    n = len(workspace.list_projects())
    with pytest.raises(A.ArchiveError):
        A.import_archive(workspace, tampered)
    assert len(workspace.list_projects()) == n


def test_projects_tab_label_and_refusal(app, workspace, monkeypatch):
    pid, path = _make_newer(workspace)
    before = path.read_bytes()
    w = MainWindow(workspace)                      # auto-open skips it
    try:
        assert w.project is None
        w.projects.refresh_requested()
        names = [w.projects.table.item(r, 0).text() for r in range(w.projects.table.rowCount())]
        assert names == ["From the future  (needs a newer Pycrograph)"]
        shown = []
        monkeypatch.setattr(QMessageBox, "critical",
                            staticmethod(lambda _p, title, text, *a: shown.append((title, text))))
        w.projects.open_project_requested.emit(pid)
        assert w.project is None
        assert shown and shown[0][0] == "Open project" and "newer than this Pycrograph" in shown[0][1]
        assert "please update" in shown[0][1]
        # The Open Project… chooser shows the same suffix.
        seen = {}

        def get_item(_parent, _title, _label, items, *a, **k):
            seen["items"] = items
            return "", False
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
        w.open_project()
        assert "(needs a newer Pycrograph)" in seen["items"][0]
    finally:
        w.close()
    assert path.read_bytes() == before                 # never written, never locked
    assert not (path.parent / ".lock").exists()


# ------------------------------------------------- the check before a save
def _project_dict(p, version):
    d = p.model_dump(mode="json")
    d["project"]["format_version"] = version
    return d


@pytest.mark.parametrize("layout", ["padding_key_first", "large_field_in_project", "decoy_first"])
@pytest.mark.parametrize("version", [NEWER, PROJECT_FORMAT_VERSION])
def test_format_version_beyond_the_first_8_kb(workspace, layout, version):
    """format_version not in the head: the whole file is parsed, never
    "not found, so OK"; a current version found that way still saves."""
    p = workspace.create_project("Far")
    d = _project_dict(p, version)
    if layout == "padding_key_first":
        d = {"padding": "x" * 20000, **d}
    elif layout == "large_field_in_project":
        meta = d["project"]
        fv = meta.pop("format_version")
        d["project"] = {**meta, "name": "N" * 20000, "format_version": fv}
    else:   # an unrelated format_version (older) comes first, inside the head
        d = {"other": {"format_version": 1}, "padding": "x" * 20000, **d}
    path = workspace.project_json(p.project.id)
    path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    assert path.read_bytes().find(b'"format_version": %d' % version) > 8192
    before = path.read_bytes()
    if version == NEWER:
        with pytest.raises(ProjectFormatTooNewError, match="newer Pycrograph"):
            workspace.save_project(p)
        assert path.read_bytes() == before
    else:
        workspace.save_project(p)
        assert path.read_bytes() != before


@pytest.mark.parametrize("content", [
    b"{not json",
    b'{"project": {"id": "x", "format_version": 99',          # truncated
    b"\xff\xfe\x00garbage",                                   # not UTF-8
    b"[]",                                                    # JSON, not a project
    b'{"images": {}}',                                        # no "project"
    b'{"project": {"format_version": "6"}}',                  # version not an integer
    b'{"project": {"format_version": true}}',
])
def test_unreadable_file_on_disk_is_never_overwritten(workspace, content):
    from pycrograph.storage import ProjectFormatError
    p = workspace.create_project("Damaged")
    path = workspace.project_json(p.project.id)
    path.write_bytes(content)
    with pytest.raises(ProjectFormatError, match="cannot be read.*not saved over"):
        workspace.save_project(p)
    assert path.read_bytes() == content
    assert [x.name for x in path.parent.iterdir()] == ["project.json"]   # no temp file left


def test_normal_save_reads_only_the_head(workspace, mm_tiff, monkeypatch):
    """The file save_project writes is checked from its first 8 KB, however
    long its log: no full parse before each save."""
    from pycrograph import storage
    p = workspace.create_project("Long log")
    for i in range(2000):
        storage.log_operation(p, "test.op", note=f"entry {i}")
    workspace.save_project(p)
    path = workspace.project_json(p.project.id)
    assert path.stat().st_size > 100 * 8192
    real_read_text = Path.read_text

    def no_full_read(self, *a, **k):
        if self == path:
            raise AssertionError("whole project.json read before a save")
        return real_read_text(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", no_full_read)
    workspace.save_project(p)
    assert storage._on_disk_format_version(path) == PROJECT_FORMAT_VERSION


def test_missing_file_is_a_first_save(workspace):
    from pycrograph import storage
    assert storage._on_disk_format_version(workspace.root / "nope" / "project.json") is None
