# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Project archives (``.pcarchive``) — PysternBlot's ``.pbarchive`` system
adapted to microscopy (handoff §8).

Format (zip, format_version 1)::

    pcarchive/manifest.json            format, level, app version, created time,
                                       SHA-256 + size of EVERY other member, SHA-256
                                       of the project state (project.json bytes, as
                                       PB format_version 2), log-chain status,
                                       per-image record
    pcarchive/project/project.json     the project, image refs relinked (below)
    pcarchive/project/images/<id>.<ext>          complete: original raw files
    pcarchive/project/extracts/<id>.ome.tif      light: what the figures use
    pcarchive/exports/<n>_<file>       optional: figure / source exports + sidecars
    pcarchive/reports/integrity.json|html        optional: integrity report

``pcarchive/project/`` mirrors the project directory, so an imported image
ref's ``stored_relpath`` is valid as archived.

Levels
------
- **light**: only what the figures use. Per image (all its panels pooled):
  the T, Z (a projection's whole range) and visible-channel planes shown,
  cut to the union of the rendered crop bounds plus MARGIN_PX, as an
  uncompressed OME-TIFF (axes TZCYX) with calibration, channel names and a
  private tag holding the original metadata and the source file's SHA-256.
  The archived ref keeps the original metadata and SHA-256 and gains an
  ``extract`` record (readers/extract.py presents it with the original
  geometry, so crops, shapes and indices are unchanged). Images no figure
  uses are not archived (their refs stay, unresolvable after import).
- **complete**: every image file of the project, byte-exact.

Every source is verified before archiving (missing -> refused; modified ->
refused unless ``allow_modified``, then logged); a hash never recorded is
computed and recorded first (logged). Image members are STORED (raw data
does not compress; the ratio limit then flags bombs), stream-copied in
CHUNK pieces with the SHA-256 computed on the way, and re-hashed from the
written member ("verified after copy"). The archived project.json carries
one ``project.archive`` log entry describing the relink, so its state and
its log agree; the live project gets one ``export.archive`` entry (archive
+ manifest SHA-256) — the caller saves.

Import treats the archive as untrusted (PB rules, made strict: ANY problem
refuses the whole import): member count, safe relative names (no absolute
path, no backslash, no "." / "..", safe components), no symlinks, no
duplicates, every member listed in the manifest and vice versa, size caps
(declared AND streamed), compression-ratio cap on binary members, total
cap; every member is streamed to a staging directory OUTSIDE the workspace
and hashed; then the project-state hash, the log chain and the image
records are checked. Only then is a NEW project created (new id, never an
overwrite), refs pointing at the extracted files, one
``project.import_archive`` log entry with the manifest hash. A failure at any
point leaves nothing in the workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable, Optional

import numpy as np
import tifffile

from . import __version__
from .jsonsafe import dumps_strict
from .labels import channel_name
from .layout import UNIT_TO_UM
from .logchain import verify_log_chain
from .models import ExtractInfo, ImageRef, Project
from .readers import open_image_ref
from .readers.extract import EXTRACT_TAG
from .render import crop_bounds
from .storage import (
    RefStatus, Workspace, check_image, load_project_file, log_operation, new_id, set_image_hash,
    sha256_file, utc_now,
)

FORMAT = "pcarchive"
FORMAT_VERSION = 1
ROOT = "pcarchive"
MANIFEST = f"{ROOT}/manifest.json"
PROJECT_DIR = f"{ROOT}/project"
PROJECT_JSON = f"{PROJECT_DIR}/project.json"
LEVELS = ("light", "complete")
CHUNK = 4 * 1024 * 1024
MARGIN_PX = 32

Progress = Callable[[int, int], None]          # (done bytes, total bytes)
Cancelled = Callable[[], bool]


class ArchiveError(ValueError):
    """Refused: the archive (or the project to archive) is not acceptable."""


class ArchiveCancelled(Exception):
    pass


@dataclass
class ArchiveResult:
    path: Path
    level: str
    archive_sha256: str
    manifest_sha256: str
    project_sha256: str
    n_members: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    project: Project
    level: str
    archive_sha256: str
    manifest_sha256: str
    n_members_verified: int


# ---------------------------------------------------------------- safety
# SHARED-CORE candidate (from PysternBlot: pysternblot/storage.py _safe_component,
# _safe_member_name, _resolve_contained) — copied unchanged.
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_component(value: str) -> bool:
    if not value or len(value) > 64:
        return False
    if value in (".", ".."):
        return False
    return bool(_SAFE_COMPONENT_RE.match(value))


def _safe_member_name(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name:
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


def _resolve_contained(base_dir: Path, *components: str) -> Optional[Path]:
    dest = base_dir.joinpath(*components).resolve()
    if not dest.is_relative_to(base_dir.resolve()):
        return None
    return dest


# SHARED-CORE candidate (from PysternBlot: pysternblot/storage.py .pbarchive resource limits)
# — adapted: a complete archive holds whole acquisitions (a MetaMorph time-lapse is
# ~1.1 GB), so the per-member and total caps are larger; members are streamed to disk,
# never held in memory, so the caps bound disk use, not RAM.
MAX_MEMBER_UNCOMPRESSED_BYTES = 16 * 1024 ** 3
MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 ** 3
MAX_ARCHIVE_MEMBERS = 10_000
MAX_COMPRESSION_RATIO = 1000          # binary members (images are stored, ~1:1)
# JSON / HTML / SVG members: legitimately compressible (a long operation log
# compresses well — PB measured ~200:1 for 4,000 entries), so a separate,
# lower cap than the binary one, plus a hard size cap.
MAX_JSON_COMPRESSION_RATIO = 200
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
DISK_SPACE_MARGIN = 1.05              # free space needed: members' total size + 5 %


def _safe_filename(name: str) -> str:
    """A member-safe version of a file name (spaces etc. -> "_")."""
    s = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or "file"
    return s[:64]


# ---------------------------------------------------------------- streaming
def _copy_stream(src: BinaryIO, dst: BinaryIO, hasher, *, limit: Optional[int] = None,
                 progress: Optional[Callable[[int], None]] = None,
                 cancelled: Optional[Cancelled] = None) -> int:
    """Copy in CHUNK pieces, hashing on the way; never the whole file in
    memory. Raises ArchiveError past `limit`, ArchiveCancelled on cancel."""
    total = 0
    while True:
        if cancelled is not None and cancelled():
            raise ArchiveCancelled()
        chunk = src.read(CHUNK)
        if not chunk:
            return total
        total += len(chunk)
        if limit is not None and total > limit:
            raise ArchiveError(f"member larger than its {limit}-byte limit")
        hasher.update(chunk)
        dst.write(chunk)
        if progress is not None:
            progress(len(chunk))


def _hash_member(zf: zipfile.ZipFile, name: str) -> str:
    h = hashlib.sha256()
    with zf.open(name) as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ light
def _ome_physical(ref: ImageRef) -> dict[str, Any]:
    cal = ref.metadata.calibration
    k = UNIT_TO_UM.get(cal.unit)
    out: dict[str, Any] = {}
    if cal.is_calibrated and k is not None:
        out.update({"PhysicalSizeX": cal.pixel_size_x * k, "PhysicalSizeXUnit": "µm",
                    "PhysicalSizeY": cal.pixel_size_y * k, "PhysicalSizeYUnit": "µm"})
    if cal.z_step and k is not None:
        out.update({"PhysicalSizeZ": cal.z_step * k, "PhysicalSizeZUnit": "µm"})
    return out


def used_planes(project: Project, image_id: str, margin: int = MARGIN_PX
                ) -> Optional[tuple[list[int], list[int], list[int], tuple[int, int, int, int]]]:
    """(T list, Z list, C list, (x0, y0, x1, y1)) the figures show of an
    image: all its panels pooled, region = union of rendered crop bounds +
    margin (clamped). None if no panel uses it."""
    ref = project.images[image_id]
    s = ref.metadata.shape
    ts, zs, cs = set(), set(), set()
    box = None
    for fig in project.figures:
        for p in fig.panels:
            if p.image_id != image_id:
                continue
            ts.add(min(p.t, s.T - 1))
            proj = p.z_projection
            zs.update(range(proj.z_start, min(proj.z_end, s.Z - 1) + 1) if proj else
                      [min(p.z, s.Z - 1)])
            vis = [c.index for c in p.channels if c.visible and c.index < s.C]
            cs.update(vis or range(s.C))
            b = crop_bounds(p.crop, s.X, s.Y)
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                         max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return None
    x0, y0 = max(0, box[0] - margin), max(0, box[1] - margin)
    x1, y1 = min(s.X, box[2] + margin), min(s.Y, box[3] + margin)
    return sorted(ts), sorted(zs), sorted(cs), (x0, y0, x1, y1)


def write_extract(workspace: Workspace, project: Project, ref: ImageRef, dest: Path,
                  margin: int = MARGIN_PX) -> Optional[ExtractInfo]:
    """Write the light extract of `ref` to `dest` (one plane read at a time)."""
    used = used_planes(project, ref.id, margin)
    if used is None:
        return None
    ts, zs, cs, (x0, y0, x1, y1) = used
    m = ref.metadata
    data = np.empty((len(ts), len(zs), len(cs), y1 - y0, x1 - x0), dtype=np.dtype(m.dtype))
    with open_image_ref(workspace.resolve_image_path(project, ref), ref) as src:
        for i, t in enumerate(ts):
            for j, z in enumerate(zs):
                for k, c in enumerate(cs):
                    data[i, j, k] = src.get_plane(t, z, c)[y0:y1, x0:x1]
    info = {"generator": f"Pycrograph {__version__}", "source_filename": ref.filename,
            "source_path": ref.path, "source_sha256": ref.sha256,
            "source_size_bytes": ref.size_bytes, "reader": ref.reader,
            "reader_version": ref.reader_version, "origin_x": x0, "origin_y": y0,
            "t": ts, "z": zs, "c": cs, "original_metadata": m.model_dump(mode="json")}
    tifffile.imwrite(dest, data, ome=True, photometric="minisblack", compression=None,
                     metadata={"axes": "TZCYX", "Name": ref.filename,
                               "Description": f"Pycrograph light extract of {ref.filename} "
                                              f"(source SHA-256 {ref.sha256})",
                               "Channel": {"Name": [channel_name(ref, c) for c in cs]},
                               **_ome_physical(ref)},
                     extratags=[(EXTRACT_TAG, "s", 0, json.dumps(info), True)])
    return ExtractInfo(sha256=sha256_file(dest), origin_x=x0, origin_y=y0, width=x1 - x0,
                       height=y1 - y0, t=ts, z=zs, c=cs, source_sha256=ref.sha256,
                       source_size_bytes=ref.size_bytes, margin_px=margin)


# ------------------------------------------------------------------ export
def _verify_sources(workspace: Workspace, project: Project, image_ids: list[str],
                    allow_modified: bool, progress: Optional[Progress]) -> list[str]:
    warnings = []
    for iid in image_ids:
        ref = project.images[iid]
        path = workspace.resolve_image_path(project, ref)
        if not path.is_file():
            raise ArchiveError(f"{ref.filename}: source file missing ({path})")
        if ref.sha256 is None:
            set_image_hash(project, iid, sha256_file(path))          # logged
            warnings.append(f"{ref.filename}: SHA-256 was not recorded; computed and recorded")
            continue
        status = check_image(workspace, project, ref, verify_hash=True)
        if status == RefStatus.MODIFIED:
            msg = f"{ref.filename}: source MODIFIED since import (SHA-256 differs)"
            if not allow_modified:
                raise ArchiveError(msg)
            warnings.append(msg + " — archived anyway")
    return warnings


def export_archive(workspace: Workspace, project: Project, dest: str | Path, level: str = "light",
                   include_exports: bool = False, include_report: bool = False,
                   allow_modified: bool = False, progress: Optional[Progress] = None,
                   cancelled: Optional[Cancelled] = None) -> ArchiveResult:
    if level not in LEVELS:
        raise ValueError(f"unknown archive level {level!r}")
    dest = Path(dest)
    if level == "light":
        ids = list(dict.fromkeys(p.image_id for f in project.figures for p in f.panels
                                 if p.image_id in project.images))
    else:
        ids = list(project.images)
    warnings = _verify_sources(workspace, project, ids, allow_modified, progress)

    staging = Path(tempfile.mkdtemp(prefix="pcarchive-out-"))
    tmp = dest.with_name(dest.name + ".part")
    try:
        # ---- the archived project and its image members
        arch = project.model_copy(deep=True)
        members: list[tuple[str, Path, Optional[str]]] = []   # (name, file, expected sha)
        images: dict[str, dict[str, Any]] = {}
        relinked: dict[str, str] = {}
        for iid in ids:
            ref = arch.images[iid]
            src_path = workspace.resolve_image_path(project, project.images[iid])
            if level == "complete":
                ext = _safe_filename(Path(ref.filename).suffix.lower().lstrip(".")) or "bin"
                rel = f"images/{iid}.{ext}"
                members.append((f"{PROJECT_DIR}/{rel}", src_path, ref.sha256))
                images[iid] = {"kind": "original", "member": f"{PROJECT_DIR}/{rel}",
                               "source_sha256": ref.sha256, "size_bytes": ref.size_bytes}
                ref.stored_relpath, ref.extract = rel, None
            else:
                rel = f"extracts/{iid}.ome.tif"
                out = staging / f"{iid}.ome.tif"
                info = write_extract(workspace, project, project.images[iid], out)
                members.append((f"{PROJECT_DIR}/{rel}", out, info.sha256))
                images[iid] = {"kind": "extract", "member": f"{PROJECT_DIR}/{rel}",
                               "source_sha256": ref.sha256, **info.model_dump()}
                ref.stored_relpath, ref.extract = rel, info
                ref.size_bytes = out.stat().st_size
            relinked[iid] = rel
        for iid in project.images:
            if iid not in images:
                images[iid] = {"kind": "not_archived",
                               "reason": "not used by any figure (light archive)"}
        log_operation(arch, "project.archive", target_type="project", target_id=arch.project.id,
                      field="images.stored_relpath", new_value={"level": level, "relinked": relinked},
                      note=f"state as archived ({level}); image refs point inside the archive"
                           + ("; " + "; ".join(warnings) if warnings else ""))
        project_bytes = dumps_strict(arch.model_dump(mode="json"), indent=2,
                                     ensure_ascii=False).encode("utf-8")
        project_sha = hashlib.sha256(project_bytes).hexdigest()

        # ---- optional extras
        extras: list[tuple[str, Path]] = []
        if include_exports:
            k = 0
            for e in project.operation_log:
                if e.operation not in ("export.figure", "export.sources") or not isinstance(e.new_value, dict):
                    continue
                for f in e.new_value.get("files", []) + [e.new_value.get("sidecar") or {}]:
                    p = Path(f.get("path") or "")
                    if p.is_file():
                        k += 1
                        extras.append((f"{ROOT}/exports/{k:03d}_{_safe_filename(p.name)}", p))
        if include_report:
            from .integrity import build_integrity_report, write_integrity_html, write_integrity_json
            report = build_integrity_report(workspace, project)
            write_integrity_json(report, staging / "integrity.json")
            write_integrity_html(report, staging / "integrity.html")
            extras += [(f"{ROOT}/reports/integrity.json", staging / "integrity.json"),
                       (f"{ROOT}/reports/integrity.html", staging / "integrity.html")]

        total = sum(p.stat().st_size for _n, p, _s in members) + sum(p.stat().st_size for _n, p in extras)
        done = [0]

        def tick(n: int) -> None:
            done[0] += n
            if progress is not None:
                progress(done[0], total)

        listing: dict[str, dict[str, Any]] = {}
        member_image = {rec["member"]: iid for iid, rec in images.items() if "member" in rec}
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            zf.writestr(zipfile.ZipInfo(PROJECT_JSON, date_time=(1980, 1, 1, 0, 0, 0)),
                        project_bytes, compress_type=_text_compression(project_bytes))
            listing[PROJECT_JSON] = {"sha256": project_sha, "size": len(project_bytes)}
            for name, path, expected in members + [(n, p, None) for n, p in extras]:
                h = hashlib.sha256()
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = (_text_compression(path.read_bytes()) if _is_text(name)
                                      and path.stat().st_size <= MAX_JSON_BYTES else zipfile.ZIP_STORED)
                with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                    size = _copy_stream(src, dst, h, progress=tick, cancelled=cancelled)
                digest = h.hexdigest()
                if expected is not None and digest != expected:
                    msg = f"{name}: SHA-256 while copying differs from the recorded one"
                    if not allow_modified:
                        raise ArchiveError(msg)
                    warnings.append(msg)
                    images[member_image[name]]["modified_allowed"] = True
                listing[name] = {"sha256": digest, "size": size}
            # verified after copy: re-read every written member
            for name, rec in listing.items():
                if _hash_member(zf, name) != rec["sha256"]:
                    raise ArchiveError(f"{name}: verification after copy failed")
            manifest = {
                "format": FORMAT, "format_version": FORMAT_VERSION, "level": level,
                "app_version": __version__, "created_utc": utc_now(),
                "project_id": project.project.id, "project_name": project.project.name,
                "project_sha256": project_sha, "log_chain": verify_log_chain(arch).model_dump(),
                "members": listing, "images": images,
                "includes": {"exports": include_exports, "report": include_report},
                "warnings": warnings,
            }
            manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            zf.writestr(zipfile.ZipInfo(MANIFEST, date_time=(1980, 1, 1, 0, 0, 0)),
                        manifest_bytes, compress_type=_text_compression(manifest_bytes))
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    archive_sha = sha256_file(dest)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    log_operation(project, "export.archive", target_type="project", target_id=project.project.id,
                  field="archive", new_value={
                      "path": str(dest), "level": level, "archive_sha256": archive_sha,
                      "manifest_sha256": manifest_sha, "project_sha256": project_sha,
                      "n_members": len(listing) + 1, "n_images": len(ids),
                      "includes": manifest["includes"]},
                  note="; ".join(warnings) or None)
    return ArchiveResult(dest, level, archive_sha, manifest_sha, project_sha, len(listing) + 1,
                         warnings)


# ------------------------------------------------------------------ import
def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _check_ratio(info: zipfile.ZipInfo, is_text: bool) -> None:
    limit = MAX_JSON_COMPRESSION_RATIO if is_text else MAX_COMPRESSION_RATIO
    ratio = info.file_size / max(info.compress_size, 1)
    if ratio > limit:
        raise ArchiveError(f"{info.filename}: compression ratio {ratio:.0f}:1 over {limit}:1 "
                           "(decompression bomb?)")


def _is_text(name: str) -> bool:
    return name.endswith((".json", ".html", ".svg"))


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _same_device(a: Path, b: Path) -> bool:
    return a.stat().st_dev == b.stat().st_dev


def _text_compression(data: bytes) -> int:
    """Deflate a text member unless it would come near the import ratio cap
    (a long log compresses very well): then store it, so our own archives
    always pass our own import checks."""
    import zlib
    ratio = len(data) / max(len(zlib.compress(data, 6)), 1)
    return zipfile.ZIP_STORED if ratio > MAX_JSON_COMPRESSION_RATIO * 0.75 else zipfile.ZIP_DEFLATED


def _check_disk_space(staging: Path, workspace_dir: Path, needed: int) -> None:
    """Room for every member, plus 5 %, where they are staged AND in the
    workspace (once if both are on the same filesystem: the final move is
    then a rename)."""
    need = int(needed * DISK_SPACE_MARGIN)
    places = [("staging directory", staging)]
    if not _same_device(staging, workspace_dir):
        places.append(("workspace", workspace_dir))
    for what, path in places:
        free = _free_bytes(path)
        if free < need:
            raise ArchiveError(
                f"not enough free disk space in the {what} ({path}): the archive needs "
                f"{need / 1e9:.2f} GB (members + 5 %), {free / 1e9:.2f} GB free")


def _read_json_member(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise ArchiveError(f"{name}: declared size {info.file_size} exceeds {limit} bytes")
    _check_ratio(info, is_text=True)
    out = bytearray()
    with zf.open(name) as fh:
        while chunk := fh.read(CHUNK):
            out += chunk
            if len(out) > limit:
                raise ArchiveError(f"{name}: larger than its {limit}-byte limit")
    return bytes(out)


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Validate the container and return the manifest (no extraction)."""
    try:
        with zipfile.ZipFile(path) as zf:
            return _check_container(zf)[0]
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a zip archive: {exc}") from exc


def _check_container(zf: zipfile.ZipFile) -> tuple[dict[str, Any], list[zipfile.ZipInfo]]:
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ArchiveError(f"too many members ({len(infos)} > {MAX_ARCHIVE_MEMBERS})")
    seen = set()
    for info in infos:
        name = info.filename
        if not _safe_member_name(name) or not name.startswith(ROOT + "/"):
            raise ArchiveError(f"unsafe member path {name!r}")
        if not all(_safe_component(part) for part in name.split("/")):
            raise ArchiveError(f"unsafe member name {name!r}")
        if info.is_dir() or _is_symlink(info):
            raise ArchiveError(f"unexpected directory or symlink member {name!r}")
        if name in seen:
            raise ArchiveError(f"duplicate member {name!r}")
        seen.add(name)
    if MANIFEST not in seen:
        raise ArchiveError(f"not a .pcarchive: no {MANIFEST}")
    try:
        manifest = json.loads(_read_json_member(zf, MANIFEST, MAX_MANIFEST_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"invalid manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ArchiveError(f"unknown archive format {manifest.get('format')!r}")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ArchiveError(f"unsupported archive version {manifest.get('format_version')!r}")
    if manifest.get("level") not in LEVELS:
        raise ArchiveError(f"unknown archive level {manifest.get('level')!r}")
    listed = manifest.get("members")
    if not isinstance(listed, dict):
        raise ArchiveError("manifest has no member list")
    present = seen - {MANIFEST}
    if set(listed) != present:
        extra, missing = sorted(present - set(listed)), sorted(set(listed) - present)
        raise ArchiveError(f"manifest and archive disagree (unlisted: {extra[:3]}, "
                           f"absent: {missing[:3]})")
    if PROJECT_JSON not in present:
        raise ArchiveError("no project.json in the archive")
    return manifest, [i for i in infos if i.filename != MANIFEST]


def import_archive(workspace: Workspace, path: str | Path, progress: Optional[Progress] = None,
                   cancelled: Optional[Cancelled] = None) -> ImportResult:
    """Verify everything in a staging directory outside the workspace, then
    create a NEW project. Raises ArchiveError (nothing written) on any
    problem."""
    path = Path(path)
    workspace.ensure()
    archive_sha = sha256_file(path)
    staging = Path(tempfile.mkdtemp(prefix="pcarchive-in-"))
    project_dir: Optional[Path] = None
    try:
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"not a zip archive: {exc}") from exc
        with zf:
            manifest, infos = _check_container(zf)
            manifest_sha = hashlib.sha256(
                _read_json_member(zf, MANIFEST, MAX_MANIFEST_BYTES)).hexdigest()
            listed = manifest["members"]
            total_declared = sum(i.file_size for i in infos)
            done = [0]

            def tick(n: int) -> None:
                done[0] += n
                if progress is not None:
                    progress(done[0], total_declared)

            # Every member's limits are checked before anything is extracted.
            for info in infos:
                is_json = _is_text(info.filename)
                limit = MAX_JSON_BYTES if is_json else MAX_MEMBER_UNCOMPRESSED_BYTES
                if info.file_size > limit:
                    raise ArchiveError(f"{info.filename}: declared size {info.file_size} over the "
                                       f"{limit}-byte limit")
                _check_ratio(info, is_json)
            if total_declared > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ArchiveError(f"archive total {total_declared} bytes over the "
                                   f"{MAX_TOTAL_UNCOMPRESSED_BYTES}-byte limit")
            _check_disk_space(staging, workspace.projects_dir, total_declared)
            running = 0
            for info in infos:
                name = info.filename
                is_json = _is_text(name)
                limit = MAX_JSON_BYTES if is_json else MAX_MEMBER_UNCOMPRESSED_BYTES
                rel = name[len(ROOT) + 1:].split("/")
                dest = _resolve_contained(staging, *rel)
                if dest is None:
                    raise ArchiveError(f"{name}: escapes the staging directory")
                dest.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                try:
                    with zf.open(info) as src, open(dest, "wb") as dst:
                        size = _copy_stream(src, dst, h, limit=min(limit, MAX_TOTAL_UNCOMPRESSED_BYTES
                                                                   - running),
                                            progress=tick, cancelled=cancelled)
                except zipfile.BadZipFile as exc:
                    raise ArchiveError(f"{name}: corrupt member ({exc})") from exc
                running += size
                rec = listed.get(name) or {}
                if h.hexdigest() != rec.get("sha256") or size != rec.get("size"):
                    raise ArchiveError(f"{name}: SHA-256 / size differ from the manifest "
                                       "(tampered or corrupt)")

        # ---- the project state
        pj = staging / "project" / "project.json"
        if sha256_file(pj) != manifest.get("project_sha256"):
            raise ArchiveError("project.json does not match the manifest's project-state hash")
        try:
            project = load_project_file(pj)
        except Exception as exc:
            raise ArchiveError(f"invalid project.json: {exc}") from exc
        chain = verify_log_chain(project)
        if chain.status != "ok":
            raise ArchiveError(f"operation log chain is {chain.status}")
        relinked: dict[str, str] = {}
        for iid, rec in (manifest.get("images") or {}).items():
            ref = project.images.get(iid)
            if ref is None:
                raise ArchiveError(f"manifest image {iid} is not in project.json")
            if rec.get("kind") == "not_archived":
                continue
            member = rec.get("member", "")
            if member not in listed or f"{PROJECT_DIR}/{ref.stored_relpath}" != member:
                raise ArchiveError(f"image {iid}: reference and archive member disagree")
            digest = listed[member]["sha256"]
            if rec["kind"] == "original":
                # A file archived despite a modification is flagged by the
                # exporter (and logged there); anything else must match.
                if ref.extract is not None or (digest != ref.sha256 and not rec.get("modified_allowed")):
                    raise ArchiveError(f"{ref.filename}: archived file is not the recorded original")
            elif rec["kind"] == "extract":
                if ref.extract is None or ref.extract.sha256 != digest:
                    raise ArchiveError(f"{ref.filename}: extract record does not match its file")
            else:
                raise ArchiveError(f"image {iid}: unknown kind {rec.get('kind')!r}")
            relinked[iid] = ref.stored_relpath

        # ---- everything verified: create the NEW project
        old_id, pid = project.project.id, new_id()
        project_dir = workspace.project_dir(pid)
        if project_dir.exists():
            raise ArchiveError("project directory already exists")
        project_dir.mkdir(parents=True)
        for item in (staging / "project").iterdir():
            if item.name != "project.json":
                shutil.move(str(item), str(project_dir / item.name))
        for extra in ("exports", "reports"):                     # kept as files, not in the model
            if (staging / extra).is_dir():
                (project_dir / "archive").mkdir(exist_ok=True)
                shutil.move(str(staging / extra), str(project_dir / "archive" / extra))
        project.project.id = pid
        project.project.is_archived = False
        for iid in relinked:
            ref = project.images[iid]
            st = (project_dir / ref.stored_relpath).stat()
            ref.size_bytes, ref.mtime_ns = st.st_size, st.st_mtime_ns
        log_operation(project, "project.import_archive", target_type="project", target_id=pid,
                      field="archive", new_value={
                          "archive_path": str(path), "archive_sha256": archive_sha,
                          "manifest_sha256": manifest_sha, "project_sha256": manifest["project_sha256"],
                          "level": manifest["level"], "original_project_id": old_id,
                          "app_version_archived": manifest.get("app_version"),
                          "members_verified": len(listed), "relinked": relinked},
                      note="imported as a new project (images relinked to the extracted files)")
        workspace.save_project(project)
        return ImportResult(project, manifest["level"], archive_sha, manifest_sha, len(listed))
    except BaseException:
        if project_dir is not None:
            shutil.rmtree(project_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
