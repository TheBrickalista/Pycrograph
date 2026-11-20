# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""THIRD_PARTY_NOTICES.txt (bundled, shown under About > Copyright &
Credits) covers every runtime dependency in uv.lock, at a locked version,
with its licence text; the generator agrees with it."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import make_third_party_notices as mtpn  # noqa: E402

from pycrograph import credits  # noqa: E402

NOTICES = ROOT / "pycrograph" / "resources" / "THIRD_PARTY_NOTICES.txt"


@pytest.fixture(scope="module")
def runtime():
    return mtpn.runtime_packages(ROOT / "uv.lock")


@pytest.fixture(scope="module")
def sections():
    """{normalised package name: (version, section text)}."""
    text = NOTICES.read_text(encoding="utf-8")
    out = {}
    for block in text.split(mtpn.RULE)[1:]:
        for name, version in re.findall(r"^Package: (\S+) (\S+)$", block, re.M):
            out[mtpn.norm(name)] = (version, block)
    return out


def test_runtime_closure_from_lock(runtime):
    assert {"pyside6", "numpy", "pillow", "pydantic", "pydantic-core", "tifffile",
            "annotated-types", "typing-extensions"} <= set(runtime)
    assert not {"pytest", "pyinstaller", "pyflakes", "twine", "pyyaml"} & set(runtime)   # extras


def test_every_runtime_dependency_has_a_notice(runtime, sections):
    missing = sorted(set(runtime) - set(sections))
    assert not missing, f"regenerate: python scripts/make_third_party_notices.py ({missing})"
    for name, versions in runtime.items():
        version, block = sections[name]
        assert version in versions, f"{name} {version} not in uv.lock {sorted(versions)}"
        assert re.search(r"^License: \S", block, re.M), name
        assert re.search(r"^\[[^\]]+\]$", block, re.M), f"{name}: no licence text"
        assert len(block) > 800, f"{name}: licence text too short"


def test_qt_section_lgpl_and_relinking_note(sections):
    _v, block = sections["pyside6"]
    for needle in ("License: LGPL-3.0-only", "dynamically linked", "replace them",
                   "https://download.qt.io/official_releases/QtForPython/",
                   "GNU LESSER GENERAL PUBLIC LICENSE", "Version 3, 29 June 2007"):
        assert needle in block, needle
    for name in ("pyside6-essentials", "pyside6-addons", "shiboken6"):
        assert sections[name][1] is block                            # one Qt section


def test_vendored_lgpl_is_the_fsf_text():
    import hashlib
    data = (ROOT / "scripts" / "licenses" / "LGPL-3.0.txt").read_bytes()
    assert hashlib.md5(data).hexdigest() == "e6a600fd5e1d9cbde2d983680233ad02"  # gnu.org lgpl-3.0.txt


def test_about_short_list_covers_runtime(runtime):
    listed = {p for _name, _lic, pkgs in credits.THIRD_PARTY for p in pkgs}
    assert listed == set(runtime)


def test_generator_matches_tracked_package_list(runtime, sections):
    """In the locked environment, the generator lists the same packages as
    the tracked file (versions may differ by Python version: uv.lock pins
    NumPy / tifffile per Python; other environments: skipped)."""
    try:
        text = mtpn.build(ROOT / "uv.lock")
    except SystemExit as exc:
        pytest.skip(f"not the locked environment: {exc}")
    generated = {mtpn.norm(n): v for n, v in re.findall(r"^Package: (\S+) (\S+)$", text, re.M)}
    assert set(generated) & set(runtime) == set(sections) & set(runtime) == set(runtime)
    for name, version in generated.items():
        if name in runtime:
            assert version in runtime[name]
