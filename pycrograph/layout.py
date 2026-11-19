# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Layout tools (docs/design/figure-editor.md §2.8), pure functions on panels.

They only change geometry (and, for the auto-layouts, create panels); the
caller wraps each call in one FigureEditor gesture, which gives one undo
step and one log entry per panel changed or added.

Size changes follow the viewport rules (§2.4, viewport.py):
- *Same width / height* and *Align magnification* scale the panel with its
  crop unchanged (the image gets bigger or smaller on the page).
- *Same width & height* scales to the width, then fits the height at that
  scale, extending or shrinking the crop (never stretching the image).

Auto-layouts copy a source panel (rendering, crop, size, T/Z) and put the
new panels below everything on the page. Every panel of one auto-layout
shares one ``lock_group`` (``split:…``, ``montage:…``, ``grid:…``). Locks
are matched by channel index, so one group covers both the rows and the
columns of a condition grid.
"""

from __future__ import annotations

import re
import uuid
from typing import Iterable, Literal, Optional, Sequence

from . import viewport as V
from .models import ImageRef, Panel

Edge = Literal["left", "right", "top", "bottom", "hcentre", "vcentre"]
DEFAULT_GAP_MM = 2.0

# µm per calibration unit (Calibration.unit); unknown units are skipped.
UNIT_TO_UM = {"um": 1.0, "µm": 1.0, "micron": 1.0, "microns": 1.0, "nm": 1e-3, "mm": 1e3}


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _r(v: float) -> float:
    return round(float(v), 6)


# ------------------------------------------------------------------ align
def bbox(panels: Sequence[Panel]) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) in mm."""
    return (min(p.x_mm for p in panels), min(p.y_mm for p in panels),
            max(p.x_mm + p.width_mm for p in panels), max(p.y_mm + p.height_mm for p in panels))


def align(panels: Sequence[Panel], edge: Edge) -> None:
    """Edges align to the selection's bounding box; centres to its centre."""
    if len(panels) < 2:
        return
    left, top, right, bottom = bbox(panels)
    for p in panels:
        if edge == "left":
            p.x_mm = _r(left)
        elif edge == "right":
            p.x_mm = _r(right - p.width_mm)
        elif edge == "top":
            p.y_mm = _r(top)
        elif edge == "bottom":
            p.y_mm = _r(bottom - p.height_mm)
        elif edge == "hcentre":
            p.x_mm = _r((left + right) / 2 - p.width_mm / 2)
        elif edge == "vcentre":
            p.y_mm = _r((top + bottom) / 2 - p.height_mm / 2)
        else:
            raise ValueError(edge)


def distribute(panels: Sequence[Panel], axis: Literal["h", "v"]) -> bool:
    """Equal gaps between panels; the first and last stay in place."""
    if len(panels) < 3:
        return False
    if axis == "h":
        ps = sorted(panels, key=lambda p: p.x_mm)
        span = ps[-1].x_mm + ps[-1].width_mm - ps[0].x_mm
        gap = (span - sum(p.width_mm for p in ps)) / (len(ps) - 1)
        x = ps[0].x_mm
        for p in ps:
            p.x_mm = _r(x)
            x += p.width_mm + gap
    else:
        ps = sorted(panels, key=lambda p: p.y_mm)
        span = ps[-1].y_mm + ps[-1].height_mm - ps[0].y_mm
        gap = (span - sum(p.height_mm for p in ps)) / (len(ps) - 1)
        y = ps[0].y_mm
        for p in ps:
            p.y_mm = _r(y)
            y += p.height_mm + gap
    return True


def rows_of(panels: Sequence[Panel]) -> list[list[Panel]]:
    """Group panels into rows from their current positions (a panel joins a
    row when its top is above the middle of the row's first panel), each
    row sorted left to right."""
    rows: list[list[Panel]] = []
    for p in sorted(panels, key=lambda p: (p.y_mm, p.x_mm)):
        if rows and p.y_mm < rows[-1][0].y_mm + rows[-1][0].height_mm / 2:
            rows[-1].append(p)
        else:
            rows.append([p])
    return [sorted(r, key=lambda p: p.x_mm) for r in rows]


