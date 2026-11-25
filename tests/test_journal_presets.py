# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Journal page presets catalog (resources/journal_presets.json): loader and
validation, built-ins in Preferences, and the page_size / font_size
consistency rules."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from pycrograph import consistency as C
from pycrograph import journal_presets as J
from pycrograph.figures import FigureEditor, create_figure
from pycrograph.models import LANDSCAPE_ASPECT, PagePreset, PageSetup, PanelLabel
from pycrograph.preferences import (
    DEFAULT_PRESET, Preferences, builtin_presets, load_preferences, preferences_path,
    save_preferences,
)
from pycrograph.selfcheck import JOURNAL_PRESETS, resource_path
from pycrograph.storage import create_dataset, register_image

ROOT = Path(__file__).resolve().parents[1]
NATURE = ("Nature single column", "Nature double column")


# ------------------------------------------------------------ loader
def test_every_entry_loads():
    cat = J.load_catalog()
    names = [p.name for p in cat.presets]
    assert len(names) == len(set(names)) == 57
    assert len(cat.publishers()) == 20
    for p in cat.presets:
        assert p.width_mm > 0 and p.max_height_mm > 0 and p.dpi >= 72
        assert p.font_size_pt > 0 and p.min_line_pt > 0
        assert p.status in J.STATUS_BADGE and p.source and p.publisher
        assert set(p.unverified) <= set(J.CHECKABLE_FIELDS)
    for n in NATURE:
        assert cat.get(n) is not None and cat.get(n).publisher == "Nature Portfolio"
    assert (cat.get("Nature single column").width_mm, cat.get("Nature double column").width_mm) \
        == (89.0, 183.0)


def test_raw_json_matches_the_model():
    """Every key of the file is read (extra="forbid" on load), and the file
    is the one the package ships."""
    raw = json.loads(resource_path(JOURNAL_PRESETS).read_text(encoding="utf-8"))
    assert len(raw["presets"]) == len(J.load_catalog().presets)
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "resources/*" in pyproject["tool"]["setuptools"]["package-data"]["pycrograph"]


def _entry(**kw):
    base = dict(name="X", publisher="P", width_mm=80, max_height_mm=200, dpi=300,
                font_family="Arial", font_size_pt=8, min_line_pt=0.5, status="official",
                unverified=[], applies_to=[], source="https://example.org", note="")
    return {**base, **kw}


def _catalog(*entries):
    return json.dumps({"schema": 1, "checked": "2026-11-25", "presets": list(entries)})


@pytest.mark.parametrize("bad", [
    [_entry(), _entry()],                                     # duplicate name
    [_entry(width_mm=0)],                                     # size <= 0
    [_entry(max_height_mm=-1)],
    [_entry(unverified=["colour"])],                          # not a field
    [_entry(font_min_pt=9)],                                  # default below the range
    [_entry(status="rumour")],
    [_entry(extra_key=1)],                                    # unknown key
    [],                                                       # empty
])
def test_invalid_catalogs_refused(bad):
    with pytest.raises(ValidationError):
        J.parse_catalog(_catalog(*bad))


def test_size_labels_unique_per_publisher():
    cat, labels = J.load_catalog(), J.size_labels()
    assert labels["Nature single column"] == "single column — 89 mm"
    assert labels["PNAS: 1 column (87 mm)"] == "1 column — 87 mm"
    assert labels["JBC: 2 columns (175 mm)"] == "JBC: 2 columns — 175 mm"      # journal kept
    assert labels["Google Slides 16:9 (10 x 5.625 in)"] == \
        "Google Slides 16:9 (10 x 5.625 in) — 254 × 142.875 mm"            # wide: both sides
    for pub in cat.publishers():
        mine = [labels[p.name] for p in cat.by_publisher(pub)]
        assert len(mine) == len(set(mine)) and all("mm" in lb for lb in mine), pub


