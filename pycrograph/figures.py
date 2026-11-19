# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Figure editing without Qt: create figures, add panels from library images,
move / resize / delete panels, snapshot undo/redo.

Gestures
--------
Every user gesture goes through FigureEditor.gesture() (one-shot) or
FigureEditor.edit(..., final=False) ... flush() (continuous: a slider drag
applies every tick to the model, but logs once):

    before = snapshot(figure)  ->  mutate  ->  log + push undo + save

The log step diffs the figure against the snapshot and writes ONE entry per
panel added, removed or changed (target_id = panel id, asset_sha256 = the
panel's image hash), plus one figure-level entry if page/name/legend changed.
So a move of five selected panels is one undo step and five log entries.
Field paths name the changed leaves (``channels[1].white``, ``crop.x``;
channels are keyed by channel index); a single changed leaf is logged as
old -> new values, several as {path: value} dicts.
Undo/redo restore a snapshot and are themselves logged the same way
(operation ``figure.undo`` / ``figure.redo``): the log is append-only.

Snapshots are ``Figure.model_dump(mode="json")`` — a figure is a few KB, so
whole-figure snapshots are simpler and safer than inverse commands
(docs/design/figure-editor.md §5).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional

from . import viewport
from .models import (
    ChannelSettings, Crop, Figure, ImageRef, PageSetup, Panel, PanelBorder, PanelLabel, Project,
    ScaleBar, ZProjection,
)
from .storage import log_operation

DEFAULT_PANEL_WIDTH_MM = 40.0
DEFAULT_GAP_MM = 2.0
MIN_PANEL_MM = 2.0
GEOMETRY_FIELDS = ("x_mm", "y_mm", "width_mm", "height_mm")
UNDO_LIMIT = 200

Snapshot = dict[str, Any]
Geometry = tuple[float, float, float, float]          # x, y, w, h in mm


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def snapshot(figure: Figure) -> Snapshot:
    return figure.model_dump(mode="json")


def restore(figure: Figure, snap: Snapshot) -> None:
    """Replace the figure's content with a snapshot, in place (the object
    stays the one held by the project)."""
    fresh = Figure.model_validate(snap)
    for name in Figure.model_fields:
        setattr(figure, name, getattr(fresh, name))


# ------------------------------------------------------------------ figures
def create_figure(project: Project, name: str, page: Optional[PageSetup] = None) -> Figure:
    fig = Figure(id=new_id(), name=name.strip() or "Figure", page=page or PageSetup())
    project.figures.append(fig)
    log_operation(project, "figure.create", target_type="figure", target_id=fig.id,
                  new_value={"name": fig.name, "page": fig.page.model_dump(mode="json")})
    return fig


def rename_figure(project: Project, figure: Figure, name: str) -> None:
    old = figure.name
    figure.name = name.strip() or old
    if figure.name != old:
        log_operation(project, "figure.rename", target_type="figure", target_id=figure.id,
                      field="name", old_value=old, new_value=figure.name)


def next_figure_name(project: Project) -> str:
    names = {f.name for f in project.figures}
    n = len(project.figures) + 1
    while f"Figure {n}" in names:
        n += 1
    return f"Figure {n}"


# ------------------------------------------------------------------- panels
def oriented_size(w: float, h: float, rotation_deg: int) -> tuple[float, float]:
    return (h, w) if rotation_deg in (90, 270) else (w, h)


def panel_aspect(panel: Panel, ref: Optional[ImageRef]) -> float:
    """Displayed width / height of the panel's image region (after rotation)."""
    if panel.crop is not None:
        w, h = panel.crop.w, panel.crop.h
    elif ref is not None:
        w, h = ref.metadata.shape.X, ref.metadata.shape.Y
    else:
        return panel.width_mm / panel.height_mm
    w, h = oriented_size(w, h, panel.rotation_deg)
    return w / h


def panel_from_image(ref: ImageRef, x_mm: float = 0.0, y_mm: float = 0.0,
                     width_mm: float = DEFAULT_PANEL_WIDTH_MM, page: int = 0) -> Panel:
    """A new panel showing the whole image with a COPY of the image's
    current display settings (channels, T/Z, orientation). Later edits to the
    library image do not change the panel (design §7.2: snapshot)."""
    d = ref.display
    s = ref.metadata.shape
    panel = Panel(
        id=new_id(), image_id=ref.id, page=page, x_mm=x_mm, y_mm=y_mm,
        t=min(d.t, s.T - 1), z=min(d.z, s.Z - 1),
        channels=[cs.model_copy(deep=True) for cs in d.channels],
        crop=Crop(x=0, y=0, w=s.X, h=s.Y),
        rotation_deg=d.rotation_deg, flip_horizontal=d.flip_horizontal,
        flip_vertical=d.flip_vertical,
        width_mm=width_mm, height_mm=width_mm,
    )
    panel.height_mm = width_mm / panel_aspect(panel, ref)
    return panel


def snap_value(v: float, grid_mm: float) -> float:
    return round(round(v / grid_mm) * grid_mm, 6)


def next_free_position(figure: Figure, width_mm: float, height_mm: float,
                       page: int = 0, gap_mm: float = DEFAULT_GAP_MM) -> tuple[float, float]:
    """Right of the last panel on the page, wrapping below all panels when
    the page width is exceeded."""
    page_w, _ = figure.page.size_mm
    on_page = [p for p in figure.panels if p.page == page]
    if not on_page:
        return 0.0, 0.0
    last = on_page[-1]
    x = last.x_mm + last.width_mm + gap_mm
    y = last.y_mm
    if x + width_mm > page_w + 1e-6:
        x = 0.0
        y = max(p.y_mm + p.height_mm for p in on_page) + gap_mm
    if figure.page.snap:
        x, y = snap_value(x, figure.page.grid_mm), snap_value(y, figure.page.grid_mm)
    return x, y


def _jsonable_panel(p: dict) -> dict:
    return {k: v for k, v in p.items() if k != "id"}


def _indexed(items: list) -> Optional[dict]:
    """{index: item} when every item is a dict with a unique 'index'."""
    if not all(isinstance(i, dict) and "index" in i for i in items):
        return None
    out = {i["index"]: i for i in items}
    return out if len(out) == len(items) else None


def leaf_diff(old: Any, new: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """[(path, old, new)] for every changed leaf. Lists of channel settings
    are matched by channel index (``channels[2]``), other lists of equal
    length by position, anything else is compared whole."""
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in list(new) + [k for k in old if k not in new]:
            out += leaf_diff(old.get(k), new.get(k), f"{path}.{k}" if path else str(k))
        return out
    if isinstance(old, list) and isinstance(new, list):
        a, b = _indexed(old), _indexed(new)
        if a is not None and b is not None and old and new and set(a) == set(b):
            out = []
            for idx in b:
                out += leaf_diff(a[idx], b[idx], f"{path}[{idx}]")
            return out
        if len(old) == len(new) and old:
            out = []
            for i, (x, y) in enumerate(zip(old, new)):
                out += leaf_diff(x, y, f"{path}[{i}]")
            return out
    return [(path, old, new)]


def _field_values(changes: list[tuple[str, Any, Any]]) -> tuple[str, Any, Any]:
    if len(changes) == 1:
        return changes[0]
    return (",".join(c[0] for c in changes), {c[0]: c[1] for c in changes},
            {c[0]: c[2] for c in changes})


def log_figure_changes(project: Project, figure: Figure, before: Snapshot, operation: str,
                       note: Optional[str] = None) -> int:
    """One log entry per panel added / removed / changed between `before`
    and the current figure, plus one for figure-level fields. Returns the
    number of entries written."""
    after = snapshot(figure)
    b = {p["id"]: p for p in before.get("panels", [])}
    a = {p["id"]: p for p in after.get("panels", [])}
    order = [p["id"] for p in before.get("panels", [])] + \
            [pid for pid in a if pid not in b]
    n = 0

    def sha(panel_dict: dict) -> Optional[str]:
        ref = project.images.get(panel_dict.get("image_id", ""))
        return ref.sha256 if ref else None

    for pid in order:
        old, new = b.get(pid), a.get(pid)
        if old == new:
            continue
        if old is None:
            log_operation(project, operation, target_type="panel", target_id=pid,
                          asset_sha256=sha(new), field="panel", new_value=_jsonable_panel(new),
                          note=_note(note, f"figure {figure.id}: panel added"))
        elif new is None:
            log_operation(project, operation, target_type="panel", target_id=pid,
                          asset_sha256=sha(old), field="panel", old_value=_jsonable_panel(old),
                          note=_note(note, f"figure {figure.id}: panel removed"))
        else:
            field, ov, nv = _field_values(leaf_diff(old, new))
            log_operation(project, operation, target_type="panel", target_id=pid,
                          asset_sha256=sha(new), field=field, old_value=ov, new_value=nv,
                          note=_note(note, f"figure {figure.id}"))
        n += 1
    fig_changes = leaf_diff({k: v for k, v in before.items() if k != "panels"},
                            {k: v for k, v in after.items() if k != "panels"})
    if fig_changes:
        field, ov, nv = _field_values(fig_changes)
        log_operation(project, operation, target_type="figure", target_id=figure.id,
                      field=field, old_value=ov, new_value=nv, note=note)
        n += 1
    return n


def _note(extra: Optional[str], base: str) -> str:
    return f"{base}; {extra}" if extra else base


# ------------------------------------------------------------------ history
class FigureHistory:
    """Snapshot undo/redo stack for one figure (not persisted)."""

    def __init__(self, limit: int = UNDO_LIMIT):
        self._undo: list[tuple[Snapshot, Snapshot, str]] = []
        self._redo: list[tuple[Snapshot, Snapshot, str]] = []
        self.limit = limit

    def push(self, before: Snapshot, after: Snapshot, label: str) -> None:
        self._undo.append((before, after, label))
        del self._undo[:-self.limit]
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1][2] if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1][2] if self._redo else ""

    def pop_undo(self) -> tuple[Snapshot, Snapshot, str]:
        step = self._undo.pop()
        self._redo.append(step)
        return step

    def pop_redo(self) -> tuple[Snapshot, Snapshot, str]:
        step = self._redo.pop()
        self._undo.append(step)
        return step


