# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""
Synthetic MetaMorph fixtures.

- metamorph_xml(): an ImageDescription modelled on MetaMorph 7.8.5 output
  (the reference file Nuc_ER_ER.tif: 0.325 um/px, wavelength 630,
  _IllumSetting_ "5 TxRed", 40xOIL NA 1.3, exposure 500 ms).
- write_metamorph_tiff(): single- or multi-page TIFF with that XML per page.
- write_metamorph_planes(): a multi-page MetaMorph TIFF (one page per plane,
  tifffile axes "IYX") whose pages carry the given z-positions: a Z-stack, a
  time-lapse at constant z, or one with autofocus drift.
- write_stk(): a MetaMorph STK written byte by byte — one IFD whose strip is
  followed by the remaining planes, plus UIC1 (calibration), UIC2 (per-plane
  Z distance + creation date/time) and UIC3 (per-plane wavelength) tags, the
  layout tifffile's STK parser expects. tifffile cannot write STK itself.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Sequence

import numpy as np
import tifffile


def metamorph_xml(*, illum="5 TxRed", wavelength=630, px=0.325, exposure_ms=500,
                  acq="20251029 15:42:11.513", z_pos=1234.5, bits=16,
                  name="Nuc_ER_ER", width=1024, height=1024, stage=None) -> str:
    desc = (f"Exposure: {exposure_ms} ms&#13;&#10;Binning: 1 x 1&#13;&#10;"
            f"Region: {width} x {height}, offset at (0, 0)&#13;&#10;"
            "Acquired from Photometrics&#13;&#10;Subtract: Off&#13;&#10;Shading: Off")
    stage_xml = "" if stage is None else (
        f'\n<prop id="stage-position-x" type="float" value="{stage[0]}"/>'
        f'\n<prop id="stage-position-y" type="float" value="{stage[1]}"/>')
    return f"""<MetaData>
<prop id="Description" type="string" value="{desc}"/>
<prop id="MetaDataVersion" type="float" value="1"/>
<prop id="ApplicationName" type="string" value="MetaMorph"/>
<prop id="ApplicationVersion" type="string" value="7.8.5.0"/>
<PlaneInfo>
<prop id="plane-type" type="string" value="plane"/>
<prop id="pixel-size-x" type="int" value="{width}"/>
<prop id="pixel-size-y" type="int" value="{height}"/>
<prop id="bits-per-pixel" type="int" value="{bits}"/>
<prop id="autoscale-state" type="bool" value="on"/>
<prop id="spatial-calibration-state" type="bool" value="on"/>
<prop id="spatial-calibration-x" type="float" value="{px}"/>
<prop id="spatial-calibration-y" type="float" value="{px}"/>
<prop id="spatial-calibration-units" type="string" value="um"/>
<prop id="image-name" type="string" value="{name}"/>
<prop id="acquisition-time-local" type="time" value="{acq}"/>
<prop id="modification-time-local" type="time" value="{acq}"/>
<prop id="z-position" type="float" value="{z_pos}"/>{stage_xml}
<prop id="wavelength" type="float" value="{wavelength}"/>
<prop id="_IllumSetting_" type="string" value="{illum}"/>
<prop id="_MagNA_" type="float" value="1.3"/>
<prop id="_MagRI_" type="float" value="1.515"/>
<prop id="_MagSetting_" type="string" value="40xOIL"/>
</PlaneInfo>
<SetInfo>
<prop id="number-of-planes" type="int" value="1"/>
</SetInfo>
</MetaData>"""


def reference_like_plane(h=64, w=64, seed=0) -> np.ndarray:
    """uint16 plane in the reference range: offset ~2400, background ~2620,
    signal up to ~13989."""
    rng = np.random.default_rng(seed)
    img = rng.normal(2620, 40, (h, w))
    yy, xx = np.mgrid[:h, :w]
    img += 11000 * np.exp(-(((yy - h / 2) ** 2 + (xx - w / 2) ** 2) / (2 * (h / 8) ** 2)))
    return np.clip(img, 2412, 13989).astype(np.uint16)


def write_metamorph_tiff(path: Path, planes: Sequence[np.ndarray],
                         xmls: Sequence[str]) -> Path:
    with tifffile.TiffWriter(path) as tw:
        for plane, xml in zip(planes, xmls):
            tw.write(plane, description=xml, metadata=None, contiguous=False)
    return path


def write_metamorph_planes(path: Path, z_positions: Sequence[float], size: int = 16) -> Path:
    """Plane i is filled with the value i + 1 (easy to identify)."""
    planes = [np.full((size, size), i + 1, np.uint16) for i in range(len(z_positions))]
    xmls = [metamorph_xml(width=size, height=size, z_pos=z,
                          acq=f"20251029 15:42:{10 + i:02d}.000")
            for i, z in enumerate(z_positions)]
    return write_metamorph_tiff(path, planes, xmls)


