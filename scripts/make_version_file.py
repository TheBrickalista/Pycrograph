# Pycrograph
# SHARED-CORE candidate (from PysternBlot: scripts/make_version_file.py) — adapted: PEP 440
# pre-releases (a/b/rc) accepted; names and licence string changed.
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Generate a PyInstaller Windows version-file from pyproject.toml's version.

pyproject.toml is the single source of truth for the app version; nothing
here hard-codes it. The macOS build's inline PlistBuddy step and the
release-tag check in publish.yml share parse_version() / full_version()
with this script, so every platform rejects the same versions the same way.

Accepted: ``x.y.z`` and PEP 440 pre-releases ``x.y.zaN``, ``x.y.zbN``,
``x.y.zrcN`` (e.g. ``0.2.0b1``). The OS version fields (CFBundleShortVersion-
String / CFBundleVersion, FileVersion / ProductVersion) take the numeric
``x.y.z`` (``x.y.z.0`` on Windows); the full string stays in the About tab,
the release tag and the artifact names. Anything else (``.devN``,
``.postN``, ``+local``, 2- or 4-part, leading ``v``, whitespace) is a build
error, never silently coerced.

Usage:
    python scripts/make_version_file.py [PYPROJECT_PATH] [OUT_PATH]

Defaults: PYPROJECT_PATH=pyproject.toml, OUT_PATH=build/version_file.txt
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")


class InvalidVersionError(ValueError):
    pass


def _raw_version(pyproject_path: str | Path) -> str:
    path = Path(pyproject_path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    try:
        return str(data["project"]["version"])
    except KeyError as exc:
        raise InvalidVersionError(f"No [project].version found in {path}") from exc


def _match(raw: str) -> re.Match:
    match = _VERSION_RE.match(raw)
    if not match:
        raise InvalidVersionError(
            f"pyproject.toml version {raw!r} is not x.y.z or a PEP 440 pre-release "
            f"x.y.z(a|b|rc)N (integers only; no .dev/.post/local suffix). OS bundle/version "
            f"metadata needs exactly three integer components."
        )
    return match


def parse_version(pyproject_path: str | Path) -> tuple[int, int, int]:
    """The numeric (x, y, z) for OS metadata; a pre-release suffix is
    validated, then dropped (0.2.0b1 -> (0, 2, 0))."""
    m = _match(_raw_version(pyproject_path))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def full_version(pyproject_path: str | Path) -> str:
    """The exact, validated version string (0.2.0b1): release tag v<this>,
    artifact names, About tab."""
    raw = _raw_version(pyproject_path)
    _match(raw)
    return raw


def is_prerelease(pyproject_path: str | Path) -> bool:
    return _match(_raw_version(pyproject_path)).group(4) is not None


def render_version_file(version: tuple[int, int, int], full: str | None = None) -> str:
    """Render a PyInstaller --version-file document. Numeric fields are
    x.y.z.0; `full` (e.g. 0.2.0b1) only appears in the free-text comments."""
    x, y, z = version
    version_str = f"{x}.{y}.{z}.0"
    note = f"\n# Full version: {full}" if full else ""
    return f"""# UTF-8
#
# For more details about fixed file info 'ffi' see:
# http://msdn.microsoft.com/en-us/library/ms646997.aspx{note}
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({x}, {y}, {z}, 0),
    prodvers=({x}, {y}, {z}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'Pycrograph contributors'),
        StringStruct(u'FileDescription', u'Pycrograph — microscopy figure builder'),
        StringStruct(u'FileVersion', u'{version_str}'),
        StringStruct(u'InternalName', u'Pycrograph'),
        StringStruct(u'LegalCopyright', u'GPL-3.0-only'),
        StringStruct(u'OriginalFilename', u'Pycrograph.exe'),
        StringStruct(u'ProductName', u'Pycrograph'),
        StringStruct(u'ProductVersion', u'{version_str}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    pyproject_path = argv[0] if len(argv) > 0 else "pyproject.toml"
    out_path = Path(argv[1] if len(argv) > 1 else "build/version_file.txt")

    try:
        version = parse_version(pyproject_path)
        full = full_version(pyproject_path)
    except InvalidVersionError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_version_file(version, full), encoding="utf-8")
    print(f"Wrote version file for {full} ({'.'.join(map(str, version))}.0) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