class FigureEditor:
    """All model changes of one figure. `save` is called after every gesture
    that wrote log entries, so project.json and the log never disagree."""

    def __init__(self, project: Project, figure: Figure, save: Callable[[], None]):
        self.project = project
        self.figure = figure
        self.save = save
        self.history = FigureHistory()
        # Open continuous gesture: (snapshot before, operation, label, key)
        self._live: Optional[tuple[Snapshot, str, str, str]] = None

    # ---- gesture plumbing ------------------------------------------------
    @property
    def has_pending(self) -> bool:
        return self._live is not None

    def flush(self) -> int:
        """Commit the open continuous gesture (slider released, 700 ms idle,
        another control touched, selection changed...)."""
        if self._live is None:
            return 0
        before, operation, label, _key = self._live
        self._live = None
        return self._commit(before, operation, label)

    @contextmanager
    def gesture(self, operation: str, label: str, note: Optional[str] = None) -> Iterator[None]:
        self.flush()
        before = snapshot(self.figure)
        try:
            yield
        except BaseException:
            restore(self.figure, before)              # no half-applied gesture
            raise
        self._commit(before, operation, label, note)

    def _commit(self, before: Snapshot, operation: str, label: str,
                note: Optional[str] = None) -> int:
        n = log_figure_changes(self.project, self.figure, before, operation, note)
        if n:
            self.history.push(before, snapshot(self.figure), label)
            self.save()
        return n

    def ref(self, panel: Panel) -> Optional[ImageRef]:
        return self.project.images.get(panel.image_id)

    def edit(self, panel_ids: Iterable[str], fn: Callable[[Panel, ImageRef], bool],
             operation: str = "figure.panel.set", label: str = "Edit panel(s)",
             key: Optional[str] = None, final: bool = True, propagate: bool = True,
             lock_values: Optional[dict[tuple[int, str], Any]] = None) -> tuple[int, int]:
        """Apply fn(panel, ref) to each panel; fn returns False to skip a
        panel (e.g. its image lacks that channel). With final=False the
        gesture stays open across calls with the same `key` (slider ticks)
        and is committed by flush() or by the next final call.

        Lock groups (propagate=True): locked channel settings changed by fn
        on a panel are applied to the same channel of every other panel of
        its lock group, in the same gesture (see propagate_locks).
        `lock_values` gives the intended values, e.g. a typed White that fn
        clamped on a lower bit-depth image. `self.last_propagated` = ids of
        panels changed by propagation only. Returns (applied, skipped)."""
        key = key or label
        if self._live is not None and self._live[3] != key:
            self.flush()
        if self._live is None:
            self._live = (snapshot(self.figure), operation, label, key)
        panel_ids = list(panel_ids)
        pre = {pid: locked_state(p) for pid in panel_ids
               if (p := self.figure.panel(pid)) is not None and p.lock_group}
        self.last_propagated: list[str] = []
        applied = skipped = 0
        try:
            for pid in panel_ids:
                panel = self.figure.panel(pid)
                ref = self.ref(panel) if panel is not None else None
                if panel is None or ref is None:
                    skipped += 1
                    continue
                if fn(panel, ref) is False:
                    skipped += 1
                else:
                    applied += 1
            if propagate and pre:
                self.last_propagated = propagate_locks(self.figure, self.project.images,
                                                       panel_ids, pre, lock_values)
        except BaseException:
            before = self._live[0]
            self._live = None
            restore(self.figure, before)
            raise
        if final:
            self.flush()
        return applied, skipped

    # ---- panels ----------------------------------------------------------
    def add_images(self, refs: Iterable[ImageRef], width_mm: float = DEFAULT_PANEL_WIDTH_MM,
                   page: int = 0) -> list[Panel]:
        refs = list(refs)
        out: list[Panel] = []
        with self.gesture("figure.panel.add", f"Add {len(refs)} panel(s)"):
            for ref in refs:
                panel = panel_from_image(ref, width_mm=width_mm, page=page)
                panel.x_mm, panel.y_mm = next_free_position(self.figure, panel.width_mm,
                                                            panel.height_mm, page)
                self.figure.panels.append(panel)
                out.append(panel)
        return out

    def delete_panels(self, panel_ids: Iterable[str]) -> int:
        ids = set(panel_ids)
        if not ids:
            return 0
        before = len(self.figure.panels)
        with self.gesture("figure.panel.delete", f"Delete {len(ids)} panel(s)"):
            self.figure.panels = [p for p in self.figure.panels if p.id not in ids]
            # An inset whose parent is gone becomes an ordinary panel.
            for p in self.figure.panels:
                if p.inset_of in ids:
                    p.inset_of = None
        return before - len(self.figure.panels)

    def set_geometry(self, changes: dict[str, Geometry], operation: str = "figure.panel.move",
                     label: str = "Move panel(s)") -> int:
        """Apply new (x, y, w, h) to several panels as one gesture. A size
        change extends/shrinks the crop at constant scale (design §2.4)."""
        changed = 0
        with self.gesture(operation, label):
            for pid, (x, y, w, h) in changes.items():
                p = self.figure.panel(pid)
                if p is None:
                    continue
                new = (float(x), float(y), max(MIN_PANEL_MM, float(w)), max(MIN_PANEL_MM, float(h)))
                if new == (p.x_mm, p.y_mm, p.width_mm, p.height_mm):
                    continue
                ref = self.ref(p)
                if (new[2], new[3]) != (p.width_mm, p.height_mm) and ref is not None:
                    s = ref.metadata.shape
                    viewport.resize(p, s.X, s.Y, new[2], new[3])
                p.x_mm, p.y_mm, p.width_mm, p.height_mm = new
                changed += 1
        return changed

    # ---- layout ------------------------------------------------------------
    def layout(self, panel_ids: Iterable[str], fn: Callable[[list[Panel], dict[str, ImageRef]], Any],
               operation: str, label: str) -> Any:
        """One layout gesture on the given panels (figure order): fn(panels,
        refs) changes geometry and/or appends panels to self.figure.panels.
        One undo step, one log entry per panel changed or added."""
        ids = set(panel_ids)
        panels = [p for p in self.figure.panels if p.id in ids]
        refs = {p.image_id: self.project.images[p.image_id] for p in panels
                if p.image_id in self.project.images}
        with self.gesture(operation, label):
            return fn(panels, refs)

    def set_page(self, page: PageSetup) -> bool:
        """Page setup (one figure-level log entry). Pages holding panels are
        never removed."""
        last = max((p.page for p in self.figure.panels), default=0)
        if page.n_pages <= last:
            raise ValueError(f"page {last + 1} holds panels: keep at least {last + 1} page(s)")
        with self.gesture("figure.page.set", "Page setup"):
            self.figure.page = page.model_copy(deep=True)
        return True

    # ---- undo / redo -----------------------------------------------------
    def undo(self) -> bool:
        self.flush()
        if not self.history.can_undo:
            return False
        current = snapshot(self.figure)
        before, _after, label = self.history.pop_undo()
        restore(self.figure, before)
        if log_figure_changes(self.project, self.figure, current, "figure.undo", note=label):
            self.save()
        return True

    def redo(self) -> bool:
        self.flush()
        if not self.history.can_redo:
            return False
        current = snapshot(self.figure)
        _before, after, label = self.history.pop_redo()
        restore(self.figure, after)
        if log_figure_changes(self.project, self.figure, current, "figure.redo", note=label):
            self.save()
        return True