def grid(panels: Sequence[Panel], gap_mm: float = DEFAULT_GAP_MM) -> None:
    """Snap to a grid: rows and columns from the current positions, `gap_mm`
    between cells, the top-left panel fixed. Column width / row height =
    the largest panel in it; panels sit at the top-left of their cell."""
    if not panels:
        return
    rows = rows_of(panels)
    x0, y0 = rows[0][0].x_mm, rows[0][0].y_mm
    n_cols = max(len(r) for r in rows)
    col_w = [max((r[j].width_mm for r in rows if j < len(r)), default=0.0) for j in range(n_cols)]
    row_h = [max(p.height_mm for p in r) for r in rows]
    y = y0
    for i, r in enumerate(rows):
        x = x0
        for j, p in enumerate(r):
            p.x_mm, p.y_mm = _r(x), _r(y)
            x += col_w[j] + gap_mm
        y += row_h[i] + gap_mm


def nudge(panels: Iterable[Panel], dx: float, dy: float) -> None:
    for p in panels:
        p.x_mm, p.y_mm = _r(p.x_mm + dx), _r(p.y_mm + dy)


# ------------------------------------------------------------------- size
def scale_panel(panel: Panel, k: float) -> None:
    """Scale the panel on the page; the crop is unchanged."""
    panel.width_mm = _r(max(0.5, panel.width_mm * k))
    panel.height_mm = _r(max(0.5, panel.height_mm * k))


def same_size(panels: Sequence[Panel], refs: dict[str, ImageRef], reference: Panel,
              mode: Literal["width", "height", "both"]) -> None:
    for p in panels:
        if p.id == reference.id:
            continue
        if mode == "width":
            scale_panel(p, reference.width_mm / p.width_mm)
        elif mode == "height":
            scale_panel(p, reference.height_mm / p.height_mm)
        else:
            scale_panel(p, reference.width_mm / p.width_mm)
            s = refs[p.image_id].metadata.shape
            V.resize(p, s.X, s.Y, reference.width_mm, reference.height_mm)
            p.width_mm, p.height_mm = reference.width_mm, reference.height_mm


def um_per_mm(panel: Panel, ref: ImageRef) -> Optional[float]:
    """Microns of sample per page millimetre (None: uncalibrated or unknown
    unit)."""
    cal = ref.metadata.calibration
    k = UNIT_TO_UM.get(cal.unit)
    if not cal.is_calibrated or k is None:
        return None
    s = ref.metadata.shape
    return V.px_per_mm(panel, s.X, s.Y) * cal.pixel_size_x * k


def top_left(panels: Sequence[Panel]) -> Panel:
    return min(panels, key=lambda p: (p.y_mm, p.x_mm))


def align_magnification(panels: Sequence[Panel], refs: dict[str, ImageRef],
                        reference: Optional[Panel] = None) -> list[str]:
    """Resize panels so µm per page-mm equals the reference's (default: the
    top-left panel): equal scale bars look equal. The crop is unchanged.
    Returns ids of panels skipped (uncalibrated)."""
    reference = reference or top_left(panels)
    target = um_per_mm(reference, refs[reference.image_id])
    if target is None:
        return [p.id for p in panels]
    skipped = []
    for p in panels:
        if p.id == reference.id:
            continue
        cur = um_per_mm(p, refs[p.image_id])
        if cur is None:
            skipped.append(p.id)
            continue
        scale_panel(p, cur / target)
    return skipped


# ------------------------------------------------------------ auto-layouts
def below_all(panels: Sequence[Panel], page: int, gap_mm: float) -> float:
    on_page = [p for p in panels if p.page == page]
    return _r(max((p.y_mm + p.height_mm for p in on_page), default=-gap_mm) + gap_mm)


