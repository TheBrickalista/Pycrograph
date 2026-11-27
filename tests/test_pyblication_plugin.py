# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Pyblication plugin (protocol v1, pycrograph/plugin.py): which project,
figure and files produced an export; read-only; no Qt; open_project.

The workspace is reached through PYCROGRAPH_WORKSPACE, as Pyblication's
process does."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph import __version__, paths, plugin  # noqa: E402
from pycrograph import archive as A  # noqa: E402
from pycrograph.export import ExportSettings, export_figure  # noqa: E402
from pycrograph.figures import FigureEditor, create_figure  # noqa: E402
from pycrograph.render import PanelRenderer  # noqa: E402
from pycrograph.storage import (  # noqa: E402
    create_dataset, register_image, set_image_hash, sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ws(app, workspace, monkeypatch):
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(workspace.root))
    return workspace


def _export(workspace, project, figure, path, fmt="pdf"):
    renderer = PanelRenderer(lambda r: workspace.resolve_image_path(project, r))
    try:
        result = export_figure(workspace, project, figure, renderer, path, ExportSettings(fmt))
    finally:
        renderer.close()
    workspace.save_project(project)
    return result


@pytest.fixture
def project(ws, mm_tiff, stk_tc, stk_z):
    """A project with three hashed images; figure "Fig 1" shows the first two."""
    p = ws.create_project("Plugin")
    ds = create_dataset(p, "A")
    refs = [register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z)]
    for r in refs:
        set_image_hash(p, r.id, sha256_file(r.path))
    fig = create_figure(p, "Fig 1")
    FigureEditor(p, fig, lambda: ws.save_project(p)).add_images(refs[:2])
    ws.save_project(p)
    return p, fig, refs


@pytest.fixture
def exported(ws, project, tmp_path):
    p, fig, refs = project
    result = _export(ws, p, fig, tmp_path / "out" / "fig.pdf")
    return p, fig, refs, result


def _raw(r):
    return [m for m in r["members"] if m["role"] == "raw"]


# ------------------------------------------------------------------ identify
def test_identify_members_and_provenance(ws, exported):
    p, fig, (one, two, _zs), result = exported
    r = plugin.identify(str(result.files[0]))
    pid = p.project.id
    assert (r["project_id"], r["project_name"]) == (pid, "Plugin")
    project_m, sidecar_m, *raw = r["members"]
    assert project_m == {"path": str(ws.project_json(pid)), "role": "project",
                         "name": "project.json"}
    assert sidecar_m == {"path": str(result.sidecar), "role": "sidecar",
                         "name": "sidecar/fig.pdf.provenance.json",
                         "sha256": result.sidecar_sha256}
    assert _sha(result.sidecar) == sidecar_m["sha256"]
    assert raw == [
        {"path": one.path, "role": "raw", "name": f"images/{one.id}/{one.filename}",
         "sha256": one.sha256},
        {"path": two.path, "role": "raw", "name": f"images/{two.id}/{two.filename}",
         "sha256": two.sha256},
    ]
    entry = p.operation_log[-1]
    assert entry.operation == "export.figure"
    prov = r["provenance"]
    assert prov["project_id"] == pid and prov["project_name"] == "Plugin"
    assert prov["figure"] == {"id": fig.id, "name": "Fig 1"}
    assert prov["export"] == {
        "timestamp_utc": entry.timestamp_utc, "format": "pdf",
        "files": [{"path": str(result.files[0]), "sha256": _sha(result.files[0])}],
        "sidecar_sha256": result.sidecar_sha256,
        "entry_hash": entry.entry_hash, "prev_hash": entry.prev_hash,
    }
    assert prov["raw_members_from"] == "sidecar"
    assert prov["log_chain"]["status"] == "ok"
    assert prov["log_chain"]["n_entries"] == len(p.operation_log)
    assert prov["pycrograph_version"] == __version__
    assert "also_logged_in" not in prov
    json.dumps(prov)                                               # JSON-safe


def test_unknown_or_missing_file_is_none(ws, exported, tmp_path):
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.4 not an export")
    assert plugin.identify(str(other)) is None
    assert plugin.identify(str(tmp_path / "missing.pdf")) is None


