# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""The Home tab (logo, version, entry-point buttons) through the real window."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFileDialog, QInputDialog, QMessageBox, QPushButton,
)

from pycrograph import __version__  # noqa: E402
from pycrograph.ui import home_tab as H  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, workspace):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    w.show()
    app.processEvents()
    yield w
    w.close()


def project_buttons(home):
    return (home.import_btn, home.figures_btn)


def test_home_shows_logo_version_and_licence(window):
    home = window.home
    assert window.tabs.currentWidget() is home
    pm = home.logo.pixmap()
    assert not pm.isNull()
    assert pm.width() / pm.devicePixelRatio() == pytest.approx(H.LOGO_WIDTH, abs=1)
    assert home.title.text() == f"Version {__version__}"
    assert home.subtitle.text() == H.SUBTITLE
    text = home.copyright.text()
    assert "GNU General Public License v3" in text and "without warranty" in text
    assert "IRCAN" in text and "Inserm U1081" in text


def test_copyright_names_the_citation_authors(window):
    import yaml
    cff = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    for a in cff["authors"]:
        assert f"{a['given-names']} {a['family-names']}" in window.home.copyright.text()


def test_logo_pixmap_is_hidpi_sharp(app):
    pm = H.logo_pixmap(320, 2.0)
    assert pm.devicePixelRatio() == 2.0 and pm.width() == 640      # 320 logical px
    assert H.logo_pixmap(320, 1.0).width() == 320


def test_project_buttons_follow_the_open_project(window, workspace, monkeypatch):
    home = window.home
    for b in project_buttons(home):
        assert not b.isEnabled() and b.toolTip() == H.NEEDS_PROJECT
    assert home.new_btn.isEnabled() and home.open_btn.isEnabled() and home.archive_btn.isEnabled()
    p = workspace.create_project("Open me")
    window.open_project_by_id(p.project.id)
    for b in project_buttons(home):
        assert b.isEnabled() and b.toolTip() != H.NEEDS_PROJECT
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    window.archive_project()                                       # closes the project
    for b in project_buttons(home):
        assert not b.isEnabled() and b.toolTip() == H.NEEDS_PROJECT


def test_new_project_button(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("From home", True)))
    window.home.new_btn.click()
    assert window.project is not None and window.project.project.name == "From home"
    assert window.home.figures_btn.isEnabled()


def test_buttons_route_to_their_tabs(window, workspace):
    home = window.home
    home.open_btn.click()
    assert window.tabs.currentWidget() is window.projects
    window.tabs.setCurrentWidget(home)
    home.about_btn.click()
    assert window.tabs.currentWidget() is window.about_tab
    window.open_project_by_id(workspace.create_project("Figs").project.id)
    window.tabs.setCurrentWidget(home)
    home.figures_btn.click()
    assert window.tabs.currentWidget() is window.figures_tab


def test_import_images_button_uses_the_import_flow(window, workspace, mm_tiff, monkeypatch):
    window.open_project_by_id(workspace.create_project("Import").project.id)
    window.tabs.setCurrentWidget(window.home)
    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(
        lambda *a, **k: asked.append(a[1]) or ([str(mm_tiff)], "")))
    window.home.import_btn.click()
    window.hashq.wait(30000)
    assert asked == ["Import images (by reference)"]
    assert len(window.project.images) == 1
    assert window.tabs.currentWidget() is window.library_tab
    # Cancelled: nothing imported, Home stays.
    window.tabs.setCurrentWidget(window.home)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], "")))
    window.home.import_btn.click()
    assert window.tabs.currentWidget() is window.home and len(window.project.images) == 1


def test_import_archive_button_opens_the_archive_picker(window, monkeypatch):
    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(
        lambda *a, **k: asked.append((a[1], a[3])) or ("", "")))
    window.home.archive_btn.click()
    assert asked == [("Import archive", "Pycrograph archive (*.pcarchive)")]


def test_logo_on_a_light_card_in_dark_mode(app):
    home = H.HomeTab()
    assert home.logo_card.styleSheet() == ""
    dark = QPalette(home.palette())
    dark.setColor(QPalette.Window, QColor("#1e1e1e"))
    dark.setColor(QPalette.WindowText, QColor("#eeeeee"))
    home.setPalette(dark)
    app.processEvents()
    assert H.is_dark(home.palette()) and "background" in home.logo_card.styleSheet()
    assert not home.logo.pixmap().isNull()
    light = QPalette(dark)
    light.setColor(QPalette.Window, QColor("#f0f0f0"))
    home.setPalette(light)
    app.processEvents()
    assert home.logo_card.styleSheet() == ""
    assert home.title.styleSheet() == "" and home.subtitle.styleSheet() == ""   # palette colours


def _luminance(c: QColor) -> float:
    def lin(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def contrast(a: QColor, b: QColor) -> float:
    """WCAG 2 contrast ratio."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def palette(window: str, text: str) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(window))
    pal.setColor(QPalette.WindowText, QColor(text))
    return pal


@pytest.mark.parametrize("window,text", [("#efefef", "#000000"),      # Fusion light
                                         ("#ececec", "#1d1d1f"),      # macOS light
                                         ("#353535", "#ffffff"),      # Fusion dark
                                         ("#1e1e1e", "#dfdfdf")],     # macOS dark
                         ids=["light", "macos-light", "dark", "macos-dark"])
def test_subtitle_contrast_at_least_4_5(app, window, text):
    home = H.HomeTab()
    home.setPalette(palette(window, text))
    app.processEvents()
    shown = home.subtitle.palette().color(QPalette.WindowText)
    assert shown == H.subtitle_color(home.palette())
    assert shown != QColor(text)                                   # softer than body text
    assert contrast(shown, QColor(window)) >= 4.5


def test_buttons_visible_without_scrolling_in_1280x800(app, window):
    window.resize(1280, 800)
    window.tabs.setCurrentWidget(window.home)
    app.processEvents()
    home = window.home
    assert home.verticalScrollBar().maximum() == 0
    view = home.viewport().rect()
    for b in home.findChildren(QPushButton):
        top_left = b.mapTo(home.viewport(), b.rect().topLeft())
        assert view.contains(top_left) and view.contains(top_left + b.rect().bottomRight()), b.text()
