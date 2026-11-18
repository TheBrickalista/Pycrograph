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

from .base import ImageSource, UnsupportedImageError
from .tiff import TiffSource

__all__ = ["ImageSource", "UnsupportedImageError", "TiffSource", "open_image", "sniff_format"]

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


def open_image(path: str | Path) -> ImageSource:
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
    return TiffSource(path)
