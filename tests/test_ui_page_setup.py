# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Page setup and Preferences: the two-level journal preset choice
(Publisher ▾ then Size ▾, plus Custom) and the preset description (offscreen)."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest

pytest.importorskip("PySide6.QtWidgets")

from pycrograph.journal_presets import load_catalog  # noqa: E402
from pycrograph.models import PagePreset, PageSetup  # noqa: E402
from pycrograph.preferences import Preferences  # noqa: E402
from pycrograph.ui.info_tabs import PreferencesTab  # noqa: E402
from pycrograph.ui.page_setup import CUSTOM_KEY, FIGURE_KEY, PageSetupDialog  # noqa: E402
from pycrograph.ui.preset_picker import CUSTOM  # noqa: E402

CELL = "Cell Press 2-column journals: 1 column (85 mm)"


def link_hrefs(html_text: str) -> list[str]:
    """The href of every <a> in a rich-text string (html.parser)."""
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hrefs: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.hrefs += [v for k, v in attrs if k == "href" and v]
    parser = Links()
    parser.feed(html_text)
    return parser.hrefs


def items(combo):
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


@pytest.fixture
def dialog(qtbot):
    def make(page: PageSetup, prefs: Preferences | None = None) -> PageSetupDialog:
        dlg = PageSetupDialog(page, prefs or Preferences())
        qtbot.addWidget(dlg)
        return dlg
    return make


def test_two_level_choice(dialog):
    prefs = Preferences()
    dlg = dialog(prefs.new_page(), prefs)
    pk = dlg.picker
    pubs = [d for _t, d in items(pk.publisher_combo)]
    assert pubs == load_catalog().publishers() + [CUSTOM]
    assert pk.publisher() == "Nature Portfolio" and pk.current_key() == "Nature double column"
    assert ("single column — 89 mm", "Nature single column") in items(pk.size_combo)
    assert "Official" in dlg.info.text()
    [source] = link_hrefs(dlg.info.text())                    # the preset's source link
    parsed = urlparse(source)
    assert parsed.scheme == "https" and parsed.hostname == "research-figure-guide.nature.com"
    assert "Nature Communications" in pk.publisher_combo.itemData(0, 3)   # tooltip: journals

    pk.choose("Cell Press")                                   # publisher: its first size
    assert pk.current_key() == CELL and dlg.width_spin.value() == 85.0
    text = dlg.info.text()
    assert "Secondary — verify" in text
    assert "225 mm max height (default, not stated by the journal)" in text
    assert "85 mm wide (default" not in text                  # stated by the journal
    pk.choose("PNAS", "PNAS: 2 columns (178 mm)")
    out = dlg.result_page()
    assert (out.preset.name, out.width_mm, out.height_mm) == ("PNAS: 2 columns (178 mm)", 178.0, 225.0)
    assert "journal range 6–12 pt" in dlg.info.text()
    assert "PNAS Digital Art Guidelines" in dlg.info.text()   # not a URL: plain text
    assert link_hrefs(dlg.info.text()) == []                  # ... and not made a link


def test_custom_page(dialog):
    dlg = dialog(Preferences().new_page())
    dlg.picker.choose(CUSTOM, CUSTOM_KEY)
    dlg.width_spin.setValue(150.0)
    dlg.height_spin.setValue(120.0)
    out = dlg.result_page()
    assert (out.preset.name, out.preset.width_mm, out.preset.max_height_mm) == ("Custom", 150.0, 120.0)
    assert out.preset.font_size_pt == 7.0                     # kept from the previous preset
    assert "User preset" in dlg.info.text() and "150 mm wide" in dlg.info.text()
    again = dialog(out)                                       # reopened: Custom selected
    assert again.picker.current_key() == CUSTOM_KEY and again.result_page().preset == out.preset


