# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Figure consistency check (docs/design/figure-editor.md §2.9): live in the
Figures tab's Checks panel and before export.

The check never changes the figure. Each `Issue` has a `level`
(critical > major > minor), a stable `code`, the `rule` that produced it, a
message and the ids of the panels concerned (the Checks panel selects them
on click). Export refuses missing / modified sources (export.py); any other
critical issue needs an explicit tick, major / minor ones "Export anyway".

Levels — one table, `RULES` below (rule → code, level):

| rule                | code          | level    | when                                                     |
|---------------------|---------------|----------|----------------------------------------------------------|
| source.missing      | source        | critical | source file missing                                      |
| source.modified     | source        | critical | source modified (SHA-256 differs)                        |
| source.not_in_project | source      | critical | panel's image not in the project                         |
| labels              | labels        | critical | label tokens that cannot be resolved (printed as "?")    |
| off_page            | off_page      | critical | a panel, or the room its outside labels need, past the   |
|                     |               |          | page edge: cut off in the export                         |
| channels.lock       | channels      | critical | different stains (name / wavelength) on the same channel |
|                     |               |          | index inside a lock group                                |
| levels.lock         | levels        | critical | levels differ inside a lock group (not explained by bit- |
|                     |               |          | depth clamping): the lock is broken                      |
| levels.row          | levels        | major    | a row / column shows the same stain with different       |
|                     |               |          | levels, LUT, gamma or invert on panels not locked        |
|                     |               |          | together                                                 |
| bit_depth           | bit_depth     | major    | a lock group mixes bit depths (equal levels are not      |
|                     |               |          | equal intensities); levels clamped per image are said so |
| tz                  | tz            | major    | in a row, different T / Z / projection (montage members  |
|                     |               |          | left out)                                                |
| saturation          | saturation    | major    | saturated pixels in the crop (full check only)           |
| resolution.panel    | resolution    | major    | a panel's effective resolution below the preset dpi: the |
|                     |               |          | image is printed larger than its data supports           |
| scalebar_fit        | scalebar_fit  | major    | scale bar longer than the panel, or not drawable         |
| scalebar.other_scale| scalebar      | major    | a scale bar on some panels of a row only, and the others |
|                     |               |          | are at another scale (µm per page-mm differs > 1 %)       |
| inset_group         | inset_group   | major    | a linked inset group is not one inset repeated           |
| source.changed      | source        | major    | source size / date changed (re-hash pending)             |
| page_size           | page_size     | major    | page wider / taller than the preset (says when the limit |
|                     |               |          | is a default, catalog `unverified`)                      |
| font_size           | font_size     | major    | text outside the journal's font range (when given; panel |
|                     |               |          | letters only against the minimum)                        |
| resolution.inset    | resolution    | minor    | an inset's effective resolution below the preset dpi: an |
|                     |               |          | inset shows pixels enlarged on purpose; nearest-neighbour |
|                     |               |          | export keeps them as N×N blocks, nothing invented        |
| line_width          | line_width    | minor    | a line thinner than the preset minimum                   |
| scalebar.same_scale | scalebar      | minor    | a scale bar on some panels of a row only, all at the     |
|                     |               |          | same scale: fine if stated in the legend                 |
| lock_groups         | lock_groups   | minor    | a row / column shows one stain from different lock       |
|                     |               |          | groups with different settings (intended?) — once per    |
|                     |               |          | row / column, not per channel                            |
| nonlinear           | nonlinear     | minor    | gamma ≠ 1 on a visible channel (disclose in the legend)  |
| light_extract       | light_extract | minor    | the panel reads a light-archive extract                  |
| source.pending_hash | source        | minor    | SHA-256 not recorded yet                                 |

Groups compared: each row and each column of every page (layout.rows_of /
columns_of) and each lock group.