# ------------------------------------------------------------ preferences
def test_builtins_always_from_catalog_user_presets_kept(tmp_path):
    stale = PagePreset(name="Nature double column", width_mm=183, max_height_mm=170,
                       font_size_pt=8.0, min_line_pt=0.5)          # the 0.2 built-in values
    mine = PagePreset(name="Lab poster", width_mm=300, max_height_mm=400)
    preferences_path(tmp_path).write_text(json.dumps(
        {"presets": [stale.model_dump(), mine.model_dump()], "default_preset": "Lab poster"}))
    prefs = load_preferences(tmp_path)
    assert prefs.preset("Nature double column") == J.journal_info("Nature double column").page_preset()
    assert [p.name for p in prefs.presets] == [p.name for p in builtin_presets()] + ["Lab poster"]
    assert prefs.user_presets() == [mine] and prefs.default() == mine
    save_preferences(tmp_path, prefs)
    saved = json.loads(preferences_path(tmp_path).read_text())
    assert [p["name"] for p in saved["presets"]] == ["Lab poster"]


def test_default_preset_and_fallback():
    assert Preferences().default().name == DEFAULT_PRESET == "Nature double column"
    assert Preferences(default_preset="gone").default().name == DEFAULT_PRESET


# ------------------------------------------------------------ consistency
@pytest.fixture
def env(workspace, mm_tiff):
    p = workspace.create_project("Journal")
    ref = register_image(p, mm_tiff, create_dataset(p, "A").id)
    ref.sha256 = "0" * 64
    fig = create_figure(p, "F")
    ed = FigureEditor(p, fig, lambda: None)
    [panel] = ed.add_images([ref])
    return p, fig, panel


def page(name: str, **kw) -> PageSetup:
    return PageSetup.from_preset(Preferences().preset(name), **kw)


def issues(p, fig, code):
    return [i for i in C.check_figure(p, fig, source_status=lambda _r: "ok") if i.code == code]


def test_page_size(env):
    p, fig, _panel = env
    fig.page = page("Nature single column")
    assert not issues(p, fig, "page_size")
    fig.page.width_mm = 120                                   # wider than 89 mm
    [i] = issues(p, fig, "page_size")
    assert "wider than the 89 mm width" in i.message and "default" not in i.message
    fig.page = page("Nature single column", orientation="landscape")      # 170 x 89
    [i] = issues(p, fig, "page_size")
    assert "wider" in i.message and "taller" not in i.message
    # Cell Press: the maximum height is a default, not a journal rule.
    fig.page = page("Cell Press 2-column journals: 1 column (85 mm)")
    fig.page.height_mm = 260
    [i] = issues(p, fig, "page_size")
    assert "taller than the 225 mm maximum height (default, not stated by the journal)" in i.message
    # A user preset has no catalog entry: its own sizes are checked.
    fig.page = PageSetup.from_preset(PagePreset(name="Lab", width_mm=100, max_height_mm=100))
    fig.page.height_mm = 150
    [i] = issues(p, fig, "page_size")
    assert "“Lab”" in i.message and "default" not in i.message


def test_existing_figure_keeps_its_snapshot(env):
    """A figure set up with the old built-in (8 pt, 0.5 pt lines) keeps it."""
    p, fig, panel = env
    old = PagePreset(name="Nature double column", font_size_pt=8.0, min_line_pt=0.5)
    fig.page = PageSetup.from_preset(old)
    Preferences()                                              # loading prefs changes nothing
    assert fig.page.preset == old
    panel.labels = [PanelLabel(text="merge")]                  # 8 pt default > Nature 7 pt max
    [i] = issues(p, fig, "font_size")
    assert "above the 7 pt maximum" in i.message and "label “merge” 8 pt" in i.message


