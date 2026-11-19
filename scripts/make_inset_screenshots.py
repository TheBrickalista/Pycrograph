# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Regenerate the screenshots of docs/guide/insets.md, offscreen.

    python scripts/make_inset_screenshots.py [--out docs/_static/img]

Everything is synthetic: random "nuclei" and "reticulum" images written to a
temporary workspace (tests/synth.py writes the MetaMorph / STK files), then
the real Figures tab is driven through its public methods and each widget
is captured with QWidget.grab(). Numbers use the English (US) locale
("2.0 mm") whatever the machine's locale. No real data, no network, nothing outside
the temporary folder is touched. Run it again whenever the inset UI changes.

Files written (all insets-*.png):
  create        the Figures tab after Create inset (Insets & Shapes tab open)
  dialog-split  Replicate dialog, split channels of one file (case a)
  dialog-wl     Replicate dialog, one file per wavelength, same stage (case b)
  dialog-cells  Replicate dialog, other cells and another objective (case c)
  linked        the Figures tab with a linked group (case a, replicated)
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))            # synth.py (synthetic MetaMorph files)

import numpy as np  # noqa: E402

SIZE = 256


def blobs(seed: int, n: int, sigma: float, amp: float, size: int = SIZE) -> np.ndarray:
    """Background + n Gaussian blobs, uint16 (a nuclei-like channel)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    img = rng.normal(600, 30, (size, size))
    for _ in range(n):
        cy, cx = rng.uniform(0.12, 0.88, 2) * size
        s = sigma * rng.uniform(0.8, 1.3)
        img += amp * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s ** 2)))
    return np.clip(img, 0, 65535).astype(np.uint16)


def reticulum(seed: int, size: int = SIZE) -> np.ndarray:
    """Background + a fine network of bright tubules (an ER-like channel):
    the zero-crossings of low-pass filtered noise."""
    rng = np.random.default_rng(seed)
    f = np.fft.fftfreq(size)
    low = np.exp(-(f[:, None] ** 2 + f[None, :] ** 2) / (2 * 0.03 ** 2))
    field = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(size=(size, size))) * low))
    field /= field.std()
    img = rng.normal(500, 25, (size, size)) + 2500 * np.exp(-(field / 0.18) ** 2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def write_inputs(folder: Path) -> dict[str, Path]:
    from synth import metamorph_xml, write_metamorph_tiff, write_stk
    out = {}
    # (a) one multichannel file: 2 channels (488 nuclei-like, 561 reticulum)
    out["multi"] = write_stk(folder / "cell 1 two channels.stk",
                             np.stack([blobs(1, 6, 18, 5000), reticulum(2)]),
                             wavelengths=[488, 561])

    def mm(name, plane, wl, stage, px=0.325):
        h, w = plane.shape
        return write_metamorph_tiff(folder / f"{name}.tif", [plane], [metamorph_xml(
            wavelength=wl, illum="GFP" if wl == 488 else "TxRed", px=px, width=w, height=h,
            name=name, stage=stage)])
    # (b) one MetaMorph file per wavelength, same stage position
    out["w1"] = mm("cell 2_w1GFP", blobs(3, 6, 18, 5000), 488, (1520.5, -340.25))
    out["w2"] = mm("cell 2_w2TxRed", reticulum(4), 561, (1520.5, -340.25))
    # (c) another cell (same objective, another field) and another objective
    out["other"] = mm("cell 3 control_w1GFP", blobs(5, 6, 18, 5000), 488, (980.0, 210.0))
    out["60x"] = mm("cell 4 60x_w1GFP", blobs(6, 4, 30, 5000, size=320), 488, (40.0, 15.0),
                    px=0.108)
    return out


def english_locale():
    """Force English (United States) number formatting — "2.0 mm", not
    "2,0 mm" — for every widget built afterwards. Returns the previous
    default, to restore."""
    from PySide6.QtCore import QLocale
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
    return previous


def make_window(tmp: Path):
    """(app, main window, {name: image id}): a temporary workspace with the
    synthetic images imported, the Figures tab and its Insets & Shapes tab
    shown. Call english_locale() first."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from pycrograph.storage import Workspace, create_dataset
    from pycrograph.ui.main_window import MainWindow
    (tmp / "data").mkdir()
    files = write_inputs(tmp / "data")
    ws = Workspace(tmp / "ws")
    ws.ensure()
    w = MainWindow(ws, auto_open=False)
    w.resize(1440, 900)
    w.show()
    p = ws.create_project("Insets how-to")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "Cells")
    ids = dict(zip(files, w.import_images(ds.id, [str(f) for f in files.values()])))
    w.hashq.wait(60000)
    app.processEvents()
    w.tabs.setCurrentWidget(w.figures_tab)
    w.figures_tab.inspector_tabs.setCurrentWidget(w.figures_tab.insets_inspector)
    return app, w, ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "docs" / "_static" / "img"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QLocale
    previous = english_locale()                    # before any widget is built
    try:
        with tempfile.TemporaryDirectory(prefix="pycro-shots-") as tmp:
            shoot(Path(tmp), out)
    finally:
        QLocale.setDefault(previous)
    return 0


def shoot(tmp: Path, out: Path) -> None:
    app, w, ids = make_window(tmp)
    tab = w.figures_tab

    def settle():
        for _ in range(5):
            app.processEvents()
        tab.canvas.fit_pages()
        app.processEvents()

    def save(widget, name):
        widget.grab().save(str(out / f"insets-{name}.png"))
        print(f"wrote {out / f'insets-{name}.png'}")

    def dialog(selected, name):
        """The Replicate dialog as Replicate to… opens it for `selected`."""
        tab.refresh(selected=set(selected))
        src, sets = tab.replicate_candidates()
        dlg = tab.make_replicate_dialog(src, sets)
        dlg.resize(940, 330)
        dlg.table.resizeColumnsToContents()
        dlg.show()
        app.processEvents()
        save(dlg, name)
        dlg.close()
        return dlg

    # (a) split channels of one file, an inset on the first channel
    tab.new_figure("Split channels")
    [panel] = tab.add_images([ids["multi"]])
    tab.refresh(selected={panel.id})
    tab.split_channels()
    tab.refresh(selected={panel.id})              # keep only the split row
    tab.delete_selected()
    row = [q for q in tab.current_figure().panels if q.inset_of is None]
    iid = tab.create_inset(row[0].id, (70, 60, 64, 64))
    settle()
    save(tab, "create")
    dlg = dialog([iid], "dialog-split")           # the split group is pre-checked
    tab.replicate_inset(iid, dlg.target_ids(), dlg.mode(), True)
    tab.refresh(selected={iid})
    settle()
    save(tab, "linked")

    # (b) one MetaMorph file per wavelength, same stage position
    tab.new_figure("One file per wavelength")
    pw1, pw2 = tab.add_images([ids["w1"], ids["w2"]])
    iid = tab.create_inset(pw1.id, (70, 60, 64, 64))
    dialog([iid, pw2.id], "dialog-wl")

    # (c) another cell (other field) and another objective (other pixel size)
    tab.new_figure("Other cells")
    pc, po, p60 = tab.add_images([ids["w1"], ids["other"], ids["60x"]])
    iid = tab.create_inset(pc.id, (70, 60, 64, 64))
    dialog([iid, po.id, p60.id], "dialog-cells")

    w.close()
    app.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
