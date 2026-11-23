# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
TIFF-family reader: plain TIFF, MetaMorph TIFF (XML ImageDescription) and
MetaMorph STK (UIC tags), all through tifffile.

Lazy access
-----------
- Uncompressed, contiguous data (the normal case for MetaMorph output) are
  memory-mapped: get_plane() touches only the bytes of that plane.
- Otherwise each plane is read from its own TIFF page.
- A layout that is neither (compressed multi-plane page) is read whole only
  if it is small (EAGER_LIMIT_BYTES); beyond that it is refused rather than
  risking an 8 GB machine.

STK axis inference
------------------
An STK stores N planes in one IFD. Pycrograph maps them to (T, Z, C):
- C: if the per-plane wavelengths (UIC3) repeat with a period c > 1, the
  planes are taken as channel-interleaved (index = m * c + channel).
- Z vs T: Z if the Z distances (UIC2) between successive plane groups are all
  non-zero, else T (time-lapse). Same rule as tifffile's own STK series.

Generic plane axis (TIFF series)
--------------------------------
tifffile names a plane axis it cannot identify `I` (or `Q`, ...). When a
series has exactly ONE such axis with more than one plane, and the file has
no T or Z axis of its own, the axis is mapped to T or Z:
- `plane_axis="T"` / `"Z"` (ImageRef.plane_axis_override, or the mapping
  recorded at import, see readers.open_image_ref) forces it;
- otherwise, for a MetaMorph file with one page per plane, Z if the per-plane
  z-positions form a regular series (`regular_z_step`: every value present,
  strictly monotonic, at least MIN_PLANES_FOR_Z planes, relative spread of the
  step ≤ Z_STEP_REL_TOL); else T, the previous behaviour (a time-lapse with
  autofocus moves z irregularly, or not at all).
The decision is recorded in metadata.raw["tiff.axis_inference"], e.g.
"I→Z (MetaMorph z-position, step 0.5 µm)" or "I→T (default)".
Other generic axes keep the old rule (T); samples (S) become C.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Sequence

import numpy as np
import tifffile

from .. import __version__
from ..models import Calibration, ChannelInfo, ImageMetadata, Shape5D
from .base import ImageSource, UnsupportedImageError
from .metamorph import PlaneSummary, parse_description, plane_summary

EAGER_LIMIT_BYTES = 512 * 1024 * 1024
_SUPPORTED_DTYPES = {np.dtype("uint8"): "uint8", np.dtype("uint16"): "uint16"}
Z_STEP_REL_TOL = 0.10       # max std(|Δz|) / mean(|Δz|) for a regular Z series
MIN_PLANES_FOR_Z = 3        # two planes cannot tell a Z step from an autofocus move
PlaneAxis = Literal["T", "Z"]
_UNIT_TO_UM = {"um": 1.0, "µm": 1.0, "micron": 1.0, "microns": 1.0, "micrometer": 1.0,
               "nm": 1e-3, "mm": 1e3}


def regular_z_step(zs: Sequence[Optional[float]]) -> Optional[float]:
    """Mean |Δz| if the per-plane z-positions form a regular Z series (every
    value present, at least MIN_PLANES_FOR_Z of them, strictly monotonic,
    near-constant non-zero step); None otherwise."""
    if len(zs) < MIN_PLANES_FOR_Z or any(z is None for z in zs):
        return None
    d = np.diff(np.asarray(zs, dtype=float))
    if not (np.all(d > 0) or np.all(d < 0)):
        return None
    step = np.abs(d)
    mean = float(np.mean(step))
    if mean <= 0 or float(np.std(step)) / mean > Z_STEP_REL_TOL:
        return None
    return mean


def axis_letter_of_inference(text: Optional[str]) -> Optional[str]:
    """"I→Z (…)" -> "Z" (None if the text is not an axis inference)."""
    if not text or "→" not in text:
        return None
    letter = text.split("→", 1)[1][:1]
    return letter if letter in ("T", "Z") else None


def _norm_unit(unit: Any) -> Optional[str]:
    if not isinstance(unit, str):
        return None
    u = unit.strip().lower().replace("\\u00b5", "µ")
    if u in _UNIT_TO_UM:
        return "um" if _UNIT_TO_UM[u] == 1.0 else u
    return None