def _copy(source: Panel, x: float, y: float, group: str) -> Panel:
    return source.model_copy(deep=True, update={
        "id": _id(), "x_mm": _r(x), "y_mm": _r(y), "lock_group": group,
        "inset_of": None, "inset_style": None})


def channel_configs(ref: ImageRef) -> list[Optional[int]]:
    """Columns of a split: each channel alone, then the merge (None)."""
    c = ref.metadata.shape.C
    return list(range(c)) + ([None] if c > 1 else [])


def _apply_config(panel: Panel, ref: ImageRef, config: Optional[int]) -> None:
    from .figures import set_channel_field                 # avoid an import cycle
    for idx in range(ref.metadata.shape.C):
        set_channel_field(panel, ref, idx, "visible", config is None or idx == config)


def split_channels(source: Panel, ref: ImageRef, x: float, y: float,
                   gap_mm: float = DEFAULT_GAP_MM) -> list[Panel]:
    """One panel per channel plus the merge, in a row starting at (x, y)."""
    group = f"split:{_id()}"
    out = []
    for j, config in enumerate(channel_configs(ref)):
        p = _copy(source, x + j * (source.width_mm + gap_mm), y, group)
        _apply_config(p, ref, config)
        out.append(p)
    return out


def parse_indices(text: str, n: int) -> list[int]:
    """'1-3, 5' (1-based, inclusive) -> [0, 1, 2, 4], clipped to [0, n)."""
    out: list[int] = []
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"not an index or range: {part!r}")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        step = 1 if b >= a else -1
        for i in range(a, b + step, step):
            if 1 <= i <= n and i - 1 not in out:
                out.append(i - 1)
    return out


def montage(source: Panel, ref: ImageRef, axis: Literal["t", "z"], indices: Sequence[int],
            x: float, y: float, gap_mm: float = DEFAULT_GAP_MM,
            direction: Literal["row", "column"] = "row") -> list[Panel]:
    """One panel per time point (or Z slice), in a row or a column."""
    group = f"montage:{_id()}"
    n = ref.metadata.shape.T if axis == "t" else ref.metadata.shape.Z
    out = []
    for k, idx in enumerate(i for i in indices if 0 <= i < n):
        if direction == "row":
            p = _copy(source, x + k * (source.width_mm + gap_mm), y, group)
        else:
            p = _copy(source, x, y + k * (source.height_mm + gap_mm), group)
        if axis == "t":
            p.t = idx
        else:
            p.z = idx
            p.z_projection = None
        out.append(p)
    return out


def condition_grid(sources: Sequence[tuple[Panel, ImageRef]], x: float, y: float,
                   gap_mm: float = DEFAULT_GAP_MM) -> list[list[Optional[Panel]]]:
    """Rows = the sources (one per condition/image), columns = channel
    configurations (each channel, then the merge). Cell size = the first
    source's panel size; each row keeps its own source's rendering and
    viewport. A row whose image has fewer channels leaves empty cells."""
    if not sources:
        return []
    group = f"grid:{_id()}"
    w0, h0 = sources[0][0].width_mm, sources[0][0].height_mm
    n_cols = max(len(channel_configs(r)) for _p, r in sources)
    rows: list[list[Optional[Panel]]] = []
    for i, (src, ref) in enumerate(sources):
        configs = channel_configs(ref)
        merge_col = n_cols - 1
        row: list[Optional[Panel]] = [None] * n_cols
        for config in configs:
            j = merge_col if config is None else config
            p = _copy(src, x + j * (w0 + gap_mm), y + i * (h0 + gap_mm), group)
            # Same rule as "same width & height": scale to the cell width
            # (crop kept), then fit the height at that scale.
            s = ref.metadata.shape
            scale_panel(p, w0 / p.width_mm)
            V.resize(p, s.X, s.Y, w0, h0)
            p.width_mm, p.height_mm = w0, h0
            _apply_config(p, ref, config)
            row[j] = p
        rows.append(row)
    return rows
