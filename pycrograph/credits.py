# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Authors, copyright, third-party credits and citation shown in the app
(Home and About tabs).

The names are those of CITATION.cff, in its order (tests/test_citation.py
checks it). The About tab's citation is built from the copy of CITATION.cff
bundled in pycrograph/resources (authors, year, version and, once Zenodo
has assigned it, the concept DOI); `parse_cff` reads the few fields needed
without a YAML dependency (PyYAML is a dev dependency only).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

AUTHORS = ("Etienne Boulter", "Chloé C. Féral")          # CITATION.cff order
INSTITUTION = "Université Côte d'Azur, CNRS, Inserm, IRCAN"
COPYRIGHT = f"Copyright © 2026 {', '.join(AUTHORS)} — {INSTITUTION}"
SOURCE_URL = "https://github.com/TheBrickalista/Pycrograph"
ISSUES_URL = f"{SOURCE_URL}/issues"
NO_DOI = "DOI: assigned by Zenodo at the first release"
GPL_STATEMENT = (
    "Pycrograph is free software: you can redistribute it and/or modify it under\n"
    "the terms of the GNU General Public License version 3 only (GPL-3.0-only).\n"
    "It is distributed in the hope that it will be useful, but WITHOUT ANY\n"
    "WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS\n"
    "FOR A PARTICULAR PURPOSE. The full licence text is under Legal.")
# Runtime dependencies bundled in the apps (THIRD_PARTY_NOTICES.txt has the
# versions and full texts; tests check both list every runtime dependency).
THIRD_PARTY = (
    ("PySide6 / shiboken6 (Qt for Python, Qt 6)", "LGPL-3.0", ("pyside6", "pyside6-essentials",
                                                                "pyside6-addons", "shiboken6")),
    ("NumPy", "BSD-3-Clause", ("numpy",)),
    ("Pillow", "MIT-CMU (HPND)", ("pillow",)),
    ("Pydantic, pydantic-core", "MIT", ("pydantic", "pydantic-core")),
    ("tifffile", "BSD-3-Clause", ("tifffile",)),
    ("annotated-types", "MIT", ("annotated-types",)),
    ("typing-extensions", "PSF-2.0", ("typing-extensions",)),
    ("typing-inspection", "MIT", ("typing-inspection",)),
)


def to_entities(text: str) -> str:
    """HTML for a Qt rich-text label: markup escaped, every non-ASCII
    character as an entity (no reliance on the source file's encoding)."""
    return html.escape(text).encode("ascii", "xmlcharrefreplace").decode("ascii")


def copyright_html() -> str:
    return to_entities(COPYRIGHT)


# ------------------------------------------------------------------ citation
@dataclass(frozen=True)
class CffCitation:
    authors: tuple[tuple[str, str], ...]          # (given names, family names)
    year: str
    version: str
    doi: Optional[str]


def _value(raw: str) -> str:
    raw = raw.split(" #", 1)[0].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    return raw


def parse_cff(text: str) -> CffCitation:
    """Top-level `authors` (given / family names), `date-released` (year),
    `version` and `doi` of a CITATION.cff. Nested keys (e.g. a reference's
    doi) and comments are ignored."""
    top: dict[str, str] = {}
    authors: list[dict[str, str]] = []
    section = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            key, _, rest = line.partition(":")
            section = key.strip()
            top[section] = _value(rest)
            continue
        if section != "authors":
            continue
        m = re.match(r"^\s*(-\s+)?([A-Za-z-]+):(.*)$", line)
        if not m:
            continue
        if m.group(1) and len(line) - len(line.lstrip()) <= 2:
            authors.append({})                     # "  - " starts an author
        if authors:
            authors[-1][m.group(2)] = _value(m.group(3))
    return CffCitation(
        authors=tuple((a.get("given-names", ""), a.get("family-names", a.get("name", "")))
                      for a in authors),
        year=(re.match(r"\d{4}", top.get("date-released", "")) or [""])[0],
        version=top.get("version", ""),
        doi=top.get("doi") or None)


def initials(given: str) -> str:
    """"Chloé C." -> "CC", "Jean-Pierre" -> "JP"."""
    return "".join(part[0] for part in re.split(r"[\s.\-]+", given) if part)


def citation_text(c: CffCitation) -> str:
    names = ", ".join(f"{family} {initials(given)}".strip() for given, family in c.authors)
    return (f"{names} ({c.year}). Pycrograph (version {c.version}) [Computer software]. "
            f"{SOURCE_URL}")


def doi_line(c: CffCitation) -> str:
    if not c.doi:
        return NO_DOI
    return (f"DOI: https://doi.org/{c.doi}\n"
            "  (concept DOI: always resolves to the latest version)")


def repository_text(app_version: str, cff_text: Optional[str]) -> str:
    lines = ["Pycrograph", f"Version {app_version}", "",
             "Source code:", f"  {SOURCE_URL}", "",
             "Bug reports & feature requests:", f"  {ISSUES_URL}", "",
             "Citation:"]
    if cff_text:
        c = parse_cff(cff_text)
        lines += [f"  {citation_text(c)}", f"  {doi_line(c)}"]
    else:
        lines += ["  See CITATION.cff in the repository.", f"  {NO_DOI}"]
    lines += ["", "Citation metadata: CITATION.cff in the repository (GitHub: Cite this "
                  "repository)."]
    return "\n".join(lines)


def credits_text(notices: Optional[str]) -> str:
    width = max(len(name) for name, _l, _p in THIRD_PARTY)
    lines = ["Pycrograph — " + COPYRIGHT, "", GPL_STATEMENT, "",
             "Third-party libraries bundled with Pycrograph:"]
    lines += [f"  • {name.ljust(width)}  {lic}" for name, lic, _p in THIRD_PARTY]
    lines += ["", "Full notices (versions, copyright lines and licence texts) follow.", ""]
    lines.append(notices.rstrip("\n") if notices else
                 "THIRD_PARTY_NOTICES.txt not found. See the repository: "
                 f"{SOURCE_URL}/blob/main/pycrograph/resources/THIRD_PARTY_NOTICES.txt")
    return "\n".join(lines)
