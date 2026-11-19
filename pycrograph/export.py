# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Figure export (design §4) and source-data export, with provenance.

The exported page is the SAME scene as the canvas (ui/scene_builder.py:
FigureScene.populate), with editor decorations off and each panel's raster
at native crop resolution (only the part of the crop on the image, placed
with viewport.image_rect_mm).

- PDF / SVG: text stays text (labels, scale-bar text), lines and shapes
  are vector, rasters are embedded at native resolution. PDF rasters are
  written lossless (QPainter.LosslessImageRendering: Flate, never JPEG);
  fonts are embedded by Qt; one PDF page per figure page, plus an optional
  info page. SVG rasters are embedded PNG; text keeps its font-family; one
  SVG per page.
- TIFF / PNG: the page rendered at a chosen dpi (default: the preset's),
  RGB 8-bit; panel rasters resampled nearest-neighbour (default) or smooth
  (bilinear), recorded in the provenance. TIFF via tifffile with resolution
  tags; one file per page.
- Sources: per panel, the raw cropped planes of the visible channels (or the
  max projection), native dtype, uncompressed OME-TIFF with calibration —
  for journals asking for source data. Raw orientation (not rotated); the
  sidecar gives the panel's orientation.

Every export writes a provenance sidecar (provenance.py), embeds the
sidecar's SHA-256 + a short summary in the file metadata (SVG <metadata>,
PDF info + XMP, TIFF ImageDescription, PNG text chunks), and appends ONE
log entry (paths, format, SHA-256 of every written file and of the
sidecar, consistency warnings that were overridden). Sources are verified
first: missing or modified (SHA-256) sources refuse the export unless
`allow_modified`; a hash never recorded is computed now and reported.
The caller saves the project afterwards.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
import tifffile
from PySide6.QtCore import QMarginsF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgGenerator

from . import __version__
from .figures import panel_channel
from .jsonsafe import dumps_strict
from .labels import channel_name
from .layout import UNIT_TO_UM
from .logchain import verify_log_chain
from .models import Figure, Project
from .provenance import SOURCES_TYPE, SIDECAR_VERSION, figure_sidecar, rendered_crop, sidecar_name
from .render import PanelRenderer, RenderError
from .storage import RefStatus, Workspace, check_image, log_operation, sha256_file, utc_now
from .ui.scene_builder import export_scene, rgb_to_qimage

Format = Literal["pdf", "svg", "tiff", "png"]
FORMATS: tuple[str, ...] = ("pdf", "svg", "tiff", "png")
RESAMPLING: tuple[str, ...] = ("nearest", "smooth")
PDF_RESOLUTION = 1200                 # device units of the PDF painter (vector precision)
SVG_UNITS_PER_MM = 10                 # SVG user units per mm (viewBox)


class ExportRefused(RuntimeError):
    """The export cannot be made faithfully (missing / modified source)."""


@dataclass
class ExportSettings:
    format: Format = "pdf"
    dpi: Optional[int] = None          # raster formats; None = page preset dpi
    resampling: str = "nearest"        # raster formats: "nearest" | "smooth"
    info_page: bool = False            # PDF only
    verify_hashes: bool = True         # re-hash every source before exporting
    allow_modified: bool = False       # export despite a modified source (logged)

    def to_dict(self, figure: Figure) -> dict[str, Any]:
        d = asdict(self)
        if self.format in ("tiff", "png"):
            d["dpi"] = self.dpi or figure.page.preset.dpi
            d["resampling_method"] = {"nearest": "nearest-neighbour",
                                      "smooth": "bilinear (Qt SmoothPixmapTransform)"}[self.resampling]
        else:
            d["dpi"] = None
            d["resampling"] = None
            d["raster_embedding"] = ("native crop resolution, lossless (Flate)" if self.format == "pdf"
                                     else "native crop resolution, PNG")
        return d


