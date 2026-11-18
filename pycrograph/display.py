# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Display pipeline (display-only; raw arrays are never written to).

Per visible channel:
    raw uint8/uint16
      -> window [black, white]  (clip to 0..1)
      -> gamma                  (out = x ** (1/gamma), PysternBlot convention)
      -> invert                 (optional, 1 - x)
      -> colour LUT             (x * (r, g, b))
then all channels are summed (additive composite), clipped to 1 and
quantised once to RGB8. Rotation (multiples of 90°) and flips are applied to
the final RGB, i.e. they are view transforms, never resampling.

The window/gamma/invert curve is evaluated once per channel into a lookup
table indexed by the raw value (65536 or 256 entries), so a render costs one
gather + one multiply-add per channel and pixel.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

import numpy as np

from .models import ChannelInfo, ChannelSettings, SaturationStats

NAMED_LUTS: dict[str, tuple[float, float, float]] = {
    "grey": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "cyan": (0.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
}
LUT_NAMES = tuple(NAMED_LUTS)
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")

# Fallback colours for multichannel images without wavelength information.
_CYCLE = ("green", "magenta", "cyan", "yellow", "red", "blue")


def lut_rgb(lut: str) -> tuple[float, float, float]:
    """Named LUT or '#RRGGBB' -> (r, g, b) in 0..1."""
    key = (lut or "").strip().lower()
    if key in ("gray",):
        key = "grey"
    if key in NAMED_LUTS:
        return NAMED_LUTS[key]
    m = _HEX_RE.match(lut or "")
    if m:
        v = int(m.group(1), 16)
        return ((v >> 16) / 255.0, ((v >> 8) & 0xFF) / 255.0, (v & 0xFF) / 255.0)
    raise ValueError(f"Unknown LUT {lut!r} (use {', '.join(LUT_NAMES)} or #RRGGBB)")


def lut_hex(lut: str) -> str:
    r, g, b = lut_rgb(lut)
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def default_lut_for_wavelength(wavelength_nm: float | None) -> str | None:
    """Rough mapping: <480 blue (DAPI/Hoechst), <570 green (488 ex / GFP em),
    else red (TxRed, Cy5...). MetaMorph's `wavelength` may be excitation or
    emission depending on the illumination setting, hence the wide bands."""
    if not wavelength_nm:
        return None
    if wavelength_nm < 480:
        return "blue"
    if wavelength_nm < 570:
        return "green"
    return "red"


def default_luts(channels: Sequence[ChannelInfo]) -> list[str]:
    """Single channel -> grey. Several -> by wavelength, falling back to a
    colour cycle, never giving two channels the same colour."""
    if len(channels) <= 1:
        return ["grey"] * len(channels)
    out: list[str] = []
    for ch in channels:
        lut = default_lut_for_wavelength(ch.wavelength_nm)
        if lut is None or lut in out:
            lut = next((c for c in _CYCLE if c not in out), "grey")
        out.append(lut)
    return out


# ------------------------------------------------------------------ curves
def window_curve(black: int, white: int, gamma: float = 1.0, invert: bool = False,
                 n_values: int = 65536) -> np.ndarray:
    """float32 LUT of length n_values: raw value -> displayed intensity 0..1.
    Same arithmetic as PysternBlot's apply_levels_uint16 (minus the uint16
    rounding): white <= black is widened to black + 1, gamma <= 0 -> 1."""
    black = int(black)
    white = int(white)
    if white <= black:
        white = black + 1
    gamma = float(gamma) if gamma and gamma > 0 else 1.0
    x = (np.arange(n_values, dtype=np.float32) - black) / float(white - black)
    np.clip(x, 0.0, 1.0, out=x)
    if gamma != 1.0:
        np.power(x, 1.0 / gamma, out=x)
    if invert:
        x = 1.0 - x
    return x


def render_composite(planes: Sequence[np.ndarray], settings: Sequence[ChannelSettings]) -> np.ndarray:
    """Additive composite of the visible channels -> (Y, X, 3) uint8.

    planes[i] is the raw plane for settings[i]. Hidden channels are skipped;
    with none visible the result is black."""
    if len(planes) != len(settings):
        raise ValueError("planes and settings must have the same length")
    if not planes:
        raise ValueError("no planes")
    h, w = planes[0].shape
    acc = np.zeros((h, w, 3), dtype=np.float32)
    for plane, cs in zip(planes, settings):
        if not cs.visible:
            continue
        if plane.shape != (h, w):
            raise ValueError("all planes must have the same shape")
        if plane.dtype == np.uint16:
            n = 65536
        elif plane.dtype == np.uint8:
            n = 256
        else:
            raise TypeError(f"unsupported plane dtype {plane.dtype}")
        curve = window_curve(cs.black, cs.white, cs.gamma, cs.invert, n)
        v = curve[plane]                      # gather, (h, w) float32
        r, g, b = lut_rgb(cs.lut)
        for k, f in enumerate((r, g, b)):
            if f:
                acc[..., k] += v * f if f != 1.0 else v
    np.clip(acc, 0.0, 1.0, out=acc)
    return np.ascontiguousarray(np.round(acc * 255.0).astype(np.uint8))


def apply_orientation(rgb: np.ndarray, rotation_deg: int = 0, flip_horizontal: bool = False,
                      flip_vertical: bool = False) -> np.ndarray:
    """Flips first, then clockwise rotation (PysternBlot order)."""
    out = rgb
    if flip_horizontal:
        out = out[:, ::-1]
    if flip_vertical:
        out = out[::-1]
    k = (int(rotation_deg) // 90) % 4
    if k:
        out = np.rot90(out, k=-k)             # negative k = clockwise
    return np.ascontiguousarray(out)


# ------------------------------------------------------------ auto levels
def auto_window(plane: np.ndarray, low_pct: float = 0.1, high_pct: float = 99.9,
                max_samples: int = 1_000_000) -> tuple[int, int]:
    """Percentile window, computed on a strided subsample for big planes.
    A suggestion only — the user's black/white are what is recorded."""
    flat = plane.ravel()
    if flat.size > max_samples:
        flat = flat[:: int(np.ceil(flat.size / max_samples))]
    lo, hi = np.percentile(flat, [low_pct, high_pct])
    lo, hi = int(np.floor(lo)), int(np.ceil(hi))
    if hi <= lo:
        hi = lo + 1
    return lo, hi


# --------------------------------------------------------------- saturation
# SHARED-CORE candidate (from PysternBlot: pysternblot/image_utils.py
# _shift_mask / compute_saturation_stats) — full_scale is a parameter here
# because microscopy cameras can be 12/14-bit inside a 16-bit container.
def _shift_mask(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    h, w = mask.shape
    out = np.zeros_like(mask)
    y0, y1 = max(0, -dy), min(h, h - dy)
    x0, x1 = max(0, -dx), min(w, w - dx)
    if y0 >= y1 or x0 >= x1:
        return out
    out[y0:y1, x0:x1] = mask[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    return out


def compute_saturation_stats(plane: np.ndarray, full_scale: int) -> SaturationStats:
    """Clipped pixels (== full_scale) and the 'solid' part surviving a 3x3
    erosion (a saturated structure rather than hot pixels). Computed on raw
    data, never on the display."""
    mask = plane >= full_scale
    total = int(plane.size)
    n_sat = int(np.count_nonzero(mask))
    solid = 0
    if n_sat:
        eroded = mask.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    eroded &= _shift_mask(mask, dy, dx)
        solid = int(np.count_nonzero(eroded))
    return SaturationStats(
        max_value=int(plane.max()) if total else 0,
        full_scale=int(full_scale),
        saturated_count=n_sat,
        total_pixels=total,
        saturated_fraction=(n_sat / total) if total else 0.0,
        solid_saturated_count=solid,
    )


def histogram(plane: np.ndarray, max_val: int, bins: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    return np.histogram(plane.ravel(), bins=bins, range=(0, max_val))


def make_thumbnail(rgb: np.ndarray, max_side: int = 256) -> np.ndarray:
    """Integer-stride downsample (no interpolation) for library thumbnails."""
    h, w = rgb.shape[:2]
    step = max(1, int(np.ceil(max(h, w) / max_side)))
    return np.ascontiguousarray(rgb[::step, ::step])


def default_channel_settings(channels: Sequence[ChannelInfo], planes: Iterable[np.ndarray],
                             full_scale: int) -> list[ChannelSettings]:
    """Initial settings at import: percentile window per channel, default LUTs."""
    luts = default_luts(channels)
    out = []
    for ch, lut, plane in zip(channels, luts, planes):
        lo, hi = auto_window(plane)
        hi = min(hi, full_scale)
        lo = min(lo, hi - 1)
        out.append(ChannelSettings(index=ch.index, lut=lut, black=max(lo, 0), white=max(hi, 1)))
    return out