def test_empty_or_absent_workspace(tmp_path, monkeypatch):
    f = tmp_path / "f.pdf"
    f.write_bytes(b"x")
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(tmp_path / "nowhere"))
    assert plugin.identify(str(f)) is None
    (tmp_path / "empty" / "projects").mkdir(parents=True)
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(tmp_path / "empty"))
    assert plugin.identify(str(f)) is None


def test_multi_page_export_identifies_each_page(ws, project, tmp_path):
    p, fig, refs = project
    fig.page.n_pages = 2
    FigureEditor(p, fig, lambda: ws.save_project(p)).add_images([refs[2]], page=1)
    result = _export(ws, p, fig, tmp_path / "out" / "multi.svg", fmt="svg")
    assert len(result.files) == 2
    entry_hash = p.operation_log[-1].entry_hash
    for page in result.files:
        r = plugin.identify(str(page))
        assert r["project_id"] == p.project.id
        assert r["provenance"]["export"]["entry_hash"] == entry_hash
        assert len(r["provenance"]["export"]["files"]) == 2
        assert {m["path"] for m in _raw(r)} == {ref.path for ref in refs}


def _clone_project(ws, p, new_id, export_timestamp):
    """A second project folder whose log records the same export."""
    data = json.loads(ws.project_json(p.project.id).read_text(encoding="utf-8"))
    data["project"]["id"] = new_id
    data["project"]["name"] = f"Copy {new_id}"
    entry = next(e for e in reversed(data["operation_log"]) if e["operation"] == "export.figure")
    entry["timestamp_utc"] = export_timestamp
    d = ws.projects_dir / new_id
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data), encoding="utf-8")


def test_same_export_in_two_projects_picks_most_recent(ws, exported):
    p, _fig, _refs, result = exported
    _clone_project(ws, p, "aaaanewer", "2999-01-01T00:00:00+00:00")
    _clone_project(ws, p, "zzzzolder", "2000-01-01T00:00:00+00:00")
    r = plugin.identify(str(result.files[0]))
    assert r["project_id"] == "aaaanewer" and r["project_name"] == "Copy aaaanewer"
    assert r["provenance"]["also_logged_in"] == sorted([p.project.id, "zzzzolder"])
    assert r["provenance"]["log_chain"]["status"] == "broken"      # its export entry was edited


def test_only_the_exported_figures_images(ws, project, tmp_path):
    p, fig, (one, two, zs) = project
    other = create_figure(p, "Fig 2")
    FigureEditor(p, other, lambda: ws.save_project(p)).add_images([zs])
    ws.save_project(p)
    result = _export(ws, p, fig, tmp_path / "out" / "fig.png", fmt="png")
    assert {m["path"] for m in _raw(plugin.identify(str(result.files[0])))} == {one.path, two.path}


def test_raw_members_are_the_exported_ones_not_the_current_figure(ws, exported):
    p, fig, (one, two, zs), result = exported
    ed = FigureEditor(p, fig, lambda: ws.save_project(p))
    ed.add_images([zs])                                     # added after the export
    fig.panels = [q for q in fig.panels if q.image_id != one.id]   # and one removed
    ws.save_project(p)

    r = plugin.identify(str(result.files[0]))
    assert r["provenance"]["raw_members_from"] == "sidecar"
    assert [m["path"] for m in _raw(r)] == [one.path, two.path]

    result.sidecar.unlink()                                 # no sidecar: the current figure
    r = plugin.identify(str(result.files[0]))
    assert r["provenance"]["raw_members_from"] == "current_figure"
    assert not any(m["role"] == "sidecar" for m in r["members"])
    assert [m["path"] for m in _raw(r)] == [two.path, zs.path]
    assert [m["sha256"] for m in _raw(r)] == [two.sha256, zs.sha256]


def test_sidecar_with_another_hash_is_not_used(ws, exported):
    _p, _fig, (one, two, _zs), result = exported
    text = result.sidecar.read_text(encoding="utf-8")
    result.sidecar.write_text(text.replace(one.filename, "tampered.tif"), encoding="utf-8")
    r = plugin.identify(str(result.files[0]))
    assert r["provenance"]["raw_members_from"] == "current_figure"
    assert not any(m["role"] == "sidecar" for m in r["members"])


