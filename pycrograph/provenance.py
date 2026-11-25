# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Provenance sidecar of an export (pure): everything needed to say how each
panel of an exported figure was made from which raw file.

Written as ``<file name>.provenance.json`` next to the exported file(s)
(e.g. ``fig.pdf.provenance.json``: exporting the same figure as PDF and SVG
keeps two sidecars). Its
SHA-256 is embedded in the exported files' metadata and logged with the
export, so a figure file, its sidecar and the project log point at each
other. The sidecar does NOT contain the exported files' hashes (they embed
the sidecar's hash: that would be circular); the log entry has both.

Per panel: the source (path, SHA-256 as verified at export, reader), T/Z
or projection, the crop AS RENDERED (pixel bounds clipped to the image, as
cut from the raw array — not the requested crop, which may run off the
image), orientation, every channel setting, flags (non-linear gamma, 8-bit
source, upsampled), labels as resolved text, the scale bar length and the
pixel size it is based on, inset links and rectangles, shapes.
"""

from __future__ import annotations

from typing import Any, Optional

from . import __version__
from . import insets as I
from . import viewport as V
from .figures import lock_members
from .layout import lock_kind
from .labels import channel_name, group_time_unit, plain_text, resolve_label
from .models import Figure, ImageRef, Panel, Project
from .readers import plane_axis_record
from .render import crop_bounds
from .scalebar import ScaleBarUnavailable, bar_geometry

SIDECAR_TYPE = "pycrograph.figure_export"
SOURCES_TYPE = "pycrograph.source_export"
# Figure sidecar (`report_version`):
#   1  consistency_warnings_overridden: [{severity: error|warning|info, code,
#      message, panel_ids}]
#   2  ... [{level: critical|major|minor, code, rule, message, panel_ids}]
#      (consistency.RULES; `code` values unchanged)
SIDECAR_VERSION = 2
# Source-data sidecar (*.sources.provenance.json): unchanged, still 1.
SOURCES_SIDECAR_VERSION = 1


def sidecar_name(name: str, sources: bool = False) -> str:
    """`name` = the exported file's name (figure) or the stem (sources)."""
    return f"{name}.sources.provenance.json" if sources else f"{name}.provenance.json"


EXTRACT_NOTE = ("the original raw file is not present: pixels come from a light-archive extract "
                "(only the archived planes and region); the original's SHA-256 is recorded, "
                "not verified")


def extract_block(ref: ImageRef, extract_verified: Optional[bool] = None) -> dict[str, Any]:
    """What a light-archive extract is, for sidecars and integrity reports.
    The ORIGINAL's SHA-256 is given as recorded, never as verified: only the
    extract file can be checked."""
    x = ref.extract
    return {"kind": "light_archive_extract", "original_present": False, "note": EXTRACT_NOTE,
            "extract_sha256": x.sha256, "extract_sha256_verified_at_export": extract_verified,
            "origin_px": {"x": x.origin_x, "y": x.origin_y},
            "size_px": {"width": x.width, "height": x.height}, "margin_px": x.margin_px,
            "archived_t": list(x.t), "archived_z": list(x.z), "archived_c": list(x.c),
            "source_sha256": x.source_sha256, "source_size_bytes": x.source_size_bytes,
            "source_sha256_verified": False}


def rendered_crop(panel: Panel, ref: ImageRef) -> dict[str, int]:
    s = ref.metadata.shape
    x0, y0, x1, y1 = crop_bounds(panel.crop, s.X, s.Y)
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "width": x1 - x0, "height": y1 - y0}


def _channels(panel: Panel, ref: ImageRef) -> list[dict[str, Any]]:
    out = []
    for c in sorted(panel.channels, key=lambda c: c.index):
        info = next((i for i in ref.metadata.channels if i.index == c.index), None)
        out.append({**c.model_dump(), "name": info.name if info else "",
                    "stain": ref.channel_stains.get(c.index, ""),
                    "label_name": channel_name(ref, c.index),
                    "wavelength_nm": info.wavelength_nm if info else None})
    return out


def _scale_bar(panel: Panel, ref: ImageRef) -> dict[str, Any]:
    bar = panel.scale_bar
    out: dict[str, Any] = {**bar.model_dump()}
    if not bar.visible:
        return out
    try:
        g = bar_geometry(panel, ref)
    except ScaleBarUnavailable as exc:
        out["unavailable"] = str(exc)
        return out
    cal = ref.metadata.calibration
    out.update({"length_on_page_mm": round(g.length_mm, 4), "text": g.text,
                "pixel_size_basis": {"axis": g.axis,
                                     "pixel_size": cal.pixel_size_x if g.axis == "x" else cal.pixel_size_y,
                                     "unit": cal.unit, "source": cal.source},
                "note": g.note})
    return out


