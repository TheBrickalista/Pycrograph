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
Every user gesture goes through FigureEditor.gesture():

    before = snapshot(figure)  ->  mutate  ->  log + push undo + save

The log step diffs the figure against the snapshot and writes ONE entry per
panel added, removed or changed (target_id = panel id, asset_sha256 = the
panel's image hash), plus one figure-level entry if page/name/legend changed.
So a move of five selected panels is one undo step and five log entries.
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

from .models import Crop, Figure, ImageRef, PageSetup, Panel, Project
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
            keys = [k for k in new if new.get(k) != old.get(k)]
            log_operation(project, operation, target_type="panel", target_id=pid,
                          asset_sha256=sha(new), field=",".join(keys),
                          old_value={k: old.get(k) for k in keys},
                          new_value={k: new.get(k) for k in keys},
                          note=_note(note, f"figure {figure.id}"))
        n += 1
    fig_keys = [k for k in after if k != "panels" and after[k] != before.get(k)]
    if fig_keys:
        log_operation(project, operation, target_type="figure", target_id=figure.id,
                      field=",".join(fig_keys),
                      old_value={k: before.get(k) for k in fig_keys},
                      new_value={k: after[k] for k in fig_keys}, note=note)
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

    # ---- gesture plumbing ------------------------------------------------
    @contextmanager
    def gesture(self, operation: str, label: str, note: Optional[str] = None) -> Iterator[None]:
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
        """Apply new (x, y, w, h) to several panels as one gesture."""
        changed = 0
        with self.gesture(operation, label):
            for pid, (x, y, w, h) in changes.items():
                p = self.figure.panel(pid)
                if p is None:
                    continue
                new = (float(x), float(y), max(MIN_PANEL_MM, float(w)), max(MIN_PANEL_MM, float(h)))
                if new != (p.x_mm, p.y_mm, p.width_mm, p.height_mm):
                    p.x_mm, p.y_mm, p.width_mm, p.height_mm = new
                    changed += 1
        return changed

    # ---- undo / redo -----------------------------------------------------
    def undo(self) -> bool:
        if not self.history.can_undo:
            return False
        current = snapshot(self.figure)
        before, _after, label = self.history.pop_undo()
        restore(self.figure, before)
        if log_figure_changes(self.project, self.figure, current, "figure.undo", note=label):
            self.save()
        return True

    def redo(self) -> bool:
        if not self.history.can_redo:
            return False
        current = snapshot(self.figure)
        _before, after, label = self.history.pop_redo()
        restore(self.figure, after)
        if log_figure_changes(self.project, self.figure, current, "figure.redo", note=label):
            self.save()
        return True