def test_existing_snapshot_kept_until_a_size_is_picked(dialog):
    old = PagePreset(name="Nature double column", font_size_pt=8.0, min_line_pt=0.5)
    dlg = dialog(PageSetup.from_preset(old))
    assert dlg.picker.current_key() == "Nature double column"
    assert "keeps its own copy" in dlg.info.text()
    assert dlg.result_page().preset == old                    # accepted unchanged
    dlg.picker.choose("Nature Portfolio", "Nature double column")
    assert dlg.result_page().preset.font_size_pt == 7.0       # now the catalog's


def test_figure_preset_in_no_list(dialog):
    gone = PagePreset(name="Deleted lab preset", width_mm=120, max_height_mm=100)
    dlg = dialog(PageSetup.from_preset(gone))
    pk = dlg.picker
    assert pk.publisher() == CUSTOM and pk.current_key() == FIGURE_KEY
    pk.choose("PLOS", "PLOS: full page (190.5 mm)")
    pk.choose(CUSTOM, FIGURE_KEY)                             # back to the figure's own
    assert dlg.result_page().preset == gone and dlg.width_spin.value() == 120.0


def test_preferences_default_uses_the_same_choice(qtbot):
    prefs = Preferences()
    saved = []
    tab = PreferencesTab(prefs, save=lambda: saved.append(prefs.default_preset))
    qtbot.addWidget(tab)
    pk = tab.default_picker
    assert pk.current_key() == "Nature double column"
    assert CUSTOM not in [d for _t, d in items(pk.publisher_combo)]       # no user preset yet
    pk.choose("PLOS", "PLOS: full page (190.5 mm)")
    assert prefs.default_preset == "PLOS: full page (190.5 mm)" and saved[-1] == prefs.default_preset
    assert prefs.new_page().width_mm == 190.5
    # Built-in rows are read-only; a duplicate is the user's and appears under Custom.
    row = [p.name for p in prefs.presets].index("Nature single column")
    tab.table.item(row, 1).setText("95")
    assert prefs.preset("Nature single column").width_mm == 89.0
    tab.table.selectRow(row)
    tab.delete_preset()
    assert "cannot be deleted" in tab.message.text() and prefs.preset("Nature single column")
    tab.duplicate_preset()
    assert [p.name for p in prefs.user_presets()] == ["Nature single column (2)"]
    pk.choose(CUSTOM, "Nature single column (2)")
    assert prefs.default_preset == "Nature single column (2)"
    status = tab.table.item(row, tab.table.columnCount() - 1).text()
    assert status == "Official"


def test_slide_preset_opens_wide_and_orientation_turns_it(dialog):
    slide = "Google Slides 16:9 (10 x 5.625 in)"
    dlg = dialog(Preferences().new_page())
    assert dlg.orientation.currentText() == "portrait"
    dlg.picker.choose("Presentation slides", slide)
    assert dlg.orientation.currentText() == "landscape"
    out = dlg.result_page()
    assert out.size_mm == (254.0, 142.875)                    # exact, not the spin's 142.9
    assert (out.width_mm, out.height_mm) == (142.875, 254.0)
    assert "dpi minimum (default, not stated by the journal)" in dlg.info.text()
    dlg.orientation.setCurrentText("portrait")
    assert dlg.result_page().size_mm == (142.875, 254.0)      # same paper, turned
    dlg.picker.choose("Nature Portfolio", "Nature single column")
    assert dlg.orientation.currentText() == "portrait"        # a journal preset: portrait again
    assert dlg.result_page().size_mm == (89.0, 170.0)
    reopened = dialog(out)                                     # a slide figure reopens wide
    assert reopened.orientation.currentText() == "landscape"
    assert reopened.result_page() == out


def test_custom_page_in_landscape_uses_the_laid_out_size(dialog):
    dlg = dialog(Preferences().new_page())
    dlg.picker.choose(CUSTOM, CUSTOM_KEY)
    dlg.width_spin.setValue(100.0)
    dlg.height_spin.setValue(250.0)
    dlg.orientation.setCurrentText("landscape")
    out = dlg.result_page()
    assert out.size_mm == (250.0, 100.0)
    assert (out.preset.width_mm, out.preset.max_height_mm) == (250.0, 100.0)
    assert "250 mm wide" in dlg.info.text()