# =================================================================== Preview
# Panel operations used with FigureEditor.edit(). Each returns False when
# the panel is skipped. They only touch the panel (the figure snapshot diff
# does the logging).

CHANNEL_FIELDS = ("visible", "lut", "black", "white", "gamma", "invert")
# What a lock group shares, per channel index (design §7.3). Never
# visibility, T/Z, projection, crop, orientation or labels.
LOCKED_FIELDS = ("lut", "black", "white", "gamma", "invert")


def locked_state(panel: Panel) -> dict[tuple[int, str], Any]:
    return {(c.index, f): getattr(c, f) for c in panel.channels for f in LOCKED_FIELDS}


def lock_members(figure: Figure, group: Optional[str]) -> list[Panel]:
    return [p for p in figure.panels if group and p.lock_group == group]


def apply_locked(panel: Panel, ref: ImageRef, values: dict[tuple[int, str], Any]) -> bool:
    """Set locked fields on one panel; channels it lacks are skipped; levels
    clamped to its own bit depth. Returns True if anything was applied."""
    by_channel: dict[int, dict[str, Any]] = {}
    for (idx, field), v in values.items():
        by_channel.setdefault(idx, {})[field] = v
    used = False
    for idx, fields in sorted(by_channel.items()):
        if idx >= ref.metadata.shape.C:
            continue
        used = True
        cs = panel_channel(panel, idx)
        if "black" in fields or "white" in fields:
            black = fields.get("black", cs.black if cs else 0)
            white = fields.get("white", cs.white if cs else ref.metadata.full_scale)
            set_levels(panel, ref, idx, black, white)
        for f in ("lut", "gamma", "invert"):
            if f in fields:
                set_channel_field(panel, ref, idx, f, fields[f])
    return used


