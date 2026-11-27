# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
# SHARED-CORE candidate (from PysternBlot: tests/test_project_lock.py)
"""Per-project edit lock (project_lock.py) and the read-only mode of a window
that does not hold it: a standalone Pycrograph and the one bundled in
Pyblication may run at once on the same workspace."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pycrograph import archive as A  # noqa: E402
from pycrograph import paths, plugin, project_lock  # noqa: E402
from pycrograph.export import ExportSettings  # noqa: E402
from pycrograph.models import PROJECT_FORMAT_VERSION  # noqa: E402
from pycrograph.storage import (  # noqa: E402
    ProjectFormatTooNewError, ProjectReadOnlyError, create_dataset, log_operation, register_image,
    set_image_hash, sha256_file,
)
from pycrograph.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(monkeypatch):
    """Record every static message box instead of showing it."""
    shown: list[tuple[str, str, str]] = []

    def recorder(kind):
        def _show(_parent, title, text, *a, **k):
            shown.append((kind, title, text))
            return QMessageBox.Ok
        return _show

    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(recorder(kind)))
    return shown


@pytest.fixture
def ask(monkeypatch):
    """Answer the lock dialog with ask.choice; ask.holders records each call."""
    class Ask:
        choice = "read_only"
        holders: list = []

    a = Ask()
    a.holders = []

    def _ask(self, holder):
        a.holders.append(holder)
        return a.choice
    monkeypatch.setattr(MainWindow, "_ask_lock_choice", _ask)
    return a


@pytest.fixture
def windows(app):
    made = []

    def make(ws):
        w = MainWindow(ws, auto_open=False)
        made.append(w)
        return w
    yield make
    for w in made:
        w.close()


@pytest.fixture
def project(workspace, mm_tiff):
    p = workspace.create_project("Locked")
    ref = register_image(p, mm_tiff, create_dataset(p, "A").id)
    set_image_hash(p, ref.id, sha256_file(ref.path))
    workspace.save_project(p)
    return p


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write_other_lock(project_dir: Path, *, pid: int, host: str | None = None,
                      created: str = "2026-01-01T00:00:00+00:00") -> str:
    info = project_lock.LockInfo(
        host=host or socket.gethostname(), pid=pid, app="Pycrograph",
        app_version="9.9.9", created_utc=created,
        executable="/other/python", token="other-token",
    )
    project_lock.lock_path(project_dir).write_text(json.dumps(project_lock.asdict(info)),
                                                    encoding="utf-8")
    return info.token


def _lock_of(project_dir: Path):
    return project_lock.LockInfo.from_file(project_lock.lock_path(project_dir))


def _bytes(workspace, p) -> bytes:
    return workspace.project_json(p.project.id).read_bytes()


# ===========================================================================
# project_lock (no Qt)
# ===========================================================================
class TestLockFile:

    def test_acquire_creates_lock_with_fields(self, tmp_path):
        result = project_lock.acquire(tmp_path)
        assert result.status == project_lock.ACQUIRED
        data = json.loads((tmp_path / ".lock").read_text(encoding="utf-8"))
        assert data["host"] == socket.gethostname()
        assert data["pid"] == os.getpid()
        assert data["app"] == "Pycrograph" and data["app_version"]
        assert data["created_utc"] and data["executable"] == sys.executable
        assert data["token"] == result.token

    def test_release_removes_own_lock(self, tmp_path):
        result = project_lock.acquire(tmp_path)
        assert project_lock.release(tmp_path, result.token)
        assert not (tmp_path / ".lock").exists()

    def test_release_keeps_a_lock_it_does_not_own(self, tmp_path):
        _write_other_lock(tmp_path, pid=os.getppid())
        assert not project_lock.release(tmp_path, "not-mine")
        assert (tmp_path / ".lock").exists()

    def test_second_acquire_is_held_by_other(self, tmp_path):
        _write_other_lock(tmp_path, pid=os.getppid())
        result = project_lock.acquire(tmp_path)
        assert result.status == project_lock.HELD_BY_OTHER
        assert result.holder.app_version == "9.9.9"
        assert _lock_of(tmp_path).token == "other-token"

    def test_other_host_is_never_stale(self, tmp_path):
        _write_other_lock(tmp_path, pid=_dead_pid(), host="another-machine.example")
        assert project_lock.acquire(tmp_path).status == project_lock.HELD_BY_OTHER

    def test_stale_lock_is_taken_over(self, tmp_path):
        _write_other_lock(tmp_path, pid=_dead_pid())
        result = project_lock.acquire(tmp_path)
        assert result.status == project_lock.TOOK_OVER_STALE
        assert _lock_of(tmp_path).token == result.token
        assert _lock_of(tmp_path).pid == os.getpid()

    def test_own_pid_with_foreign_token_is_stale(self, tmp_path):
        _write_other_lock(tmp_path, pid=os.getpid())
        assert project_lock.acquire(tmp_path).status == project_lock.TOOK_OVER_STALE

    @pytest.mark.parametrize("content", ["not json", "{}", '{"host": "h", "pid": "x"}'])
    def test_corrupt_lock_is_not_taken(self, tmp_path, content):
        (tmp_path / ".lock").write_text(content, encoding="utf-8")
        result = project_lock.acquire(tmp_path)
        assert result.status == project_lock.HELD_BY_OTHER and result.holder is None
        assert (tmp_path / ".lock").read_text(encoding="utf-8") == content

    def test_force_replaces_and_still_ours(self, tmp_path):
        _write_other_lock(tmp_path, pid=os.getppid())
        token = project_lock.force(tmp_path)
        assert project_lock.still_ours(tmp_path, token)
        assert not project_lock.still_ours(tmp_path, "other-token")

    def test_pid_alive(self):
        assert project_lock.pid_alive(os.getpid())
        assert not project_lock.pid_alive(_dead_pid())
        assert not project_lock.pid_alive(0)


class TestLockTimeDisplay:
    PARIS_WINTER = timezone(timedelta(hours=1))
    NOW = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)

    def _fmt(self, iso, tz=PARIS_WINTER, now=NOW):
        return project_lock.format_lock_time(iso, now=now, tz=tz)

    def test_today_and_other_day(self):
        assert self._fmt("2026-11-27T13:15:00+00:00") == "today at 14:15"
        assert self._fmt("2026-11-26T13:15:00+00:00") == "26 Nov 2026 at 14:15"

    def test_day_is_decided_in_local_time(self):
        assert self._fmt("2026-11-26T23:30:00+00:00") == "today at 00:30"
        assert self._fmt("2026-11-26T23:30:00+00:00", tz=timezone.utc) == "26 Nov 2026 at 23:30"

    def test_z_suffix_naive_and_unparseable(self):
        assert self._fmt("2026-11-27T13:15:00Z") == "today at 14:15"
        assert self._fmt("2026-11-27T13:15:00") == "today at 14:15"
        assert self._fmt("unknown time") == "unknown time"

    def test_file_keeps_utc_iso(self, tmp_path):
        project_lock.acquire(tmp_path)
        raw = json.loads((tmp_path / ".lock").read_text(encoding="utf-8"))["created_utc"]
        assert datetime.fromisoformat(raw).utcoffset() == timedelta(0)


# ===========================================================================
# Workspace
# ===========================================================================
class TestWorkspaceLocks:

    def test_saves_refused_for_read_only_ids(self, workspace, project):
        before = _bytes(workspace, project)
        workspace.read_only_ids.add(project.project.id)
        log_operation(project, "test.change")
        with pytest.raises(ProjectReadOnlyError):
            workspace.save_project(project)
        assert _bytes(workspace, project) == before
        workspace.read_only_ids.clear()
        workspace.save_project(project)
        assert _bytes(workspace, project) != before

    def test_save_never_overwrites_a_newer_format(self, workspace, project):
        path = workspace.project_json(project.project.id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["project"]["format_version"] = PROJECT_FORMAT_VERSION + 1
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        before = path.read_bytes()
        with pytest.raises(ProjectFormatTooNewError, match="newer Pycrograph"):
            workspace.save_project(project)
        assert path.read_bytes() == before

    def test_release_removes_folder_left_empty(self, workspace):
        d = workspace.project_dir("neverSaved")
        d.mkdir(parents=True)
        assert workspace.acquire_lock("neverSaved").acquired
        workspace.release_lock("neverSaved")
        assert not d.exists()

    @pytest.mark.parametrize("stray", ["notes.txt", ".DS_Store", "sub/"])
    def test_release_keeps_folder_with_a_stray_entry(self, workspace, stray):
        d = workspace.project_dir("withStray")
        d.mkdir(parents=True)
        workspace.acquire_lock("withStray")
        if stray.endswith("/"):
            (d / stray).mkdir()
        else:
            (d / stray).write_text("keep me", encoding="utf-8")
        workspace.release_lock("withStray")
        assert d.is_dir() and (d / stray.rstrip("/")).exists()
        assert not (d / ".lock").exists()

    def test_release_keeps_the_project(self, workspace, project):
        pid = project.project.id
        workspace.acquire_lock(pid)
        workspace.release_lock(pid)
        assert workspace.project_json(pid).is_file() and not (workspace.project_dir(pid) / ".lock").exists()

    def test_folder_untouched_when_the_lock_is_not_ours(self, workspace):
        d = workspace.project_dir("theirs")
        d.mkdir(parents=True)
        workspace.acquire_lock("theirs")
        _write_other_lock(d, pid=os.getppid())          # "Open anyway" elsewhere
        workspace.release_lock("theirs")
        assert _lock_of(d).token == "other-token"

    def test_release_of_an_unheld_project_touches_nothing(self, workspace):
        d = workspace.project_dir("unheld")
        d.mkdir(parents=True)
        workspace.release_lock("unheld")
        assert d.is_dir()

    def test_locked_by_other(self, workspace, project):
        pid = project.project.id
        d = workspace.project_dir(pid)
        assert workspace.locked_by_other(pid) is None                      # no lock
        workspace.acquire_lock(pid)
        assert workspace.locked_by_other(pid) is None                      # ours
        _write_other_lock(d, pid=os.getppid())
        assert workspace.locked_by_other(pid).token == "other-token"
        _write_other_lock(d, pid=_dead_pid())
        assert workspace.locked_by_other(pid) is None                      # stale
        (d / ".lock").write_text("garbage", encoding="utf-8")
        assert workspace.locked_by_other(pid).host == "unknown host"       # unreadable: held


# ===========================================================================
# The window
# ===========================================================================
class TestWindow:

    def test_lock_created_on_open_and_removed_on_close(self, windows, workspace, project):
        d = workspace.project_dir(project.project.id)
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        assert _lock_of(d).token == workspace.held_locks[project.project.id]
        assert not w._is_read_only() and not w.read_only_banner.isVisibleTo(w)
        w.close()
        assert not (d / ".lock").exists() and workspace.project_json(project.project.id).is_file()

    def test_switching_project_releases_the_previous_lock(self, windows, workspace, project):
        other = workspace.create_project("Other")
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        w.open_project_by_id(other.project.id)
        assert not (workspace.project_dir(project.project.id) / ".lock").exists()
        assert (workspace.project_dir(other.project.id) / ".lock").exists()

    def test_reopening_the_same_project_keeps_its_lock(self, windows, workspace, project, ask):
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        token = workspace.held_locks[project.project.id]
        w.open_project_by_id(project.project.id)
        assert ask.holders == [] and not w._is_read_only()
        assert _lock_of(workspace.project_dir(project.project.id)).token == token

    def test_second_window_opens_read_only(self, app, windows, workspace, project, ask, dialogs):
        # Two windows of one process have separate Workspace objects, as two
        # installations would.
        from pycrograph.storage import Workspace
        first = windows(workspace)
        first.open_project_by_id(project.project.id)
        ws2 = Workspace(workspace.root)
        second = windows(ws2)
        second.open_project_by_id(project.project.id)
        (holder,) = ask.holders
        assert holder.token == workspace.held_locks[project.project.id] and holder.pid == os.getpid()
        assert second._is_read_only() and second.windowTitle().endswith("— read-only")
        assert second.read_only_banner.isVisibleTo(second)
        assert "another Pycrograph" in second.read_only_banner.text()
        assert holder.describe() in second.read_only_banner.text()
        assert not first._is_read_only()
        # Its lock is not ours: closing the second window leaves it.
        second.close()
        assert _lock_of(workspace.project_dir(project.project.id)).token == holder.token

    def _read_only_window(self, windows, workspace, project, ask):
        _write_other_lock(workspace.project_dir(project.project.id), pid=os.getppid())
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        assert w._is_read_only()
        return w

    def test_saves_and_log_entries_never_reach_disk(self, windows, workspace, project, ask, dialogs):
        w = self._read_only_window(windows, workspace, project, ask)
        before = _bytes(workspace, project)
        w.add_to_figure(list(w.project.images))       # a figure gesture: logs, then save()
        log_operation(w.project, "test.change")
        w.save()
        assert _bytes(workspace, project) == before
        with pytest.raises(ProjectReadOnlyError):      # also refused below the UI
            workspace.save_project(w.project)

    def test_first_blocked_change_notifies_once(self, windows, workspace, project, ask, dialogs):
        w = self._read_only_window(windows, workspace, project, ask)
        assert dialogs == []                             # opening read-only says nothing more
        for _ in range(3):
            w.save()
        assert [d[:2] for d in dialogs] == [("information", "Read-only")]
        assert "not saved" in dialogs[0][2]
        assert "not saved" in w.statusBar().currentMessage()

    def test_imports_and_exports_refused(self, app, windows, workspace, project, ask, dialogs,
                                         stk_tc, tmp_path):
        w = self._read_only_window(windows, workspace, project, ask)
        before = _bytes(workspace, project)
        n_images = len(w.project.images)
        assert w.import_images(paths=[str(stk_tc)]) == []
        assert len(w.project.images) == n_images
        w.add_to_figure(list(w.project.images))
        tab = w.figures_tab
        out = tmp_path / "exports"
        assert tab.export_figure(out / "f.pdf", ExportSettings("pdf"), confirm=lambda i: True) is None
        assert tab.export_sources(out) is None
        assert w.export_archive(out / "a.pcarchive") is None
        image_id = next(iter(w.project.images))
        w.copy_into(image_id)
        w.relink(image_id)
        assert not out.exists() and w.project.images[image_id].stored_relpath is None
        refused = [t for k, t, _ in dialogs if t == "Read-only"]
        assert len(refused) >= 6
        assert _bytes(workspace, project) == before

    def test_stale_lock_taken_over_without_asking(self, windows, workspace, project, ask, dialogs):
        _write_other_lock(workspace.project_dir(project.project.id), pid=_dead_pid())
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        assert ask.holders == [] and dialogs == [] and not w._is_read_only()
        assert _lock_of(workspace.project_dir(project.project.id)).pid == os.getpid()

    @pytest.mark.parametrize("lock", ["foreign_host", "corrupt"])
    def test_foreign_or_corrupt_lock_is_never_taken(self, windows, workspace, project, ask, lock):
        d = workspace.project_dir(project.project.id)
        if lock == "corrupt":
            (d / ".lock").write_text("{broken", encoding="utf-8")
        else:
            _write_other_lock(d, pid=_dead_pid(), host="another-machine.example")
        before = (d / ".lock").read_bytes()
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        assert len(ask.holders) == 1 and w._is_read_only()
        assert (ask.holders[0] is None) == (lock == "corrupt")
        w.close()
        assert (d / ".lock").read_bytes() == before

    def test_open_anyway_takes_the_lock(self, windows, workspace, project, ask, dialogs):
        d = workspace.project_dir(project.project.id)
        _write_other_lock(d, pid=os.getppid())
        ask.choice = "anyway"
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        assert not w._is_read_only()
        assert _lock_of(d).token == workspace.held_locks[project.project.id] != "other-token"
        log_operation(w.project, "test.change")
        w.save()
        assert json.loads(_bytes(workspace, project))["operation_log"][-1]["operation"] == "test.change"

    def test_cancel_keeps_the_current_project(self, windows, workspace, project, ask):
        other = workspace.create_project("Busy elsewhere")
        _write_other_lock(workspace.project_dir(other.project.id), pid=os.getppid())
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        ask.choice = "cancel"
        w.open_project_by_id(other.project.id)
        assert w.project.project.id == project.project.id and not w._is_read_only()
        assert project.project.id in workspace.held_locks
        assert other.project.id not in workspace.held_locks
        assert _lock_of(workspace.project_dir(other.project.id)).token == "other-token"

    def test_lost_lock_switches_to_read_only(self, windows, workspace, project, dialogs):
        d = workspace.project_dir(project.project.id)
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        _write_other_lock(d, pid=os.getppid())       # another window chose "Open anyway"
        before = _bytes(workspace, project)
        log_operation(w.project, "test.change")
        w.save()
        assert _bytes(workspace, project) == before
        assert w._is_read_only() and w.windowTitle().endswith("— read-only")
        assert [d_[:2] for d_ in dialogs] == [("warning", "Project opened elsewhere")]
        w.save()                                      # no second notice
        assert len(dialogs) == 1
        w.close()
        assert _lock_of(d).token == "other-token"     # never removes the new holder's lock

    def test_archiving_a_project_edited_elsewhere_is_refused(self, windows, workspace, project,
                                                             dialogs):
        other = workspace.create_project("Elsewhere")
        _write_other_lock(workspace.project_dir(other.project.id), pid=os.getppid())
        before = _bytes(workspace, other)
        w = windows(workspace)
        w._archive_from_projects(other.project.id, True)
        assert _bytes(workspace, other) == before
        assert dialogs and "being edited in another Pycrograph" in dialogs[0][2]

    def test_real_dialog_defaults_to_read_only_and_shows_local_time(self, windows, workspace,
                                                                    project, monkeypatch):
        _write_other_lock(workspace.project_dir(project.project.id), pid=os.getppid())
        seen = {}

        def fake_exec(box):
            seen["text"], seen["default"] = box.text(), box.defaultButton().text()
            seen["buttons"] = [b.text() for b in box.buttons()]
            return 0
        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box.defaultButton())
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        assert w._is_read_only()
        assert seen["default"] == "Open read-only"
        assert {"Open read-only", "Open anyway"} <= set(seen["buttons"])
        assert project_lock.format_lock_time("2026-01-01T00:00:00+00:00") in seen["text"]
        assert "Pycrograph 9.9.9" in seen["text"]


# ===========================================================================
# Reading never needs the lock
# ===========================================================================
class TestReadersIgnoreTheLock:

    def test_lock_is_never_archived(self, windows, workspace, project, tmp_path):
        w = windows(workspace)
        w.open_project_by_id(project.project.id)
        w.add_to_figure(list(w.project.images))
        assert (workspace.project_dir(project.project.id) / ".lock").exists()
        for level in ("light", "complete"):
            res = w.export_archive(tmp_path / f"{level}.pcarchive", level, include_exports=True)
            with zipfile.ZipFile(res.path) as zf:
                assert not any(Path(n).name == ".lock" for n in zf.namelist())
            q = A.import_archive(workspace, res.path).project
            assert not (workspace.project_dir(q.project.id) / ".lock").exists()

    def test_plugin_and_listing_ignore_the_lock(self, app, workspace, project, tmp_path,
                                                monkeypatch):
        from pycrograph.export import export_figure
        from pycrograph.figures import FigureEditor, create_figure
        from pycrograph.render import PanelRenderer
        monkeypatch.setenv(paths.WORKSPACE_ENV, str(workspace.root))
        fig = create_figure(project, "F")
        FigureEditor(project, fig, lambda: None).add_images(list(project.images.values()))
        renderer = PanelRenderer(lambda r: workspace.resolve_image_path(project, r))
        result = export_figure(workspace, project, fig, renderer, tmp_path / "f.pdf",
                               ExportSettings("pdf"))
        renderer.close()
        workspace.save_project(project)
        d = workspace.project_dir(project.project.id)
        for content in (None, "{corrupt"):
            if content is None:
                _write_other_lock(d, pid=os.getppid())
            else:
                (d / ".lock").write_text(content, encoding="utf-8")
            before = (d / ".lock").read_bytes()
            r = plugin.identify(str(result.files[0]))
            assert r["project_id"] == project.project.id
            assert not any(Path(m["path"]).name == ".lock" for m in r["members"])
            assert [m.id for m in workspace.list_projects()] == [project.project.id]
            assert workspace.load_project(project.project.id).project.id == project.project.id
            assert (d / ".lock").read_bytes() == before
