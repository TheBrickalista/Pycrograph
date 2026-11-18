# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Repo invariants carried over from PysternBlot: dependency floors equal the
CI minimum pins, one version string everywhere, docs Python matches CI."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

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
                "pycrograph/ui/zoomable_graphics_view.py", "pycrograph/ui/levels_histogram.py"):
        assert "SHARED-CORE candidate (from PysternBlot" in (ROOT / rel).read_text(encoding="utf-8"), rel