def propagate_locks(figure: Figure, images: dict[str, ImageRef], edited: list[str],
                    pre: dict[str, dict], intended: Optional[dict] = None) -> list[str]:
    """After an edit: for each lock group, the locked channel fields that
    changed on the FIRST edited panel of the group (or `intended` values
    for those fields) are applied to every other member. Returns the ids
    of members changed that were not themselves edited."""
    done_groups: set[str] = set()
    propagated: list[str] = []
    for pid in edited:
        if pid not in pre:
            continue
        p = figure.panel(pid)
        if p is None or not p.lock_group or p.lock_group in done_groups:
            continue
        post = locked_state(p)
        changed = {k: v for k, v in post.items() if pre[pid].get(k) != v}
        if not changed:
            continue
        done_groups.add(p.lock_group)
        if intended:            # the typed value, not this panel's clamped copy of it
            changed.update({k: v for k, v in intended.items() if k in changed})
        for m in lock_members(figure, p.lock_group):
            ref = images.get(m.image_id)
            if m.id == p.id or ref is None:
                continue
            before = locked_state(m)
            apply_locked(m, ref, changed)
            if locked_state(m) != before and m.id not in edited:
                propagated.append(m.id)
    return propagated


def lock_panels(panels: list[Panel], images: dict[str, ImageRef], group: str) -> None:
    """Put panels in one lock group; the others take the first panel's
    locked channel settings (so the group starts consistent)."""
    first = panels[0]
    values = locked_state(first)
    for p in panels:
        p.lock_group = group
        if p is not first and p.image_id in images:
            apply_locked(p, images[p.image_id], values)


