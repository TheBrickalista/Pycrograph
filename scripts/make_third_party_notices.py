# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Write pycrograph/resources/THIRD_PARTY_NOTICES.txt from the installed
(locked) environment.

    python scripts/make_third_party_notices.py [--out FILE] [--lock uv.lock]

The packages are the runtime closure of pycrograph in uv.lock (no dev /
build extras): what the PyInstaller builds bundle. Each one must be
installed at a version uv.lock lists, or the script fails. For each package
it writes the version, the licence, a copyright line and the full text of
every licence file the distribution declares (dist-info ``License-File``).
The Python runtime the builds embed is listed too.

PySide6 / shiboken6 wheels ship no licence file: the Qt for Python section
uses the verbatim LGPL-3.0 text kept in scripts/licenses/LGPL-3.0.txt, with
the dynamic-linking / replacement / source note.

The release builds run this script before PyInstaller, so the notices in
each app match its own platform's wheels (NumPy and Pillow wheels vendor
different libraries on macOS and Windows). The tracked copy is regenerated
by hand; tests/test_third_party_notices.py checks that it covers every
runtime dependency in uv.lock.
"""

from __future__ import annotations

import argparse
import re
import sys
import sysconfig
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "pycrograph" / "resources" / "THIRD_PARTY_NOTICES.txt"
LGPL3 = Path(__file__).resolve().parent / "licenses" / "LGPL-3.0.txt"
RULE = "=" * 78
THIN = "-" * 78

QT = ("pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6")
QT_NOTE = """\
PySide6 (Qt for Python), shiboken6 and the Qt 6 libraries they contain are
offered under LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0; Pycrograph uses them
under the GNU Lesser General Public License version 3 (LGPL-3.0).

- The Qt libraries are dynamically linked shared libraries (Qt*.framework on
  macOS, Qt6*.dll on Windows), bundled unmodified as separate files. You may
  replace them with your own (modified) build of a compatible Qt / PySide6
  version: in the macOS app they are in
  Pycrograph.app/Contents/Frameworks/PySide6/Qt/lib; the Windows one-file
  executable unpacks them at start, so rebuild it with PyInstaller (the
  complete source and build scripts of Pycrograph are public, GPL-3.0) or
  run Pycrograph from source against your Qt.
- Qt source code: https://download.qt.io/official_releases/qt/ and
  https://code.qt.io/cgit/qt/ ; Qt for Python (PySide6, shiboken6) source:
  https://download.qt.io/official_releases/QtForPython/ and
  https://code.qt.io/cgit/pyside/pyside-setup.git/ .
- The LGPL-3.0 incorporates the GNU General Public License version 3, whose
  full text is Pycrograph's own licence (LICENSE.txt, About > Legal)."""
