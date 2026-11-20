# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Figure editing without Qt: add from library, move/resize/delete,
snapshot undo/redo, one log entry per gesture per panel, then save."""

from __future__ import annotations

import pytest

from pycrograph.figures import (
    FigureEditor, create_figure, next_figure_name, next_free_position, panel_aspect,
    panel_from_image, rename_figure,
)
from pycrograph.logchain import verify_log_chain
from pycrograph.models import PageSetup
from pycrograph.storage import create_dataset, register_image


@pytest.fixture
def project_with_images(workspace, mm_tiff, stk_tc):
    p = workspace.create_project("Fig")
    ds = create_dataset(p, "A")
    a = register_image(p, mm_tiff, ds.id)          # 64 x 64
    b = register_image(p, stk_tc, ds.id)           # 32 x 24, 2 channels
    return p, a, b


def make_editor(workspace, project):
    saves = []

    def save():
        workspace.save_project(project)
        saves.append(len(project.operation_log))
    fig = create_figure(project, "Figure 1")
    return FigureEditor(project, fig, save), saves


def test_panel_copies_display_settings(project_with_images):
    _p, _a, b = project_with_images
    b.display.channels[1].lut = "magenta"
    b.display.channels[1].white = 4000
    b.display.t = 2
    b.display.rotation_deg = 90
    panel = panel_from_image(b, width_mm=30)
    assert [c.model_dump() for c in panel.channels] == [c.model_dump() for c in b.display.channels]
    assert panel.t == 2 and panel.rotation_deg == 90
    assert (panel.crop.x, panel.crop.y, panel.crop.w, panel.crop.h) == (0, 0, 32, 24)
    # 32 x 24 rotated 90° -> displayed 24 wide, 32 high.
    assert panel_aspect(panel, b) == pytest.approx(24 / 32)
    assert panel.height_mm == pytest.approx(40.0)
    # A copy, not a link: later library edits do not reach the panel.
    b.display.channels[1].white = 9999
    assert panel.channels[1].white == 4000


def test_next_free_position_wraps(project_with_images, workspace):
    p, a, _b = project_with_images
    fig = create_figure(p, "F", PageSetup(width_mm=100, height_mm=100))
    assert next_free_position(fig, 40, 40) == (0.0, 0.0)
    fig.panels.append(panel_from_image(a, 0, 0, 40))
    assert next_free_position(fig, 40, 40) == (42.0, 0.0)
    fig.panels.append(panel_from_image(a, 42, 0, 40))
    assert next_free_position(fig, 40, 40) == (0.0, 42.0)


def test_add_move_resize_delete_log_per_panel(workspace, project_with_images):
    p, a, b = project_with_images
    ed, saves = make_editor(workspace, p)
    n0 = len(p.operation_log)

    panels = ed.add_images([a, b])
    assert len(ed.figure.panels) == 2
    added = p.operation_log[n0:]
    assert [e.operation for e in added] == ["figure.panel.add"] * 2
    assert {e.target_id for e in added} == {q.id for q in panels}
    assert all(e.target_type == "panel" for e in added)
    assert saves[-1] == len(p.operation_log)                  # saved after logging
    assert panels[1].x_mm == pytest.approx(panels[0].width_mm + 2)

    # Move both panels: ONE gesture -> one undo step, one entry per panel.
    n1 = len(p.operation_log)
    changes = {q.id: (q.x_mm + 5, q.y_mm + 10, q.width_mm, q.height_mm) for q in panels}
    assert ed.set_geometry(changes) == 2
    moved = p.operation_log[n1:]
    assert [e.operation for e in moved] == ["figure.panel.move"] * 2
    assert moved[0].field == "x_mm,y_mm"
    assert moved[0].new_value["y_mm"] == pytest.approx(moved[0].old_value["y_mm"] + 10)
    assert moved[0].asset_sha256 == a.sha256

    # A no-op gesture logs nothing and adds no undo step.
    n2 = len(p.operation_log)
    assert ed.set_geometry({panels[0].id: (panels[0].x_mm, panels[0].y_mm,
                                           panels[0].width_mm, panels[0].height_mm)}) == 0
    assert len(p.operation_log) == n2

    ed.set_geometry({panels[0].id: (0, 0, 20, 20)}, "figure.panel.resize", "Resize")
    e = p.operation_log[-1]
    assert e.operation == "figure.panel.resize"
    # Size + the crop shrunk at constant scale, all in ONE entry.
    assert e.field.split(",")[:4] == ["x_mm", "y_mm", "width_mm", "height_mm"]
    assert "crop.w" in e.field and e.new_value["crop.w"] == pytest.approx(32)   # 64 px / 40 mm * 20 mm

    n3 = len(p.operation_log)
    assert ed.delete_panels([q.id for q in panels]) == 2
    deleted = p.operation_log[n3:]
    assert [e.operation for e in deleted] == ["figure.panel.delete"] * 2
    assert deleted[0].old_value["image_id"] == a.id and deleted[0].new_value is None
    assert ed.figure.panels == []

    reloaded = workspace.load_project(p.project.id)
    assert reloaded == p
    assert verify_log_chain(reloaded).status == "ok"


def test_undo_redo_restores_and_logs(workspace, project_with_images):
    p, a, b = project_with_images
    ed, _saves = make_editor(workspace, p)
    [pa, pb] = ed.add_images([a, b])
    after_add = ed.figure.model_dump()
    ed.set_geometry({pa.id: (50, 60, pa.width_mm, pa.height_mm),
                     pb.id: (70, 80, pb.width_mm, pb.height_mm)})
    after_move = ed.figure.model_dump()
    ed.delete_panels([pa.id])

    n = len(p.operation_log)
    assert ed.undo()                                       # undo delete
    assert [q.id for q in ed.figure.panels] == [pa.id, pb.id]
    assert ed.figure.model_dump() == after_move
    assert [(e.operation, e.target_id) for e in p.operation_log[n:]] == [("figure.undo", pa.id)]

    n = len(p.operation_log)
    assert ed.undo()                                       # undo move (2 panels)
    assert ed.figure.model_dump() == after_add
    assert [e.operation for e in p.operation_log[n:]] == ["figure.undo"] * 2
    assert p.operation_log[-1].note.endswith("Move panels")

    assert ed.redo()
    assert ed.figure.model_dump() == after_move
    assert p.operation_log[-1].operation == "figure.redo"

    # A new gesture clears the redo stack.
    ed.set_geometry({pa.id: (0, 0, pa.width_mm, pa.height_mm)})
    assert not ed.history.can_redo and not ed.redo()

    # The figure object held by the project is updated in place.
    assert p.figure(ed.figure.id) is ed.figure
    reloaded = workspace.load_project(p.project.id)
    assert reloaded == p
    assert verify_log_chain(reloaded).status == "ok"


def test_failed_gesture_is_rolled_back(workspace, project_with_images):
    p, a, _b = project_with_images
    ed, _ = make_editor(workspace, p)
    ed.add_images([a])
    before = ed.figure.model_dump()
    n = len(p.operation_log)
    with pytest.raises(RuntimeError):
        with ed.gesture("figure.panel.move", "x"):
            ed.figure.panels[0].x_mm = 99
            raise RuntimeError("boom")
    assert ed.figure.model_dump() == before
    assert len(p.operation_log) == n


def test_figure_create_rename(workspace, project_with_images):
    p, _a, _b = project_with_images
    assert next_figure_name(p) == "Figure 1"
    fig = create_figure(p, "Figure 1")
    assert next_figure_name(p) == "Figure 2"
    assert p.operation_log[-1].operation == "figure.create"
    rename_figure(p, fig, "Fig. 2 — nuclei")
    assert fig.name == "Fig. 2 — nuclei"
    assert p.operation_log[-1].field == "name"
    n = len(p.operation_log)
    rename_figure(p, fig, "Fig. 2 — nuclei")                # unchanged: not logged
    assert len(p.operation_log) == n
