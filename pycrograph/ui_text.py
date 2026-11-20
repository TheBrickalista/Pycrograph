# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""User-facing wording helpers: proper singular / plural, never "panel(s)".

    plural(1, "panel")  -> "1 panel"       noun(1, "channel") -> "channel"
    plural(3, "panel")  -> "3 panels"      noun(2, "channel") -> "channels"

Not in ui/ because non-Qt modules (consistency, figures, export) word
messages too.
"""

from __future__ import annotations

from typing import Optional


def noun(n: int, word: str, plural_form: Optional[str] = None) -> str:
    """The word alone, singular for exactly 1, else plural (word + "s"
    unless `plural_form` is given)."""
    return word if n == 1 else (plural_form or word + "s")


def plural(n: int, word: str, plural_form: Optional[str] = None) -> str:
    """'1 panel', '0 panels', '3 panels'."""
    return f"{n} {noun(n, word, plural_form)}"