def panel_channel(panel: Panel, index: int) -> Optional[ChannelSettings]:
    return next((c for c in panel.channels if c.index == index), None)


def set_channel_field(panel: Panel, ref: ImageRef, index: int, field: str, value: Any) -> bool:
    """Matched by channel index; skipped when the panel's image has no such
    channel. Levels are clamped per image (its own full scale)."""
    if field not in CHANNEL_FIELDS:
        raise ValueError(field)
    if index >= ref.metadata.shape.C:
        return False
    cs = panel_channel(panel, index)
    if cs is None:
        cs = ChannelSettings(index=index, white=ref.metadata.full_scale)
        panel.channels.append(cs)
        panel.channels.sort(key=lambda c: c.index)
    fs = ref.metadata.full_scale
    if field == "black":
        value = max(0, min(int(value), fs - 1, cs.white - 1))
    elif field == "white":
        value = min(fs, max(int(value), cs.black + 1, 1))
    elif field == "gamma":
        value = float(value)
    elif field in ("visible", "invert"):
        value = bool(value)
    setattr(cs, field, value)
    return True


def set_levels(panel: Panel, ref: ImageRef, index: int, black: int, white: int) -> bool:
    if index >= ref.metadata.shape.C:
        return False
    fs = ref.metadata.full_scale
    white = min(fs, max(int(white), 1))
    black = max(0, min(int(black), white - 1))
    cs = panel_channel(panel, index)
    if cs is None:
        return set_channel_field(panel, ref, index, "white", white) and \
            set_channel_field(panel, ref, index, "black", black)
    cs.black, cs.white = black, white
    return True


