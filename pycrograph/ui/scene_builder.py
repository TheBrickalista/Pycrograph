# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
One scene builder for the canvas and the export (design §4).

`FigureScene.populate()` builds pages, panel items and overlays from the
model; this module supplies what differs between the two uses:

- `panel_visual(..., max_side)`: the panel raster from PanelRenderer, and
  where it goes (viewport.image_rect_mm of render.crop_bounds). The canvas
  decimates to `max_side`; the export passes None = NATIVE crop resolution.
- `overlay_builder(...)`: labels / scale bar / border / insets / shapes, with
  one [time] unit per lock group.
- `export_scene(...)`: a scene with editor decorations OFF (no outlines,
  selection, handles, guides, page border), images scaled nearest-neighbour
  unless `smooth`.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtGui import QImage, QPixmap

from .. import figures as F
from .. import viewport as V
from ..labels import group_time_unit
from ..models import Figure, Panel, Project
from ..render import PanelRenderer, RenderError, crop_bounds
from .figure_canvas import FigureScene, PanelItem, PanelVisual
from .panel_overlays import build_overlays


def rgb_to_qimage(rgb) -> QImage:
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def panel_visual(project: Project, renderer: PanelRenderer, panel: Panel,
                 max_side: Optional[int]) -> PanelVisual:
    ref = project.images.get(panel.image_id)
    if ref is None:
        return None, "image not in project", None
    try:
        rgb = renderer.render(ref, panel, max_side=max_side)
    except RenderError as exc:
        return None, f"{ref.filename}\n{exc}", None
    s = ref.metadata.shape
    rect = V.image_rect_mm(panel, s.X, s.Y, crop_bounds(panel.crop, s.X, s.Y))
    return QPixmap.fromImage(rgb_to_qimage(rgb)), "", rect


def time_unit_for(project: Project, figure: Figure, panel: Panel) -> Optional[str]:
    """One [time] unit per lock group / montage."""
    if not panel.lock_group:
        return None
    return group_time_unit(F.lock_members(figure, panel.lock_group), project.images)


def overlay_builder(project: Project, figure_getter: Callable[[], Optional[Figure]],
                    selected_shapes: Optional[Callable[[str], frozenset]] = None
                    ) -> Callable[[PanelItem, Panel], None]:
    def build(item: PanelItem, panel: Panel) -> None:
        fig = figure_getter()
        if fig is None:
            return
        build_overlays(item, panel, project.images.get(panel.image_id), project, fig,
                       time_unit=time_unit_for(project, fig, panel),
                       selected_shapes=selected_shapes(panel.id) if selected_shapes else frozenset())
    return build


_APP = None


def ensure_qt_app():
    """Graphics scenes need a QApplication; scripts exporting without the GUI
    get one here (otherwise Qt crashes the interpreter)."""
    global _APP
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        _APP = app = QApplication([])
    return app


def export_scene(project: Project, figure: Figure, renderer: PanelRenderer,
                 smooth: bool = False, max_side: Optional[int] = None) -> FigureScene:
    """The figure as exported: native rasters, no editor decorations."""
    ensure_qt_app()
    scene = FigureScene()
    scene.decorations = False
    scene.smooth_images = smooth
    scene.overlay_builder = overlay_builder(project, lambda: figure)
    scene.populate(figure, lambda p: panel_visual(project, renderer, p, max_side))
    return scene
