# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Reader for light-archive extracts (archive.py).

An extract is an OME-TIFF (axes TZCYX) holding a subset of an original
image: the T, Z and C indices listed in ExtractInfo and the region
[origin, origin + size) of the original's raw pixels. This source presents
it with the ORIGINAL metadata and geometry, so panels keep their crop,
shapes, T/Z and channel indices unchanged: get_plane returns a full-size
plane with the archived region in place and zeros elsewhere (no pixels were
archived there). A plane that was not archived raises PlaneNotArchived.
One page is read per call (never the whole file).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from ..models import ExtractInfo, ImageMetadata
from .base import ImageSource, UnsupportedImageError

EXTRACT_TAG = 65000          # private TIFF tag: JSON with the source description


class PlaneNotArchived(UnsupportedImageError):
    """The light archive did not keep this (t, z, c) plane."""


class ExtractSource(ImageSource):
    reader_name = "pycrograph-extract"
    reader_version = "1"

    def __init__(self, path: str | Path, metadata: ImageMetadata, extract: ExtractInfo):
        super().__init__(path)
        self._meta = metadata
        self._x = extract
        self._tif = tifffile.TiffFile(self.path)
        n = len(extract.t) * len(extract.z) * len(extract.c)
        if len(self._tif.pages) != n:
            self._tif.close()
            raise UnsupportedImageError(f"{self.path.name}: {len(self._tif.pages)} pages, "
                                        f"the extract record says {n}")

    @property
    def metadata(self) -> ImageMetadata:
        return self._meta

    def _read_plane(self, t: int, z: int, c: int) -> np.ndarray:
        x = self._x
        try:
            ti, zi, ci = x.t.index(t), x.z.index(z), x.c.index(c)
        except ValueError:
            raise PlaneNotArchived(
                f"{self.path.name}: plane T{t + 1} Z{z + 1} C{c + 1} is not in this light "
                "archive (only the planes the figures showed were kept)") from None
        page = self._tif.pages[(ti * len(x.z) + zi) * len(x.c) + ci]
        region = page.asarray()
        s = self._meta.shape
        out = np.zeros((s.Y, s.X), dtype=np.dtype(self._meta.dtype))
        out[x.origin_y:x.origin_y + x.height, x.origin_x:x.origin_x + x.width] = region
        return out

    def close(self) -> None:
        self._tif.close()