def set_t(panel: Panel, ref: ImageRef, t: int) -> bool:
    panel.t = max(0, min(int(t), ref.metadata.shape.T - 1))
    return True


def set_z(panel: Panel, ref: ImageRef, z: int) -> bool:
    panel.z = max(0, min(int(z), ref.metadata.shape.Z - 1))
    return True


def set_projection(panel: Panel, ref: ImageRef, z_range: Optional[tuple[int, int]]) -> bool:
    """Max-intensity projection over [z_start, z_end] (inclusive), clamped
    to the image; None = single plane. Skipped for images with one Z."""
    n = ref.metadata.shape.Z
    if z_range is None:
        panel.z_projection = None
        return True
    if n < 2:
        return False
    a, b = sorted(int(v) for v in z_range)
    a, b = max(0, min(a, n - 1)), max(0, min(b, n - 1))
    panel.z_projection = ZProjection(z_start=a, z_end=b)
    return True


def rotate(panel: Panel, ref: ImageRef, delta: int) -> bool:
    s = ref.metadata.shape
    viewport.set_rotation(panel, s.X, s.Y, panel.rotation_deg + delta)
    return True


def flip(panel: Panel, ref: ImageRef, attr: str) -> bool:
    if attr not in ("flip_horizontal", "flip_vertical"):
        raise ValueError(attr)
    setattr(panel, attr, not getattr(panel, attr))
    return True


