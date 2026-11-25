# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Two-level journal preset choice — Publisher ▾ then Size ▾ ("1 column —
89 mm") — shared by Page setup and the Preferences tab, and the HTML block
that describes a preset (sizes, dpi, font, line, journals, note, source,
status badge; fields the journal does not state are greyed as defaults)."""

from __future__ import annotations

import html
from typing import Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from ..journal_presets import DEFAULT_NOTE, JournalPreset, journal_info, load_catalog, size_label
from ..models import PagePreset
from ..preferences import Preferences

CUSTOM = "Custom"                       # the publisher entry for everything else

BADGE_STYLE = {"official": "background:#dcfce7; color:#166534;",
               "secondary": "background:#fef3c7; color:#92400e;",
               "mixed": "background:#e0e7ff; color:#3730a3;"}
GREY = "color:#9ca3af;"


class PresetPicker(QWidget):
    """Publisher ▾ + Size ▾. Items carry a key: a preset name, or one of the
    `extra` keys given by the caller (listed under Custom). `chosen` is
    emitted with the key on a user pick (not on `set_current`)."""

    chosen = Signal(str)

    def __init__(self, prefs: Preferences, extra: Sequence[tuple[str, str]] = (), parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.publisher_combo = QComboBox()
        self.size_combo = QComboBox()
        self.publisher_combo.setToolTip("Publisher")
        self.size_combo.setToolTip("Size (final printed width)")
        self.size_combo.setMinimumContentsLength(24)
        lay.addWidget(self.publisher_combo)
        lay.addWidget(self.size_combo, 1)
        self.publisher_combo.activated.connect(self._on_publisher)
        self.size_combo.activated.connect(self._on_size)
        self.rebuild(prefs, extra)

    # ------------------------------------------------------------ content
    def rebuild(self, prefs: Preferences, extra: Sequence[tuple[str, str]] = ()) -> None:
        """Groups: catalog publishers (catalog order), then Custom (user
        presets + `extra` (label, key) items)."""
        cat = load_catalog()
        self._groups: dict[str, list[tuple[str, str]]] = {}
        for pub in cat.publishers():
            self._groups[pub] = [(size_label(p.name), p.name) for p in cat.by_publisher(pub)]
        self._groups[CUSTOM] = [(p.name, p.name) for p in prefs.user_presets()] + list(extra)
        current = self.current_key()
        self.publisher_combo.clear()
        for pub, items in self._groups.items():
            if not items:
                continue
            self.publisher_combo.addItem(pub, pub)
            journals = list(dict.fromkeys(j for _l, name in items
                                          if (info := journal_info(name)) for j in info.applies_to))
            if journals:
                self.publisher_combo.setItemData(self.publisher_combo.count() - 1,
                                                 "\n".join(journals), Qt.ToolTipRole)
        if current is not None:
            self.set_current(current)
        else:
            self._fill_sizes(self.publisher_combo.currentData())

    def _fill_sizes(self, publisher: Optional[str]) -> None:
        self.size_combo.clear()
        for label, key in self._groups.get(publisher or "", []):
            self.size_combo.addItem(label, key)

    def group_of(self, key: str) -> Optional[str]:
        return next((g for g, items in self._groups.items() if any(k == key for _l, k in items)), None)

    # ------------------------------------------------------------ state
    def current_key(self) -> Optional[str]:
        return self.size_combo.currentData()

    def publisher(self) -> Optional[str]:
        return self.publisher_combo.currentData()

    def set_current(self, key: str) -> bool:
        group = self.group_of(key)
        if group is None:
            return False
        self.publisher_combo.setCurrentIndex(self.publisher_combo.findData(group))
        self._fill_sizes(group)
        self.size_combo.setCurrentIndex(self.size_combo.findData(key))
        return True

    def choose(self, publisher: str, key: Optional[str] = None) -> None:
        """What a user pick does (also used by tests): select and emit."""
        i = self.publisher_combo.findData(publisher)
        if i < 0:
            return
        self.publisher_combo.setCurrentIndex(i)
        self._on_publisher(i)
        if key is not None:
            j = self.size_combo.findData(key)
            if j >= 0:
                self.size_combo.setCurrentIndex(j)
                self._on_size(j)

    def _on_publisher(self, _i: int) -> None:
        self._fill_sizes(self.publisher_combo.currentData())
        if self.size_combo.count():
            self.size_combo.setCurrentIndex(0)
            self.chosen.emit(self.size_combo.currentData())

    def _on_size(self, _i: int) -> None:
        key = self.size_combo.currentData()
        if key is not None:
            self.chosen.emit(key)


# ------------------------------------------------------------ description
def _value(text: str, default: bool) -> str:
    text = html.escape(text)
    return f'<span style="{GREY}">{text} {html.escape(DEFAULT_NOTE)}</span>' if default else text


def preset_info_html(preset: PagePreset, info: Optional[JournalPreset] = None) -> str:
    """Describe a preset. `info` is its catalog entry (None: a user preset).
    Values shown are the preset's (a figure's snapshot may differ from the
    catalog: then a line says so)."""
    d = (lambda f: info is not None and info.is_default(f))      # noqa: E731
    rows = [
        ("Size", _value(f"{preset.width_mm:g} mm wide", d("width_mm")) + " × "
         + _value(f"{preset.max_height_mm:g} mm max height", d("max_height_mm"))),
        ("Resolution", _value(f"{preset.dpi} dpi minimum", d("dpi"))),
    ]
    font = _value(f"{preset.font_family} {preset.font_size_pt:g} pt",
                  d("font_size_pt") or d("font_family"))
    if info is not None and (info.font_min_pt is not None or info.font_max_pt is not None):
        lo = f"{info.font_min_pt:g}" if info.font_min_pt is not None else "…"
        hi = f"{info.font_max_pt:g}" if info.font_max_pt is not None else "…"
        font += f" (journal range {lo}–{hi} pt)"
    rows.append(("Font", font))
    rows.append(("Min line", _value(f"{preset.min_line_pt:g} pt", d("min_line_pt"))))
    if info is None:
        head = f'<span style="{GREY}">User preset — values entered in Preferences.</span>'
    else:
        head = (f'<span style="{BADGE_STYLE[info.status]} padding:1px 6px; border-radius:3px;">'
                f'{html.escape(info.badge)}</span> {html.escape(info.publisher)}')
        if info.applies_to:
            rows.append(("Applies to", html.escape(", ".join(info.applies_to))))
        if info.note:
            rows.append(("Note", html.escape(info.note)))
        src = html.escape(info.source)
        rows.append(("Source", f'<a href="{src}">{src}</a>' if info.source_is_url else src))
        stored = info.page_preset()
        if stored != preset:
            rows.append(("", '<span style="color:#b45309;">This figure keeps its own copy of '
                             'the preset, which differs from the current catalog: choose the '
                             'size again to update it.</span>'))
    body = "".join(f"<tr><td style='{GREY} padding-right:8px;'>{html.escape(k)}</td>"
                   f"<td>{v}</td></tr>" for k, v in rows)
    return (f"{head}<table cellspacing='0' cellpadding='1'>{body}</table>"
            "<p style='color:#6b7280;'>Journal rules change: verify with the journal's "
            "current author guidelines.</p>")