# ----------------------------------------------------------------- STK writer
_SHORT, _LONG, _RATIONAL, _ASCII = 3, 4, 5, 2


def write_stk(path: Path, planes: np.ndarray, *, wavelengths: Sequence[float] | None = None,
              z_distances: Sequence[float] | None = None, times_ms: Sequence[int] | None = None,
              x_cal: float = 0.325, y_cal: float = 0.325, units: str = "um",
              julian_day: int = 2461042, descriptions: Sequence[str] | None = None) -> Path:
    """planes: (N, Y, X) uint16. Little-endian classic TIFF."""
    planes = np.ascontiguousarray(planes, dtype="<u2")
    n, h, w = planes.shape
    wavelengths = list(wavelengths) if wavelengths is not None else [0] * n
    z_distances = list(z_distances) if z_distances is not None else [0.0] * n
    times_ms = list(times_ms) if times_ms is not None else [1000 * i for i in range(n)]
    descriptions = list(descriptions) if descriptions is not None else [
        f"Exposure: 100 ms\r\nIllumination: {'GFP' if wl in (0, 488) else 'TxRed'}\r\n"
        f"Wavelength: {wl} nm" for wl in wavelengths]
    desc_bytes = ("\x00".join(descriptions) + "\x00").encode("ascii")

    blob = bytearray()          # out-of-IFD values, placed after the IFD
    entries: list[tuple[int, int, int, bytes | int]] = []

    def frac(v: float, den: int = 10000) -> tuple[int, int]:
        return int(round(v * den)), den

    # UIC1: (tagid, value) pairs; fractions and strings by offset.
    uic1_items: list[tuple[int, object]] = [
        (3, 1),                          # SpatialCalibration
        (4, ("frac", frac(x_cal))),      # XCalibration
        (5, ("frac", frac(y_cal))),      # YCalibration
        (6, ("str", units)),             # CalibrationUnits
        (7, ("str", "synthetic")),       # Name
    ]
    # UIC2: N x (zdist num, den, date created, time created, date mod, time mod)
    uic2 = b"".join(struct.pack("<6I", *frac(z), julian_day, t, julian_day, t)
                    for z, t in zip(z_distances, times_ms))
    uic3 = b"".join(struct.pack("<2I", *frac(wl, 1)) for wl in wavelengths)

    n_entries = 14
    ifd_offset = 8
    ifd_size = 2 + 12 * n_entries + 4
    blob_start = ifd_offset + ifd_size

    def add_blob(data: bytes) -> int:
        if len(blob) % 2:
            blob.append(0)
        off = blob_start + len(blob)
        blob.extend(data)
        return off

    desc_off = add_blob(desc_bytes)
    uic1_pairs = []
    for tagid, val in uic1_items:
        if isinstance(val, tuple) and val[0] == "frac":
            uic1_pairs.append((tagid, add_blob(struct.pack("<2I", *val[1]))))
        elif isinstance(val, tuple) and val[0] == "str":
            s = val[1].encode("ascii") + b"\x00"
            uic1_pairs.append((tagid, add_blob(struct.pack("<I", len(s)) + s)))
        else:
            uic1_pairs.append((tagid, int(val)))
    uic1_off = add_blob(b"".join(struct.pack("<2I", a, b) for a, b in uic1_pairs))
    uic2_off = add_blob(uic2)
    uic3_off = add_blob(uic3)
    if len(blob) % 2:
        blob.append(0)
    data_off = blob_start + len(blob)
    strip_bytes = h * w * 2

    entries = [
        (256, _LONG, 1, w), (257, _LONG, 1, h), (258, _SHORT, 1, 16), (259, _SHORT, 1, 1),
        (262, _SHORT, 1, 1), (270, _ASCII, len(desc_bytes), desc_off),
        (273, _LONG, 1, data_off), (277, _SHORT, 1, 1), (278, _LONG, 1, h),
        (279, _LONG, 1, strip_bytes),
        (33628, _LONG, len(uic1_pairs), uic1_off),
        (33629, _RATIONAL, n, uic2_off),
        (33630, _RATIONAL, n, uic3_off),
        (284, _SHORT, 1, 1),
    ]
    entries.sort(key=lambda e: e[0])
    assert len(entries) == n_entries

    out = bytearray(b"II*\x00" + struct.pack("<I", ifd_offset))
    out += struct.pack("<H", n_entries)
    for tag, typ, count, value in entries:
        if typ == _SHORT and count == 1:
            out += struct.pack("<HHIHH", tag, typ, count, value, 0)
        else:
            out += struct.pack("<HHII", tag, typ, count, value)
    out += struct.pack("<I", 0)
    out += blob
    assert len(out) == data_off
    out += planes.tobytes()
    Path(path).write_bytes(bytes(out))
    return Path(path)
