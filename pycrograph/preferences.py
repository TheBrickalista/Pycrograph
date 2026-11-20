# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Application preferences: journal page presets (data, editable in the
Preferences tab), the default preset for new figures, the layout gap and
the Figures canvas outline options (toolbar **Outlines ▾**).

Stored in ``<workspace>/preferences.json`` (local, atomic write). They are
not project data: editing them is not logged. Applying a preset to a figure
(Page setup) is a figure change and is logged there.

Built-in presets are only those whose values were given to us (Nature);
add other journals in the Preferences tab from their author guidelines.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError

from .jsonsafe import dumps_strict
from .layout import DEFAULT_GAP_MM
from .models import PagePreset, PageSetup

FILENAME = "preferences.json"


def builtin_presets() -> list[PagePreset]:
    common = dict(max_height_mm=170.0, dpi=300, font_family="Arial", font_size_pt=8.0,
                  min_line_pt=0.5)
    return [PagePreset(name="Nature single column", width_mm=89.0, **common),
            PagePreset(name="Nature double column", width_mm=183.0, **common)]


class Preferences(BaseModel):
    presets: List[PagePreset] = Field(default_factory=builtin_presets)
    default_preset: str = "Nature double column"
    layout_gap_mm: float = Field(DEFAULT_GAP_MM, ge=0)
    # Figures canvas, editor only (never exported): dashed outline on every
    # unselected panel, and on panels whose crop leaves page background.
    canvas_outlines_all: bool = False
    canvas_outlines_empty: bool = True

    def preset(self, name: str) -> Optional[PagePreset]:
        return next((p for p in self.presets if p.name == name), None)

    def default(self) -> PagePreset:
        return self.preset(self.default_preset) or (self.presets[0] if self.presets else PagePreset())

    def new_page(self) -> PageSetup:
        return PageSetup.from_preset(self.default().model_copy(deep=True))

    def validate_presets(self) -> list[str]:
        """Problems that would make the list unusable (shown, not raised)."""
        names = [p.name.strip() for p in self.presets]
        errors = []
        if not self.presets:
            errors.append("at least one preset is needed")
        if any(not n for n in names):
            errors.append("every preset needs a name")
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            errors.append(f"duplicate names: {', '.join(dup)}")
        return errors


def preferences_path(root: str | Path) -> Path:
    return Path(root).expanduser() / FILENAME


def load_preferences(root: str | Path) -> Preferences:
    """Defaults when the file is missing or unreadable (never fails)."""
    path = preferences_path(root)
    try:
        return Preferences.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        return Preferences()


def save_preferences(root: str | Path, prefs: Preferences) -> Path:
    path = preferences_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_strict(prefs.model_dump(mode="json"), indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(prefix=".preferences.", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