def rendering_of(panel: Panel) -> list[dict]:
    """What 'Copy rendering' carries: the channel settings."""
    return [c.model_dump() for c in panel.channels]


def paste_rendering(panel: Panel, ref: ImageRef, rendering: list[dict]) -> bool:
    """Channel settings matched by index; channels the image lacks are
    ignored; levels clamped to the image's full scale."""
    used = False
    for d in rendering:
        cs = ChannelSettings.model_validate(d)
        if cs.index >= ref.metadata.shape.C:
            continue
        for f in ("visible", "lut", "gamma", "invert"):
            set_channel_field(panel, ref, cs.index, f, getattr(cs, f))
        set_levels(panel, ref, cs.index, cs.black, cs.white)
        used = True
    return used


def paste_viewport(panel: Panel, ref: ImageRef, view: dict) -> bool:
    viewport.paste_view(panel, view)
    return True


def reset_to_image_defaults(panel: Panel, ref: ImageRef) -> bool:
    """Rendering, T/Z and orientation from the library image (projection
    cleared); the viewport keeps its centre and scale."""
    d = ref.display
    s = ref.metadata.shape
    panel.channels = [c.model_copy(deep=True) for c in d.channels]
    panel.t, panel.z = min(d.t, s.T - 1), min(d.z, s.Z - 1)
    panel.z_projection = None
    panel.flip_horizontal, panel.flip_vertical = d.flip_horizontal, d.flip_vertical
    viewport.set_rotation(panel, s.X, s.Y, d.rotation_deg)
    return True


def save_as_image_defaults(project: Project, panel: Panel) -> int:
    """Copy the panel's rendering, T/Z and orientation to its library image
    (the defaults of future panels). One log entry on the image."""
    ref = project.images[panel.image_id]
    before = ref.display.model_dump(mode="json")
    d = ref.display
    d.channels = [c.model_copy(deep=True) for c in panel.channels]
    d.t, d.z = panel.t, panel.z
    d.rotation_deg = panel.rotation_deg
    d.flip_horizontal, d.flip_vertical = panel.flip_horizontal, panel.flip_vertical
    changes = leaf_diff(before, d.model_dump(mode="json"))
    if not changes:
        return 0
    field, ov, nv = _field_values([("display." + p, o, n) for p, o, n in changes])
    log_operation(project, "image.display.from_panel", target_type="image", target_id=ref.id,
                  asset_sha256=ref.sha256, field=field, old_value=ov, new_value=nv,
                  note=f"saved as image defaults from panel {panel.id}")
    return 1


def replace_problems(panel: Panel, old: Optional[ImageRef], new: ImageRef) -> tuple[list[str], list[str]]:
    """(errors, warnings) for showing `new` in `panel` with all its
    settings. Errors refuse the replacement."""
    errors: list[str] = []
    warnings: list[str] = []
    m = new.metadata
    used = max((c.index for c in panel.channels), default=-1)
    if old is not None and old.metadata.shape.C != m.shape.C:
        errors.append(f"{new.filename}: {m.shape.C} channel(s), the panel's image has "
                      f"{old.metadata.shape.C}")
    elif used >= m.shape.C:
        errors.append(f"{new.filename}: only {m.shape.C} channel(s), the panel uses channel "
                      f"{used + 1}")
    if panel.scale_bar.visible and not m.calibration.is_calibrated:
        errors.append(f"{new.filename}: not calibrated, the panel shows a scale bar")
    if old is not None:
        o = old.metadata
        if (o.shape.X, o.shape.Y) != (m.shape.X, m.shape.Y):
            warnings.append(f"size differs ({o.shape.X}×{o.shape.Y} → {m.shape.X}×{m.shape.Y} px): "
                            "the crop is kept in pixels")
        if o.bit_depth != m.bit_depth:
            warnings.append(f"bit depth differs ({o.bit_depth} → {m.bit_depth}): levels clamped")
        if (o.calibration.pixel_size_x != m.calibration.pixel_size_x):
            warnings.append("pixel size differs: the same crop shows a different area")
    if panel.t >= m.shape.T or panel.z >= m.shape.Z or (
            panel.z_projection and panel.z_projection.z_end >= m.shape.Z):
        warnings.append("T/Z position outside the new image: clamped")
    return errors, warnings


