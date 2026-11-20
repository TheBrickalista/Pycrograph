# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Repo invariants carried over from PysternBlot: dependency floors equal the
CI minimum pins, one version string everywhere, docs Python matches CI."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

import pycrograph

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_dependency_floors_match_ci_minimum_pins():
    floors = {}
    for dep in _pyproject()["project"]["dependencies"]:
        m = re.match(r"^([A-Za-z0-9_.-]+)>=([^,]+)", dep)
        assert m, f"every runtime dependency needs a >= floor: {dep}"
        floors[m.group(1).lower()] = m.group(2)
    pins = {}
    for line in (ROOT / "ci" / "requirements-min.in").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==")
            pins[name.strip().lower()] = ver.strip()
    for name, floor in floors.items():
        assert pins.get(name) == floor, f"{name}: pyproject floor {floor} != ci pin {pins.get(name)}"


def test_single_version_string():
    v = pycrograph.__version__
    assert _pyproject()["project"]["version"] == v
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(rf"^version: {re.escape(v)}$", cff, re.M)
    conf = (ROOT / "docs" / "conf.py").read_text(encoding="utf-8")
    assert f'release = "{v}"' in conf
    assert f"## [{v}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_docs_python_matches_ci_docs_job():
    rtd = yaml.safe_load((ROOT / ".readthedocs.yaml").read_text())
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "pytest.yml").read_text())
    steps = wf["jobs"]["docs"]["steps"]
    py = next(s["with"]["python-version"] for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    assert str(rtd["build"]["tools"]["python"]) == str(py)


def test_shared_core_markers_present():
    """Files copied from PysternBlot keep their provenance marker."""
    for rel in ("pycrograph/logchain.py", "pycrograph/jsonsafe.py", "pycrograph/integrity.py",
                "pycrograph/ui/zoomable_graphics_view.py", "pycrograph/ui/levels_histogram.py",
                "pycrograph/archive.py", "scripts/make_version_file.py",
                "tests/test_make_version_file.py", "tests/test_workflows_hardening.py",
                ".github/workflows/build-macos.yml", ".github/workflows/build-windows.yml",
                ".github/workflows/publish.yml", ".github/macos/entitlements.plist",
                "ci/build-constraints.in"):
        assert "SHARED-CORE candidate (from PysternBlot" in (ROOT / rel).read_text(encoding="utf-8"), rel


@pytest.mark.parametrize("bundled,source", [("LICENSE.txt", "LICENSE"),
                                            ("CITATION.cff", "CITATION.cff")])
def test_bundled_copies_identical_to_repository_files(bundled, source):
    """pycrograph/resources keeps tracked copies (shown in the About tab):
    after editing the repository file, copy it over."""
    copy = ROOT / "pycrograph" / "resources" / bundled
    assert copy.read_bytes() == (ROOT / source).read_bytes(), (
        f"cp {source} pycrograph/resources/{bundled}")


def test_release_files_present():
    """What the release workflows need from the source tree."""
    from PIL import Image
    for name in ("pycrograph_logo.png", "pycrograph_icon.png"):
        with Image.open(ROOT / "pycrograph" / "resources" / name) as im:
            assert im.size == (1024, 1024), name
    for name in ("pycrograph_logo.svg", "pycrograph_icon.svg"):             # sources
        assert (ROOT / "docs" / "_static" / "logo" / name).is_file(), name
    tmpl = (ROOT / ".github" / "RELEASE_TEMPLATE.md").read_text(encoding="utf-8")
    assert "Pycrograph-vVERSION-macOS.zip" in tmpl and "Pycrograph-vVERSION-Windows.exe" in tmpl
    assert (ROOT / "launcher.py").is_file()
    pyproject = _pyproject()
    assert "pyinstaller" in " ".join(pyproject["project"]["optional-dependencies"]["build"])
    assert "twine" in " ".join(pyproject["project"]["optional-dependencies"]["dev"])
