# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""The offline --selfcheck the release builds run on the packaged app."""

from __future__ import annotations

import pytest

from pycrograph.main import main
from pycrograph.selfcheck import logo_path, run_selfcheck


def test_selfcheck_passes_and_writes_report(tmp_path):
    out = tmp_path / "selfcheck.txt"
    assert run_selfcheck(str(out)) == 0
    text = out.read_text()
    assert text.rstrip().endswith("selfcheck passed")
    assert "OK   register → render → export" in text and "lossless" in text


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


def test_logo_is_a_1024_png():
    from PIL import Image
    with Image.open(logo_path()) as im:
        assert im.size == (1024, 1024) and im.format == "PNG"