@dataclass
class ExportResult:
    files: list[Path]
    sidecar: Path
    file_sha256: dict[str, str]
    sidecar_sha256: str
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ sources
def verify_sources(workspace: Workspace, project: Project, image_ids: list[str],
                   verify_hashes: bool = True, allow_modified: bool = False
                   ) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Check every source before an export. Returns ({image id: record},
    warnings); raises ExportRefused for a missing source, or a modified one
    unless allow_modified."""
    records: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for iid in dict.fromkeys(image_ids):
        ref = project.images[iid]
        path = workspace.resolve_image_path(project, ref)
        status = check_image(workspace, project, ref,
                             verify_hash=verify_hashes and ref.sha256 is not None)
        rec: dict[str, Any] = {"resolved_path": str(path), "source_status": status.value,
                               "sha256_verified_at_export": None}
        if status == RefStatus.MISSING:
            raise ExportRefused(f"{ref.filename}: source file missing ({path})")
        if status == RefStatus.MODIFIED:
            msg = f"{ref.filename}: source MODIFIED since import (SHA-256 differs)"
            if not allow_modified:
                raise ExportRefused(msg)
            warnings.append(msg + " — exported anyway")
            rec["sha256_at_export"] = sha256_file(path)
        elif status == RefStatus.PENDING_HASH:
            rec["sha256_at_export"] = sha256_file(path)
            warnings.append(f"{ref.filename}: no SHA-256 had been recorded; computed at export")
        elif status == RefStatus.CHANGED:
            warnings.append(f"{ref.filename}: size/date changed since import, not re-hashed")
        elif verify_hashes:
            rec["sha256_verified_at_export"] = True
        records[iid] = rec
    return records, warnings


# ------------------------------------------------------------------- naming
def output_paths(path: Path, fmt: str, n_pages: int) -> list[Path]:
    if fmt == "pdf" or n_pages == 1:
        return [path]
    return [path.with_name(f"{path.stem}_p{i + 1}{path.suffix}") for i in range(n_pages)]


def _summary(figure: Figure, fmt: str, sidecar: Path, sidecar_sha: str) -> dict[str, str]:
    return {"generator": f"Pycrograph {__version__}", "figure": figure.name, "figure_id": figure.id,
            "format": fmt, "provenance_sidecar": sidecar.name, "provenance_sha256": sidecar_sha}


# ------------------------------------------------------------------- writers
def _pdf(scene, figure: Figure, path: Path, meta: dict[str, str], info_page) -> None:
    w_mm, h_mm = figure.page.size_mm
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QSizeF(w_mm, h_mm), QPageSize.Millimeter))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setResolution(PDF_RESOLUTION)
    writer.setTitle(figure.name)
    writer.setCreator(meta["generator"])
    writer.setDocumentXmpMetadata(_xmp(figure, meta).encode("utf-8"))
    painter = QPainter(writer)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                           | QPainter.LosslessImageRendering)
    target = QRectF(0, 0, writer.width(), writer.height())
    for page in range(figure.page.n_pages):
        if page:
            writer.newPage()
        scene.render(painter, target, scene.page_rect(page), Qt.IgnoreAspectRatio)
    if info_page is not None:
        info_page(writer, painter)
    painter.end()


def _xmp(figure: Figure, meta: dict[str, str]) -> str:
    e = {k: html.escape(v) for k, v in meta.items()}
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:pdf="http://ns.adobe.com/pdf/1.3/" '
            'xmlns:pycrograph="https://github.com/TheBrickalista/Pycrograph/ns/1.0/">'
            f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{html.escape(figure.name)}'
            '</rdf:li></rdf:Alt></dc:title>'
            f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">Figure exported by '
            f'{e["generator"]}; provenance in {e["provenance_sidecar"]} '
            f'(SHA-256 {e["provenance_sha256"]})</rdf:li></rdf:Alt></dc:description>'
            f'<xmp:CreatorTool>{e["generator"]}</xmp:CreatorTool>'
            f'<pdf:Producer>{e["generator"]}</pdf:Producer>'
            f'<pycrograph:figureId>{e["figure_id"]}</pycrograph:figureId>'
            f'<pycrograph:provenanceSidecar>{e["provenance_sidecar"]}</pycrograph:provenanceSidecar>'
            f'<pycrograph:provenanceSha256>{e["provenance_sha256"]}</pycrograph:provenanceSha256>'
            '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')


def _svg(scene, figure: Figure, page: int, path: Path, meta: dict[str, str]) -> None:
    w_mm, h_mm = figure.page.size_mm
    k = SVG_UNITS_PER_MM
    gen = QSvgGenerator()
    gen.setFileName(str(path))
    gen.setSize(QSize(round(w_mm * k), round(h_mm * k)))
    gen.setViewBox(QRectF(0, 0, w_mm * k, h_mm * k))
    gen.setResolution(round(25.4 * k))                 # -> width/height written in mm
    gen.setTitle(figure.name)
    gen.setDescription(f"{meta['generator']}; provenance {meta['provenance_sidecar']} "
                       f"sha256={meta['provenance_sha256']}")
    painter = QPainter(gen)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    scene.render(painter, QRectF(0, 0, w_mm * k, h_mm * k), scene.page_rect(page), Qt.IgnoreAspectRatio)
    painter.end()
    text = path.read_text(encoding="utf-8")
    block = (f'<metadata id="pycrograph-provenance">{html.escape(json.dumps(meta))}</metadata>\n')
    i = text.find(">", text.find("<svg")) + 1
    path.write_text(text[:i] + "\n" + block + text[i:], encoding="utf-8")


def render_page_raster(scene, figure: Figure, page: int, dpi: int) -> np.ndarray:
    """(H, W, 3) uint8 of one page at `dpi`."""
    w_mm, h_mm = figure.page.size_mm
    w_px, h_px = max(1, round(w_mm / 25.4 * dpi)), max(1, round(h_mm / 25.4 * dpi))
    img = QImage(w_px, h_px, QImage.Format_RGB32)
    img.fill(QColor(figure.page.background))
    painter = QPainter(img)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    scene.render(painter, QRectF(0, 0, w_px, h_px), scene.page_rect(page), Qt.IgnoreAspectRatio)
    painter.end()
    rgb = img.convertToFormat(QImage.Format_RGB888)
    buf = np.frombuffer(rgb.constBits(), np.uint8, rgb.sizeInBytes()).reshape(h_px, rgb.bytesPerLine())
    return np.ascontiguousarray(buf[:, : w_px * 3].reshape(h_px, w_px, 3))


def _tiff(arr: np.ndarray, path: Path, dpi: int, meta: dict[str, str]) -> None:
    tifffile.imwrite(path, arr, photometric="rgb", resolution=(dpi, dpi), resolutionunit="INCH",
                     description=json.dumps(meta), software=meta["generator"], metadata=None)


def _png(arr: np.ndarray, path: Path, dpi: int, meta: dict[str, str]) -> None:
    img = rgb_to_qimage(arr)
    dpm = round(dpi / 0.0254)
    img.setDotsPerMeterX(dpm)
    img.setDotsPerMeterY(dpm)
    img.setText("Software", meta["generator"])
    img.setText("Title", meta["figure"])
    img.setText("pycrograph:provenance", json.dumps(meta))
    if not img.save(str(path), "PNG"):
        raise OSError(f"cannot write {path}")


# ----------------------------------------------------------------- info page
def _info_page(project: Project, figure: Figure, renderer: PanelRenderer, sidecar: dict,
               sidecar_sha: str):
    """OMERO.figure-style info page(s): legend, then per panel a thumbnail,
    the file, its hash and the settings."""
    def draw(writer: QPdfWriter, painter: QPainter) -> None:
        k = writer.resolution() / 25.4                  # device px per mm
        w_mm, h_mm = figure.page.size_mm
        margin, thumb = 12.0, 22.0
        y = margin

        def new_page():
            writer.newPage()
            return margin

        def text(x_mm, y_mm, w_mm_, s, size=7.0, bold=False) -> float:
            f = QFont(figure.page.preset.font_family)
            f.setPointSizeF(size)
            f.setBold(bold)
            painter.setFont(f)
            painter.setPen(QColor("#000000"))
            r = QRectF(x_mm * k, y_mm * k, w_mm_ * k, 1e6)
            used = painter.boundingRect(r, Qt.TextWordWrap, s)
            painter.drawText(r, Qt.TextWordWrap, s)
            return used.height() / k

        y = new_page()
        y += text(margin, y, w_mm - 2 * margin, f"{figure.name} — provenance", 11, True) + 2
        if figure.legend:
            y += text(margin, y, w_mm - 2 * margin, figure.legend, 8) + 3
        y += text(margin, y, w_mm - 2 * margin,
                  f"Pycrograph {__version__} · project {project.project.name} · "
                  f"log chain {sidecar['log_chain'].get('status')} · provenance sidecar "
                  f"SHA-256 {sidecar_sha}", 6) + 4
        for rec, panel in zip(sidecar["panels"], figure.panels):
            if y + thumb > h_mm - margin:
                y = new_page()
            ref = project.images.get(panel.image_id)
            if ref is not None:
                try:
                    img = rgb_to_qimage(renderer.render(ref, panel, max_side=400))
                    s = min(thumb / img.width(), thumb / img.height())
                    painter.drawImage(QRectF(margin * k, y * k, img.width() * s * k,
                                             img.height() * s * k), img)
                except RenderError:
                    pass
            if "image" not in rec:
                continue
            im, c = rec["image"], rec["crop_rendered_px"]
            proj = rec["z_projection"]
            chans = "; ".join(
                f"{ch['label_name']} {ch['lut']} {ch['black']}–{ch['white']}"
                + (f" γ{ch['gamma']:g}" if abs(ch["gamma"] - 1) > 1e-9 else "")
                + (" inv" if ch["invert"] else "") for ch in rec["channels"] if ch["visible"])
            lines = [f"Panel {rec['index']}: {im['filename']}",
                     f"SHA-256 {im['sha256_recorded'] or im.get('sha256_at_export') or '—'}",
                     f"T {rec['t'] + 1} · " + (f"max Z {proj['z_start'] + 1}–{proj['z_end'] + 1}"
                                               if proj else f"Z {rec['z'] + 1}")
                     + f" · crop x {c['x0']}–{c['x1']}, y {c['y0']}–{c['y1']} px"
                     + f" · rotation {rec['orientation']['rotation_deg']}°"
                     + f" · {rec['effective_ppi']:.0f} ppi",
                     f"Channels: {chans or '—'}"]
            if rec["labels"]:
                lines.append("Labels: " + " | ".join(lb["text"] for lb in rec["labels"]))
            if rec["flags"]:
                lines.append("Flags: " + ", ".join(rec["flags"]))
            used = text(margin + thumb + 3, y, w_mm - 2 * margin - thumb - 3, "\n".join(lines), 6)
            y += max(thumb, used) + 3
    return draw


# -------------------------------------------------------------------- export
def export_figure(workspace: Workspace, project: Project, figure: Figure, renderer: PanelRenderer,
                  path: str | Path, settings: ExportSettings,
                  overridden: Optional[list[dict]] = None) -> ExportResult:
    """Write the figure, its sidecar, and log one entry. `overridden` = the
    consistency issues the user chose to export despite (logged)."""
    path = Path(path)
    fmt = settings.format
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    if settings.resampling not in RESAMPLING:
        raise ValueError(f"unknown resampling {settings.resampling!r}")
    sources, warnings = verify_sources(workspace, project, [p.image_id for p in figure.panels],
                                       settings.verify_hashes, settings.allow_modified)
    outputs = output_paths(path, fmt, figure.page.n_pages)
    sidecar_path = path.with_name(sidecar_name(path.name))
    sidecar = figure_sidecar(project, figure, settings.to_dict(figure), [p.name for p in outputs],
                             sources, verify_log_chain(project).model_dump(), utc_now(),
                             overridden, warnings)
    text = dumps_strict(sidecar, indent=2, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(text, encoding="utf-8")
    sidecar_sha = sha256_file(sidecar_path)
    meta = _summary(figure, fmt, sidecar_path, sidecar_sha)

    scene = export_scene(project, figure, renderer, smooth=settings.resampling == "smooth")
    if fmt == "pdf":
        info = _info_page(project, figure, renderer, sidecar, sidecar_sha) if settings.info_page else None
        _pdf(scene, figure, outputs[0], meta, info)
    elif fmt == "svg":
        for page, out in enumerate(outputs):
            _svg(scene, figure, page, out, meta)
    else:
        dpi = settings.dpi or figure.page.preset.dpi
        for page, out in enumerate(outputs):
            arr = render_page_raster(scene, figure, page, dpi)
            (_tiff if fmt == "tiff" else _png)(arr, out, dpi, meta)

    hashes = {str(p): sha256_file(p) for p in outputs}
    note = []
    if overridden:
        note.append(f"exported despite {len(overridden)} consistency warning(s): "
                    + "; ".join(i["message"] for i in overridden[:10]))
    note += warnings
    log_operation(project, "export.figure", target_type="figure", target_id=figure.id,
                  field="export", new_value={
                      "format": fmt, "settings": settings.to_dict(figure),
                      "files": [{"path": p, "sha256": h} for p, h in hashes.items()],
                      "sidecar": {"path": str(sidecar_path), "sha256": sidecar_sha},
                      "consistency_warnings": overridden or []},
                  note="; ".join(note) or None)
    return ExportResult(outputs, sidecar_path, hashes, sidecar_sha, warnings)


def _um(cal) -> Optional[tuple[float, float]]:
    k = UNIT_TO_UM.get(cal.unit)
    if not cal.is_calibrated or k is None:
        return None
    return cal.pixel_size_x * k, cal.pixel_size_y * k


def export_sources(workspace: Workspace, project: Project, figure: Figure, renderer: PanelRenderer,
                   panel_ids: list[str], directory: str | Path, stem: Optional[str] = None,
                   verify_hashes: bool = True) -> ExportResult:
    """Per panel: raw cropped planes of the visible channels (or the max
    projection), native dtype, as an uncompressed OME-TIFF with calibration.
    Pixels are written as read: never resampled, rescaled or re-encoded."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = stem or figure.name.replace(" ", "_")
    panels = [p for p in figure.panels if p.id in set(panel_ids)]
    sources, warnings = verify_sources(workspace, project, [p.image_id for p in panels],
                                       verify_hashes)
    records, outputs = [], []
    for p in panels:
        k = figure.panels.index(p) + 1
        ref = project.images[p.image_id]
        idx = [c.index for c in sorted(p.channels, key=lambda c: c.index)
               if c.visible and c.index < ref.metadata.shape.C]
        idx = idx or list(range(ref.metadata.shape.C))
        data = np.stack([renderer.crop_plane(ref, p, c) for c in idx])
        out = directory / f"{stem}_panel{k:02d}_{Path(ref.filename).stem}.ome.tif"
        md: dict[str, Any] = {"axes": "CYX", "Name": f"{figure.name} panel {k}",
                              "Channel": {"Name": [channel_name(ref, c) for c in idx]}}
        um = _um(ref.metadata.calibration)
        if um:
            md.update({"PhysicalSizeX": um[0], "PhysicalSizeXUnit": "µm",
                       "PhysicalSizeY": um[1], "PhysicalSizeYUnit": "µm"})
        tifffile.imwrite(out, data, ome=True, photometric="minisblack", metadata=md,
                         software=f"Pycrograph {__version__}", compression=None)
        outputs.append(out)
        proj = p.z_projection
        records.append({
            "file": out.name, "panel_index": k, "panel_id": p.id, "image_id": ref.id,
            "image": {"filename": ref.filename, "path": ref.path, "sha256_recorded": ref.sha256,
                      **sources[ref.id]},
            "t": p.t, "z": None if proj else p.z,
            "z_projection": {"method": "max", **proj.model_dump()} if proj else None,
            "crop_px": rendered_crop(p, ref), "channels": [
                {"index": c, "name": channel_name(ref, c),
                 "display": (panel_channel(p, c).model_dump() if panel_channel(p, c) else None)}
                for c in idx],
            "dtype": ref.metadata.dtype, "shape_cyx": list(data.shape),
            "calibration_um": list(um) if um else None,
            "orientation_of_panel": {"rotation_deg": p.rotation_deg,
                                     "flip_horizontal": p.flip_horizontal,
                                     "flip_vertical": p.flip_vertical,
                                     "note": "pixels written in raw orientation"},
        })
    sidecar_path = directory / sidecar_name(stem, sources=True)
    sidecar = {"report_type": SOURCES_TYPE, "report_version": SIDECAR_VERSION,
               "generated_utc": utc_now(), "app": {"name": "Pycrograph", "version": __version__},
               "project": {"id": project.project.id, "name": project.project.name},
               "figure": {"id": figure.id, "name": figure.name},
               "export": {"format": "ome-tiff", "compression": "none", "files": [o.name for o in outputs]},
               "log_chain": verify_log_chain(project).model_dump(),
               "warnings": warnings, "panels": records}
    sidecar_path.write_text(dumps_strict(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
    sidecar_sha = sha256_file(sidecar_path)
    hashes = {str(o): sha256_file(o) for o in outputs}
    log_operation(project, "export.sources", target_type="figure", target_id=figure.id,
                  field="export", new_value={
                      "format": "ome-tiff", "panels": [p.id for p in panels],
                      "files": [{"path": o, "sha256": h} for o, h in hashes.items()],
                      "sidecar": {"path": str(sidecar_path), "sha256": sidecar_sha}},
                  note="; ".join(warnings) or None)
    return ExportResult(outputs, sidecar_path, hashes, sidecar_sha, warnings)
