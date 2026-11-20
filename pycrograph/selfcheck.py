# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
``pycrograph --selfcheck [--selfcheck-out FILE]``: offline smoke test of an
installed or PyInstaller-packaged app, run by the release builds on the
artifact users download (build-macos.yml, build-windows.yml).

PysternBlot's selfcheck tests its update check (TLS to GitHub). Pycrograph
is local only (no network), so this one exercises what a broken bundle
would break: package metadata (``--copy-metadata``), bundled resources,
the Qt plugins used (PNG writer, PDF writer, SVG module), and an end-to-end
register -> render -> export of a synthetic image in a temporary workspace.

Exit code 0 = passed, 1 = failed. The report is printed and, with
``--selfcheck-out``, written to FILE (GUI builds have no console).
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, Optional

LOGO = "pycrograph_logo.png"      # full logo + wordmark (Home tab)
ICON = "pycrograph_icon.png"      # PG mark only (window / app icon)
# Both are rendered from docs/_static/logo/*.svg with rsvg: QtSvg cannot draw
# the logo's clipPath / blur, so the .svg files are never loaded in Qt.


def _resource(name: str) -> Path:
    from importlib.resources import files
    return Path(str(files("pycrograph.resources").joinpath(name)))


def logo_path() -> Path:
    return _resource(LOGO)


def icon_path() -> Path:
    return _resource(ICON)


def run_selfcheck(out: Optional[str] = None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines: list[str] = []
    ok = True

    def check(name: str, fn: Callable[[], str]) -> None:
        nonlocal ok
        try:
            lines.append(f"OK   {name}: {fn()}")
        except Exception as exc:                          # report, do not crash
            ok = False
            lines.append(f"FAIL {name}: {exc!r}")
            lines.extend("     " + ln for ln in traceback.format_exc().splitlines()[-4:])

    from . import __version__
    lines.append(f"Pycrograph {__version__} selfcheck — Python {sys.version.split()[0]}, "
                 f"{sys.platform}, frozen={getattr(sys, 'frozen', False)}")

    def metadata() -> str:
        from importlib.metadata import version
        v = version("pycrograph")
        assert v == __version__, f"package metadata {v} != __version__ {__version__}"
        return v

    def resources() -> str:
        found = []
        for p in (logo_path(), icon_path()):
            assert p.is_file() and p.stat().st_size > 1000, f"missing {p}"
            found.append(p.name)
        return ", ".join(found)

    def qt() -> str:
        from PySide6 import __version__ as qv
        from PySide6.QtGui import QImageWriter
        from PySide6.QtSvg import QSvgGenerator
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        fmts = {bytes(f).decode() for f in QImageWriter.supportedImageFormats()}
        assert "png" in fmts, f"Qt PNG writer missing ({sorted(fmts)})"
        assert QSvgGenerator().resolution() > 0, "QtSvg unusable"
        return f"PySide6 {qv}"

    def end_to_end() -> str:
        import numpy as np
        import tifffile

        from .export import ExportSettings, export_figure
        from .figures import FigureEditor, create_figure
        from .logchain import verify_log_chain
        from .render import PanelRenderer
        from .storage import Workspace, create_dataset, register_image, set_image_hash, sha256_file
        with tempfile.TemporaryDirectory(prefix="pycrograph-selfcheck-") as d:
            d = Path(d)
            img = d / "selfcheck.tif"
            yy, xx = np.mgrid[:48, :64]
            tifffile.imwrite(img, (1000 + 30 * xx + 10 * yy).astype(np.uint16))
            ws = Workspace(d / "ws")
            ws.ensure()
            p = ws.create_project("selfcheck")
            ref = register_image(p, img, create_dataset(p, "A").id)
            set_image_hash(p, ref.id, sha256_file(img))
            fig = create_figure(p, "selfcheck")
            FigureEditor(p, fig, lambda: ws.save_project(p)).add_images([ref])
            r = PanelRenderer(lambda rr: ws.resolve_image_path(p, rr))
            try:
                pdf = export_figure(ws, p, fig, r, d / "f.pdf", ExportSettings("pdf"))
                png = export_figure(ws, p, fig, r, d / "f.png", ExportSettings("png", dpi=150))
            finally:
                r.close()
            data = pdf.files[0].read_bytes()
            assert b"DCTDecode" not in data, "PDF raster is JPEG-encoded"
            assert re.search(rb"/Width 64\s*/Height 48", data), "PDF raster not at native size"
            assert png.files[0].stat().st_size > 0
            return (f"PDF {len(data)} B (lossless, native 64×48), PNG ok, "
                    f"log chain {verify_log_chain(p).status}")

    check("package metadata", metadata)
    check("bundled resources", resources)
    check("Qt", qt)
    check("register → render → export", end_to_end)
    lines.append("selfcheck passed" if ok else "selfcheck FAILED")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if out:
        Path(out).write_text(text, encoding="utf-8")
    return 0 if ok else 1