LICENCE_OVERRIDE = {"pillow": "MIT-CMU (also known as HPND)"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_packages(lock_path: Path = ROOT / "uv.lock") -> dict[str, set[str]]:
    """{normalised name: versions in uv.lock} of pycrograph's runtime
    dependency closure (every marker branch; no optional extras)."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    by_name: dict[str, list[dict]] = {}
    for pkg in lock["package"]:
        by_name.setdefault(norm(pkg["name"]), []).append(pkg)
    [root] = by_name["pycrograph"]
    out: dict[str, set[str]] = {}
    stack = [norm(d["name"]) for d in root.get("dependencies", [])]
    while stack:
        name = stack.pop()
        if name in out:
            continue
        out[name] = {p["version"] for p in by_name[name]}
        for p in by_name[name]:
            stack.extend(norm(d["name"]) for d in p.get("dependencies", []))
    return out


def licence_of(dist: metadata.Distribution) -> str:
    md = dist.metadata
    expr = md.get("License-Expression")
    if expr:
        return expr
    lic = (md.get("License") or "").strip()
    if lic and "\n" not in lic and len(lic) < 80:
        return lic
    classifiers = [c.split(" :: ")[-1] for c in md.get_all("Classifier") or []
                   if c.startswith("License ::")]
    return "; ".join(classifiers) or "see the licence text below"


def licence_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """[(file name, text)] of the licence files the distribution declares."""
    names = dist.metadata.get_all("License-File") or []
    out = []
    for name in names:
        for candidate in (f"licenses/{name}", name):
            text = dist.read_text(candidate)
            if text is not None:
                out.append((name, text.strip("\n")))
                break
    if not out:                                     # older metadata: look in dist-info
        for f in dist.files or []:
            if re.search(r"(LICEN[CS]E|COPYING|NOTICE)", f.name, re.I) and ".dist-info" in str(f):
                out.append((f.name, f.read_text(encoding="utf-8").strip("\n")))
    return out


def copyright_line(texts: list[tuple[str, str]], dist: metadata.Distribution) -> str:
    for _name, text in texts:
        for line in text.splitlines():
            if re.match(r"\s*(Copyright|\(c\)|©)", line, re.I) and re.search(r"\d{4}", line):
                return line.strip()
    author = dist.metadata.get("Author") or dist.metadata.get("Author-email") or ""
    return f"Copyright the {dist.metadata['Name']} authors" + (f" ({author})" if author else "")


def _section(packages: list[tuple[str, str]], licence: str, copyright: str,
             note: str, texts: list[tuple[str, str]]) -> list[str]:
    lines = [RULE] + [f"Package: {name} {version}" for name, version in packages]
    lines += [f"License: {licence}", copyright]
    if note:
        lines += ["", note]
    for fname, text in texts:
        lines += [THIN, f"[{fname}]", "", text]
    return lines + [""]


def build(lock_path: Path = ROOT / "uv.lock") -> str:
    wanted = runtime_packages(lock_path)
    problems, dists = [], {}
    for name, versions in sorted(wanted.items()):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            problems.append(f"{name}: not installed (uv.lock: {', '.join(sorted(versions))})")
            continue
        if dist.version not in versions:
            problems.append(f"{name}: installed {dist.version}, uv.lock has "
                            f"{', '.join(sorted(versions))}")
        dists[name] = dist
    if problems:
        raise SystemExit("Not the locked environment (run `uv sync --locked`):\n  "
                         + "\n  ".join(problems))

    summary, body = [], []
    qt = [dists[n] for n in QT if n in dists]
    if qt:
        summary.append(("PySide6 / shiboken6 (Qt for Python, Qt 6)", "LGPL-3.0-only"))
        body += _section([(d.metadata["Name"], d.version) for d in qt], "LGPL-3.0-only",
                         "Copyright (C) The Qt Company Ltd. and other contributors.", QT_NOTE,
                         [("LGPL-3.0.txt", LGPL3.read_text(encoding="utf-8").strip("\n"))])
    for name, dist in sorted(dists.items()):
        if name in QT:
            continue
        texts = licence_texts(dist)
        if not texts:
            raise SystemExit(f"{name}: no licence file found in its distribution")
        licence = LICENCE_OVERRIDE.get(name, licence_of(dist))
        summary.append((f"{dist.metadata['Name']} {dist.version}", licence))
        body += _section([(dist.metadata["Name"], dist.version)], licence,
                         copyright_line(texts, dist), "", texts)

    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_licence = Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt"
    if py_licence.is_file():
        summary.append((f"Python {py} (runtime embedded in the apps)", "PSF-2.0"))
        body += _section([("Python", py)], "PSF-2.0",
                         "Copyright (c) 2001 Python Software Foundation; All Rights Reserved.", "",
                         [("LICENSE.txt", py_licence.read_text(encoding="utf-8").strip("\n"))])

    width = max(len(s) for s, _l in summary)
    head = [
        "Pycrograph — third-party notices",
        "",
        "Pycrograph is free software under the GNU General Public License v3.0 only",
        "(GPL-3.0-only, LICENSE.txt). The applications bundle the components below,",
        "each under its own licence, reproduced in full.",
        "",
        "Generated by scripts/make_third_party_notices.py from the locked environment",
        f"(uv.lock; {sys.platform}). Do not edit by hand.",
        "",
    ] + [f"  {s.ljust(width)}  {lic}" for s, lic in summary] + [""]
    return "\n".join(head + body).rstrip("\n") + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--lock", default=str(ROOT / "uv.lock"))
    args = ap.parse_args(argv)
    text = build(Path(args.lock))
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
