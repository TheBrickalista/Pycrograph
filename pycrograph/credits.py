# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Authors, affiliation and copyright line shown in the app (Home and About
tabs). The names are those of CITATION.cff, in its order; CITATION.cff is
not shipped with the app, so they are written here and tests/test_citation.py
checks that both agree.
"""

from __future__ import annotations

import html

AUTHORS = ("Etienne Boulter", "Chloé C. Féral")          # CITATION.cff order
INSTITUTION = "Université Côte d'Azur, CNRS, Inserm, IRCAN"
COPYRIGHT = f"Copyright © 2026 {', '.join(AUTHORS)} — {INSTITUTION}"
CITATION = "Boulter E, Féral CC"                          # Vancouver-style author list


def to_entities(text: str) -> str:
    """HTML for a Qt rich-text label: markup escaped, every non-ASCII
    character as an entity (no reliance on the source file's encoding)."""
    return html.escape(text).encode("ascii", "xmlcharrefreplace").decode("ascii")


def copyright_html() -> str:
    return to_entities(COPYRIGHT)