class TiffSource(ImageSource):
    # Revision 2: generic plane axis mapped to T or Z (z-position inference,
    # plane_axis override). Bump when the same file can give other metadata.
    reader_revision = 2
    reader_version = (f"pycrograph {__version__} (tiff reader {reader_revision}); "
                      f"tifffile {tifffile.__version__}")

    def __init__(self, path: str | Path, plane_axis: Optional[PlaneAxis] = None,
                 plane_axis_reason: str = "user override"):
        """plane_axis forces the mapping of a single generic plane axis (see
        the module docstring); plane_axis_reason is what the inference
        string says about it ("user override", "as imported")."""
        super().__init__(path)
        if plane_axis not in (None, "T", "Z"):
            raise ValueError(f"plane_axis must be 'T', 'Z' or None, not {plane_axis!r}")
        self._plane_axis = plane_axis
        self._plane_axis_reason = plane_axis_reason
        # File letter of the generic plane axis that can be T or Z (e.g. "I"),
        # None when the file has none (STK, hyperstacks, single planes).
        self.generic_plane_axis: Optional[str] = None
        self.reader_name = "tiff"
        self._tif = tifffile.TiffFile(str(self.path))
        self._mm: Optional[np.ndarray] = None
        self._eager: Optional[np.ndarray] = None
        self._pages: Optional[list] = None
        try:
            if self._tif.pages[0].is_stk:
                self._setup_stk()
            else:
                self._setup_series()
        except Exception:
            self._tif.close()
            raise

    # ------------------------------------------------------------------ API
    @property
    def metadata(self) -> ImageMetadata:
        return self._metadata

    @property
    def access_mode(self) -> str:
        """'memmap' | 'pages' | 'eager' — exposed for tests and the info panel."""
        if self._mm is not None:
            return "memmap"
        if self._pages is not None:
            return "pages"
        return "eager"

    def close(self) -> None:
        mm, self._mm = self._mm, None
        if mm is not None and getattr(mm, "_mmap", None) is not None:
            try:
                mm._mmap.close()
            except (BufferError, ValueError):
                pass  # still referenced by a returned plane; GC will close it
        self._eager = None
        self._tif.close()

    # -------------------------------------------------------------- helpers
    def _check_dtype(self, dtype) -> str:
        dt = np.dtype(dtype)
        name = _SUPPORTED_DTYPES.get(dt.newbyteorder("="))
        if name is None:
            raise UnsupportedImageError(
                f"{self.path.name}: sample format {dt} is not supported yet "
                "(Pycrograph reads 8- and 16-bit unsigned integer images)."
            )
        return name

    def _byteorder_dtype(self, dtype) -> np.dtype:
        return np.dtype(dtype).newbyteorder(self._tif.byteorder)

    def _memmap(self, offset: int, shape: tuple[int, ...], dtype) -> np.ndarray:
        return np.memmap(self.path, dtype=self._byteorder_dtype(dtype), mode="r",
                         offset=int(offset), shape=tuple(int(s) for s in shape))

    def _eager_or_refuse(self, nbytes: int, reader) -> np.ndarray:
        if nbytes > EAGER_LIMIT_BYTES:
            raise UnsupportedImageError(
                f"{self.path.name}: compressed multi-plane layout of "
                f"{nbytes / 2**20:.0f} MB cannot be read lazily; refusing to load it whole."
            )
        return np.asarray(reader())

    # ============================================================ STK files
    def _setup_stk(self) -> None:
        self.reader_name = "metamorph-stk"
        tif = self._tif
        page = tif.pages[0]
        meta = tif.stk_metadata or {}
        n = int(meta.get("NumberPlanes", 1))
        dtype_name = self._check_dtype(page.dtype)
        if page.samplesperpixel != 1:
            raise UnsupportedImageError(f"{self.path.name}: RGB STK files are not supported.")
        Y, X = int(page.imagelength), int(page.imagewidth)

        if page.compression == 1 and len(page.dataoffsets) >= 1 and page.is_contiguous:
            self._mm = self._memmap(page.dataoffsets[0], (n, Y, X), page.dtype)
        else:
            self._eager = self._eager_or_refuse(
                n * Y * X * page.dtype.itemsize, lambda: tif.series[0].asarray()
            ).reshape(n, Y, X)

        wl = meta.get("Wavelengths")
        wl = np.asarray(wl, dtype=float) if wl is not None and len(wl) == n else None
        C = _channel_period(wl, n)
        M = n // C
        zdist = np.asarray(meta.get("ZDistance", np.zeros(n)), dtype=float)
        if len(zdist) != n:
            zdist = np.zeros(n)
        group_z = np.add.reduceat(zdist, np.arange(0, n, C)) if n else zdist
        is_z = M > 1 and bool(np.all(group_z[:-1] != 0))
        T, Z = (1, M) if is_z else (M, 1)
        self._stk_C = C
        self._stk_is_z = is_z

        descs = meta.get("PlaneDescriptions") or []
        props0 = parse_description(descs[0]) if descs else {}
        s0 = plane_summary(props0)

        # Calibration: UIC1 tags first, plane-description XML as fallback.
        cal = Calibration()
        unit = _norm_unit(meta.get("CalibrationUnits"))
        xcal, ycal = meta.get("XCalibration"), meta.get("YCalibration")
        if meta.get("SpatialCalibration", 0) and xcal and ycal and unit:
            scale = _UNIT_TO_UM.get(unit, 1.0)
            cal = Calibration(pixel_size_x=float(xcal) * scale, pixel_size_y=float(ycal) * scale,
                              unit="um", source="stk-uic")
        elif s0.calibrated:
            cal = Calibration(pixel_size_x=s0.pixel_size_x, pixel_size_y=s0.pixel_size_y,
                              unit=s0.unit, source="metamorph-xml")
        if is_z:
            steps = np.abs(group_z[:-1])
            if steps.size:
                cal.z_step = float(np.mean(steps)) * _UNIT_TO_UM.get(unit or "um", 1.0)

        channels = []
        for c in range(C):
            sc = plane_summary(parse_description(descs[c])) if c < len(descs) else s0
            wavelength = float(wl[c]) if wl is not None and wl[c] > 0 else sc.wavelength_nm
            channels.append(ChannelInfo(index=c, name=sc.illumination or (
                f"{wavelength:g} nm" if wavelength else f"Channel {c + 1}"),
                wavelength_nm=wavelength, exposure_ms=sc.exposure_ms))

        time_offsets = None
        acq = s0.acquisition_time
        dt = meta.get("DatetimeCreated")
        if dt is not None and len(dt) == n:
            dt = np.asarray(dt, dtype="datetime64[ns]")
            if not is_z and T > 1:
                firsts = dt[::C][:T]
                time_offsets = [float(x) for x in (firsts - firsts[0]) / np.timedelta64(1, "s")]
            if acq is None:
                acq = str(np.datetime_as_string(dt[0], unit="ms"))

        raw = dict(s0.raw)
        raw.update({"stk.NumberPlanes": n, "stk.Name": meta.get("Name", ""),
                    "stk.axis_inference": f"C={C} ({'interleaved' if C > 1 else 'single'}), "
                                          f"{'Z' if is_z else 'T'}={M}"})
        self._metadata = ImageMetadata(
            shape=Shape5D(T=T, Z=Z, C=C, Y=Y, X=X), dtype=dtype_name,
            bit_depth=s0.bits_per_pixel or np.dtype(dtype_name).itemsize * 8,
            calibration=cal, channels=channels, objective=s0.objective,
            numerical_aperture=s0.numerical_aperture, acquisition_time=acq,
            time_offsets_s=time_offsets, raw=raw,
        )

    def _stk_index(self, t: int, z: int, c: int) -> int:
        m = z if self._stk_is_z else t
        return m * self._stk_C + c

    # ============================================= plain / MetaMorph TIFF
    def _setup_series(self) -> None:
        tif = self._tif
        series = tif.series[0]
        dtype_name = self._check_dtype(series.dtype)
        axes, shape = series.axes, tuple(int(s) for s in series.shape)
        if "Y" not in axes or "X" not in axes:
            raise UnsupportedImageError(f"{self.path.name}: no Y/X axes in {axes!r}")

        # Map file axes onto T/Z/C. Unknown plane axes (I, Q, ...) become T,
        # samples (S, e.g. RGB) become C. A single generic axis may become Z
        # instead (forced, or inferred below from MetaMorph z-positions).
        generic = [i for i, (a, n) in enumerate(zip(axes, shape))
                   if a not in "TZCSYX" and n > 1]
        gi = generic[0] if len(generic) == 1 and "T" not in axes and "Z" not in axes else None
        if gi is not None:
            self.generic_plane_axis = axes[gi]
        canon: list[str] = []
        for i, (a, n) in enumerate(zip(axes, shape)):
            if i == gi:
                canon.append(self._plane_axis or "T")
                continue
            if a in "YX":
                canon.append(a)
            elif a == "S":
                canon.append("C")
            elif a in "TZC":
                canon.append(a)
            elif n == 1:
                canon.append("_")          # degenerate axis, always index 0
            else:
                canon.append("T")
        if any(canon.count(k) > 1 for k in "TZC"):
            raise UnsupportedImageError(
                f"{self.path.name}: cannot map axes {axes!r} onto T/Z/C unambiguously."
            )
        self._axes_canon = canon
        self._series_shape = shape
        self._samples_last = axes.endswith("S")

        def axis_sizes() -> dict[str, int]:
            return {k: (shape[canon.index(k)] if k in canon else 1) for k in "TZCYX"}
        sizes = axis_sizes()

        if series.dataoffset is not None:
            self._mm = self._memmap(series.dataoffset, shape, series.dtype)
        else:
            lead = [i for i, a in enumerate(axes) if a not in "YXS"]
            n_planes = int(np.prod([shape[i] for i in lead])) if lead else 1
            pages = list(series.pages)
            if len(pages) == n_planes and all(p is not None for p in pages):
                self._pages = pages
                self._lead_axes = lead
            else:
                self._eager = self._eager_or_refuse(series.size * series.dtype.itemsize,
                                                    series.asarray)

        # ---- metadata -------------------------------------------------
        first_desc = tif.pages[0].description if len(tif.pages) else ""
        props0 = parse_description(first_desc) if first_desc else {}
        is_mm = bool(props0) and ("MetaData" in first_desc[:64] or "_IllumSetting_" in props0
                                  or "spatial-calibration-x" in props0)
        self.reader_name = "metamorph-tiff" if is_mm else "tiff"
        s0 = plane_summary(props0) if is_mm else PlaneSummary()

        if s0.calibrated:
            cal = Calibration(pixel_size_x=s0.pixel_size_x, pixel_size_y=s0.pixel_size_y,
                              unit=s0.unit, source="metamorph-xml")
        else:
            cal = _calibration_from_tags(tif)

        # Per-channel / per-time metadata from page descriptions, when every
        # plane has its own page (MetaMorph multi-page TIFF).
        n_pages = len(tif.pages)
        plane_count = sizes["T"] * sizes["Z"] * (1 if self._samples_last else sizes["C"])
        per_page = is_mm and n_pages == plane_count and n_pages > 1

        def summary_at(t: int, z: int, c: int) -> PlaneSummary:
            if not per_page:
                return s0
            idx = self._page_index(t, z, c)
            try:
                return plane_summary(parse_description(tif.pages[idx].description))
            except ValueError:
                return s0

        inference = None
        if gi is not None:
            letter = axes[gi]
            if self._plane_axis is not None:
                inference = f"{letter}→{self._plane_axis} ({self._plane_axis_reason})"
            elif per_page:
                # canon[gi] is "T" here: walk the generic axis as T, channel 0.
                zs = [summary_at(t, 0, 0).z_position for t in range(shape[gi])]
                step = regular_z_step(zs)
                if step is not None:
                    canon[gi] = "Z"
                    sizes = axis_sizes()
                    inference = f"{letter}→Z (MetaMorph z-position, step {step:g} µm)"
                else:
                    inference = f"{letter}→T (MetaMorph z-position not a regular Z series)"
            else:
                inference = f"{letter}→T (default)"

        channels = []
        for c in range(sizes["C"]):
            sc = summary_at(0, 0, c) if not self._samples_last else s0
            name = sc.illumination
            if self._samples_last:
                name = ("Red", "Green", "Blue", "Alpha")[c] if c < 4 else f"Sample {c}"
            channels.append(ChannelInfo(index=c, name=name or (
                f"{sc.wavelength_nm:g} nm" if sc.wavelength_nm else f"Channel {c + 1}"),
                wavelength_nm=None if self._samples_last else sc.wavelength_nm,
                exposure_ms=sc.exposure_ms))

        time_offsets = None
        if per_page and sizes["T"] > 1:
            times = [summary_at(t, 0, 0).acquisition_time for t in range(sizes["T"])]
            if all(times):
                t0 = np.datetime64(times[0])
                time_offsets = [float((np.datetime64(x) - t0) / np.timedelta64(1, "s")) for x in times]
        if per_page and sizes["Z"] > 1:
            zs = [summary_at(0, z, 0).z_position for z in range(sizes["Z"])]
            if gi is not None:
                # Generic axis forced to Z: a step only for a regular series
                # (an irregular z-position is no Z step: none is recorded).
                cal.z_step = regular_z_step(zs)
            elif all(v is not None for v in zs):
                cal.z_step = float(np.mean(np.abs(np.diff(zs))))

        bit_depth = s0.bits_per_pixel or np.dtype(dtype_name).itemsize * 8
        if bit_depth > np.dtype(dtype_name).itemsize * 8:
            bit_depth = np.dtype(dtype_name).itemsize * 8
        raw = dict(s0.raw)
        raw["tiff.axes"] = axes
        if inference:
            raw["tiff.axis_inference"] = inference
        if s0.application:
            raw["application"] = s0.application
        self._metadata = ImageMetadata(
            shape=Shape5D(**sizes), dtype=dtype_name, bit_depth=bit_depth,
            calibration=cal, channels=channels, objective=s0.objective,
            numerical_aperture=s0.numerical_aperture,
            acquisition_time=s0.acquisition_time, time_offsets_s=time_offsets, raw=raw,
        )

    def _page_index(self, t: int, z: int, c: int) -> int:
        """Flat plane index over the non-YX(S) axes, in file order."""
        want = {"T": t, "Z": z, "C": c, "_": 0}
        idx, dims = [], []
        for a, n in zip(self._axes_canon, self._series_shape):
            if a in "YX" or (a == "C" and self._samples_last):
                continue
            idx.append(want[a])
            dims.append(n)
        return int(np.ravel_multi_index(idx, dims)) if dims else 0

    # ================================================================ read
    def _read_plane(self, t: int, z: int, c: int) -> np.ndarray:
        if self.reader_name == "metamorph-stk":
            i = self._stk_index(t, z, c)
            arr = self._mm if self._mm is not None else self._eager
            return np.asarray(arr[i])

        if self._pages is not None:
            data = self._pages[self._page_index(t, z, c)].asarray()
            if self._samples_last:
                data = data[..., c]
            return np.ascontiguousarray(data)

        arr = self._mm if self._mm is not None else self._eager
        want = {"T": t, "Z": z, "C": c, "_": 0}
        key = []
        for a in self._axes_canon:
            if a in "YX" or (a == "C" and self._samples_last):
                key.append(slice(None))
            else:
                key.append(want[a])
        data = arr[tuple(key)]
        if self._samples_last:
            data = data[..., c]
        return np.asarray(data)


