# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
# SHARED-CORE candidate (from PysternBlot: pysternblot/plugin.py)

"""Pyblication plugin (plugin protocol v1).

Pyblication tracks the figures of a manuscript. When a figure exported by
Pycrograph lands in a Pyblication workspace, Pyblication asks this plugin
which project produced it, and which files make up its source, so it can
snapshot them with every revision of the panel.

Registered as an entry point (metadata only; Pycrograph never imports
Pyblication)::

    [project.entry-points."pyblication.plugins"]
    pycrograph = "pycrograph.plugin:describe"

The protocol is specified in Pyblication's docs/plugin-protocol.md; see
docs/concepts/pyblication-plugin.md here.

Guarantees:

* No Qt: this module imports only the standard library, paths.py and (for
  the log-chain status) Pycrograph's pydantic models, migrations and log
  chain, so Pyblication's headless CLI can load it. It never imports
  storage, export, provenance or render (export imports Qt).
* Read-only: identify() reads project.json files and the export's sidecar
  and hashes files; it never writes anywhere and ignores edit locks.
  open_project() only launches the app.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator, Optional

from . import __version__
from .paths import WORKSPACE_ENV, default_workspace

PROTOCOL = 1
EXTENSIONS = [".pdf", ".svg", ".png", ".tif", ".tiff"]   # export.FORMATS (+ ".tif")
_CHUNK = 4 << 20
_HEX = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def describe() -> dict[str, Any]:
    return {
        "name": "pycrograph",
        "version": __version__,
        "protocol": PROTOCOL,
        "app_name": "Pycrograph",
        "extensions": list(EXTENSIONS),
        "identify": identify,
        "open_project": open_project,
    }


# -- helpers ------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _hex(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _HEX.match(value) else None


def _when(timestamp: Any) -> datetime:
    """A log timestamp as an aware datetime; unparseable ones sort first."""
    try:
        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def safe_name(name: str) -> str:
    """One path part valid on Windows and POSIX (tree member names)."""
    name = _UNSAFE.sub("_", name).rstrip(" .")
    if not name or name in (".", ".."):
        return "_"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name


def _read_projects(workspace: Path) -> Iterator[tuple[Path, dict]]:
    """(project.json path, parsed dict) for every readable project. The
    .lock next to it is ignored: reading never needs it."""
    projects_dir = workspace / "projects"
    if not projects_dir.is_dir():
        return
    for path in sorted(projects_dir.glob("*/project.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            yield path, data


def _export_entries(data: dict, digest: str) -> Iterator[tuple[int, dict]]:
    """(log index, entry) of every export.figure entry listing a file with
    this SHA-256 (a multi-page SVG / PNG / TIFF export lists one per page)."""
    log = data.get("operation_log")
    if not isinstance(log, list):
        return
    for index, entry in enumerate(log):
        if not isinstance(entry, dict) or entry.get("operation") != "export.figure":
            continue
        value = entry.get("new_value")
        files = value.get("files") if isinstance(value, dict) else None
        if isinstance(files, list) and any(
                isinstance(f, dict) and f.get("sha256") == digest for f in files):
            yield index, entry


def _meta(data: dict) -> dict:
    meta = data.get("project")
    return meta if isinstance(meta, dict) else {}


def _project_id(path: Path, data: dict) -> str:
    pid = _meta(data).get("id")
    return pid if isinstance(pid, str) and pid else path.parent.name


def _project_name(data: dict, fallback: str) -> str:
    name = _meta(data).get("name")
    return name if isinstance(name, str) and name.strip() else fallback


def _figure(data: dict, figure_id: Any) -> dict:
    figures = data.get("figures")
    for f in figures if isinstance(figures, list) else []:
        if isinstance(f, dict) and f.get("id") == figure_id:
            return f
    return {}


def _log_chain(data: dict) -> dict[str, Any]:
    """Status of the hash chain, as logchain.verify_log_chain computes it."""
    try:
        from .logchain import verify_log_chain
        from .migrations import migrate_project_dict
        from .models import Project

        migrated, _old = migrate_project_dict(json.loads(json.dumps(data)))
        result = verify_log_chain(Project.model_validate(migrated))
    except Exception:  # an invalid or newer project still identifies its export
        return {"status": "unreadable"}
    return {
        "status": result.status,
        "n_entries": result.n_entries,
        "n_chained": result.n_chained,
        "first_broken_index": result.first_broken_index,
    }


# -- sidecar and raw members ---------------------------------------------------


def _sidecar(export_path: Path, value: dict) -> tuple[Optional[Path], Optional[dict]]:
    """The export's <export>.provenance.json, if found with the SHA-256 the
    log entry recorded: the logged path first, then next to export_path (a
    copy in a Pyblication workspace). (path, parsed) or (None, None)."""
    side = value.get("sidecar")
    if not isinstance(side, dict):
        return None, None
    expected = _hex(side.get("sha256"))
    if expected is None:
        return None, None
    candidates = []
    if isinstance(side.get("path"), str) and side["path"]:
        candidates.append(Path(side["path"]))
    candidates.append(export_path.with_name(export_path.name + ".provenance.json"))
    for c in candidates:
        try:
            if not c.is_file() or _sha256(c) != expected:
                continue
            parsed = json.loads(c.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        return c, parsed if isinstance(parsed, dict) else None
    return None, None


def _resolve(project_dir: Path, path: Any, stored_relpath: Any) -> Optional[Path]:
    """Like Workspace.resolve_image_path: a stored copy or light-archive
    extract (stored_relpath, inside the project folder), else the raw path."""
    if isinstance(stored_relpath, str) and stored_relpath:
        return project_dir / stored_relpath
    if isinstance(path, str) and path:
        return Path(path)
    return None


def _raw_member(image_id: str, path: Path, sha: Optional[str]) -> dict[str, Any]:
    """images/<image_id>/<name of the file read>: the raw file itself, a
    stored copy ("<sha12>_<filename>") or a light-archive extract
    ("<image_id>.ome.tif"). sha256 only when one is recorded."""
    member = {
        "path": str(path.absolute()),
        "role": "raw",
        "name": f"images/{safe_name(image_id)}/{safe_name(path.name)}",
    }
    if sha:
        member["sha256"] = sha
    return member


def _raw_from_sidecar(project_dir: Path, sidecar: dict) -> Optional[list[dict]]:
    """Raw members as recorded at export time: each panel record's image
    (id, path, stored copy or extract, the hash of the file read). None if
    the sidecar has no usable panel records."""
    panels = sidecar.get("panels")
    if not isinstance(panels, list):
        return None
    out: dict[str, dict] = {}
    for rec in panels:
        if not isinstance(rec, dict) or not isinstance(rec.get("image_id"), str):
            continue
        iid = rec["image_id"]
        img = rec.get("image")
        if iid in out or not isinstance(img, dict):
            continue
        path = _resolve(project_dir, img.get("path"), img.get("stored_relpath"))
        if path is None:
            continue
        extract = rec.get("extract")
        # The hash recorded in the project (never one computed at export): a
        # file changed since import is then reported by Pyblication. For a
        # light-archive extract, only the extract is read; the original's hash
        # would never match it.
        if isinstance(extract, dict):
            sha = _hex(extract.get("extract_sha256"))
        else:
            sha = _hex(img.get("sha256_recorded"))
        out[iid] = _raw_member(iid, path, sha)
    return list(out.values()) if out or panels == [] else None


def _raw_from_figure(project_dir: Path, data: dict, figure: dict) -> list[dict]:
    """Raw members from the figure's current panels and the project's image
    references (no valid sidecar)."""
    images = data.get("images") if isinstance(data.get("images"), dict) else {}
    panels = figure.get("panels")
    out: dict[str, dict] = {}
    for p in panels if isinstance(panels, list) else []:
        iid = p.get("image_id") if isinstance(p, dict) else None
        ref = images.get(iid) if isinstance(iid, str) else None
        if iid in out or not isinstance(ref, dict):
            continue
        path = _resolve(project_dir, ref.get("path"), ref.get("stored_relpath"))
        if path is None:
            continue
        extract = ref.get("extract")
        sha = (_hex(extract.get("sha256")) if isinstance(extract, dict)
               else _hex(ref.get("sha256")))
        out[iid] = _raw_member(iid, path, sha)
    return list(out.values())


# -- identify -------------------------------------------------------------------


def identify(export_path: str) -> Optional[dict[str, Any]]:
    """The Pycrograph project whose operation log records this export.

    Searches workspace/projects/*/project.json for an ``export.figure``
    entry listing a file whose SHA-256 is this file's. None if the file
    isn't a Pycrograph figure export of this workspace. Read-only.
    """
    export = Path(export_path)
    try:
        digest = _sha256(export)
    except OSError:
        return None
    matches = []  # (when, index, project id, path, data, entry)
    for path, data in _read_projects(default_workspace()):
        pid = _project_id(path, data)
        for index, entry in _export_entries(data, digest):
            matches.append((_when(entry.get("timestamp_utc")), index, pid, path, data, entry))
    if not matches:
        return None
    # Most recent entry; ties: later in its log, then project id.
    matches.sort(key=lambda m: (m[0], m[1], m[2]))
    _, _, pid, path, data, entry = matches[-1]
    others = sorted({m[2] for m in matches} - {pid})

    value = entry.get("new_value") or {}
    figure_id = entry.get("target_id")
    figure = _figure(data, figure_id)
    project_dir = path.parent
    sidecar_path, sidecar = _sidecar(export, value)
    raw = _raw_from_sidecar(project_dir, sidecar) if sidecar is not None else None
    raw_from = "sidecar" if raw is not None else "current_figure"
    if raw is None:
        raw = _raw_from_figure(project_dir, data, figure)

    side = value.get("sidecar") if isinstance(value.get("sidecar"), dict) else {}
    files = value.get("files") if isinstance(value.get("files"), list) else []
    figure_name = figure.get("name")
    if not isinstance(figure_name, str) and isinstance(sidecar, dict):
        figure_name = (sidecar.get("figure") or {}).get("name")
    provenance: dict[str, Any] = {
        "project_id": pid,
        "project_name": _project_name(data, pid),
        "figure": {"id": figure_id, "name": figure_name if isinstance(figure_name, str) else None},
        "export": {
            "timestamp_utc": entry.get("timestamp_utc"),
            "format": value.get("format"),
            "files": [{"path": f.get("path"), "sha256": f.get("sha256")}
                      for f in files if isinstance(f, dict)],
            "sidecar_sha256": side.get("sha256"),
            "entry_hash": entry.get("entry_hash"),
            "prev_hash": entry.get("prev_hash"),
        },
        "raw_members_from": raw_from,
        "log_chain": _log_chain(data),
        "pycrograph_version": __version__,
    }
    if others:
        provenance["also_logged_in"] = others

    members = [{"path": str(path.absolute()), "role": "project", "name": "project.json"}]
    if sidecar_path is not None:
        members.append({
            "path": str(sidecar_path.absolute()),
            "role": "sidecar",
            "name": f"sidecar/{safe_name(sidecar_path.name)}",
            "sha256": side["sha256"],
        })
    members += raw
    return {
        "project_id": pid,
        "project_name": provenance["project_name"],
        "members": members,
        "provenance": provenance,
    }


# -- open_project -------------------------------------------------------------------


def _command(project_id: str) -> list[str]:
    return [sys.executable, "-m", "pycrograph", "--project", project_id]


def _workspace_of(project_path: str) -> Optional[str]:
    """…/projects/<id>/project.json -> the workspace folder, else None."""
    for cls in (Path, PureWindowsPath, PurePosixPath):
        p = cls(project_path)
        if p.name == "project.json" and p.parent.parent.name == "projects":
            return str(p.parent.parent.parent)
    return None


def open_project(project_id: str, project_path: Optional[str]) -> None:
    """Launch Pycrograph on the project, as a separate process (no Qt in the
    caller's process). The workspace is the one holding project_path
    (…/projects/<id>/project.json), else the default one."""
    env = dict(os.environ)
    workspace = _workspace_of(project_path) if project_path else None
    if workspace:
        env[WORKSPACE_ENV] = workspace
    subprocess.Popen(
        _command(project_id),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
