# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Regenerate the preset table of docs/guide/page-setup.md from
pycrograph/resources/journal_presets.json.

    python scripts/make_journal_presets_table.py [--check]

The table sits between the BEGIN / END marker comments of the page; the rest
of the page is left alone. ``--check`` exits 1 when the page is out of date
(tests/test_journal_presets.py does the same check). Values that the
journal does not state (catalog ``unverified``) are printed in italics.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pycrograph.journal_presets import JournalPreset, load_catalog  # noqa: E402

PAGE = ROOT / "docs" / "guide" / "page-setup.md"
BEGIN = "<!-- BEGIN journal presets table (scripts/make_journal_presets_table.py) -->"
END = "<!-- END journal presets table -->"


def _cell(p: JournalPreset, field: str, text: str) -> str:
    return f"*{text}*" if p.is_default(field) else text


def _font(p: JournalPreset) -> str:
    text = _cell(p, "font_size_pt", f"{p.font_size_pt:g}")
    if p.font_min_pt is not None or p.font_max_pt is not None:
        lo = f"{p.font_min_pt:g}" if p.font_min_pt is not None else "…"
        hi = f"{p.font_max_pt:g}" if p.font_max_pt is not None else "…"
        text += f" ({lo}–{hi})"
    return text


def render_table() -> str:
    cat = load_catalog()
    lines = [BEGIN, "",
             f"Catalog checked {cat.checked}. *Italics*: a default, not stated by the journal.",
             "",
             "| Publisher | Preset | Width (mm) | Max height (mm) | dpi | Font (pt) | Min line (pt) | Status |",
             "|---|---|---|---|---|---|---|---|"]
    for p in cat.presets:
        name = p.name.replace("|", "\\|")
        if p.source_is_url:
            name = f"[{name}]({p.source})"
        lines.append(f"| {p.publisher} | {name} | {_cell(p, 'width_mm', f'{p.width_mm:g}')} | "
                     f"{_cell(p, 'max_height_mm', f'{p.max_height_mm:g}')} | "
                     f"{_cell(p, 'dpi', str(p.dpi))} | {_font(p)} | "
                     f"{_cell(p, 'min_line_pt', f'{p.min_line_pt:g}')} | {p.badge} |")
    if cat.not_included:
        lines += ["", "Not included yet:", ""]
        lines += [f"- **{k}**: {v}" for k, v in cat.not_included.items()]
    lines += ["", END]
    return "\n".join(lines)


def updated_page(text: str) -> str:
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + render_table() + text[end:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the page is out of date")
    args = ap.parse_args(argv)
    text = PAGE.read_text(encoding="utf-8")
    new = updated_page(text)
    if args.check:
        if new != text:
            print(f"{PAGE.relative_to(ROOT)} is out of date: run {Path(__file__).name}")
            return 1
        return 0
    PAGE.write_text(new, encoding="utf-8")
    print(f"wrote {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