def test_sidecar_next_to_a_copied_export(ws, exported, tmp_path):
    """Pyblication copies the export to panels/<id>.pdf: a sidecar copied
    next to it is found when the logged one has gone."""
    _p, _fig, _refs, result = exported
    panels = tmp_path / "pyb" / "panels"
    panels.mkdir(parents=True)
    copy = panels / "p1.pdf"
    shutil.copy2(result.files[0], copy)
    shutil.move(str(result.sidecar), str(panels / "p1.pdf.provenance.json"))
    r = plugin.identify(str(copy))
    side = next(m for m in r["members"] if m["role"] == "sidecar")
    assert side["path"] == str(panels / "p1.pdf.provenance.json")
    assert side["name"] == "sidecar/p1.pdf.provenance.json"
    assert r["provenance"]["raw_members_from"] == "sidecar"


def test_light_archive_extracts_are_the_raw_members(ws, exported, tmp_path):
    p, fig, (one, two, _zs), _result = exported
    res = A.export_archive(ws, p, tmp_path / "light.pcarchive", "light")
    q = A.import_archive(ws, res.path).project
    qfig = next(f for f in q.figures if f.id == fig.id)
    result = _export(ws, q, qfig, tmp_path / "out2" / "from_light.pdf")
    r = plugin.identify(str(result.files[0]))
    assert r["project_id"] == q.project.id
    qdir = ws.project_dir(q.project.id)
    for m, ref in zip(_raw(r), (q.images[one.id], q.images[two.id])):
        assert ref.extract is not None
        assert m["path"] == str(qdir / "extracts" / f"{ref.id}.ome.tif")
        assert m["name"] == f"images/{ref.id}/{ref.id}.ome.tif"
        assert m["sha256"] == ref.extract.sha256 == _sha(m["path"])   # the file read
        assert m["sha256"] != ref.sha256                              # not the absent original
    result.sidecar.unlink()                                           # same through the fallback
    fallback = _raw(plugin.identify(str(result.files[0])))
    assert [m["sha256"] for m in fallback] == [m["sha256"] for m in _raw(r)]


def test_copied_into_project_image(ws, project, tmp_path):
    from pycrograph.storage import copy_into_project
    p, fig, (one, _two, _zs) = project
    copy_into_project(ws, p, one.id)
    ws.save_project(p)
    result = _export(ws, p, fig, tmp_path / "out" / "fig.pdf")
    m = _raw(plugin.identify(str(result.files[0])))[0]
    assert m["path"] == str(ws.project_dir(p.project.id) / one.stored_relpath)
    assert m["sha256"] == one.sha256 == _sha(m["path"])


def test_missing_raw_file_is_still_listed(ws, exported):
    _p, _fig, (one, _two, _zs), result = exported
    Path(one.path).unlink()
    r = plugin.identify(str(result.files[0]))
    m = next(m for m in _raw(r) if m["name"].startswith(f"images/{one.id}/"))
    assert m["path"] == one.path and not Path(m["path"]).exists()
    assert m["sha256"] == one.sha256           # Pyblication reports the missing file


def test_sha_only_when_recorded(ws, mm_tiff, tmp_path):
    p = ws.create_project("Unhashed")
    ref = register_image(p, mm_tiff, create_dataset(p, "A").id)
    fig = create_figure(p, "F")
    FigureEditor(p, fig, lambda: ws.save_project(p)).add_images([ref])
    ws.save_project(p)
    result = _export(ws, p, fig, tmp_path / "out" / "u.pdf")   # hash computed at export only
    assert p.images[ref.id].sha256 is None
    for drop_sidecar in (False, True):
        if drop_sidecar:
            result.sidecar.unlink()
        (m,) = _raw(plugin.identify(str(result.files[0])))
        assert "sha256" not in m


def test_windows_unsafe_names_are_sanitised():
    assert plugin.safe_name('a:b<c>"d|e?f*.tif') == "a_b_c__d_e_f_.tif"
    assert plugin.safe_name("CON.tif") == "_CON.tif"
    assert plugin.safe_name("trailing. ") == "trailing"
    assert plugin.safe_name("..") == "_"


