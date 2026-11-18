# Pycrograph
# SHARED-CORE candidate (from PysternBlot: pysternblot/integrity.py) — adapted, see docstring.
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Integrity report (JSON), adapted from PysternBlot's integrity.py — same philosophy and log-chain section, reduced to
what Pycrograph has in session 1: per-image source verification, import
warnings, display settings with non-linear (gamma != 1) flags. The HTML
report, exports section and per-panel records come with the figure canvas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .jsonsafe import dumps_strict
from .logchain import verify_log_chain
from .models import Project
from .storage import RefStatus, Workspace, check_image, utc_now


def _image_record(workspace: Workspace, project: Project, ref, verify_hashes: bool) -> dict[str, Any]:
    status = check_image(workspace, project, ref, verify_hash=verify_hashes and ref.sha256 is not None)
    nonlinear = [cs.index for cs in ref.display.channels if abs(cs.gamma - 1.0) > 1e-9]
    flags = []
    if status != RefStatus.OK:
        flags.append(f"source_{status.value}")
    if nonlinear:
        flags.append("nonlinear_gamma")
    if ref.metadata.dtype == "uint8":
        flags.append("8bit_source")
    m = ref.metadata
    return {
        "id": ref.id,
        "filename": ref.filename,
        "path": str(workspace.resolve_image_path(project, ref)),
        "original_path": ref.original_path,
        "stored_in_project": ref.stored_relpath is not None,
        "sha256": ref.sha256,
        "source_status": status.value,
        "hash_verified_now": bool(verify_hashes and ref.sha256 is not None),
        "size_bytes": ref.size_bytes,
        "reader": ref.reader,
        "reader_version": ref.reader_version,
        "shape_tzcyx": list(m.shape.as_tuple()),
        "dtype": m.dtype,
        "bit_depth": m.bit_depth,
        "calibration": m.calibration.model_dump(),
        "channels": [
            {**info.model_dump(), "stain": ref.channel_stains.get(info.index, ""),
             "display": next((cs.model_dump() for cs in ref.display.channels
                              if cs.index == info.index), None)}
            for info in m.channels
        ],
        "view": {"t": ref.display.t, "z": ref.display.z,
                 "rotation_deg": ref.display.rotation_deg,
                 "flip_horizontal": ref.display.flip_horizontal,
                 "flip_vertical": ref.display.flip_vertical},
        "nonlinear_channels": nonlinear,
        "warnings": list(ref.warnings),
        "flags": flags,
    }


def build_integrity_report(workspace: Workspace, project: Project,
                           verify_hashes: bool = False) -> dict[str, Any]:
    chain = verify_log_chain(project)
    images = [_image_record(workspace, project, r, verify_hashes) for r in project.images.values()]
    return {
        "report_type": "pycrograph.integrity",
        "report_version": 1,
        "generated_utc": utc_now(),
        "app": {"name": "Pycrograph", "version": __version__},
        "project": project.project.model_dump(),
        "log_chain": chain.model_dump(),
        "n_operations": len(project.operation_log),
        "images": images,
        "summary": {
            "n_images": len(images),
            "n_sources_not_ok": sum(1 for i in images if i["source_status"] != "ok"),
            "n_nonlinear_images": sum(1 for i in images if i["nonlinear_channels"]),
            "n_8bit_sources": sum(1 for i in images if i["dtype"] == "uint8"),
        },
    }


def write_integrity_json(report: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.write_text(dumps_strict(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
