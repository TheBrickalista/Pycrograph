# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""About tab (as PysternBlot's): Legal / Copyright & Credits / Repository."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph import __version__, credits  # noqa: E402
from pycrograph.ui import info_tabs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COPYRIGHT = ("Copyright © 2026 Etienne Boulter, Chloé C. Féral — "
             "Université Côte d'Azur, CNRS, Inserm, IRCAN")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def about(app):
    tab = info_tabs.AboutTab()
    yield tab
    tab.deleteLater()


def test_structure_and_legal_by_default(about):
    shown = [b.text().replace("&&", "&") for b in about.buttons.buttons()]   # && = literal &
    assert shown == ["Legal", "Copyright & Credits", "Repository"]
    assert about.view.isReadOnly() and about.legal_btn.isChecked()
    from PySide6.QtGui import QFontInfo
    assert QFontInfo(about.view.font()).fixedPitch()                # columns line up
    assert about.view.toPlainText() == (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_legal_fallback_when_licence_missing(about, monkeypatch):
    monkeypatch.setattr(info_tabs, "_resource_text", lambda name: None)
    about.credits_btn.click()
    about.legal_btn.click()
    text = about.view.toPlainText()
    assert text.startswith("License file not found.")
    assert "https://www.gnu.org/licenses/gpl-3.0.html" in text


def test_copyright_and_credits(about):
    about.credits_btn.click()
    assert about.credits_btn.isChecked() and not about.legal_btn.isChecked()
    text = about.view.toPlainText()
    assert text.splitlines()[0] == f"Pycrograph — {COPYRIGHT}"
    assert "GNU General Public License version 3 only" in text and "WITHOUT ANY" in text
    for name, lic, _pkgs in credits.THIRD_PARTY:
        assert f"• {name}" in text and lic in text, name
    notices = (ROOT / "pycrograph" / "resources" / "THIRD_PARTY_NOTICES.txt").read_text(
        encoding="utf-8")
    assert text.endswith(notices.rstrip("\n"))                     # the full notices follow
    assert text.index("Third-party libraries") < text.index("Package: PySide6 ")


def test_repository(about):
    about.repo_btn.click()
    text = about.view.toPlainText()
    lines = text.splitlines()
    assert lines[:2] == ["Pycrograph", f"Version {__version__}"]
    assert "  https://github.com/TheBrickalista/Pycrograph" in lines
    assert "  https://github.com/TheBrickalista/Pycrograph/issues" in lines
    assert (f"  Boulter E, Féral CC (2026). Pycrograph (version {__version__}) "
            "[Computer software]. https://github.com/TheBrickalista/Pycrograph") in lines
    # CITATION.cff has no top-level doi yet (PysternBlot's reference doi is not ours).
    assert "  DOI: assigned by Zenodo at the first release" in lines
    assert "zenodo.20185279" not in text


def test_repository_text_with_a_doi():
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert credits.parse_cff(cff).doi is None
    with_doi = cff.replace("# doi: added once the first release is archived on Zenodo",
                           "doi: 10.5281/zenodo.30000001")
    assert with_doi != cff
    c = credits.parse_cff(with_doi)
    assert c.doi == "10.5281/zenodo.30000001"
    assert c.authors == (("Etienne", "Boulter"), ("Chloé C.", "Féral"))
    assert (c.year, c.version) == ("2026", __version__)
    text = credits.repository_text(__version__, with_doi)
    assert "  DOI: https://doi.org/10.5281/zenodo.30000001" in text.splitlines()
    assert "concept DOI" in text and credits.NO_DOI not in text


def test_initials():
    assert credits.initials("Chloé C.") == "CC"
    assert credits.initials("Jean-Pierre") == "JP"
    assert credits.initials("Etienne") == "E"