def test_broken_chain_is_reported(ws, exported):
    p, _fig, _refs, result = exported
    path = ws.project_json(p.project.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["operation_log"][0]["note"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    chain = plugin.identify(str(result.files[0]))["provenance"]["log_chain"]
    assert chain["status"] == "broken" and chain["first_broken_index"] == 0


def test_invalid_or_newer_project_still_identifies(ws, exported):
    p, _fig, _refs, result = exported
    path = ws.project_json(p.project.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["project"]["format_version"] = 999                # a newer Pycrograph
    data["images"] = "not a dict"
    path.write_text(json.dumps(data), encoding="utf-8")
    r = plugin.identify(str(result.files[0]))
    assert r["project_id"] == p.project.id
    assert r["provenance"]["log_chain"] == {"status": "unreadable"}


def test_unreadable_project_json_is_skipped(ws, exported):
    _p, _fig, _refs, result = exported
    bad = ws.projects_dir / "broken"
    bad.mkdir()
    (bad / "project.json").write_text("{not json", encoding="utf-8")
    (ws.projects_dir / "binary").mkdir()
    (ws.projects_dir / "binary" / "project.json").write_bytes(b"\xff\xfe\x00")
    assert plugin.identify(str(result.files[0])) is not None


def _snapshot(*roots):
    out = {}
    for root in roots:
        for f in sorted(Path(root).rglob("*")):
            st = f.stat()
            out[str(f)] = (f.is_dir(), st.st_mtime_ns,
                           None if f.is_dir() else hashlib.sha256(f.read_bytes()).hexdigest())
    return out


def test_identify_never_writes(ws, exported, data_dir):
    _p, _fig, _refs, result = exported
    roots = (ws.root, data_dir, result.files[0].parent)
    before = _snapshot(*roots)
    for _ in range(2):
        assert plugin.identify(str(result.files[0])) is not None
    assert plugin.identify(str(data_dir / "Nuc_ER_ER.tif")) is None
    assert _snapshot(*roots) == before


def test_plugin_imports_without_qt(ws, exported):
    """In a fresh interpreter: import the plugin, identify an export; PySide6
    is never imported."""
    _p, _fig, _refs, result = exported
    code = (
        "import json, sys\n"
        "from importlib.metadata import entry_points\n"
        "import pycrograph.plugin as pl\n"
        "d = pl.describe()\n"
        "r = d['identify'](sys.argv[1])\n"
        "print(json.dumps({'qt': sorted(m for m in sys.modules if m.startswith('PySide6')),"
        " 'pid': r and r['project_id']}))\n"
    )
    env = dict(os.environ, **{paths.WORKSPACE_ENV: str(ws.root)})
    env.pop("QT_QPA_PLATFORM", None)
    out = subprocess.run([sys.executable, "-c", code, str(result.files[0])], env=env,
                         capture_output=True, text=True, timeout=60, check=True)
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got == {"qt": [], "pid": _p.project.id}


# -------------------------------------------------------------- open_project
def test_open_project_launches_the_app_only(ws, exported):
    p, _fig, _refs, _result = exported
    with patch.object(plugin.subprocess, "Popen") as popen:
        assert plugin.open_project(p.project.id, str(ws.project_json(p.project.id))) is None
    (cmd,), kwargs = popen.call_args
    assert cmd == [sys.executable, "-m", "pycrograph", "--project", p.project.id]
    assert kwargs["env"][paths.WORKSPACE_ENV] == str(ws.root)
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL


def test_open_project_without_a_workspace_path_keeps_the_environment(monkeypatch):
    monkeypatch.delenv(paths.WORKSPACE_ENV, raising=False)
    for project_path in (None, "/somewhere/else/file.json"):
        with patch.object(plugin.subprocess, "Popen") as popen:
            plugin.open_project("abc123", project_path)
        assert paths.WORKSPACE_ENV not in popen.call_args.kwargs["env"]


def test_open_project_infers_a_windows_workspace():
    assert plugin._workspace_of(r"C:\Users\me\.pycrograph\projects\abc\project.json") \
        == r"C:\Users\me\.pycrograph"


def test_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.WORKSPACE_ENV, str(tmp_path / "w"))
    assert paths.default_workspace() == tmp_path / "w"
    monkeypatch.delenv(paths.WORKSPACE_ENV)
    assert paths.default_workspace() == Path.home() / ".pycrograph"
    from pycrograph import app
    assert app.default_workspace is paths.default_workspace          # moved, still importable
