# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""What the Pyblication plugin relies on in the app:

* An export's export.figure entry is on disk (project.json) when the window
  reports the export ("Exported …" in the status bar), so Pyblication can
  identify the file straight away.
* --project <id> opens that project; without it the app starts as before.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pycrograph import app as app_module  # noqa: E402
from pycrograph import main as main_module  # noqa: E402
from pycrograph import paths, plugin  # noqa: E402
from pycrograph.export import ExportSettings  # noqa: E402
from pycrograph.storage import create_dataset, sha256_file  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, workspace, mm_tiff, stk_tc, monkeypatch):
    from pycrograph.ui.main_window import MainWindow
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(workspace.root))
    w = MainWindow(workspace, auto_open=False)
    w.show()
    p = workspace.create_project("Exp")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    ids = w.import_images(ds.id, [str(mm_tiff), str(stk_tc)])
    assert w.hashq.wait(30000)
    app.processEvents()
    w.add_to_figure(ids)
    yield w
    w.close()


@pytest.mark.parametrize(("fmt", "n_pages"), [("pdf", 1), ("svg", 1), ("png", 2)])
def test_export_entry_is_on_disk_when_success_is_reported(app, window, tmp_path, fmt, n_pages):
    w = window
    tab = w.figures_tab
    tab.current_figure().page.n_pages = n_pages
    dest = tmp_path / f"figure.{fmt}"
    seen = {}

    def at_success(message: str) -> None:
        if not message.startswith("Exported"):
            return
        # The moment the user is told "Exported": what is on disk now?
        on_disk = json.loads(w.ws.project_json(w.project.project.id).read_text(encoding="utf-8"))
        seen["logged"] = [[f["sha256"] for f in e["new_value"]["files"]]
                          for e in on_disk["operation_log"] if e["operation"] == "export.figure"]
        seen["identified"] = [plugin.identify(str(f)) for f in sorted(tmp_path.glob("figure*"))
                              if not f.name.endswith(".json")]

    tab.status_message.connect(at_success)
    try:
        result = tab.export_figure(dest, ExportSettings(fmt), confirm=lambda issues: True)
    finally:
        tab.status_message.disconnect(at_success)
    assert result is not None and len(result.files) == n_pages
    assert seen["logged"] == [[sha256_file(f) for f in result.files]]
    assert len(seen["identified"]) == n_pages
    assert all(r is not None and r["project_id"] == w.project.project.id
               for r in seen["identified"])


# ------------------------------------------------------------------ --project
@pytest.mark.parametrize(("argv", "expected"), [
    ([], None),
    (["--project", "abc123"], "abc123"),
    (["--project=abc123"], "abc123"),
    (["--project="], None),
    (["--project"], None),
    (["-x", "--project", "id9", "--other"], "id9"),
])
def test_parse_project_arg(argv, expected):
    assert main_module.parse_project_arg(argv) == expected


def test_main_routes_project_arg(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "run", lambda *a, **k: calls.append((a, k)))
    main_module.main(["--project", "abc123"])
    main_module.main([])
    assert calls == [((["--project", "abc123"],), {"project_id": "abc123"}), (([],), {})]


def _set_modified(workspace, project, when: str) -> None:
    """Make project the most (or least) recent without a sleep."""
    path = workspace.project_json(project.project.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["project"]["modified_utc"] = when
    path.write_text(json.dumps(data), encoding="utf-8")


def _run_without_event_loop(monkeypatch):
    """app.run() up to the event loop: the window it built."""
    shown = []
    monkeypatch.setattr(app_module.MainWindow, "show", lambda self: shown.append(self))
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    return shown


def test_run_with_project_opens_it(app, workspace, monkeypatch):
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(workspace.root))
    wanted = workspace.create_project("Wanted")
    recent = workspace.create_project("Most recent")         # auto-open would pick this one
    _set_modified(workspace, wanted, "2000-01-01T00:00:00+00:00")
    _set_modified(workspace, recent, "2999-01-01T00:00:00+00:00")
    shown = _run_without_event_loop(monkeypatch)
    app_module.run([], project_id=wanted.project.id)
    (w,) = shown
    try:
        assert w.project.project.id == wanted.project.id
    finally:
        w.close()


def test_run_without_project_is_unchanged(app, workspace, monkeypatch):
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(workspace.root))
    older = workspace.create_project("Older")
    recent = workspace.create_project("Most recent")
    _set_modified(workspace, older, "2000-01-01T00:00:00+00:00")
    _set_modified(workspace, recent, "2999-01-01T00:00:00+00:00")
    shown = _run_without_event_loop(monkeypatch)
    app_module.run([])
    (w,) = shown
    try:
        assert w.project.project.id == recent.project.id     # the most recent, as before
    finally:
        w.close()


def test_run_with_unknown_project_warns(app, workspace, monkeypatch):
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(workspace.root))
    workspace.create_project("Other")
    warned = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: warned.append(a[2]))
    shown = _run_without_event_loop(monkeypatch)
    app_module.run([], project_id="nosuchproject")
    (w,) = shown
    try:
        assert w.project is None
        assert warned and "Cannot open project" in warned[0]
    finally:
        w.close()
