# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Label tokens (docs/design/figure-editor.md §2.5), resolved at render time.

A label's text is stored as typed, tokens included; what is drawn is
recomputed from the panel and the image metadata at every render, so a
label follows T/Z changes, channel edits, annotations, replaced images...

| Token            | Value                                                        |
|------------------|--------------------------------------------------------------|
| [channels]       | visible channels: stain if set, else the channel name, each  |
|                  | in its LUT colour, joined by " / "                           |
| [time]           | t − Panel.time_offset_s, "12 s" below 1 min, "4.5 min" below |
|                  | 1 h, else "1.25 h"                                           |
| [time:s]         | seconds                                                      |
| [time:min]       | minutes                                                      |
| [time:hh:mm]     | hours:minutes ("-0:05" before the offset)                    |
| [z]              | Z plane number (1-based); a projection shows "1–5"           |
| [z:um]           | Z position = plane × Z step − Panel.z_offset, in µm          |
| [zoom]           | magnification vs the whole image fitted in the panel ("2×")  |
| [image]          | file name without extension                                  |
| [dataset]        | datasets holding the image, joined by ", "                   |
| [tags]           | the image's tags, joined by ", "                             |
| [kv:Key]         | value of the image's key–value pair "Key"                    |

A value that cannot be resolved (no time stamps, no Z step, no such key,
no tag...) is drawn as "?" and reported in `missing`, never silently
dropped. Bracketed text that is not a token is kept literally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Optional

from . import viewport as V
from .display import lut_hex
from .layout import UNIT_TO_UM
from .models import ImageRef, Panel, Project

MISSING = "?"
CHANNEL_SEPARATOR = " / "
TOKEN_RE = re.compile(
    r"\[(channels|time(?::(?:s|min|hh:mm))?|z(?::um)?|zoom|image|dataset|tags|kv:[^\[\]]+)\]")
TOKENS = ("[channels]", "[time]", "[time:s]", "[time:min]", "[time:hh:mm]", "[z]", "[z:um]",
          "[zoom]", "[image]", "[dataset]", "[tags]", "[kv:Key]")


@dataclass(frozen=True)
class Run:
    """A piece of label text; color None = the label's own colour."""
    text: str
    color: Optional[str] = None


def _num(v: float, decimals: int = 1) -> str:
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def format_time(seconds: float, unit: str = "auto") -> str:
    if unit == "s":
        return f"{_num(seconds)} s"
    if unit == "min":
        return f"{_num(seconds / 60.0)} min"
    if unit == "hh:mm":
        total = int(round(abs(seconds) / 60.0))
        sign = "-" if seconds < 0 and total else ""
        return f"{sign}{total // 60}:{total % 60:02d}"
    a = abs(seconds)
    if a < 60:
        return f"{_num(seconds)} s"
    if a < 3600:
        return f"{_num(seconds / 60.0)} min"
    return f"{_num(seconds / 3600.0, 2)} h"


def channel_name(ref: ImageRef, index: int) -> str:
    stain = ref.channel_stains.get(index, "").strip()
    if stain:
        return stain
    info = next((c for c in ref.metadata.channels if c.index == index), None)
    return (info.name.strip() if info and info.name else "") or f"Ch{index + 1}"


def channel_runs(panel: Panel, ref: ImageRef) -> list[Run]:
    """Visible channels, each coloured like its LUT. Grey/white LUTs use
    the label's own colour (white text on a white page would vanish)."""
    runs: list[Run] = []
    for cs in sorted(panel.channels, key=lambda c: c.index):
        if not cs.visible or cs.index >= ref.metadata.shape.C:
            continue
        if runs:
            runs.append(Run(CHANNEL_SEPARATOR))
        try:
            colour = lut_hex(cs.lut)
        except ValueError:
            colour = None
        runs.append(Run(channel_name(ref, cs.index), None if colour == "#FFFFFF" else colour))
    return runs


def _token_value(token: str, panel: Panel, ref: ImageRef,
                 project: Optional[Project]) -> Optional[str]:
    m = ref.metadata
    if token.startswith("time"):
        unit = token.split(":", 1)[1] if ":" in token else "auto"
        offs = m.time_offsets_s
        if not offs or panel.t >= len(offs) or offs[panel.t] is None:
            return None
        return format_time(offs[panel.t] - panel.time_offset_s, unit)
    if token == "z":
        p = panel.z_projection
        return f"{p.z_start + 1}–{p.z_end + 1}" if p else str(panel.z + 1)
    if token == "z:um":
        step = m.calibration.z_step
        k = UNIT_TO_UM.get(m.calibration.unit)
        if not step or k is None:
            return None
        p = panel.z_projection

        def um(z: int) -> str:
            return _num(z * step * k - panel.z_offset, 2)
        return f"{um(p.z_start)}–{um(p.z_end)} µm" if p else f"{um(panel.z)} µm"
    if token == "zoom":
        s = m.shape
        return f"{_num(V.zoom_percent(panel, s.X, s.Y) / 100.0, 2)}×"
    if token == "image":
        return PurePath(ref.filename).stem or None
    if token == "dataset":
        names = [d.name for d in project.datasets_containing(ref.id)] if project else []
        return ", ".join(names) or None
    if token == "tags":
        return ", ".join(ref.tags) or None
    if token.startswith("kv:"):
        v = ref.key_values.get(token[3:].strip())
        return v if v not in (None, "") else None
    return None


def resolve_label(text: str, panel: Panel, ref: Optional[ImageRef],
                  project: Optional[Project] = None) -> tuple[list[Run], list[str]]:
    """-> (runs to draw, tokens that could not be resolved)."""
    runs: list[Run] = []
    missing: list[str] = []
    pos = 0
    for m in TOKEN_RE.finditer(text):
        if m.start() > pos:
            runs.append(Run(text[pos:m.start()]))
        token = m.group(1)
        if ref is None:
            runs.append(Run(MISSING))
            missing.append(m.group(0))
        elif token == "channels":
            ch = channel_runs(panel, ref)
            if ch:
                runs.extend(ch)
            else:
                runs.append(Run(MISSING))
                missing.append(m.group(0))
        else:
            value = _token_value(token, panel, ref, project)
            if value is None:
                runs.append(Run(MISSING))
                missing.append(m.group(0))
            else:
                runs.append(Run(value))
        pos = m.end()
    if pos < len(text):
        runs.append(Run(text[pos:]))
    return [r for r in runs if r.text], missing


def plain_text(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)
