# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
On-disk format migrations of project.json, applied to the raw JSON dict
before Pydantic validation.

Each step takes the dict at version N and returns it at version N + 1. The
loader records what was done in the operation log (``project.migrate``), so
the next save writes the new format and its log entry together.

Version history
---------------
1 -> 2  ``Figure.page`` was a ``PagePreset``; it becomes a ``PageSetup``
        wrapping that preset, with the page size taken from the preset
        (width = preset width, height = preset maximum height).
2 -> 3  ``InsetStyle`` gains ``region_group`` (None: not replicated) and
        ``region_link`` ("region"); written explicitly into every existing
        inset style so the migrated file is self-describing.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from .models import PROJECT_FORMAT_VERSION


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    for fig in data.get("figures") or []:
        page = fig.get("page")
        if page is None:
            continue                               # PageSetup defaults apply
        if isinstance(page, dict) and "preset" in page:
            continue                               # already a PageSetup
        preset = dict(page)
        setup: dict[str, Any] = {"preset": preset}
        if "width_mm" in preset:
            setup["width_mm"] = preset["width_mm"]
        if "max_height_mm" in preset:
            setup["height_mm"] = preset["max_height_mm"]
        fig["page"] = setup
    return data


def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    for fig in data.get("figures") or []:
        for panel in fig.get("panels") or []:
            style = panel.get("inset_style")
            if isinstance(style, dict):
                style.setdefault("region_group", None)
                style.setdefault("region_link", "region")
    return data


def _v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    """InsetStyle.show_number (numbers were always drawn before)."""
    for fig in data.get("figures") or []:
        for panel in fig.get("panels") or []:
            style = panel.get("inset_style")
            if isinstance(style, dict):
                style.setdefault("show_number", True)
    return data


MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
}


def migrate_project_dict(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Bring a raw project dict up to PROJECT_FORMAT_VERSION.

    Returns (migrated copy, original version). Versions newer than this app
    are returned unchanged (the loader refuses them)."""
    meta = data.get("project") or {}
    version = int(meta.get("format_version", 1))
    if version >= PROJECT_FORMAT_VERSION:
        return data, version
    out = copy.deepcopy(data)
    v = version
    while v < PROJECT_FORMAT_VERSION:
        out = MIGRATIONS[v](out)
        v += 1
    out["project"]["format_version"] = PROJECT_FORMAT_VERSION
    return out, version
