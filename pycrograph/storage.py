# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Workspace I/O and the image library: import BY REFERENCE, hashing, relink.

Layout (default ~/.pycrograph)::

    projects/<project_id>/project.json
    projects/<project_id>/images/        (only for "Copy into project")
    cache/thumbnails/<image_id>.png      (disposable)

Import by reference
-------------------
Microscopy files are too large to copy (a time-lapse can be ~1.1 GB),
so register_image() records the absolute path, size, mtime, reader + version
and parsed metadata, and leaves sha256=None. The SHA-256 is computed
afterwards (chunked, in a background thread in the UI) and stored with
set_image_hash(). check_image() tells whether a reference is still valid;
relink_image() / relink_by_prefix() repair broken paths (drive renamed,
remounted as "/Volumes/LaCie 1", ...) and only ever accept a file whose
SHA-256 equals the recorded one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from . import __version__, project_lock
from .display import default_channel_settings
from .jsonsafe import dumps_strict, finite_or_none
from .logchain import append_log_entry
from .migrations import migrate_project_dict
from .models import (
    PROJECT_FORMAT_VERSION,
    Dataset,
    ImageDisplay,
    ImageRef,
    OperationLogEntry,
    Project,
    ProjectMeta,
)
from .readers import UnsupportedImageError, open_image, open_image_ref, recorded_plane_axis

HASH_CHUNK = 4 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------ hashing
class HashCancelled(Exception):
    pass


# SHARED-CORE candidate (from PysternBlot: pysternblot/storage.py sha256_file)
# — chunked; adds progress + cancellation for multi-GB files.
def sha256_file(path: str | Path, progress: Optional[Callable[[int, int], None]] = None,
                cancelled: Optional[Callable[[], bool]] = None) -> str:
    path = Path(path)
    total = path.stat().st_size
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            if cancelled is not None and cancelled():
                raise HashCancelled(str(path))
            h.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)
    return h.hexdigest()


# --------------------------------------------------------------- op log
def log_operation(project: Project, operation: str, *, target_type: Optional[str] = None,
                  target_id: Optional[str] = None, asset_sha256: Optional[str] = None,
                  field: Optional[str] = None, old_value: Any = None, new_value: Any = None,
                  note: Optional[str] = None) -> OperationLogEntry:
    entry = OperationLogEntry(
        timestamp_utc=utc_now(), operation=operation, target_type=target_type,
        target_id=target_id, asset_sha256=asset_sha256, field=field,
        old_value=finite_or_none(old_value), new_value=finite_or_none(new_value), note=note,
    )
    return append_log_entry(project, entry)


# ---------------------------------------------------------------- workspace
class ProjectFormatError(ValueError):
    pass


class ProjectFormatTooNewError(ProjectFormatError):
    """project.json was written by a newer Pycrograph (format_version above
    PROJECT_FORMAT_VERSION): never read as this version's model, never saved over."""


class ProjectReadOnlyError(RuntimeError):
    """A save was refused: the project is open read-only in this window."""


def needs_newer_app(meta: ProjectMeta) -> bool:
    """True for a listed project written by a newer Pycrograph (shown, not opened)."""
    return meta.format_version > PROJECT_FORMAT_VERSION


def _raw_format_version(data: Any) -> int:
    try:
        return int(data["project"].get("format_version", 1))
    except (AttributeError, KeyError, TypeError, ValueError):
        return 1


_HEAD_BYTES = 8192
_PROJECT_FIRST = re.compile(r'\s*\{\s*"project"\s*:\s*')


def _unreadable(path: Path, why: str) -> ProjectFormatError:
    return ProjectFormatError(
        f"{path}: the project file on disk cannot be read ({why}). It was not saved over, "
        "so that nothing in it is lost. Move it aside or restore it, then save again.")