def test_font_size(env):
    p, fig, panel = env
    fig.page = page("Nature double column")                    # Nature: 5–7 pt
    panel.labels = [PanelLabel(text="merge"), PanelLabel(text="a", position="top-left",
                                                         font_size_pt=8)]
    assert not issues(p, fig, "font_size")                     # panel letter: min only
    panel.labels[0].font_size_pt = 4
    panel.scale_bar.visible, panel.scale_bar.font_size_pt = True, 9
    [i] = issues(p, fig, "font_size")
    assert "below the 5 pt minimum — label “merge” 4 pt" in i.message
    assert "above the 7 pt maximum — scale bar text 9 pt" in i.message
    assert "default" not in i.message and i.panel_ids == (panel.id,)
    panel.labels[1].font_size_pt = 3                           # letters still need the minimum
    assert "label “a” 3 pt" in issues(p, fig, "font_size")[0].message
    # JCI gives no range: skipped. A user preset: skipped.
    fig.page = page("JCI / JCI Insight: 90 mm")
    assert not issues(p, fig, "font_size")
    fig.page = PageSetup.from_preset(PagePreset(name="Lab"))
    assert not issues(p, fig, "font_size")


def test_font_limit_named_as_default_when_unverified(env, monkeypatch):
    p, fig, panel = env
    info = J.JournalPreset.model_validate(_entry(name="Lab", font_min_pt=6,
                                                 unverified=["font_min_pt"]))
    monkeypatch.setattr(C, "journal_info", lambda name: info if name == "Lab" else None)
    fig.page = PageSetup.from_preset(info.page_preset())
    panel.labels = [PanelLabel(text="merge", font_size_pt=5)]
    [i] = issues(p, fig, "font_size")
    assert "below the 6 pt minimum (default, not stated by the journal)" in i.message


def test_guide_table_in_sync_with_catalog():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_journal_presets_table as mk
    text = mk.PAGE.read_text(encoding="utf-8")
    assert mk.updated_page(text) == text, "run scripts/make_journal_presets_table.py"
    assert text.count("](https://") >= 50 and "Secondary — verify" in text


# ------------------------------------------------------------ orientation (slides)
SLIDE = "PowerPoint widescreen 16:9 (13.333 x 7.5 in)"


def test_slide_presets_open_wide_in_landscape():
    slides = J.load_catalog().by_publisher("Presentation slides")
    assert len(slides) == 4
    for s in slides:
        assert s.width_mm > s.max_height_mm and s.font_min_pt is None and s.font_max_pt is None
        assert {"dpi", "font_size_pt", "min_line_pt"} <= set(s.unverified)
        pg = PageSetup.from_preset(s.page_preset())
        assert pg.orientation == "landscape" and pg.size_mm == (s.width_mm, s.max_height_mm)
        assert pg.width_mm < pg.height_mm                     # stored as the portrait size
    tall = PageSetup.from_preset(J.journal_info(SLIDE).page_preset(), orientation="portrait")
    assert tall.size_mm == (190.5, 338.67)                     # the same paper, turned
    # Journal presets: portrait as before, even slightly wider than tall.
    for name in ("Nature double column", "JCI / JCI Insight: 180 mm"):
        info = J.journal_info(name)
        assert info.width_mm > info.max_height_mm
        pg = page(name)
        assert (pg.orientation, pg.size_mm) == ("portrait", (info.width_mm, info.max_height_mm))
    assert page("Nature double column", orientation="landscape").size_mm == (170.0, 183.0)
    widest = max(J.load_catalog().presets, key=lambda e: e.width_mm / e.max_height_mm
                 if e.publisher != "Presentation slides" else 0)
    assert widest.width_mm / widest.max_height_mm < LANDSCAPE_ASPECT


def test_slide_page_size_and_no_font_range(env):
    p, fig, panel = env
    fig.page = page(SLIDE)
    panel.labels = [PanelLabel(text="merge", font_size_pt=40)]
    assert not issues(p, fig, "page_size") and not issues(p, fig, "font_size")
    fig.page.orientation = "portrait"                          # turned: taller than the slide
    [i] = issues(p, fig, "page_size")
    assert "taller than the 190.5 mm maximum height" in i.message and "wider" not in i.message
