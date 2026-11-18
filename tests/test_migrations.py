# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""project.json format 1 -> 2 (Figure.page: PagePreset -> PageSetup)."""

from __future__ import annotations

import json

from pycrograph.logchain import verify_log_chain
from pycrograph.migrations import migrate_project_dict
from pycrograph.models import PROJECT_FORMAT_VERSION
from pycrograph.storage import load_project_file


def v1_project_dict(workspace) -> dict:
    """A real v1 file: created with the current code, then rewritten in the
    v1 shape (format_version 1, Figure.page = a bare PagePreset)."""
    p = workspace.create_project("Old")
    d = p.model_dump(mode="json")
    d["project"]["format_version"] = 1
    d["figures"] = [{
        "id": "f1", "name": "Fig 1",
        "page": {"name": "Cell full width", "width_mm": 174.0, "max_height_mm": 225.0,
                 "dpi": 300, "font_family": "Arial", "font_size_pt": 7.0, "min_line_pt": 0.5},
        "panels": [{"id": "p1", "image_id": "img", "x_mm": 3, "y_mm": 4,
                    "width_mm": 40, "height_mm": 30}],
        "overlays": [], "hidden": False,
    }]
    return d


def test_migrate_dict_v1_to_v2(workspace):
    d = v1_project_dict(workspace)
    out, old = migrate_project_dict(d)
    assert old == 1 and out["project"]["format_version"] == PROJECT_FORMAT_VERSION
    page = out["figures"][0]["page"]
    assert page["preset"]["name"] == "Cell full width"
    assert (page["width_mm"], page["height_mm"]) == (174.0, 225.0)
    assert "preset" not in d["figures"][0]["page"]           # input not mutated
    same, v = migrate_project_dict(out)
    assert same is out and v == PROJECT_FORMAT_VERSION


def test_load_v1_logs_migration_then_save_is_consistent(workspace):
    d = v1_project_dict(workspace)
    pid = d["project"]["id"]
    path = workspace.project_json(pid)
    path.write_text(json.dumps(d))

    p = workspace.load_project(pid)
    fig = p.figures[0]
    assert fig.page.preset.name == "Cell full width"
    assert fig.page.size_mm == (174.0, 225.0)
    assert fig.panels[0].page == 0 and not fig.panels[0].border.visible
    last = p.operation_log[-1]
    assert last.operation == "project.migrate"
    assert (last.old_value, last.new_value) == (1, PROJECT_FORMAT_VERSION)
    assert verify_log_chain(p).status == "ok"
    # The file is untouched until the next save...
    assert json.loads(path.read_text())["project"]["format_version"] == 1

    workspace.save_project(p)
    q = load_project_file(path)
    assert q == p                                            # no second migration entry
    assert q.project.format_version == PROJECT_FORMAT_VERSION
    assert verify_log_chain(q).status == "ok"


def test_v1_without_figures(workspace):
    d = v1_project_dict(workspace)
    d["figures"] = []
    out, _ = migrate_project_dict(d)
    assert out["figures"] == []
