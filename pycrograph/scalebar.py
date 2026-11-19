# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Scale bar geometry (docs/design/figure-editor.md §2.6), pure.

The bar is horizontal on the page. Its length on the page is

    bar_mm = length / (pixel size along the bar × raw px per page-mm)

with raw px per page-mm from the viewport (viewport.px_per_mm), so it
follows zoom, panel resize, rotation and Align magnification. "Along the
bar" is the raw X axis for rotations 0/180° and the raw Y axis for 90/270°;
with non-isotropic pixels that axis's size is used and `note` says so.
Images without spatial calibration have no scale bar (`ScaleBarUnavailable`
carries the reason shown in the inspector).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import viewport as V
from .layout import UNIT_TO_UM
from .models import ImageRef, Panel

UNIT_LABELS = {"um": "µm", "µm": "µm", "nm": "nm", "mm": "mm"}
BAR_UNITS = ("um", "nm", "mm")


class ScaleBarUnavailable(ValueError):
    """The reason this panel cannot show a scale bar."""


@dataclass(frozen=True)
class BarGeometry:
    length_mm: float
    text: str
    axis: str                       # raw axis along the bar: "x" or "y"
    note: Optional[str] = None      # e.g. non-isotropic pixels


def unit_label(unit: str) -> str:
    return UNIT_LABELS.get(unit, unit)


def bar_geometry(panel: Panel, ref: ImageRef) -> BarGeometry:
    cal = ref.metadata.calibration
    if not cal.is_calibrated:
        raise ScaleBarUnavailable(f"{ref.filename}: no spatial calibration")
    cal_um = UNIT_TO_UM.get(cal.unit)
    if cal_um is None:
        raise ScaleBarUnavailable(f"{ref.filename}: unknown calibration unit {cal.unit!r}")
    bar = panel.scale_bar
    bar_um = UNIT_TO_UM.get(bar.unit)
    if bar_um is None:
        raise ScaleBarUnavailable(f"unknown scale bar unit {bar.unit!r}")
    axis = "x" if panel.rotation_deg in (0, 180) else "y"
    px = cal.pixel_size_x if axis == "x" else cal.pixel_size_y
    note = None
    if not math.isclose(cal.pixel_size_x, cal.pixel_size_y, rel_tol=1e-6):
        note = (f"non-isotropic pixels ({cal.pixel_size_x:g} × {cal.pixel_size_y:g} "
                f"{unit_label(cal.unit)}): the bar uses the {axis.upper()} pixel size, "
                "the axis along the bar")
    s = ref.metadata.shape
    length_px = bar.length * bar_um / (px * cal_um)
    length_mm = length_px / V.px_per_mm(panel, s.X, s.Y)
    return BarGeometry(length_mm=length_mm, text=f"{bar.length:g} {unit_label(bar.unit)}",
                       axis=axis, note=note)
