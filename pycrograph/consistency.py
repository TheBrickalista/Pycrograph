# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Figure consistency check (docs/design/figure-editor.md §2.9), before export.

Warnings only — the check never changes the figure. Each `Issue` has a
severity ("error" > "warning" > "info"), a stable `code`, a message and
the ids of the panels concerned (the dialog selects them on click).

Groups compared: each row and each column of every page (layout.rows_of /
columns_of) and each lock group.

| code          | severity | rule                                                           |
|---------------|----------|----------------------------------------------------------------|
| levels        | warning  | in a group, the same VISIBLE channel with different black,     |
|               |          | white, gamma, LUT or invert                                    |
| tz            | warning  | in a row, different T / Z / projection (montage members, which |
|               |          | differ on purpose, are left out)                               |
| scalebar      | warning  | in a row, a scale bar on some panels but not the others        |
|               |          | (ordinary panels and insets compared separately)               |
| channels      | warning  | in a group, the same channel index with a different channel   |
|               |          | name / wavelength (figures.channel_mismatches)                 |
| bit_depth     | warning  | a lock group mixes images of different bit depths: equal       |
|               |          | levels are not equal intensities (selects the group's panels)  |
| nonlinear     | info     | gamma ≠ 1 on a visible channel                                 |
| source        | error    | source missing or modified (SHA-256 differs)                   |
|               | warning  | source changed (size/date; re-hash pending) or hash not        |
|               |          | recorded yet                                                   |
| page_size     | warning  | page wider than the preset width or taller than the preset    |
|               |          | maximum height (the message says when that limit is a default |
|               |          | rather than a journal rule: catalog `unverified`)              |
| font_size     | warning  | label or scale-bar text smaller than the journal's            |
|               |          | font_min_pt or larger than font_max_pt (catalog entry of the  |
|               |          | figure's preset; skipped when the journal gives no range).    |
|               |          | A one-letter label (panel letter "a", "B'") is only checked   |
|               |          | against the minimum: journals size them separately            |
| resolution    | warning  | effective resolution below the page preset dpi (upsampled)    |
| line_width    | warning  | border, scale bar, shape or inset line thinner than the       |
|               |          | preset minimum line width                                      |
| saturation    | warning  | saturated pixels in the panel's crop (visible channels)        |
| off_page      | warning  | a panel, or the room its outside labels need (estimated from  |
|               |          | the font size, layout.label_band_mm), past the page edge: cut |
|               |          | off in the export                                              |
| scalebar_fit  | warning  | scale bar longer than the panel, or unavailable (reason)       |
| labels        | warning  | label tokens that cannot be resolved (drawn as "?")            |
| inset_group   | warning  | a linked inset group is not one inset repeated: page size,     |
|               |          | orientation, magnification, region (or µm size) differ, or a  |
|               |          | pair cannot be linked (defensive: the editor keeps them equal) |
| light_extract | info     | the panel reads a light-archive extract, not the original raw  |
|               |          | file (only the archived planes / region exist)                 |

The journal rules come from the figure's own preset snapshot
(`Figure.page.preset`: sizes) and its catalog entry, looked up by name
(journal_presets.journal_info: font range, which fields are defaults). A
user-defined preset has no catalog entry: only its sizes are checked.

Saturation needs pixels: pass `crop_plane(panel, ref, channel)` (e.g.
PanelRenderer.crop_plane); without it that rule is skipped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable, Literal, Optional

import numpy as np

from . import layout as L
from . import viewport as V
from .display import compute_saturation_stats
from .figures import LOCKED_FIELDS, bit_depths, channel_mismatches, lock_members, panel_channel
from .journal_presets import DEFAULT_NOTE, JournalPreset, journal_info
from .labels import group_time_unit, resolve_label
from .models import Figure, ImageRef, Panel, Project
from .scalebar import ScaleBarUnavailable, bar_geometry
from .ui_text import noun, plural

Severity = Literal["error", "warning", "info"]
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
SEVERITY_ICON = {"error": "⛔", "warning": "⚠", "info": "ℹ"}

CropPlane = Callable[[Panel, ImageRef, int], np.ndarray]
SourceStatus = Callable[[ImageRef], str]          # storage.RefStatus value


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    panel_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["panel_ids"] = list(self.panel_ids)
        return d

    def text(self) -> str:
        return f"{SEVERITY_ICON[self.severity]} {self.message}"


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


def _levels(name: str, panels: list[Panel], images: dict[str, ImageRef]) -> list[Issue]:
    out = []
    indices = sorted({c.index for p in panels for c in p.channels})
    for idx in indices:
        showing = [p for p in panels
                   if (cs := panel_channel(p, idx)) is not None and cs.visible
                   and (r := images.get(p.image_id)) is not None and idx < r.metadata.shape.C]
        if len(showing) < 2:
            continue
        diffs = []
        for f in LOCKED_FIELDS:
            vals = list(dict.fromkeys(getattr(panel_channel(p, idx), f) for p in showing))
            if len(vals) > 1:
                diffs.append(f"{f} " + " / ".join(_fmt(v) for v in vals))
        if diffs:
            out.append(Issue("warning", "levels",
                             f"{name}: channel {idx + 1} shown with different " + "; ".join(diffs),
                             tuple(p.id for p in showing)))
    return out


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
    return [Issue("warning", "page_size", f"page {w:g} × {h:g} mm is "
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
    return Issue("warning", "font_size", f"{label}: " + "; ".join(parts)
                 + f" ({info.publisher})", (p.id,))


def check_figure(project: Project, figure: Figure,
                 source_status: Optional[SourceStatus] = None,
                 crop_plane: Optional[CropPlane] = None) -> list[Issue]:
    images = project.images
    preset = figure.page.preset
    info = journal_info(preset.name)
    issues: list[Issue] = _page_size(figure, info)

    # ---- groups: rows, columns, lock groups
    for kind, name, panels in groups(figure):
        issues += _levels(name, panels, images)
        if kind == "lock":
            depths = bit_depths(panels, images)
            if len(depths) > 1:
                issues.append(Issue("warning", "bit_depth", f"{name}: mixes "
                                    + " and ".join(f"{b}-bit" for b in depths) + " images",
                                    tuple(p.id for p in panels)))
        for m in channel_mismatches(panels, images):
            issues.append(Issue("warning", "channels", f"{name}: {m.message()}", m.panel_ids))
        if kind != "row":
            continue
        compared = [p for p in panels if not (p.lock_group or "").startswith("montage:")]
        keys = list(dict.fromkeys(_tz_key(p) for p in compared))
        if len(keys) > 1:
            issues.append(Issue("warning", "tz", f"{name}: different time / Z — "
                                + ", ".join(_tz_text(k) for k in keys),
                                tuple(p.id for p in compared)))
        # Insets start without a bar: compare ordinary panels with each other
        # and insets with each other, never a parent's bar with its inset.
        for kind_panels, what in (([p for p in panels if not p.inset_of], "panel"),
                                  ([p for p in panels if p.inset_of], "inset")):
            with_bar = [p for p in kind_panels if p.scale_bar.visible]
            if with_bar and len(with_bar) < len(kind_panels):
                issues.append(Issue("warning", "scalebar",
                                    f"{name}: scale bar on {len(with_bar)} of "
                                    f"{plural(len(kind_panels), what)}",
                                    tuple(p.id for p in kind_panels)))

    # ---- per panel
    min_line = preset.min_line_pt
    for k, p in enumerate(figure.panels, start=1):
        ref = images.get(p.image_id)
        label = f"panel {k}" + (f" ({ref.filename})" if ref else "")
        if ref is None:
            issues.append(Issue("error", "source", f"{label}: image not in the project", (p.id,)))
            continue
        if ref.extract is not None:
            issues.append(Issue("info", "light_extract", f"{label}: panel uses a light-archive "
                                "extract, not the original raw file", (p.id,)))
        nonlinear = [c.index + 1 for c in p.channels if c.visible and abs(c.gamma - 1.0) > 1e-9]
        if nonlinear:
            issues.append(Issue("info", "nonlinear", f"{label}: non-linear display (gamma ≠ 1) "
                                f"on {noun(len(nonlinear), 'channel')} {', '.join(map(str, nonlinear))}", (p.id,)))
        s = ref.metadata.shape
        ppi = V.px_per_mm(p, s.X, s.Y) * 25.4
        if ppi < preset.dpi:
            issues.append(Issue("warning", "resolution", f"{label}: effective resolution "
                                f"{ppi:.0f} ppi < {preset.dpi} dpi (upsampled in raster exports)",
                                (p.id,)))
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
            issues.append(Issue("warning", "line_width", f"{label}: lines below the {min_line:g} pt "
                                f"minimum — " + ", ".join(thin), (p.id,)))
        font = _font_size(label, p, figure, info)
        if font is not None:
            issues.append(font)
        off = _off_page(p, figure)
        if off:
            issues.append(Issue("warning", "off_page", f"{label}: {off} — cut off in the export",
                                (p.id,)))
        if p.scale_bar.visible:
            try:
                g = bar_geometry(p, ref)
                if g.length_mm > p.width_mm - 2.0:
                    issues.append(Issue("warning", "scalebar_fit", f"{label}: scale bar "
                                        f"({g.text} = {g.length_mm:.1f} mm) longer than the panel "
                                        f"({p.width_mm:.1f} mm)", (p.id,)))
            except ScaleBarUnavailable as exc:
                issues.append(Issue("warning", "scalebar_fit", f"{label}: scale bar not drawn — {exc}",
                                    (p.id,)))
        unit = group_time_unit(lock_members(figure, p.lock_group), images) if p.lock_group else None
        missing = list(dict.fromkeys(t for lb in p.labels
                                     for t in resolve_label(lb.text, p, ref, project, unit)[1]))
        if missing:
            issues.append(Issue("warning", "labels", f"{label}: {noun(len(missing), 'value')} not found, drawn as “?”: "
                                + ", ".join(missing), (p.id,)))
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
                issues.append(Issue("warning", "saturation", f"{label}: saturated pixels in the "
                                    "crop — " + "; ".join(sat), (p.id,)))

    # ---- replicated insets (defensive)
    from .insets import group_invariant_issues
    for msg, ids in group_invariant_issues(figure, images):
        issues.append(Issue("warning", "inset_group", msg, tuple(ids)))

    # ---- sources (once per image)
    for image_id in dict.fromkeys(p.image_id for p in figure.panels):
        ref = images.get(image_id)
        if ref is None:
            continue
        ids = tuple(p.id for p in figure.panels if p.image_id == image_id)
        status = source_status(ref) if source_status else ("pending_hash" if ref.sha256 is None else "ok")
        if status == "missing":
            issues.append(Issue("error", "source", f"{ref.filename}: source file missing", ids))
        elif status == "modified":
            issues.append(Issue("error", "source", f"{ref.filename}: source MODIFIED since import "
                                "(SHA-256 differs)", ids))
        elif status == "changed":
            issues.append(Issue("warning", "source", f"{ref.filename}: source size/date changed "
                                "(re-hash to verify)", ids))
        elif status == "pending_hash":
            issues.append(Issue("warning", "source", f"{ref.filename}: SHA-256 not recorded yet",
                                ids))

    # ---- dedupe (rows, columns and lock groups can report the same thing)
    seen, out = set(), []
    for i in issues:
        key = (i.code, i.message.split(": ", 1)[-1], frozenset(i.panel_ids))
        if key not in seen:
            seen.add(key)
            out.append(i)
    return sorted(out, key=lambda i: SEVERITY_ORDER[i.severity])


def summary(issues: list[Issue]) -> str:
    counts = {s: sum(1 for i in issues if i.severity == s) for s in SEVERITY_ORDER}
    return ", ".join(plural(n, s, "info" if s == "info" else None)
                     for s, n in counts.items() if n) or "no issue"
