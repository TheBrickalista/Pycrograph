# Pycrograph
# SHARED-CORE candidate (from PysternBlot: pysternblot/integrity.py) — adapted, see docstring.
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Integrity report (JSON + HTML), adapted from PysternBlot's integrity.py —
same philosophy, log-chain section and HTML style: per-image source
verification, import warnings, display settings with non-linear
(gamma != 1) flags, and every export recorded in the log (figure and source
data), each written file re-checked against the SHA-256 logged at export.
Per-panel provenance lives in each export's sidecar (provenance.py).
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .jsonsafe import dumps_strict
from .logchain import verify_log_chain
from .models import Project
from .storage import RefStatus, Workspace, check_image, sha256_file, utc_now


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


def _exports(project: Project) -> list[dict[str, Any]]:
    """Every export in the log; each written file re-checked now."""
    out = []
    for e in project.operation_log:
        if e.operation not in ("export.figure", "export.sources") or not isinstance(e.new_value, dict):
            continue
        files = []
        for f in e.new_value.get("files", []) + [e.new_value.get("sidecar") or {}]:
            if not f.get("path"):
                continue
            path = Path(f["path"])
            status = "missing" if not path.is_file() else (
                "ok" if sha256_file(path) == f.get("sha256") else "modified")
            files.append({**f, "status": status})
        out.append({"timestamp_utc": e.timestamp_utc, "operation": e.operation,
                    "figure_id": e.target_id, "format": e.new_value.get("format"),
                    "files": files, "n_consistency_warnings":
                        len(e.new_value.get("consistency_warnings") or []),
                    "note": e.note})
    return out


def build_integrity_report(workspace: Workspace, project: Project,
                           verify_hashes: bool = False) -> dict[str, Any]:
    chain = verify_log_chain(project)
    images = [_image_record(workspace, project, r, verify_hashes) for r in project.images.values()]
    exports = _exports(project)
    return {
        "report_type": "pycrograph.integrity",
        "report_version": 1,
        "generated_utc": utc_now(),
        "app": {"name": "Pycrograph", "version": __version__},
        "project": project.project.model_dump(),
        "log_chain": chain.model_dump(),
        "n_operations": len(project.operation_log),
        "images": images,
        "exports": exports,
        "summary": {
            "n_images": len(images),
            "n_sources_not_ok": sum(1 for i in images if i["source_status"] != "ok"),
            "n_nonlinear_images": sum(1 for i in images if i["nonlinear_channels"]),
            "n_8bit_sources": sum(1 for i in images if i["dtype"] == "uint8"),
            "n_exports": len(exports),
            "n_export_files_not_ok": sum(1 for x in exports for f in x["files"] if f["status"] != "ok"),
        },
    }


def write_integrity_json(report: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.write_text(dumps_strict(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# SHARED-CORE candidate (from PysternBlot: pysternblot/integrity.py write_integrity_html) — same
# page style; content adapted to Pycrograph's report.
_WARN = 'style="background:#fef3c7;color:#92400e;font-weight:bold;"'
_BAD = 'style="background:#fee2e2;color:#991b1b;font-weight:bold;"'


def write_integrity_html(report: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    esc = lambda v: _html.escape("" if v is None else str(v))  # noqa: E731
    chain = report["log_chain"]
    img_rows = []
    for im in report["images"]:
        status = im["source_status"]
        status_cell = (f"<td>{esc(status)}</td>" if status == "ok" else
                       f"<td {_BAD if status in ('missing', 'modified') else _WARN}>{esc(status)}</td>")
        cal = im["calibration"]
        scale = (f"{cal['pixel_size_x']:g} × {cal['pixel_size_y']:g} {esc(cal['unit'])}/px "
                 f"({esc(cal['source'])})" if cal.get("pixel_size_x") else "not calibrated")
        flags = ", ".join(im["flags"])
        img_rows.append(
            f"<tr><td>{esc(im['filename'])}<br><code>{esc(im['path'])}</code></td>"
            f"<td><code>{esc(im['sha256'])}</code></td>{status_cell}"
            f"<td>{esc(im['reader'])} {esc(im['reader_version'])}</td>"
            f"<td>{'×'.join(map(str, im['shape_tzcyx']))} · {esc(im['dtype'])} "
            f"({im['bit_depth']}-bit)</td><td>{scale}</td>"
            f"<td {_WARN if flags else ''}>{esc(flags)}</td></tr>")
    exp_rows = []
    for x in report.get("exports", []):
        files = "<br>".join(
            f"{esc(Path(f['path']).name)} — <code>{esc(f.get('sha256'))}</code> "
            + (f"<b>{esc(f['status'])}</b>" if f["status"] == "ok" else
               f"<span {_BAD}>{esc(f['status'])}</span>") for f in x["files"])
        warn = x["n_consistency_warnings"]
        exp_rows.append(
            f"<tr><td>{esc(x['timestamp_utc'])}</td><td>{esc(x['operation'])}</td>"
            f"<td>{esc(x['format'])}</td><td>{files}</td>"
            f"<td {_WARN if warn else ''}>{warn}</td><td>{esc(x['note'])}</td></tr>")
    chain_cell = (f"<span {_BAD}>{esc(chain.get('status'))}</span>"
                  if chain.get("status") != "ok" else esc(chain.get("status")))
    doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Pycrograph integrity report</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; color: #222; }}
h1, h2 {{ margin-bottom: 0.3em; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ccc; padding: 6px; vertical-align: top; }}
th {{ background: #eee; }}
code {{ font-size: 11px; word-break: break-all; }}
.summary {{ background: #f7f7f7; padding: 12px; border: 1px solid #ddd; }}
</style>
</head>
<body>
<h1>Pycrograph integrity report</h1>
<div class="summary">
<p><strong>Project:</strong> {esc(report["project"]["name"])} (<code>{esc(report["project"]["id"])}</code>)</p>
<p><strong>Generated UTC:</strong> {esc(report["generated_utc"])}</p>
<p><strong>Pycrograph version:</strong> {esc(report["app"]["version"])}</p>
<p><strong>Operation log:</strong> {report["n_operations"]} entries, hash chain {chain_cell}</p>
<p><strong>Summary:</strong> {esc(json.dumps(report["summary"]))}</p>
</div>
<h2>Source images</h2>
<table>
<thead><tr><th>File</th><th>SHA-256 (recorded)</th><th>Status</th><th>Reader</th>
<th>Shape (T×Z×C×Y×X)</th><th>Pixel size</th><th>Flags</th></tr></thead>
<tbody>
{"".join(img_rows)}
</tbody>
</table>
<h2>Exports</h2>
<table>
<thead><tr><th>Time (UTC)</th><th>Operation</th><th>Format</th><th>Files (SHA-256 at export, status now)</th>
<th>Consistency warnings overridden</th><th>Note</th></tr></thead>
<tbody>
{"".join(exp_rows) or '<tr><td colspan="6">No export yet.</td></tr>'}
</tbody>
</table>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return path