def _meta_format_version(meta: Any, path: Path) -> int:
    """format_version of a "project" block, as load_project_file reads it
    (absent: format 1); anything else is refused, never guessed."""
    if not isinstance(meta, dict):
        raise _unreadable(path, 'no "project" object')
    version = meta.get("format_version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise _unreadable(path, f"format_version is {version!r}")
    return version


def _on_disk_format_version(path: Path) -> Optional[int]:
    """format_version of the project.json about to be saved over; None only
    if there is no file. Raises ProjectFormatError when the file exists but
    cannot be read as a project, so a save never overwrites what it could
    not check.

    Fast path, run before every save: when the file starts with the
    "project" object (as save_project writes it) and that object ends within
    the first 8 KB, only it is decoded. Otherwise (other key order, a large
    field before format_version) the whole file is parsed."""
    try:
        with open(path, "rb") as f:
            head = f.read(_HEAD_BYTES)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _unreadable(path, str(exc)) from exc
    text = head.decode("utf-8", errors="ignore")   # a cut multi-byte char is at the end
    m = _PROJECT_FIRST.match(text)
    if m:
        try:
            meta, _end = json.JSONDecoder().raw_decode(text, m.end())
        except ValueError:
            meta = None                            # the object runs past the head
        if isinstance(meta, dict):
            return _meta_format_version(meta, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _unreadable(path, f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _unreadable(path, "not a JSON object")
    return _meta_format_version(data.get("project"), path)


def _newer_meta(path: Path) -> Optional[ProjectMeta]:
    """The listing row of a project written by a newer Pycrograph, read from
    the raw JSON (its model may not validate here); None if it isn't one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data["project"]
        version = _raw_format_version(data)
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(meta, dict) or version <= PROJECT_FORMAT_VERSION:
        return None
    name = meta.get("name")

    def text(key: str) -> str:
        v = meta.get(key)
        return v if isinstance(v, str) else ""
    return ProjectMeta(
        id=path.parent.name,
        name=name if isinstance(name, str) and name.strip() else path.parent.name,
        created_utc=text("created_utc"), modified_utc=text("modified_utc") or None,
        app_version=text("app_version"), format_version=version,
        is_archived=meta.get("is_archived") is True,
    )


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        self.projects_dir = self.root / "projects"
        self.cache_dir = self.root / "cache"
        self.thumbnails_dir = self.cache_dir / "thumbnails"
        # Projects open read-only in this window: save_project refuses them.
        self.read_only_ids: set[str] = set()
        # Edit locks this process holds (project_lock.py): project id -> token.
        self.held_locks: dict[str, str] = {}

    def ensure(self) -> None:
        for d in (self.projects_dir, self.thumbnails_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- paths -----------------------------------------------------------
    def project_dir(self, project_id: str) -> Path:
        if not project_id or not project_id.replace("-", "").isalnum():
            raise ValueError(f"invalid project id {project_id!r}")
        return self.projects_dir / project_id

    def project_json(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def thumbnail_path(self, image_id: str) -> Path:
        return self.thumbnails_dir / f"{image_id}.png"

    def resolve_image_path(self, project: Project, ref: ImageRef) -> Path:
        if ref.stored_relpath:
            return self.project_dir(project.project.id) / ref.stored_relpath
        return Path(ref.path)

    # ---- projects --------------------------------------------------------
    def create_project(self, name: str) -> Project:
        pid = new_id()
        now = utc_now()
        project = Project(project=ProjectMeta(id=pid, name=name.strip() or "Untitled",
                                              created_utc=now, modified_utc=now,
                                              app_version=__version__))
        log_operation(project, "project.create", target_type="project", target_id=pid,
                      new_value=project.project.name)
        self.save_project(project)
        return project

    def save_project(self, project: Project) -> Path:
        """Atomic write: temp file in the same directory, then os.replace.

        Refused (nothing written) for a project open read-only in this window
        (ProjectReadOnlyError), over a project.json written meanwhile by a
        newer Pycrograph (ProjectFormatTooNewError), e.g. after "Open anyway",
        and over one that cannot be read as a project (ProjectFormatError):
        its format is never guessed."""
        if project.project.id in self.read_only_ids:
            raise ProjectReadOnlyError(
                f"Project “{project.project.name}” is open read-only; it was not saved.")
        d = self.project_dir(project.project.id)
        target = d / "project.json"
        on_disk = _on_disk_format_version(target)
        if on_disk is not None and on_disk > PROJECT_FORMAT_VERSION:
            raise ProjectFormatTooNewError(
                f"{target}: project format {on_disk} was written by a newer Pycrograph "
                f"(this one writes {PROJECT_FORMAT_VERSION}); not saved over it.")
        project.project.modified_utc = utc_now()
        project.project.app_version = __version__
        d.mkdir(parents=True, exist_ok=True)
        text = dumps_strict(project.model_dump(mode="json"), indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(prefix=".project.", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target

    def load_project(self, project_id: str) -> Project:
        return load_project_file(self.project_json(project_id))

    def list_projects(self, include_archived: bool = False) -> list[ProjectMeta]:
        """Every readable project, most recently modified first. A project
        written by a newer Pycrograph is listed too, from its raw JSON
        (needs_newer_app(meta)); opening it is refused."""
        out = []
        if not self.projects_dir.is_dir():
            return out
        for p in sorted(self.projects_dir.glob("*/project.json")):
            try:
                meta = load_project_file(p).project
            except ProjectFormatTooNewError:
                meta = _newer_meta(p)
                if meta is None:
                    continue
            except (ValueError, OSError):
                continue
            if include_archived or not meta.is_archived:
                out.append(meta)
        out.sort(key=lambda m: m.modified_utc or m.created_utc, reverse=True)
        return out

    def set_project_archived(self, project: Project, archived: bool) -> None:
        """Soft-hide / restore (never deletes)."""
        old = project.project.is_archived
        project.project.is_archived = bool(archived)
        log_operation(project, "project.archive" if archived else "project.restore",
                      target_type="project", target_id=project.project.id,
                      field="project.is_archived", old_value=old, new_value=bool(archived))
        self.save_project(project)

    # ---- edit locks (project_lock.py); reading never needs one ----------
    def acquire_lock(self, project_id: str) -> project_lock.LockResult:
        result = project_lock.acquire(self.project_dir(project_id),
                                      frozenset(self.held_locks.values()))
        if result.acquired:
            self.held_locks[project_id] = result.token
        return result

    def force_lock(self, project_id: str) -> str:
        """"Open anyway": replace whatever lock is there with ours."""
        token = project_lock.force(self.project_dir(project_id))
        self.held_locks[project_id] = token
        return token

    def release_lock(self, project_id: str) -> None:
        token = self.held_locks.pop(project_id, None)
        project_dir = self.project_dir(project_id)
        if project_lock.release(project_dir, token):
            # A folder left holding nothing but our lock is removed, so it
            # does not look like a project. os.rmdir only, never rmtree: it
            # fails (ignored) as soon as any file or folder is left in it.
            try:
                project_dir.rmdir()
            except OSError:
                pass

    def release_all_locks(self) -> None:
        for project_id in list(self.held_locks):
            self.release_lock(project_id)

    def still_holds_lock(self, project_id: str) -> bool:
        token = self.held_locks.get(project_id)
        return bool(token) and project_lock.still_ours(self.project_dir(project_id), token)

    def locked_by_other(self, project_id: str) -> Optional[project_lock.LockInfo]:
        """The live holder of another process's or window's lock on this
        project, else None (no lock, our own, or a stale one). An unreadable
        lock is reported as held, by an unknown holder."""
        path = project_lock.lock_path(self.project_dir(project_id))
        if not path.exists():
            return None
        holder = project_lock.LockInfo.from_file(path)
        own = frozenset(self.held_locks.values())
        if holder is None:
            return project_lock.LockInfo("unknown host", 0, "", "", "unknown time")
        if holder.token in own or project_lock.is_stale(holder, own):
            return None
        return holder


def load_project_file(path: str | Path) -> Project:
    """Read, migrate (older format versions) and validate a project.json.

    A migration happens in memory only and is recorded as one
    ``project.migrate`` log entry; the file is rewritten (new format + that
    entry) at the next save, so the file and its log never disagree."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectFormatError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("project"), dict):
        raise ProjectFormatError(f"{path}: not a Pycrograph project")
    version = int(data["project"].get("format_version", 1))
    if version > PROJECT_FORMAT_VERSION:
        raise ProjectFormatTooNewError(
            f"{path}: project format {version} is newer than this "
            f"Pycrograph ({PROJECT_FORMAT_VERSION}); please update the app."
        )
    data, old_version = migrate_project_dict(data)
    project = Project.model_validate(data)
    if old_version < PROJECT_FORMAT_VERSION:
        log_operation(project, "project.migrate", target_type="project",
                      target_id=project.project.id, field="project.format_version",
                      old_value=old_version, new_value=PROJECT_FORMAT_VERSION)
    return project


# ------------------------------------------------------------------ datasets
def create_dataset(project: Project, name: str) -> Dataset:
    ds = Dataset(id=new_id(), name=name.strip() or "Dataset")
    project.datasets.append(ds)
    log_operation(project, "dataset.create", target_type="dataset", target_id=ds.id,
                  new_value=ds.name)
    return ds


def rename_dataset(project: Project, dataset_id: str, name: str) -> None:
    ds = _dataset(project, dataset_id)
    old = ds.name
    ds.name = name.strip() or old
    log_operation(project, "dataset.rename", target_type="dataset", target_id=ds.id,
                  field="name", old_value=old, new_value=ds.name)


def set_dataset_hidden(project: Project, dataset_id: str, hidden: bool) -> None:
    ds = _dataset(project, dataset_id)
    old = ds.hidden
    ds.hidden = bool(hidden)
    log_operation(project, "dataset.hide" if hidden else "dataset.restore",
                  target_type="dataset", target_id=ds.id, field="hidden",
                  old_value=old, new_value=ds.hidden)


def add_image_to_dataset(project: Project, image_id: str, dataset_id: str) -> None:
    ds = _dataset(project, dataset_id)
    if image_id not in project.images:
        raise KeyError(image_id)
    if image_id not in ds.image_ids:
        ds.image_ids.append(image_id)
        log_operation(project, "dataset.add_image", target_type="dataset", target_id=ds.id,
                      new_value=image_id)


def remove_image_from_dataset(project: Project, image_id: str, dataset_id: str) -> None:
    """Removes the link only; the ImageRef stays in the project."""
    ds = _dataset(project, dataset_id)
    if image_id in ds.image_ids:
        ds.image_ids.remove(image_id)
        log_operation(project, "dataset.remove_image", target_type="dataset",
                      target_id=ds.id, old_value=image_id)


def _dataset(project: Project, dataset_id: str) -> Dataset:
    ds = project.dataset(dataset_id)
    if ds is None:
        raise KeyError(f"no dataset {dataset_id}")
    return ds


# -------------------------------------------------------------------- import
def _canonical(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def find_image_by_path(project: Project, path: str | Path) -> Optional[ImageRef]:
    key = _canonical(path)
    return next((r for r in project.images.values() if r.path == key), None)


def register_image(project: Project, path: str | Path, dataset_id: Optional[str] = None) -> ImageRef:
    """Register a file by reference. Opens it once with its reader (validates
    the format, parses metadata, computes default display settings from the
    first plane of each channel) and returns the ImageRef with sha256=None.

    Registering a path that is already in the project returns the existing
    reference (and adds it to dataset_id) — one raw file, many datasets."""
    path = Path(_canonical(path))
    existing = find_image_by_path(project, path)
    if existing is not None:
        if dataset_id:
            add_image_to_dataset(project, existing.id, dataset_id)
        return existing

    st = path.stat()
    with open_image(path) as src:
        meta = src.metadata
        s = meta.shape
        planes = [src.get_plane(0, 0, c) for c in range(s.C)]
        channels = default_channel_settings(meta.channels, planes, meta.full_scale)
        reader, reader_version = src.reader_name, src.reader_version

    warnings = []
    if meta.dtype == "uint8":
        warnings.append("8-bit source: reduced dynamic range compared with a 16-bit acquisition.")
    if not meta.calibration.is_calibrated:
        warnings.append("No spatial calibration found: scale bars are unavailable.")

    ref = ImageRef(
        id=new_id(), filename=path.name, path=str(path), original_path=str(path),
        size_bytes=st.st_size, mtime_ns=st.st_mtime_ns, reader=reader,
        reader_version=reader_version, imported_utc=utc_now(), metadata=meta,
        display=ImageDisplay(channels=channels), warnings=warnings,
    )
    project.images[ref.id] = ref
    log_operation(project, "image.register", target_type="image", target_id=ref.id,
                  new_value={"path": ref.path, "size_bytes": ref.size_bytes,
                             "reader": reader, "reader_version": reader_version},
                  note="; ".join(warnings) or None)
    if dataset_id:
        add_image_to_dataset(project, ref.id, dataset_id)
    return ref


def set_image_hash(project: Project, image_id: str, sha256: str,
                   size_bytes: Optional[int] = None, mtime_ns: Optional[int] = None) -> ImageRef:
    """Record the background-computed hash. Refuses to overwrite a different
    recorded hash (that would hide a modified file)."""
    ref = project.images[image_id]
    if ref.sha256 is not None and ref.sha256 != sha256:
        raise ValueError(f"{ref.filename}: recorded hash differs; use check_image/relink")
    if ref.sha256 == sha256:
        return ref
    ref.sha256 = sha256
    if size_bytes is not None:
        ref.size_bytes = size_bytes
    if mtime_ns is not None:
        ref.mtime_ns = mtime_ns
    dup = [r.filename for r in project.images.values() if r.id != ref.id and r.sha256 == sha256]
    log_operation(project, "image.hash", target_type="image", target_id=ref.id,
                  asset_sha256=sha256,
                  note=f"identical content to: {', '.join(dup)}" if dup else None)
    return ref


class RefStatus(str, Enum):
    OK = "ok"                       # fingerprint unchanged (or hash verified)
    PENDING_HASH = "pending_hash"   # registered, hash not computed yet
    MISSING = "missing"             # path does not exist (drive unplugged, moved)
    CHANGED = "changed"             # size/mtime differ; needs re-hash to decide
    MODIFIED = "modified"           # re-hashed and content differs


def expected_sha256(ref: ImageRef) -> Optional[str]:
    """SHA-256 of the file this ref READS: the original raw file, or the
    light-archive extract (ref.sha256 then still names the original)."""
    return ref.extract.sha256 if ref.extract is not None else ref.sha256


def check_image(workspace: Workspace, project: Project, ref: ImageRef,
                verify_hash: bool = False) -> RefStatus:
    """Cheap check by default (exists + size + mtime). verify_hash re-reads
    the file and compares SHA-256 (slow for big files). For a light-archive
    extract the extract's own hash is compared."""
    path = workspace.resolve_image_path(project, ref)
    if not path.is_file():
        return RefStatus.MISSING
    if ref.sha256 is None:
        return RefStatus.PENDING_HASH
    st = path.stat()
    same_fp = st.st_size == ref.size_bytes and st.st_mtime_ns == ref.mtime_ns
    if not verify_hash:
        return RefStatus.OK if same_fp else RefStatus.CHANGED
    return RefStatus.OK if sha256_file(path) == expected_sha256(ref) else RefStatus.MODIFIED


class RelinkError(ValueError):
    pass


def relink_image(project: Project, image_id: str, new_path: str | Path,
                 sha256: Optional[str] = None, allow_unverified: bool = False) -> ImageRef:
    """Point an image at a new location after verifying its content.

    sha256 may be passed when already computed (background thread). The new
    file must hash to the recorded SHA-256. Without a recorded hash (the drive
    was unplugged before hashing finished) there is nothing to verify
    against: refused unless allow_unverified, in which case the new file's
    hash is recorded and the log entry says the relink is unverified."""
    ref = project.images[image_id]
    new_path = Path(_canonical(new_path))
    if not new_path.is_file():
        raise RelinkError(f"{new_path} does not exist")
    if ref.sha256 is None:
        if not allow_unverified:
            raise RelinkError(f"{ref.filename}: no recorded hash yet; cannot verify a relink.")
        st = new_path.stat()
        digest = sha256 or sha256_file(new_path)
        old = ref.path
        ref.path, ref.sha256 = str(new_path), digest
        ref.size_bytes, ref.mtime_ns = st.st_size, st.st_mtime_ns
        log_operation(project, "image.relink", target_type="image", target_id=ref.id,
                      asset_sha256=digest, field="path", old_value=old, new_value=ref.path,
                      note="UNVERIFIED: no hash had been recorded before the relink")
        return ref
    st = new_path.stat()
    if st.st_size != ref.size_bytes:
        raise RelinkError(f"{new_path.name}: size {st.st_size} != recorded {ref.size_bytes}")
    digest = sha256 or sha256_file(new_path)
    if digest != ref.sha256:
        raise RelinkError(f"{new_path.name}: content differs from the imported file (SHA-256 mismatch)")
    old = ref.path
    ref.path = str(new_path)
    ref.mtime_ns = st.st_mtime_ns
    log_operation(project, "image.relink", target_type="image", target_id=ref.id,
                  asset_sha256=ref.sha256, field="path", old_value=old, new_value=ref.path)
    return ref


def relink_by_prefix(workspace: Workspace, project: Project, old_prefix: str,
                     new_prefix: str) -> list[str]:
    """After one image was relinked from old_prefix to new_prefix (e.g.
    '/Volumes/LaCie' -> '/Volumes/LaCie 1'), try the same substitution for
    every other missing image. Returns ids of images relinked."""
    done = []
    old_prefix = old_prefix.rstrip("/")
    new_prefix = new_prefix.rstrip("/")
    for ref in project.images.values():
        if ref.stored_relpath or not ref.path.startswith(old_prefix + "/"):
            continue
        if check_image(workspace, project, ref) != RefStatus.MISSING:
            continue
        candidate = Path(new_prefix + ref.path[len(old_prefix):])
        if not candidate.is_file():
            continue
        try:
            relink_image(project, ref.id, candidate)      # always hash-verified
        except RelinkError:
            continue
        done.append(ref.id)
    return done


def common_prefix_change(old_path: str, new_path: str) -> tuple[str, str]:
    """Longest differing leading parts with identical tails:
    ('/Volumes/LaCie/a b/c.tif', '/Volumes/LaCie 1/a b/c.tif')
    -> ('/Volumes/LaCie', '/Volumes/LaCie 1')."""
    a, b = Path(old_path).parts, Path(new_path).parts
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n] == b[-1 - n]:
        n += 1
    pa = Path(*a[: len(a) - n]) if len(a) - n else Path("/")
    pb = Path(*b[: len(b) - n]) if len(b) - n else Path("/")
    return str(pa), str(pb)


def copy_into_project(workspace: Workspace, project: Project, image_id: str) -> ImageRef:
    """Copy the raw file into projects/<id>/images/ (self-contained project),
    byte-exact with post-copy SHA-256 verification (PysternBlot rule)."""
    ref = project.images[image_id]
    if ref.stored_relpath:
        return ref
    src = Path(ref.path)
    digest = ref.sha256 or sha256_file(src)
    if ref.sha256 is None:
        set_image_hash(project, image_id, digest)
    elif sha256_file(src) != ref.sha256:
        raise RelinkError(f"{ref.filename}: source changed since import; not copying")
    rel = Path("images") / f"{digest[:12]}_{ref.filename}"
    dest = workspace.project_dir(project.project.id) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copy2(src, tmp)                     # keeps the acquisition file's dates
    if sha256_file(tmp) != digest:
        tmp.unlink(missing_ok=True)
        raise OSError(f"{ref.filename}: copy verification failed (SHA-256 mismatch)")
    os.replace(tmp, dest)
    ref.stored_relpath = rel.as_posix()
    # From now on the fingerprint describes the stored copy (what is read).
    st = dest.stat()
    ref.size_bytes, ref.mtime_ns = st.st_size, st.st_mtime_ns
    log_operation(project, "image.copy_into_project", target_type="image", target_id=ref.id,
                  asset_sha256=digest, field="stored_relpath", new_value=ref.stored_relpath)
    return ref


# ------------------------------------------------------------- plane axis
class PlaneAxisError(ValueError):
    """set_plane_axis refused (message meant for the user)."""


def figures_using_image(project: Project, image_id: str) -> list[str]:
    """Names of the figures with a panel (or inset) showing the image."""
    return [f.name for f in project.figures if any(p.image_id == image_id for p in f.panels)]


def set_plane_axis(workspace: Workspace, project: Project, ref: ImageRef, axis: str) -> bool:
    """Map the image's single generic plane axis (tifffile "I") to T or Z.

    Refused (PlaneAxisError) for a light-archive extract, a file without
    such an axis or with a single plane, a source changed since import, and
    an image used by any figure panel or inset: panels store T / Z indices
    and projections that would silently point at other planes (remapping
    them is not supported). Otherwise the file is re-read with the
    override, `ref.metadata` replaced (shape, Z step, time offsets), the
    library position reset to T1 Z1 and one `image.set` entry logged
    (field plane_axis). The raw file and its SHA-256 are untouched. Returns
    False when `axis` is already the mapping (nothing done). The caller
    saves."""
    if axis not in ("T", "Z"):
        raise ValueError(f"axis must be 'T' or 'Z', not {axis!r}")
    if ref.extract is not None:
        raise PlaneAxisError(
            f"{ref.filename} was restored from a light archive: only the archived planes "
            "exist, under the T / Z indices they were archived with.")
    current = recorded_plane_axis(ref)
    s = ref.metadata.shape
    if s.T * s.Z <= 1:
        raise PlaneAxisError(f"{ref.filename} has a single plane: nothing to map to T or Z.")
    if current is None:
        raise PlaneAxisError(
            f"{ref.filename}: its T and Z come from the file itself "
            f"({ref.reader}); only a generic plane axis can be switched.")
    used = figures_using_image(project, ref.id)
    if used:
        raise PlaneAxisError(
            f"{ref.filename} is used in {'figure' if len(used) == 1 else 'figures'} "
            f"{', '.join(f'“{n}”' for n in used)}. Panels and insets keep T / Z positions "
            "and projections, which would point at other planes; changing the plane axis "
            "of an image in a figure is not supported. Remove its panels first, or import "
            "the file again into another project.")
    if axis == current:
        return False
    path = workspace.resolve_image_path(project, ref)
    if not path.is_file():
        raise PlaneAxisError(f"{ref.filename}: source file not found ({path}). Relink it first.")
    st = path.stat()
    if (st.st_size, st.st_mtime_ns) != (ref.size_bytes, ref.mtime_ns):
        raise PlaneAxisError(f"{ref.filename}: the file changed since import. Run Check "
                             "Sources first.")
    with open_image(path, plane_axis=axis, plane_axis_reason="user override") as src:
        if getattr(src, "generic_plane_axis", None) is None:
            raise PlaneAxisError(
                f"{ref.filename}: the file has no generic plane axis that can be T or Z.")
        meta, reader_version = src.metadata, src.reader_version
    new = meta.shape
    if (new.T * new.Z, new.C, new.Y, new.X) != (s.T * s.Z, s.C, s.Y, s.X):
        raise PlaneAxisError(f"{ref.filename}: the file no longer matches its recorded shape.")
    ref.metadata = meta
    ref.reader_version = reader_version
    ref.plane_axis_override = axis
    ref.display.t = ref.display.z = 0
    log_operation(project, "image.set", target_type="image", target_id=ref.id,
                  asset_sha256=ref.sha256, field="plane_axis", old_value=current,
                  new_value=axis,
                  note=f"T {new.T} · Z {new.Z}; {meta.raw.get('tiff.axis_inference', '')}")
    return True


# --------------------------------------------------------------- annotations
def set_image_annotation(project: Project, image_id: str, field: str, value: Any) -> None:
    """tags / key_values / rating / channel_stains, logged like PB's
    antibody-name edits."""
    if field not in ("tags", "key_values", "rating", "channel_stains"):
        raise ValueError(field)
    ref = project.images[image_id]
    old_dump = _jsonable(getattr(ref, field))
    # Validate through pydantic (ranges, types) before touching the ref.
    validated = ImageRef.model_validate({**ref.model_dump(), field: value})
    new = getattr(validated, field)
    setattr(ref, field, new)
    if _jsonable(new) != old_dump:
        log_operation(project, "image.annotate", target_type="image", target_id=image_id,
                      asset_sha256=ref.sha256, field=field,
                      old_value=old_dump, new_value=_jsonable(new))


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): val for k, val in v.items()}
    if isinstance(v, list):
        return list(v)
    return v


def search_images(project: Project, text: str = "", min_rating: int = 0,
                  tag: Optional[str] = None) -> list[ImageRef]:
    """Filter by free text (filename, tags, key/values, channel names/stains),
    rating and tag."""
    needle = text.strip().lower()
    out = []
    for ref in project.images.values():
        if ref.rating < min_rating:
            continue
        if tag and tag not in ref.tags:
            continue
        if needle:
            hay = " ".join([ref.filename, *ref.tags, *ref.key_values.keys(),
                            *ref.key_values.values(), *ref.channel_stains.values(),
                            *(c.name for c in ref.metadata.channels)]).lower()
            if needle not in hay:
                continue
        out.append(ref)
    return out


# ---------------------------------------------------------------- thumbnail
def write_thumbnail(workspace: Workspace, project: Project, ref: ImageRef) -> Optional[Path]:
    """Render plane (t, z) of the current display settings to a small PNG."""
    from PIL import Image

    from .display import apply_orientation, make_thumbnail, render_composite

    path = workspace.resolve_image_path(project, ref)
    try:
        with open_image_ref(path, ref) as src:
            d = ref.display
            planes = [src.get_plane(min(d.t, ref.metadata.shape.T - 1),
                                    min(d.z, ref.metadata.shape.Z - 1), cs.index)
                      for cs in d.channels]
            rgb = render_composite(planes, d.channels)
    except (OSError, UnsupportedImageError, ValueError):
        return None
    rgb = apply_orientation(make_thumbnail(rgb), d.rotation_deg, d.flip_horizontal,
                            d.flip_vertical)
    out = workspace.thumbnail_path(ref.id)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgb), "RGB").save(out)
    return out