def replace_image(panel: Panel, new: ImageRef) -> None:
    """Keep every setting; clamp T/Z, projection and levels to `new`."""
    s = new.metadata.shape
    fs = new.metadata.full_scale
    panel.image_id = new.id
    panel.t, panel.z = min(panel.t, s.T - 1), min(panel.z, s.Z - 1)
    if panel.z_projection is not None:
        if s.Z < 2:
            panel.z_projection = None
        else:
            panel.z_projection = ZProjection(z_start=min(panel.z_projection.z_start, s.Z - 1),
                                             z_end=min(panel.z_projection.z_end, s.Z - 1))
    for c in panel.channels:
        c.white = min(c.white, fs)
        c.black = min(c.black, c.white - 1)


# ================================================================== Labels
# Operations for FigureEditor.edit() on all selected panels. A label is
# identified across panels by (text, position): editing or deleting a row
# of the Labels tab acts on every selected panel holding that label.

LabelKey = tuple[str, str]


def label_key(label: PanelLabel) -> LabelKey:
    return label.text, label.position


def add_label(panel: Panel, ref: ImageRef, label: PanelLabel) -> bool:
    """Append a copy; skipped if the panel already has the same label."""
    if any(label_key(lb) == label_key(label) for lb in panel.labels):
        return False
    panel.labels.append(label.model_copy(deep=True))
    return True


def update_labels(panel: Panel, ref: ImageRef, key: LabelKey, fields: dict[str, Any]) -> bool:
    found = False
    for i, lb in enumerate(panel.labels):
        if label_key(lb) == key:
            panel.labels[i] = PanelLabel.model_validate({**lb.model_dump(), **fields})
            found = True
    return found


def delete_labels(panel: Panel, ref: ImageRef, key: LabelKey) -> bool:
    n = len(panel.labels)
    panel.labels = [lb for lb in panel.labels if label_key(lb) != key]
    return len(panel.labels) != n


def set_scale_bar(panel: Panel, ref: ImageRef, fields: dict[str, Any]) -> bool:
    """Skipped (False) for uncalibrated images: they cannot show a bar."""
    if not ref.metadata.calibration.is_calibrated:
        return False
    panel.scale_bar = ScaleBar.model_validate({**panel.scale_bar.model_dump(), **fields})
    return True


def set_border(panel: Panel, ref: ImageRef, fields: dict[str, Any]) -> bool:
    panel.border = PanelBorder.model_validate({**panel.border.model_dump(), **fields})
    return True


def copy_panels(panels: Iterable[Panel]) -> list[dict]:
    return [p.model_dump(mode="json") for p in panels]


def pasted_panels(copied: list[dict], offset_mm: float = 5.0) -> list[Panel]:
    """New panels from copied ones: new ids, shifted by offset_mm, labels
    and every setting kept; not in any lock group, not an inset (they are
    independent panels until locked again)."""
    out = []
    for d in copied:
        p = Panel.model_validate(d)
        out.append(p.model_copy(update={
            "id": new_id(), "x_mm": round(p.x_mm + offset_mm, 6),
            "y_mm": round(p.y_mm + offset_mm, 6), "lock_group": None,
            "inset_of": None, "inset_style": None}))
    return out
