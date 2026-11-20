# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Regenerate the screenshots of docs/guide/locks.md, offscreen.

    python scripts/make_lock_screenshots.py [--out docs/_static/img]

Same approach as make_inset_screenshots.py (whose synthetic-image and
locale helpers it reuses): synthetic files in a temporary workspace, the
real Figures tab driven through its public methods, QWidget.grab(), English
(US) number format. No real data, nothing outside the temporary folder.

Files written (all locks-*.png):
  split     split channels: the Preview tab's lock line on a locked panel
  confirm   Lock selection on control + treated: the confirmation
  depth     Lock selection on a 16-bit and an 8-bit image (bit-depth warning)
  mismatch  Lock selection on files whose channel 1 is another stain
  grid      a condition grid (shared rendering): one lock group
  check     the consistency check naming a lock group (kind + panels)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_inset_screenshots as base  # noqa: E402  (also sets QT_QPA_PLATFORM, sys.path)

import numpy as np  # noqa: E402


def write_inputs(folder: Path) -> dict[str, Path]:
    """control / treated: same channel order (488 nuclei, 561 reticulum);
    swapped: channel 1 is the 561 stain; eight: an 8-bit single-channel image."""
    from synth import write_stk
    out = {}
    out["control"] = write_stk(folder / "control_cell1.stk",
                               np.stack([base.blobs(11, 6, 18, 5000), base.reticulum(12)]),
                               wavelengths=[488, 561])
    out["treated"] = write_stk(folder / "treated_cell1.stk",
                               np.stack([base.blobs(13, 6, 18, 9000), base.reticulum(14) // 2]),
                               wavelengths=[488, 561])
    from synth import metamorph_xml, write_metamorph_tiff
    nuclei8 = (base.blobs(17, 6, 18, 5000).astype(np.float64) / 6000 * 255).clip(0, 255)
    out["eight"] = write_metamorph_tiff(folder / "camera B 8-bit.tif", [nuclei8.astype(np.uint8)],
                                        [metamorph_xml(width=256, height=256, bits=8,
                                                       name="camera B 8-bit", wavelength=488,
                                                       illum="GFP")])
    out["swapped"] = write_stk(folder / "other_order_cell2.stk",
                               np.stack([base.reticulum(15), base.blobs(16, 6, 18, 5000)]),
                               wavelengths=[561, 488])
    return out


def make_window(tmp: Path):
    """(app, main window, {name: image id}) with the lock images imported and
    the Figures tab's Preview inspector shown. Call base.english_locale()
    first."""
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
    p = ws.create_project("Locks how-to")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "Cells")
    ids = dict(zip(files, w.import_images(ds.id, [str(f) for f in files.values()])))
    w.hashq.wait(60000)
    app.processEvents()
    w.tabs.setCurrentWidget(w.figures_tab)
    w.figures_tab.inspector_tabs.setCurrentWidget(w.figures_tab.inspector)
    return app, w, ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(base.ROOT / "docs" / "_static" / "img"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QLocale
    previous = base.english_locale()               # before any widget is built
    try:
        with tempfile.TemporaryDirectory(prefix="pycro-locks-") as tmp:
            shoot(Path(tmp), out)
    finally:
        QLocale.setDefault(previous)
    return 0


def shoot(tmp: Path, out: Path) -> None:
    from PySide6.QtWidgets import QMessageBox
    from pycrograph.ui.export_dialogs import ConsistencyDialog
    app, w, ids = make_window(tmp)
    tab = w.figures_tab

    def settle():
        for _ in range(5):
            app.processEvents()
        tab.canvas.fit_pages()
        app.processEvents()

    def save(widget, name):
        widget.grab().save(str(out / f"locks-{name}.png"))
        print(f"wrote {out / f'locks-{name}.png'}")

    def confirmation(panel_ids, name):
        """Lock selection on `panel_ids`: shoot the confirmation it asks,
        then lock for real."""
        tab.refresh(selected=set(panel_ids))
        asked = []
        tab.lock_selection(confirm=lambda text: asked.append(text) or False)
        box = QMessageBox(QMessageBox.Question, "Lock selection", asked[0],
                          QMessageBox.Yes | QMessageBox.No, tab)
        box.show()
        app.processEvents()
        save(box, name)
        box.close()
        tab.refresh(selected=set(panel_ids))
        tab.lock_selection(confirm=lambda _t: True)

    # Split channels: the lock line in the Preview tab.
    tab.new_figure("Split channels")
    [panel] = tab.add_images([ids["control"]])
    tab.refresh(selected={panel.id})
    tab.split_channels()
    tab.refresh(selected={panel.id})              # keep only the split row
    tab.delete_selected()
    row = tab.current_figure().panels
    tab.refresh(selected={row[0].id})
    settle()
    save(tab, "split")

    # (a) control vs treated: different levels, Lock selection asks first.
    tab.new_figure("Control vs treated")
    pc, pt = tab.add_images([ids["control"], ids["treated"]])
    pt.channels[0].white = max(2, pt.channels[0].white // 2)
    confirmation([pc.id, pt.id], "confirm")

    # (b) 16-bit and 8-bit images: the bit-depth warning in the confirmation.
    tab.new_figure("Mixed bit depths")
    p16, p8 = tab.add_images([ids["control"], ids["eight"]])
    confirmation([p16.id, p8.id], "depth")

    # (c) channel 1 is another stain: the mismatch warning in the confirmation.
    tab.new_figure("Different channel order")
    pa, ps = tab.add_images([ids["control"], ids["swapped"]])
    ps.channels[0].white = max(2, ps.channels[0].white // 2)
    confirmation([pa.id, ps.id], "mismatch")
    ps.x_mm, ps.y_mm = pa.x_mm + pa.width_mm + 10, pa.y_mm + pa.height_mm + 10
    tab.refresh()                                 # not a row / column: only the lock group
    issues = [i for i in tab.check_consistency(saturation=False) if i.code == "channels"]
    dlg = ConsistencyDialog(issues, tab.canvas.select_ids, "check", tab)
    dlg.resize(820, 300)
    dlg.show()
    app.processEvents()
    save(dlg, "check")
    dlg.close()

    # Condition grid (shared rendering): one group for the whole grid.
    tab.new_figure("Condition grid")
    pc, pt = tab.add_images([ids["control"], ids["treated"]])
    tab.refresh(selected={pc.id, pt.id})
    tab.condition_grid(own_rendering=False)
    grid = [q for q in tab.current_figure().panels if q.lock_group]
    tab.refresh(selected={pc.id, pt.id})          # the source panels: not part of the grid
    tab.delete_selected()
    tab.refresh(selected={grid[0].id})
    settle()
    save(tab, "grid")

    w.close()
    app.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