def panel_record(project: Project, figure: Figure, panel: Panel, index: int,
                 source: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    ref = project.images.get(panel.image_id)
    rec: dict[str, Any] = {
        "index": index, "id": panel.id, "page": panel.page,
        "geometry_mm": {"x": panel.x_mm, "y": panel.y_mm, "width": panel.width_mm,
                        "height": panel.height_mm},
        "image_id": panel.image_id, "lock_group": panel.lock_group,
        "lock_kind": lock_kind(panel.lock_group) if panel.lock_group else None,
    }
    if ref is None:
        rec["error"] = "image not in project"
        return rec
    m = ref.metadata
    s = m.shape
    unit = group_time_unit(lock_members(figure, panel.lock_group), project.images) \
        if panel.lock_group else None
    labels = []
    for lb in panel.labels:
        runs, missing = resolve_label(lb.text, panel, ref, project, unit)
        labels.append({"template": lb.text, "text": plain_text(runs), "position": lb.position,
                       "missing": missing, "font_family": lb.font_family or figure.page.preset.font_family,
                       "font_size_pt": lb.font_size_pt or figure.page.preset.font_size_pt,
                       "color": lb.color})
    flags = []
    if any(c.visible and abs(c.gamma - 1.0) > 1e-9 for c in panel.channels):
        flags.append("nonlinear_gamma")
    if m.dtype == "uint8":
        flags.append("8bit_source")
    ppi = V.px_per_mm(panel, s.X, s.Y) * 25.4
    if ppi < figure.page.preset.dpi:
        flags.append("upsampled")
    if ref.extract is not None:
        flags.append("light_extract")
    x0, y0, x1, y1 = crop_bounds(panel.crop, s.X, s.Y)
    oriented_w, oriented_h = V.oriented_wh(x1 - x0, y1 - y0, panel.rotation_deg)
    rec.update({
        "image": {"filename": ref.filename, "path": ref.path, "original_path": ref.original_path,
                  "stored_relpath": ref.stored_relpath, "sha256_recorded": ref.sha256,
                  "original_present": ref.extract is None,
                  "reader": ref.reader, "reader_version": ref.reader_version,
                  "shape_tzcyx": list(s.as_tuple()), "plane_axis": plane_axis_record(ref),
                  "dtype": m.dtype, "bit_depth": m.bit_depth,
                  "calibration": m.calibration.model_dump(), **(source or {})},
        "t": panel.t, "z": panel.z,
        "time_s": (m.time_offsets_s[panel.t] if m.time_offsets_s and panel.t < len(m.time_offsets_s)
                   else None),
        "z_projection": panel.z_projection.model_dump() if panel.z_projection else None,
        "crop_requested": panel.crop.model_dump() if panel.crop else None,
        "crop_rendered_px": rendered_crop(panel, ref),
        "rendered_size_px": {"width": oriented_w, "height": oriented_h},
        "orientation": {"rotation_deg": panel.rotation_deg, "flip_horizontal": panel.flip_horizontal,
                        "flip_vertical": panel.flip_vertical,
                        "order": "flips, then clockwise rotation"},
        "effective_ppi": round(ppi, 1),
        "zoom_percent": round(V.zoom_percent(panel, s.X, s.Y), 2),
        "channels": _channels(panel, ref),
        "flags": flags,
        "labels": labels,
        "scale_bar": _scale_bar(panel, ref),
        "border": panel.border.model_dump(),
        "shapes": [sh.model_dump(mode="json") for sh in panel.shapes],
        "time_offset_s": panel.time_offset_s, "z_offset": panel.z_offset,
    })
    if ref.extract is not None:
        rec["extract"] = extract_block(ref, (source or {}).get("extract_sha256_verified_at_export"))
    if panel.inset_of:
        parent = figure.panel(panel.inset_of)
        rect = I.rect_on_parent(parent, panel, ref) if parent is not None else None
        c = V.effective_crop(panel, s.X, s.Y)
        rec["inset"] = {"parent_id": panel.inset_of, "number": I.inset_number(figure, panel),
                        "number_shown": panel.inset_style.show_number if panel.inset_style else True,
                        "style": panel.inset_style.model_dump() if panel.inset_style else None,
                        "rectangle_raw_px": {"x": c.x, "y": c.y, "width": c.w, "height": c.h},
                        "rectangle_on_parent_mm": (dict(zip(("x", "y", "width", "height"),
                                                            (round(v, 4) for v in rect)))
                                                   if rect else None)}
    kids = I.insets_of(figure, panel.id)
    if kids:
        rec["insets"] = [k.id for k in kids]
    return rec


def figure_sidecar(project: Project, figure: Figure, settings: dict[str, Any], files: list[str],
                   sources: dict[str, dict[str, Any]], log_chain: dict[str, Any],
                   generated_utc: str, consistency: Optional[list[dict]] = None,
                   warnings: Optional[list[str]] = None) -> dict[str, Any]:
    return {
        "report_type": SIDECAR_TYPE, "report_version": SIDECAR_VERSION,
        "generated_utc": generated_utc,
        "app": {"name": "Pycrograph", "version": __version__},
        "project": {"id": project.project.id, "name": project.project.name},
        "export": {**settings, "files": files},
        "figure": {"id": figure.id, "name": figure.name, "legend": figure.legend,
                   "page": figure.page.model_dump(), "n_panels": len(figure.panels)},
        "log_chain": log_chain,
        "consistency_warnings_overridden": consistency or [],
        "warnings": warnings or [],
        "panels": [panel_record(project, figure, p, k, sources.get(p.image_id))
                   for k, p in enumerate(figure.panels, start=1)],
    }
