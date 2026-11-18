# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
MetaMorph metadata parsing (MetaMorph 7.x, tested against 7.8.5 output).

MetaMorph writes its metadata into the TIFF ImageDescription tag in one of
two forms:

1. XML (single-plane TIFF, multi-page TIFF — one document per page)::

       <MetaData>
         <prop id="Description" type="string" value="Exposure: 500 ms&#13;&#10;..."/>
         <PlaneInfo>
           <prop id="spatial-calibration-x" type="float" value="0.325"/>
           <prop id="_IllumSetting_" type="string" value="5 TxRed"/>
           ...
         </PlaneInfo>
         <SetInfo>...</SetInfo>
       </MetaData>

2. Plain "Key: value" lines (STK plane descriptions, NUL-separated per plane).

Both are reduced to a flat dict of typed values (MetaMorphProps), from which
plane_summary() extracts what Pycrograph needs. The XML parser is fed text
from the user's own files, but it is still run with entity expansion limited
to the XML predefined entities (ElementTree does not resolve external
entities or DTDs).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Optional

MetaMorphProps = dict[str, Any]

_EXPOSURE_RE = re.compile(r"Exposure\s*(?:Time)?\s*[:=]\s*([0-9]*\.?[0-9]+)\s*(ms|s|us|µs)?", re.I)
_KV_LINE_RE = re.compile(r"^\s*([^:\r\n]{1,64}?)\s*:\s*(.*?)\s*$")


def looks_like_metamorph_xml(text: str | None) -> bool:
    return bool(text) and text.lstrip().startswith("<MetaData")


def _typed(value: str, kind: str) -> Any:
    kind = (kind or "").lower()
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
        if kind == "bool":
            return value.strip().lower() in ("on", "true", "1", "yes")
    except ValueError:
        return value
    return value


def parse_metamorph_xml(text: str) -> MetaMorphProps:
    """Flatten every <prop>/<custom-prop> (id, type, value) into a dict.

    Raises ValueError if the text is not well-formed XML."""
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("MetaMorph XML must not contain DTD/entity declarations")
    try:
        root = ET.fromstring(text.strip().rstrip("\x00"))
    except ET.ParseError as exc:
        raise ValueError(f"Invalid MetaMorph XML: {exc}") from exc
    props: MetaMorphProps = {}
    for el in root.iter():
        pid = el.attrib.get("id")
        if pid is None or "value" not in el.attrib:
            continue
        props[pid] = _typed(el.attrib["value"], el.attrib.get("type", "string"))
    return props


def parse_key_value_text(text: str) -> MetaMorphProps:
    """Parse 'Key: value' lines (MetaMorph STK plane descriptions)."""
    props: MetaMorphProps = {}
    for line in re.split(r"[\r\n]+", text or ""):
        m = _KV_LINE_RE.match(line)
        if not m or not m.group(2):
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        props[key] = value
    # Keep the whole text too: exposure is parsed out of it.
    props.setdefault("Description", text or "")
    return props


def parse_description(text: str | None) -> MetaMorphProps:
    if not text:
        return {}
    if looks_like_metamorph_xml(text):
        return parse_metamorph_xml(text)
    return parse_key_value_text(text)


def parse_metamorph_time(value: Any) -> Optional[str]:
    """'20251029 15:42:11.513' -> '2025-10-29T15:42:11.513' (local time, no tz)."""
    if not isinstance(value, str):
        return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})\s+(\d{2}):(\d{2}):(\d{2})(\.\d+)?$", value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}{frac or ''}"


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"[-+]?[0-9]*\.?[0-9]+", str(value))
    return float(m.group(0)) if m else None


def _first(props: MetaMorphProps, *keys: str) -> Any:
    for k in keys:
        if k in props and props[k] not in (None, ""):
            return props[k]
    return None


@dataclass
class PlaneSummary:
    """What Pycrograph uses from one MetaMorph plane's metadata."""
    pixel_size_x: Optional[float] = None
    pixel_size_y: Optional[float] = None
    unit: str = "um"
    calibrated: bool = False
    bits_per_pixel: Optional[int] = None
    illumination: Optional[str] = None
    wavelength_nm: Optional[float] = None
    exposure_ms: Optional[float] = None
    objective: Optional[str] = None
    numerical_aperture: Optional[float] = None
    acquisition_time: Optional[str] = None
    z_position: Optional[float] = None
    application: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


# Raw keys copied verbatim into ImageMetadata.raw (provenance / info panel).
_KEEP_RAW = (
    "ApplicationName", "ApplicationVersion", "image-name", "bits-per-pixel",
    "spatial-calibration-state", "spatial-calibration-x", "spatial-calibration-y",
    "spatial-calibration-units", "acquisition-time-local", "modification-time-local",
    "wavelength", "_IllumSetting_", "_MagSetting_", "_MagNA_", "_MagRI_",
    "camera-binning-x", "camera-binning-y", "z-position", "stage-position-x",
    "stage-position-y", "camera-chip-offset-x", "camera-chip-offset-y",
    "Illumination", "Wavelength", "Exposure", "Binning",
)


def plane_summary(props: MetaMorphProps) -> PlaneSummary:
    s = PlaneSummary()
    cal_state = props.get("spatial-calibration-state")
    px = _float_or_none(props.get("spatial-calibration-x"))
    py = _float_or_none(props.get("spatial-calibration-y"))
    if cal_state is not False and px and py and px > 0 and py > 0:
        s.pixel_size_x, s.pixel_size_y, s.calibrated = px, py, True
        unit = str(props.get("spatial-calibration-units") or "um").strip()
        s.unit = {"µm": "um", "micron": "um", "microns": "um"}.get(unit.lower(), unit)

    bpp = props.get("bits-per-pixel")
    if isinstance(bpp, int) and 1 <= bpp <= 16:
        s.bits_per_pixel = bpp

    illum = _first(props, "_IllumSetting_", "Illumination", "illumination")
    s.illumination = str(illum).strip() if illum is not None else None
    s.wavelength_nm = _float_or_none(_first(props, "wavelength", "Wavelength"))
    if s.wavelength_nm is not None and s.wavelength_nm <= 0:
        s.wavelength_nm = None

    # Exposure: an explicit prop if any, otherwise the Description text.
    exp = None
    for k, v in props.items():
        if k.lower().startswith("exposure") and k != "Description":
            exp = _parse_exposure(f"Exposure: {v}")
            if exp is not None:
                break
    if exp is None:
        exp = _parse_exposure(str(props.get("Description", "")))
    s.exposure_ms = exp

    mag = _first(props, "_MagSetting_", "Objective")
    s.objective = str(mag).strip() if mag is not None else None
    s.numerical_aperture = _float_or_none(_first(props, "_MagNA_", "NA"))
    s.acquisition_time = parse_metamorph_time(props.get("acquisition-time-local"))
    s.z_position = _float_or_none(props.get("z-position"))
    app = _first(props, "ApplicationName")
    ver = _first(props, "ApplicationVersion")
    s.application = f"{app} {ver}".strip() if app else None
    s.raw = {k: props[k] for k in _KEEP_RAW if k in props}
    return s


def _parse_exposure(text: str) -> Optional[float]:
    m = _EXPOSURE_RE.search(text or "")
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "ms").lower()
    if unit == "s":
        value *= 1000.0
    elif unit in ("us", "µs"):
        value /= 1000.0
    return value
