# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Application preferences: journal page presets, the default preset for new
figures, the layout gap and the Figures canvas outline options (toolbar
**Outlines ▾**).

Stored in ``<workspace>/preferences.json`` (local, atomic write). They are
not project data: editing them is not logged. Applying a preset to a figure
(Page setup) is a figure change and is logged there.

Built-in presets always come from the bundled catalog
(``resources/journal_presets.json``, journal_presets.py), never from the
saved file: a corrected catalog reaches every user, and preferences.json
only stores the user's own presets (added or duplicated in the Preferences
tab; a saved preset with a built-in name is replaced by the catalog's).
Figures keep the snapshot of the preset they were set up with
(``Figure.page.preset``), so a catalog change never alters a figure.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from .journal_presets import builtin_names, builtin_page_presets
from .jsonsafe import dumps_strict
from .layout import DEFAULT_GAP_MM
from .models import PagePreset, PageSetup

FILENAME = "preferences.json"


DEFAULT_PRESET = "Nature double column"


def builtin_presets() -> list[PagePreset]:
    """The catalog's presets, in catalog order (fresh copies)."""
    return builtin_page_presets()


def is_builtin(name: str) -> bool:
    return name in builtin_names()


class Preferences(BaseModel):
    presets: List[PagePreset] = Field(default_factory=builtin_presets)
    default_preset: str = DEFAULT_PRESET
    layout_gap_mm: float = Field(DEFAULT_GAP_MM, ge=0)
    # Figures canvas, editor only (never exported): dashed outline on every
    # unselected panel, and on panels whose crop leaves page background.
    canvas_outlines_all: bool = False
    canvas_outlines_empty: bool = True

    @model_validator(mode="after")
    def _builtins_from_catalog(self) -> "Preferences":
        """Catalog presets first, then the user's (kept in their order)."""
        builtin = builtin_presets()
        names = {p.name for p in builtin}
        self.presets = builtin + [p for p in self.presets if p.name not in names]
        return self

    def user_presets(self) -> list[PagePreset]:
        names = builtin_names()
        return [p for p in self.presets if p.name not in names]

    def preset(self, name: str) -> Optional[PagePreset]:
        return next((p for p in self.presets if p.name == name), None)

    def default(self) -> PagePreset:
        return (self.preset(self.default_preset) or self.preset(DEFAULT_PRESET)
                or (self.presets[0] if self.presets else PagePreset()))

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
    data = prefs.model_dump(mode="json")
    data["presets"] = [p.model_dump(mode="json") for p in prefs.user_presets()]   # built-ins: catalog
    text = dumps_strict(data, indent=2, ensure_ascii=False)
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
