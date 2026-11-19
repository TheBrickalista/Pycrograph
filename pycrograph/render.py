# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
PanelRenderer: one panel -> RGB8, used by the canvas (and later by export).

    raw plane(s) at (t, z)        (max over the Z range for a projection)
      -> crop by array slicing     (memmap: only the touched rows are read)
      -> display.render_composite  (window, gamma, invert, LUT, composite)
      -> orientation               (flips, 90° rotations: index operations)

Nothing is resampled here. For the on-screen canvas a `max_side` can be
given: the RGB is then decimated by an integer stride (pixels dropped, never
interpolated), exactly like library thumbnails. Export renders with
max_side=None (native crop resolution).

Results are cached per panel state, keyed by the source identity (SHA-256,
else path + size + mtime) and every setting that changes the pixels, so
moving or resizing a panel on the page never re-reads the file.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Hashable, Optional

import numpy as np

from .display import apply_orientation, make_thumbnail, render_composite
from .models import Crop, ImageRef, Panel
from .readers import ImageSource, open_image_ref


class RenderError(RuntimeError):
    """The panel cannot be rendered (missing or unreadable source)."""


def crop_bounds(crop: Optional[Crop], width: int, height: int) -> tuple[int, int, int, int]:
    """Integer slice bounds (x0, y0, x1, y1) covering the crop, clamped to
    the image. A crop entirely outside the image yields an empty slice
    clamped to one pixel so rendering never fails."""
    if crop is None:
        return 0, 0, width, height
    x0 = max(0, min(width - 1, int(math.floor(crop.x))))
    y0 = max(0, min(height - 1, int(math.floor(crop.y))))
    x1 = max(x0 + 1, min(width, int(math.ceil(crop.x + crop.w))))
    y1 = max(y0 + 1, min(height, int(math.ceil(crop.y + crop.h))))
    return x0, y0, x1, y1


def panel_cache_key(ref: ImageRef, panel: Panel, max_side: Optional[int]) -> Hashable:
    source = ref.sha256 or (ref.path, ref.stored_relpath, ref.size_bytes, ref.mtime_ns)
    proj = panel.z_projection
    return (
        source, panel.t, panel.z,
        (proj.method, proj.z_start, proj.z_end) if proj else None,
        (panel.crop.x, panel.crop.y, panel.crop.w, panel.crop.h) if panel.crop else None,
        tuple((c.index, c.visible, c.lut, c.black, c.white, c.gamma, c.invert)
              for c in panel.channels),
        panel.rotation_deg, panel.flip_horizontal, panel.flip_vertical, max_side,
    )


class PanelRenderer:
    """Renders panels, keeping a few sources open and an LRU of results.

    `resolve_path(ref)` maps an ImageRef to the file to read (the workspace
    knows about copies stored inside the project)."""

    def __init__(self, resolve_path: Callable[[ImageRef], Path], cache_size: int = 64,
                 max_open_sources: int = 4):
        self._resolve = resolve_path
        self._cache: OrderedDict[Hashable, np.ndarray] = OrderedDict()
        self._cache_size = cache_size
        self._sources: OrderedDict[str, tuple[Path, ImageSource]] = OrderedDict()
        self._max_open = max_open_sources

    # ---- sources ---------------------------------------------------------
    def _source(self, ref: ImageRef) -> ImageSource:
        path = Path(self._resolve(ref))
        entry = self._sources.get(ref.id)
        if entry is not None and entry[0] == path:
            self._sources.move_to_end(ref.id)
            return entry[1]
        if entry is not None:
            entry[1].close()
            del self._sources[ref.id]
        try:
            src = open_image_ref(path, ref)
        except FileNotFoundError as exc:
            raise RenderError(f"source file not found: {path}") from exc
        except (OSError, ValueError) as exc:
            raise RenderError(f"cannot read {path.name}: {exc}") from exc
        self._sources[ref.id] = (path, src)
        while len(self._sources) > self._max_open:
            _k, (_p, old) = self._sources.popitem(last=False)
            old.close()
        return src

    def close(self) -> None:
        for _p, src in self._sources.values():
            src.close()
        self._sources.clear()
        self._cache.clear()

    def invalidate(self, image_id: Optional[str] = None) -> None:
        """Forget cached results (all, or after a relink of one image)."""
        self._cache.clear()
        if image_id is not None and image_id in self._sources:
            self._sources.pop(image_id)[1].close()

    # ---- rendering -------------------------------------------------------
    def render(self, ref: ImageRef, panel: Panel, max_side: Optional[int] = None) -> np.ndarray:
        """(H, W, 3) uint8, oriented as on the page. Read-only array (shared
        with the cache)."""
        key = panel_cache_key(ref, panel, max_side)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        rgb = self._render_uncached(ref, panel)
        if max_side is not None:
            rgb = make_thumbnail(rgb, max_side)
        rgb = np.ascontiguousarray(rgb)
        rgb.flags.writeable = False
        self._cache[key] = rgb
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return rgb

    def _render_uncached(self, ref: ImageRef, panel: Panel) -> np.ndarray:
        s = ref.metadata.shape
        x0, y0, x1, y1 = crop_bounds(panel.crop, s.X, s.Y)
        settings = [cs for cs in panel.channels if cs.index < s.C]
        visible = [cs for cs in settings if cs.visible]
        if not visible:
            rgb = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
        else:
            src = self._source(ref)
            t = min(panel.t, s.T - 1)
            planes = [self._plane(src, panel, t, cs.index, s.Z, (x0, y0, x1, y1))
                      for cs in visible]
            rgb = render_composite(planes, visible)
        return apply_orientation(rgb, panel.rotation_deg, panel.flip_horizontal,
                                 panel.flip_vertical)

    def crop_plane(self, ref: ImageRef, panel: Panel, channel: int) -> np.ndarray:
        """The raw cropped plane of one channel (max projection if set), as
        a private copy: histogram, saturation and Auto levels are computed on
        what the panel shows (design §4)."""
        s = ref.metadata.shape
        bounds = crop_bounds(panel.crop, s.X, s.Y)
        src = self._source(ref)
        return np.array(self._plane(src, panel, min(panel.t, s.T - 1), channel, s.Z, bounds))

    @staticmethod
    def _plane(src: ImageSource, panel: Panel, t: int, c: int, n_z: int,
               bounds: tuple[int, int, int, int]) -> np.ndarray:
        """The cropped raw plane (or max projection of cropped planes)."""
        x0, y0, x1, y1 = bounds
        proj = panel.z_projection
        if proj is None:
            return src.get_plane(t, min(panel.z, n_z - 1), c)[y0:y1, x0:x1]
        z0 = min(proj.z_start, n_z - 1)
        z1 = min(max(proj.z_end, z0), n_z - 1)
        out = np.array(src.get_plane(t, z0, c)[y0:y1, x0:x1])     # writable copy
        for z in range(z0 + 1, z1 + 1):
            np.maximum(out, src.get_plane(t, z, c)[y0:y1, x0:x1], out=out)
        return out