- **Rows and columns** compare channels by **stain** (figures.channel_identity:
  name / wavelength), by channel index only when the file gives neither, so
  a row of single-channel files of different wavelengths (`_w1`, `_w2`) is
  never compared across stains. Panels count by rendering unit: a lock
  group, or a panel on its own; an inset that follows its parent's
  rendering counts as its parent (no duplicate of the parent's issue).
- **Lock groups** match channels by index, as locks do.

The journal rules come from the figure's own preset snapshot
(`Figure.page.preset`: sizes) and its catalog entry, looked up by name
(journal_presets.journal_info: font range, which fields are defaults). A
user-defined preset has no catalog entry: only its sizes are checked.

Saturation needs pixels: pass `crop_plane(panel, ref, channel)` (e.g.
PanelRenderer.crop_plane); without it that rule is skipped (live check).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Literal, Optional

import numpy as np

from . import layout as L
from . import viewport as V
from .display import compute_saturation_stats
from .figures import (
    LOCKED_FIELDS, bit_depths, channel_identity, channel_mismatches, lock_members, panel_channel,
)
from .journal_presets import DEFAULT_NOTE, JournalPreset, journal_info
from .labels import group_time_unit, resolve_label
from .models import ChannelSettings, Figure, ImageRef, InsetStyle, Panel, Project
from .scalebar import ScaleBarUnavailable, bar_geometry
from .ui_text import noun, plural

Level = Literal["critical", "major", "minor"]
LEVELS: tuple[Level, ...] = ("critical", "major", "minor")
LEVEL_ORDER = {lv: i for i, lv in enumerate(LEVELS)}
LEVEL_ICON = {"critical": "⛔", "major": "⚠", "minor": "ℹ"}
LEVEL_LABEL = {"critical": "Critical", "major": "Major", "minor": "Minor"}

# rule -> (code, level). The one place levels are decided (docstring table).
RULES: dict[str, tuple[str, Level]] = {
    "source.missing": ("source", "critical"),
    "source.modified": ("source", "critical"),
    "source.not_in_project": ("source", "critical"),
    "labels": ("labels", "critical"),
    "off_page": ("off_page", "critical"),
    "channels.lock": ("channels", "critical"),
    "levels.lock": ("levels", "critical"),
    "levels.row": ("levels", "major"),
    "bit_depth": ("bit_depth", "major"),
    "tz": ("tz", "major"),
    "saturation": ("saturation", "major"),
    "resolution.panel": ("resolution", "major"),
    "scalebar_fit": ("scalebar_fit", "major"),
    "scalebar.other_scale": ("scalebar", "major"),
    "inset_group": ("inset_group", "major"),
    "source.changed": ("source", "major"),
    "page_size": ("page_size", "major"),
    "font_size": ("font_size", "major"),
    "resolution.inset": ("resolution", "minor"),
    "line_width": ("line_width", "minor"),
    "scalebar.same_scale": ("scalebar", "minor"),
    "lock_groups": ("lock_groups", "minor"),
    "nonlinear": ("nonlinear", "minor"),
    "light_extract": ("light_extract", "minor"),
    "source.pending_hash": ("source", "minor"),
}
SCALE_REL_TOL = 0.01          # same magnification: µm per page-mm within 1 %
# Sources that refuse the export outright (export.py raises ExportRefused).
REFUSING_RULES = frozenset({"source.missing", "source.modified"})

CropPlane = Callable[[Panel, ImageRef, int], np.ndarray]
SourceStatus = Callable[[ImageRef], str]          # storage.RefStatus value


@dataclass(frozen=True)
class Issue:
    level: Level
    code: str
    message: str
    panel_ids: tuple[str, ...]
    rule: str = ""

    def to_dict(self) -> dict:
        """As recorded in the export sidecar (report_version 2) and log."""
        return {"level": self.level, "code": self.code, "rule": self.rule,
                "message": self.message, "panel_ids": list(self.panel_ids)}

    def text(self) -> str:
        return f"{LEVEL_ICON[self.level]} {LEVEL_LABEL[self.level]}: {self.message}"


def _issue(rule: str, message: str, panel_ids) -> Issue:
    code, level = RULES[rule]
    return Issue(level, code, message, tuple(panel_ids), rule)


def groups(figure: Figure) -> list[tuple[str, str, list[Panel]]]:
    """(kind, name, panels) for every row, column and lock group (≥ 2 panels)."""
    out: list[tuple[str, str, list[Panel]]] = []
    for page in sorted({p.page for p in figure.panels}):
        on_page = [p for p in figure.panels if p.page == page]
        suffix = f" (page {page + 1})" if figure.page.n_pages > 1 else ""
        for i, row in enumerate(L.rows_of(on_page), start=1):
            if len(row) > 1:
                out.append(("row", f"row {i}{suffix}", row))
        for j, col in enumerate(L.columns_of(on_page), start=1):
            if len(col) > 1:
                out.append(("column", f"column {j}{suffix}", col))
    for g in dict.fromkeys(p.lock_group for p in figure.panels if p.lock_group):
        members = lock_members(figure, g)
        if len(members) > 1:
            out.append(("lock", L.lock_group_name(figure, g), members))
    return out


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _tz_key(p: Panel):
    proj = p.z_projection
    return (p.t, p.z if proj is None else None,
            (proj.z_start, proj.z_end) if proj is not None else None)


def _tz_text(key) -> str:
    t, z, proj = key
    return f"T{t + 1} " + (f"max Z{proj[0] + 1}–{proj[1] + 1}" if proj else f"Z{z + 1}")


def _off_page(p: Panel, figure: Figure) -> str:
    """Which side(s) of the page the panel or its outside labels cross."""
    w, h = figure.page.size_mm
    band = lambda n, size: n * L.label_band_mm(size)          # noqa: E731
    size = lambda lb: lb.font_size_pt or figure.page.preset.font_size_pt  # noqa: E731
    need = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    for pos in ("top", "bottom"):
        need[pos] = sum(band(1, size(lb)) for lb in p.labels if lb.position == pos)
    for pos, side in (("left-vertical", "left"), ("right-vertical", "right")):
        need[side] += sum(band(1, size(lb)) for lb in p.labels if lb.position == pos)
    sides = []
    if p.y_mm - need["top"] < -1e-6:
        sides.append("top" + (" labels" if p.y_mm >= 0 else ""))
    if p.y_mm + p.height_mm + need["bottom"] > h + 1e-6:
        sides.append("bottom" + (" labels" if p.y_mm + p.height_mm <= h else ""))
    if p.x_mm - need["left"] < -1e-6:
        sides.append("left" + (" labels" if p.x_mm >= 0 else ""))
    if p.x_mm + p.width_mm + need["right"] > w + 1e-6:
        sides.append("right" + (" labels" if p.x_mm + p.width_mm <= w else ""))
    return ("past the page edge: " + ", ".join(sides)) if sides else ""


def _default_note(info: Optional[JournalPreset], field: str) -> str:
    return f" {DEFAULT_NOTE}" if info is not None and info.is_default(field) else ""


def _page_size(figure: Figure, info: Optional[JournalPreset]) -> list[Issue]:
    preset = figure.page.preset
    w, h = figure.page.size_mm
    over = []
    if w > preset.width_mm + 1e-6:
        over.append(f"wider than the {preset.width_mm:g} mm width"
                    + _default_note(info, "width_mm"))
    if h > preset.max_height_mm + 1e-6:
        over.append(f"taller than the {preset.max_height_mm:g} mm maximum height"
                    + _default_note(info, "max_height_mm"))
    if not over:
        return []
    return [_issue("page_size", f"page {w:g} × {h:g} mm is "
                   + " and ".join(over) + f" of “{preset.name}”", ())]


_PANEL_LETTER = re.compile(r"^\(?[A-Za-z][′'’]?\)?$")


def _font_size(label: str, p: Panel, figure: Figure,
               info: Optional[JournalPreset]) -> Optional[Issue]:
    if info is None or (info.font_min_pt is None and info.font_max_pt is None):
        return None
    default = figure.page.preset.font_size_pt
    texts = [(f"label “{lb.text.strip()}”", lb.font_size_pt or default,
              bool(_PANEL_LETTER.match(lb.text.strip())))
             for lb in p.labels if lb.text.strip()]
    if p.scale_bar.visible and p.scale_bar.show_label:
        texts.append(("scale bar text", p.scale_bar.font_size_pt or default, False))
    small = [f"{what} {size:g} pt" for what, size, _l in texts
             if info.font_min_pt is not None and size < info.font_min_pt - 1e-6]
    large = [f"{what} {size:g} pt" for what, size, letter in texts
             if info.font_max_pt is not None and not letter and size > info.font_max_pt + 1e-6]
    parts = []
    if small:
        parts.append(f"text below the {info.font_min_pt:g} pt minimum"
                     + _default_note(info, "font_min_pt") + " — " + ", ".join(small))
    if large:
        parts.append(f"text above the {info.font_max_pt:g} pt maximum"
                     + _default_note(info, "font_max_pt") + " — " + ", ".join(large))
    if not parts:
        return None
    return _issue("font_size", f"{label}: " + "; ".join(parts)
                  + f" ({info.publisher})", (p.id,))



# ------------------------------------------------------------ channel comparison
def _stain(ref: ImageRef, index: int) -> tuple[str, str]:
    """What a channel shows: its identity (name / wavelength), or its index
    when the file says neither."""
    info = next((c for c in ref.metadata.channels if c.index == index), None)
    if info is not None and (info.name or info.wavelength_nm):
        return "id", channel_identity(ref, index)
    return "index", f"channel {index + 1}"


def _unit(p: Panel, figure: Figure) -> str:
    """The rendering unit a panel belongs to: an inset that follows its
    parent's rendering counts as the parent; then the lock group; else the
    panel alone."""
    seen = set()
    while (p.inset_of and (p.inset_style or InsetStyle()).follow_parent_rendering
           and p.id not in seen
           and (parent := figure.panel(p.inset_of)) is not None):
        seen.add(p.id)
        p = parent
    return f"lock:{p.lock_group}" if p.lock_group else f"panel:{p.id}"


def _values(cs: ChannelSettings) -> tuple:
    return tuple(getattr(cs, f) for f in LOCKED_FIELDS)


def _diffs(values: list[tuple]) -> str:
    out = []
    for k, f in enumerate(LOCKED_FIELDS):
        vals = list(dict.fromkeys(v[k] for v in values))
        if len(vals) > 1:
            out.append(f"{f} " + " / ".join(_fmt(v) for v in vals))
    return "; ".join(out)


def _row_levels(name: str, panels: list[Panel], figure: Figure, images: dict[str, ImageRef],
                seen: set) -> list[Issue]:
    """A row / column: each stain against itself only."""
    by_stain: dict[tuple[str, str], list[tuple[Panel, ChannelSettings]]] = {}
    for p in panels:
        ref = images.get(p.image_id)
        if ref is None:
            continue
        for cs in p.channels:
            if cs.visible and cs.index < ref.metadata.shape.C:
                by_stain.setdefault(_stain(ref, cs.index), []).append((p, cs))
    out, lock_mix, lock_ids, lock_units = [], [], [], set()
    for (_kind, stain), shown in by_stain.items():
        units: dict[str, tuple] = {}
        for p, cs in shown:
            units.setdefault(_unit(p, figure), _values(cs))
        if len(set(units.values())) < 2:
            continue
        key = (stain, frozenset(units.items()))
        if key in seen:                       # the same comparison through insets / another line
            continue
        seen.add(key)
        ids = [p.id for p, _cs in shown]
        if all(u.startswith("lock:") for u in units):
            lock_mix.append(stain)
            lock_ids += ids
            lock_units |= set(units)
        else:
            out.append(_issue("levels.row", f"{name}: {stain} shown with different "
                              + _diffs(list(units.values())) + " (panels not locked together)",
                              ids))
    if lock_mix:
        out.append(_issue("lock_groups", f"{name}: {len(lock_units)} lock groups show "
                          + ", ".join(lock_mix) + " with different settings — intended?",
                          dict.fromkeys(lock_ids)))
    return out


def _clamped(values: tuple, fs: int) -> tuple:
    """`values` as figures.set_levels stores them on an image of full scale `fs`."""
    v = dict(zip(LOCKED_FIELDS, values))
    white = min(fs, max(int(v["white"]), 1))
    v["black"], v["white"] = max(0, min(int(v["black"]), white - 1)), white
    return tuple(v[f] for f in LOCKED_FIELDS)


def _lock_levels(name: str, members: list[Panel], images: dict[str, ImageRef]
                 ) -> tuple[list[Issue], list[str]]:
    """Inside a lock group (by index). Returns (critical issues, channels
    whose differences are only bit-depth clamping)."""
    out, clamped = [], []
    for idx in sorted({c.index for p in members for c in p.channels}):
        shown = [(p, cs, images[p.image_id].metadata.full_scale) for p in members
                 if (cs := panel_channel(p, idx)) is not None and cs.visible
                 and p.image_id in images and idx < images[p.image_id].metadata.shape.C]
        values = [_values(cs) for _p, cs, _fs in shown]
        if len(set(values)) < 2:
            continue
        if any(all(_clamped(ref, fs) == _values(cs) for _p, cs, fs in shown) for ref in values):
            clamped.append(f"channel {idx + 1}")
            continue
        out.append(_issue("levels.lock", f"{name}: channel {idx + 1} shown with different "
                          + _diffs(values) + " inside the lock group (lock broken)",
                          [p.id for p, _cs, _fs in shown]))
    return out, clamped


# ------------------------------------------------------------ scale bars
def _um_per_page_mm(p: Panel, ref: Optional[ImageRef]) -> Optional[float]:
    if ref is None or not ref.metadata.calibration.is_calibrated:
        return None
    cal = ref.metadata.calibration
    to_um = L.UNIT_TO_UM.get(cal.unit)
    if to_um is None:
        return None
    s = ref.metadata.shape
    return cal.pixel_size_x * to_um * V.px_per_mm(p, s.X, s.Y)


def _scalebars(name: str, panels: list[Panel], images: dict[str, ImageRef]) -> list[Issue]:
    """Ordinary panels and insets separately (insets start without a bar)."""
    out = []
    for kind_panels, what in (([p for p in panels if not p.inset_of], "panel"),
                              ([p for p in panels if p.inset_of], "inset")):
        with_bar = [p for p in kind_panels if p.scale_bar.visible]
        if not with_bar or len(with_bar) == len(kind_panels):
            continue
        scales = [_um_per_page_mm(p, images.get(p.image_id)) for p in with_bar]
        same = all(
            (s := _um_per_page_mm(p, images.get(p.image_id))) is not None
            and any(b is not None and math.isclose(s, b, rel_tol=SCALE_REL_TOL) for b in scales)
            for p in kind_panels if not p.scale_bar.visible)
        head = f"{name}: scale bar on {len(with_bar)} of {plural(len(kind_panels), what)}"
        ids = [p.id for p in kind_panels]
        if same:
            out.append(_issue("scalebar.same_scale", f"{head} — same scale, fine if stated in "
                              "the legend", ids))
        else:
            out.append(_issue("scalebar.other_scale", f"{head} — the others are at another "
                              "scale (or uncalibrated): each needs its own bar", ids))
    return out


def effective_ppi(p: Panel, ref: ImageRef) -> float:
    """Image pixels per inch of page, as laid out (viewport.px_per_mm)."""
    s = ref.metadata.shape
    return V.px_per_mm(p, s.X, s.Y) * 25.4


SMOOTH_INSET_WARNING = ("smooth resampling interpolates the enlarged inset pixels (values not in "
                        "the raw data); use nearest for insets")


def insets_below_dpi(project: Project, figure: Figure) -> list[Panel]:
    """Insets whose effective resolution is below the page preset dpi (their
    pixels are enlarged in a raster export)."""
    dpi = figure.page.preset.dpi
    return [p for p in figure.panels if p.inset_of and (ref := project.images.get(p.image_id))
            is not None and effective_ppi(p, ref) < dpi]


def check_figure(project: Project, figure: Figure,
                 source_status: Optional[SourceStatus] = None,
                 crop_plane: Optional[CropPlane] = None) -> list[Issue]:
    images = project.images
    preset = figure.page.preset
    info = journal_info(preset.name)
    issues: list[Issue] = _page_size(figure, info)

    # ---- groups: rows, columns (by stain), lock groups (by index)
    seen: set = set()
    for kind, name, panels in groups(figure):
        if kind == "lock":
            lock_issues, clamped = _lock_levels(name, panels, images)
            issues += lock_issues
            depths = bit_depths(panels, images)
            if len(depths) > 1:
                issues.append(_issue("bit_depth", f"{name}: mixes "
                                     + " and ".join(f"{b}-bit" for b in depths) + " images"
                                     + ("; levels clamped to each image's range on "
                                        + ", ".join(clamped) if clamped else ""),
                                     [p.id for p in panels]))
            for m in channel_mismatches(panels, images):
                issues.append(_issue("channels.lock", f"{name}: {m.message()} — different stains "
                                     "on one locked channel", m.panel_ids))
            continue
        issues += _row_levels(name, panels, figure, images, seen)
        if kind != "row":
            continue
        compared = [p for p in panels if not (p.lock_group or "").startswith("montage:")]
        keys = list(dict.fromkeys(_tz_key(p) for p in compared))
        if len(keys) > 1:
            issues.append(_issue("tz", f"{name}: different time / Z — "
                                 + ", ".join(_tz_text(k) for k in keys),
                                 [p.id for p in compared]))
        issues += _scalebars(name, panels, images)

    # ---- per panel
    min_line = preset.min_line_pt
    for k, p in enumerate(figure.panels, start=1):
        ref = images.get(p.image_id)
        label = f"panel {k}" + (f" ({ref.filename})" if ref else "")
        if ref is None:
            issues.append(_issue("source.not_in_project", f"{label}: image not in the project",
                                 (p.id,)))
            continue
        if ref.extract is not None:
            issues.append(_issue("light_extract", f"{label}: panel uses a light-archive "
                                 "extract, not the original raw file", (p.id,)))
        nonlinear = [c.index + 1 for c in p.channels if c.visible and abs(c.gamma - 1.0) > 1e-9]
        if nonlinear:
            issues.append(_issue("nonlinear", f"{label}: non-linear display (gamma ≠ 1) on "
                                 f"{noun(len(nonlinear), 'channel')} "
                                 f"{', '.join(map(str, nonlinear))} — disclose in the legend",
                                 (p.id,)))
        s = ref.metadata.shape
        ppi = effective_ppi(p, ref)
        if ppi < preset.dpi and p.inset_of:
            issues.append(_issue("resolution.inset", f"inset {k}" + f" ({ref.filename})"
                                 f": pixels shown enlarged ×{preset.dpi / ppi:.1f} ({ppi:.0f} ppi "
                                 f"< {preset.dpi} dpi); fine for an inset, exported "
                                 "nearest-neighbour", (p.id,)))
        elif ppi < preset.dpi:
            issues.append(_issue("resolution.panel", f"{label}: effective resolution {ppi:.0f} ppi "
                                 f"< {preset.dpi} dpi (upsampled in raster exports)", (p.id,)))
        thin = []
        if p.border.visible and p.border.width_pt < min_line:
            thin.append(f"border {p.border.width_pt:g} pt")
        if p.scale_bar.visible and p.scale_bar.thickness_pt < min_line:
            thin.append(f"scale bar {p.scale_bar.thickness_pt:g} pt")
        thin += [f"{sh.kind} {sh.width_pt:g} pt" for sh in p.shapes if sh.width_pt < min_line]
        st = p.inset_style if p.inset_of else None
        if st is not None and (st.show_on_parent or st.border_on_inset) and st.width_pt < min_line:
            thin.append(f"inset line {st.width_pt:g} pt")
        if thin:
            issues.append(_issue("line_width", f"{label}: lines below the {min_line:g} pt "
                                 "minimum — " + ", ".join(thin), (p.id,)))
        font = _font_size(label, p, figure, info)
        if font is not None:
            issues.append(font)
        off = _off_page(p, figure)
        if off:
            issues.append(_issue("off_page", f"{label}: {off} — cut off in the export", (p.id,)))
        if p.scale_bar.visible:
            try:
                g = bar_geometry(p, ref)
                if g.length_mm > p.width_mm - 2.0:
                    issues.append(_issue("scalebar_fit", f"{label}: scale bar ({g.text} = "
                                         f"{g.length_mm:.1f} mm) longer than the panel "
                                         f"({p.width_mm:.1f} mm)", (p.id,)))
            except ScaleBarUnavailable as exc:
                issues.append(_issue("scalebar_fit", f"{label}: scale bar not drawn — {exc}",
                                     (p.id,)))
        unit = group_time_unit(lock_members(figure, p.lock_group), images) if p.lock_group else None
        missing = list(dict.fromkeys(t for lb in p.labels
                                     for t in resolve_label(lb.text, p, ref, project, unit)[1]))
        if missing:
            issues.append(_issue("labels", f"{label}: {noun(len(missing), 'value')} not found, "
                                 "printed as “?”: " + ", ".join(missing), (p.id,)))
        if crop_plane is not None:
            sat = []
            for c in p.channels:
                if not c.visible or c.index >= s.C:
                    continue
                try:
                    st_ = compute_saturation_stats(crop_plane(p, ref, c.index), ref.metadata.full_scale)
                except Exception:                      # unreadable: reported by the source rule
                    continue
                if st_.saturated_count:
                    sat.append(f"channel {c.index + 1}: {st_.saturated_count} px "
                               f"({st_.solid_saturated_count} in solid regions)")
            if sat:
                issues.append(_issue("saturation", f"{label}: saturated pixels in the crop — "
                                     + "; ".join(sat), (p.id,)))

    # ---- replicated insets (defensive)
    from .insets import group_invariant_issues
    for msg, ids in group_invariant_issues(figure, images):
        issues.append(_issue("inset_group", msg, ids))

    # ---- sources (once per image)
    for image_id in dict.fromkeys(p.image_id for p in figure.panels):
        ref = images.get(image_id)
        if ref is None:
            continue
        ids = tuple(p.id for p in figure.panels if p.image_id == image_id)
        status = source_status(ref) if source_status else ("pending_hash" if ref.sha256 is None else "ok")
        if status == "missing":
            issues.append(_issue("source.missing", f"{ref.filename}: source file missing", ids))
        elif status == "modified":
            issues.append(_issue("source.modified", f"{ref.filename}: source MODIFIED since "
                                 "import (SHA-256 differs)", ids))
        elif status == "changed":
            issues.append(_issue("source.changed", f"{ref.filename}: source size/date changed "
                                 "(re-hash to verify)", ids))
        elif status == "pending_hash":
            issues.append(_issue("source.pending_hash", f"{ref.filename}: SHA-256 not recorded "
                                 "yet", ids))

    # ---- dedupe (rows, columns and lock groups can report the same thing)
    done, out = set(), []
    for i in issues:
        key = (i.code, i.message.split(": ", 1)[-1], frozenset(i.panel_ids))
        if key not in done:
            done.add(key)
            out.append(i)
    return sorted(out, key=lambda i: LEVEL_ORDER[i.level])


def counts(issues: list[Issue]) -> dict[str, int]:
    return {lv: sum(1 for i in issues if i.level == lv) for lv in LEVELS}


def summary(issues: list[Issue]) -> str:
    """'1 critical, 3 major, 2 minor' (levels with no issue left out)."""
    return ", ".join(f"{n} {lv}" for lv, n in counts(issues).items() if n) or "no issue"


def critical_needing_consent(issues: list[Issue]) -> list[Issue]:
    """Critical issues that do not refuse the export by themselves: the user
    must tick "Export anyway" for them."""
    return [i for i in issues if i.level == "critical" and i.rule not in REFUSING_RULES]
