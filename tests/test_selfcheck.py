# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""The offline --selfcheck the release builds run on the packaged app."""

from __future__ import annotations

import pytest

from pycrograph.main import main
from pycrograph.selfcheck import icon_path, logo_path, run_selfcheck


def test_selfcheck_passes_and_writes_report(tmp_path):
    out = tmp_path / "selfcheck.txt"
    assert run_selfcheck(str(out)) == 0
    text = out.read_text()
    assert text.rstrip().endswith("selfcheck passed")
    assert "OK   register → render → export" in text and "lossless" in text
    assert ("OK   bundled resources: pycrograph_logo.png, pycrograph_icon.png, LICENSE.txt, "
            "THIRD_PARTY_NOTICES.txt, CITATION.cff") in text


def test_selfcheck_fails_without_the_icon(tmp_path, monkeypatch):
    import pycrograph.selfcheck as sc
    monkeypatch.setattr(sc, "ICON", "missing_icon.png")
    out = tmp_path / "s.txt"
    assert run_selfcheck(str(out)) == 1
    assert "FAIL bundled resources" in out.read_text()


def test_selfcheck_fails_loudly(tmp_path, monkeypatch):
    import importlib.metadata as md
    monkeypatch.setattr(md, "version", lambda name: "0.0.0-wrong")
    out = tmp_path / "s.txt"
    assert run_selfcheck(str(out)) == 1
    assert "FAIL package metadata" in out.read_text() and "selfcheck FAILED" in out.read_text()


def test_cli_flag_exits_with_code(tmp_path):
    out = tmp_path / "s.txt"
    with pytest.raises(SystemExit) as exc:
        main(["--selfcheck", "--selfcheck-out", str(out)])
    assert exc.value.code == 0 and out.is_file()


@pytest.mark.parametrize("path", [logo_path, icon_path], ids=["logo", "icon"])
def test_logo_and_icon_are_1024_pngs(path):
    from PIL import Image
    with Image.open(path()) as im:
        assert im.size == (1024, 1024) and im.format == "PNG" and im.mode == "RGBA"
