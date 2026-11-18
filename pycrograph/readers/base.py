# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
ImageSource: the single interface every reader implements.

Contract
--------
- Dimensions are always exposed as (T, Z, C, Y, X), whatever the file layout.
- get_plane(t, z, c) returns ONE 2-D plane and must not read the rest of the
  file: 8 GB machines, ~1.1 GB time-lapse stacks. Readers memory-map when the
  data are stored uncompressed and contiguously, and read page by page
  otherwise.
- Returned planes are read-only: raw pixels are never modified.
- Sources hold an open file; use them as context managers or call close().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from ..models import ImageMetadata, Shape5D


class UnsupportedImageError(ValueError):
    """The file is not an image Pycrograph accepts (format, dtype, layout)."""


class ImageSource(ABC):
    reader_name: str = "base"
    reader_version: str = "0"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ---- description ------------------------------------------------------
    @property
    @abstractmethod
    def metadata(self) -> ImageMetadata: ...

    @property
    def shape(self) -> Shape5D:
        return self.metadata.shape

    @property
    def dtype(self) -> np.dtype:
        return np.dtype(self.metadata.dtype)

    # ---- pixels -----------------------------------------------------------
    @abstractmethod
    def _read_plane(self, t: int, z: int, c: int) -> np.ndarray: ...

    def get_plane(self, t: int = 0, z: int = 0, c: int = 0) -> np.ndarray:
        s = self.shape
        for name, value, size in (("t", t, s.T), ("z", z, s.Z), ("c", c, s.C)):
            if not 0 <= int(value) < size:
                raise IndexError(f"{name}={value} out of range [0, {size})")
        plane = self._read_plane(int(t), int(z), int(c))
        if plane.shape != (s.Y, s.X):
            raise RuntimeError(
                f"{self.reader_name}: plane shape {plane.shape} != {(s.Y, s.X)}"
            )
        if plane.flags.writeable:
            plane = plane.view()
            plane.flags.writeable = False
        return plane

    # ---- lifetime ---------------------------------------------------------
    def close(self) -> None:  # pragma: no cover - trivial default
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
