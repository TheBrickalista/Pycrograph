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
Microscopy files are too large to copy (a MetaMorph time-lapse is ~1.1 GB),
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
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from . import __version__
from .display import default_channel_settings
from .jsonsafe import dumps_strict, finite_or_none
from .logchain import append_log_entry
from .models import (
    PROJECT_FORMAT_VERSION,
    Dataset,
    ImageDisplay,
    ImageRef,
    OperationLogEntry,
    Project,
    ProjectMeta,
)
from .readers import UnsupportedImageError, open_image

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


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        self.projects_dir = self.root / "projects"
        self.cache_dir = self.root / "cache"
        self.thumbnails_dir = self.cache_dir / "thumbnails"

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
        """Atomic write: temp file in the same directory, then os.replace."""
        project.project.modified_utc = utc_now()
        project.project.app_version = __version__
        d = self.project_dir(project.project.id)
        d.mkdir(parents=True, exist_ok=True)
        target = d / "project.json"
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
        out = []
        if not self.projects_dir.is_dir():
            return out
        for p in sorted(self.projects_dir.glob("*/project.json")):
            try:
                meta = load_project_file(p).project
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


def load_project_file(path: str | Path) -> Project:
    text = Path(path).read_text(encoding="utf-8")
    project = Project.model_validate_json(text)
    if project.project.format_version > PROJECT_FORMAT_VERSION:
        raise ProjectFormatError(
            f"{path}: project format {project.project.format_version} is newer than this "
            f"Pycrograph ({PROJECT_FORMAT_VERSION}); please update the app."
        )
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


def check_image(workspace: Workspace, project: Project, ref: ImageRef,
                verify_hash: bool = False) -> RefStatus:
    """Cheap check by default (exists + size + mtime). verify_hash re-reads
    the file and compares SHA-256 (slow for big files)."""
    path = workspace.resolve_image_path(project, ref)
    if not path.is_file():
        return RefStatus.MISSING
    if ref.sha256 is None:
        return RefStatus.PENDING_HASH
    st = path.stat()
    same_fp = st.st_size == ref.size_bytes and st.st_mtime_ns == ref.mtime_ns
    if not verify_hash:
        return RefStatus.OK if same_fp else RefStatus.CHANGED
    return RefStatus.OK if sha256_file(path) == ref.sha256 else RefStatus.MODIFIED


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
        with open_image(path) as src:
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
