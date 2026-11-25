# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Journal page presets catalog (``resources/journal_presets.json``).

The catalog is the source of the built-in page presets. Each entry gives a
final printed size (width, maximum height), a minimum raster dpi, a default
font, a minimum line width, and where the numbers come from:

- ``status``: *official* (read on the publisher's own page / PDF),
  *secondary* (only consistent third-party summaries) or *mixed*;
- ``unverified``: fields the source does not state, filled with a
  conservative default (shown greyed in Page setup, and named as defaults
  in consistency warnings);
- ``font_min_pt`` / ``font_max_pt``: the journal's text range, when given.

Only the `PagePreset` part (name, sizes, dpi, font, line) is stored in a
figure (``Figure.page.preset``, a snapshot); the rest stays here and is
looked up by preset name (`journal_info`). Nothing in this module is project
data, so the catalog can be corrected without a project format change, and
existing figures keep their snapshot.

The catalog is validated at load (`load_catalog`): unknown keys, a missing
field, a size <= 0, a duplicate name, an ``unverified`` entry that is not a
field, or a default font size outside the journal range all raise.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import LANDSCAPE_ASPECT, PagePreset

SCHEMA = 1
Status = Literal["official", "secondary", "mixed"]
STATUS_BADGE = {"official": "Official", "secondary": "Secondary — verify", "mixed": "Mixed"}
DEFAULT_NOTE = "(default, not stated by the journal)"
CHECKABLE_FIELDS = ("width_mm", "max_height_mm", "dpi", "font_family", "font_size_pt",
                    "font_min_pt", "font_max_pt", "min_line_pt")


class JournalPreset(BaseModel):
    """One catalog entry: the page preset values plus their provenance."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    width_mm: float = Field(gt=0)
    max_height_mm: float = Field(gt=0)
    dpi: int = Field(ge=72)
    font_family: str = Field(min_length=1)
    font_size_pt: float = Field(gt=0)
    font_min_pt: Optional[float] = Field(None, gt=0)
    font_max_pt: Optional[float] = Field(None, gt=0)
    min_line_pt: float = Field(gt=0)
    status: Status
    unverified: tuple[str, ...] = ()
    applies_to: tuple[str, ...] = ()
    source: str = Field(min_length=1)
    note: str = ""

    @model_validator(mode="after")
    def _coherent(self) -> "JournalPreset":
        bad = [f for f in self.unverified if f not in CHECKABLE_FIELDS]
        if bad:
            raise ValueError(f"{self.name}: unverified names unknown fields {bad}")
        lo, hi = self.font_min_pt, self.font_max_pt
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{self.name}: font_min_pt > font_max_pt")
        if (lo is not None and self.font_size_pt < lo) or (hi is not None and self.font_size_pt > hi):
            raise ValueError(f"{self.name}: font_size_pt outside the journal range")
        return self

    def is_default(self, field: str) -> bool:
        """True when `field` is a conservative default, not a journal rule."""
        return field in self.unverified

    @property
    def badge(self) -> str:
        return STATUS_BADGE[self.status]

    @property
    def source_is_url(self) -> bool:
        return self.source.startswith(("https://", "http://"))

    def page_preset(self) -> PagePreset:
        """The part stored in a figure."""
        return PagePreset(name=self.name, width_mm=self.width_mm,
                          max_height_mm=self.max_height_mm, dpi=self.dpi,
                          font_family=self.font_family, font_size_pt=self.font_size_pt,
                          min_line_pt=self.min_line_pt)


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: int = Field(alias="schema")
    checked: str
    legend: Dict[str, str] = Field(default_factory=dict)
    presets: tuple[JournalPreset, ...] = Field(min_length=1)
    not_included: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> "Catalog":
        if self.schema_ != SCHEMA:
            raise ValueError(f"journal presets schema {self.schema_} (expected {SCHEMA})")
        names = [p.name for p in self.presets]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate preset names: {', '.join(dup)}")
        return self

    def get(self, name: str) -> Optional[JournalPreset]:
        return next((p for p in self.presets if p.name == name), None)

    def publishers(self) -> List[str]:
        """In catalog order."""
        return list(dict.fromkeys(p.publisher for p in self.presets))

    def by_publisher(self, publisher: str) -> List[JournalPreset]:
        return [p for p in self.presets if p.publisher == publisher]


def parse_catalog(text: str) -> Catalog:
    return Catalog.model_validate_json(text)


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    """The bundled catalog (validated once; raises if the file is invalid)."""
    from .selfcheck import JOURNAL_PRESETS, resource_path
    return parse_catalog(resource_path(JOURNAL_PRESETS).read_text(encoding="utf-8"))


def builtin_page_presets() -> list[PagePreset]:
    return [p.page_preset() for p in load_catalog().presets]


def builtin_names() -> set[str]:
    return {p.name for p in load_catalog().presets}


def journal_info(name: str) -> Optional[JournalPreset]:
    """Catalog entry of a preset name (None for user-defined presets)."""
    return load_catalog().get(name)


# ------------------------------------------------------------ size labels
_MM_SUFFIX = re.compile(r"\s*\(((?:max\s+)?[\d.]+\s*mm)\)\s*$")


def _finish(label: str, entry: JournalPreset) -> str:
    label = re.sub(r"^\((.*?)\)", r"\1", label.strip(" :"))     # "(large format): …"
    width_mm = entry.width_mm
    if width_mm >= LANDSCAPE_ASPECT * entry.max_height_mm:     # a slide: both sides matter
        return f"{label} — {width_mm:g} × {entry.max_height_mm:g} mm"
    m = _MM_SUFFIX.search(label)
    if m:
        return f"{label[:m.start()]} — {m.group(1)}"
    return label if re.search(r"[\d.]+\s*mm\b", label) else f"{label} — {width_mm:g} mm"


def _schemes(publisher: str, entries: List[JournalPreset]):
    names = [p.name for p in entries]
    first = publisher.split()[0]
    heads = {n.split(": ", 1)[0] for n in names if ": " in n}
    # "PNAS: 1 column (87 mm)" -> "1 column …", but keep a journal name that
    # is not the publisher's ("JBC: …" under ASBMB, "JCI / JCI Insight: …").
    if all(": " in n for n in names) and len(heads) == 1 and next(iter(heads)).startswith(first):
        yield [n.split(": ", 1)[1] for n in names]
    if all(n.startswith(publisher + " ") for n in names):            # "Cell Press 2-column …"
        yield [n[len(publisher):] for n in names]
    if all(n.startswith(first + " ") for n in names):                # "Nature single column"
        yield [n[len(first):] for n in names]
    yield names


@lru_cache(maxsize=1)
def size_labels() -> Dict[str, str]:
    """Preset name -> short label for the Size ▾ list of its publisher,
    e.g. "Nature single column" -> "single column — 89 mm". The shortest
    naming scheme that keeps the labels of one publisher distinct and
    unambiguous is used; otherwise the full name."""
    cat = load_catalog()
    out: Dict[str, str] = {}
    for pub in cat.publishers():
        entries = cat.by_publisher(pub)
        for stems in _schemes(pub, entries):
            labels = [_finish(s, e) for s, e in zip(stems, entries)]
            if all(labels) and len(set(labels)) == len(labels):
                break
        out.update({e.name: lb for e, lb in zip(entries, labels)})
    return out


def size_label(name: str) -> str:
    return size_labels().get(name, name)
