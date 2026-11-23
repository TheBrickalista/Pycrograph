# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Reader registry. open_image(path) picks the reader from the file's magic
bytes (never from the extension alone) and returns an ImageSource.

Planned: MetaMorph .nd sets, then .czi / .nd2 / .lif through Java-free bioio
plugins (no Bio-Formats, to keep PyInstaller builds simple).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import ImageSource, UnsupportedImageError
from .tiff import PlaneAxis, TiffSource, axis_letter_of_inference

__all__ = ["ImageSource", "UnsupportedImageError", "TiffSource", "open_image", "open_image_ref",
           "plane_axis_record", "recorded_plane_axis", "sniff_format"]

IMPORT_FILE_FILTER = "Microscopy images (*.tif *.tiff *.stk *.TIF *.TIFF *.STK);;All files (*)"


def sniff_format(path: str | Path) -> str:
    """'tiff' | 'jpeg' | 'png' | 'unknown', from the first bytes."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "tiff"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "unknown"


def open_image(path: str | Path, plane_axis: Optional[PlaneAxis] = None,
               plane_axis_reason: str = "user override") -> ImageSource:
    """plane_axis: T or Z for a single generic plane axis (TiffSource);
    None = infer it (import)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    kind = sniff_format(path)
    if kind == "jpeg":
        raise UnsupportedImageError(
            f"{path.name}: JPEG is lossy compression and is rejected "
            "(as in PysternBlot). Use the original acquisition file."
        )
    if kind != "tiff":
        raise UnsupportedImageError(f"{path.name}: unsupported file format ({kind}).")
    return TiffSource(path, plane_axis=plane_axis, plane_axis_reason=plane_axis_reason)


def recorded_plane_axis(ref) -> Optional[PlaneAxis]:
    """The T/Z mapping of the image's generic plane axis as stored in the
    project: the user override, else the one recorded at import
    (raw["tiff.axis_inference"]), else T for images imported before the
    inference existed (the old rule) when the file had a generic axis."""
    override = getattr(ref, "plane_axis_override", None)
    if override:
        return override
    raw = ref.metadata.raw
    letter = axis_letter_of_inference(raw.get("tiff.axis_inference"))
    if letter:
        return letter
    axes = raw.get("tiff.axes")
    if isinstance(axes, str) and any(a not in "TZCSYX" for a in axes):
        return "T"
    return None


def plane_axis_record(ref) -> Optional[dict]:
    """How the image's generic plane axis is mapped, for the info pane,
    integrity report and provenance sidecar; None without such an axis.
    {"axis": "T"|"Z", "override": "T"|"Z"|None, "inference": str}."""
    axis = recorded_plane_axis(ref)
    if axis is None:
        return None
    inference = ref.metadata.raw.get("tiff.axis_inference")
    if not inference:
        letter = next(a for a in ref.metadata.raw.get("tiff.axes", "") if a not in "TZCSYX")
        inference = f"{letter}→T (imported before plane-axis detection)"
    return {"axis": axis, "override": getattr(ref, "plane_axis_override", None),
            "inference": inference}


def open_image_ref(path: str | Path, ref) -> ImageSource:
    """Open what an ImageRef points at: the original file, or a light-archive
    extract (ref.extract set) presented with the original geometry.

    The file is opened with the plane-axis mapping stored in the project
    (recorded_plane_axis), never re-inferred: an image keeps the shape it
    was imported with (or that the user chose) whatever the reader version.
    Extracts use ref.metadata, which already holds that mapping."""
    if getattr(ref, "extract", None) is not None:
        from .extract import ExtractSource
        if not Path(path).is_file():
            raise FileNotFoundError(path)
        return ExtractSource(path, ref.metadata, ref.extract)
    axis = recorded_plane_axis(ref)
    reason = "user override" if getattr(ref, "plane_axis_override", None) else "as imported"
    return open_image(path, plane_axis=axis, plane_axis_reason=reason)