def _channel_period(wl: Optional[np.ndarray], n: int) -> int:
    """Smallest period c > 1 with which the per-plane wavelengths repeat,
    provided they are not all equal; else 1."""
    if wl is None or n < 2 or np.all(wl == wl[0]):
        return 1
    for c in range(2, min(n, 8) + 1):
        if n % c:
            continue
        block = wl.reshape(n // c, c)
        if np.all(block == block[0]) and len(set(block[0].tolist())) == c:
            return c
    return 1


def _calibration_from_tags(tif: tifffile.TiffFile) -> Calibration:
    """ImageJ (unit + resolution) or a centimetre ResolutionUnit. Inch-based
    resolutions (72/300 dpi) are print settings, not microscope calibration,
    and are ignored."""
    page = tif.pages[0]
    tags = page.tags
    xres, yres = tags.get("XResolution"), tags.get("YResolution")
    if xres is None or yres is None:
        return Calibration()
    try:
        xr = xres.value[0] / xres.value[1]
        yr = yres.value[0] / yres.value[1]
    except (TypeError, ZeroDivisionError, IndexError):
        return Calibration()
    if xr <= 0 or yr <= 0:
        return Calibration()
    ij = tif.imagej_metadata or {}
    unit = _norm_unit(ij.get("unit"))
    if unit:
        scale = _UNIT_TO_UM[unit]
        cal = Calibration(pixel_size_x=scale / xr, pixel_size_y=scale / yr, unit="um",
                          source="tiff-resolution")
        if ij.get("spacing"):
            cal.z_step = float(ij["spacing"]) * scale
        return cal
    ru = tags.get("ResolutionUnit")
    if ru is not None and int(ru.value) == 3:  # centimetre
        return Calibration(pixel_size_x=1e4 / xr, pixel_size_y=1e4 / yr, unit="um",
                           source="tiff-resolution")
    return Calibration()
