# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Lock warnings in the real window (offscreen): mixed bit depths on Lock
selection and auto-layouts; skipped channels in the status bar."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph.figures import panel_channel  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402
from synth import metamorph_xml, write_metamorph_tiff  # noqa: E402

DEPTH = ("These images have different bit depths (8-bit: panel 2; 16-bit: panel 1). "
         "Equal levels do not mean equal intensities across bit depths or cameras.")


@pytest.fixture
def setup(workspace, mm_tiff, stk_tc, data_dir):
    from pycrograph.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    plane = (np.arange(64 * 64).reshape(64, 64) % 256).astype(np.uint8)
    eight = write_metamorph_tiff(data_dir / "camera8.tif", [plane],
                                 [metamorph_xml(width=64, height=64, bits=8)])
    w = MainWindow(workspace, auto_open=False)
    w.resize(1400, 900)
    w.show()
    p = workspace.create_project("LockUI")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    ids = w.import_images(ds.id, [str(mm_tiff), str(eight), str(stk_tc)])
    assert w.hashq.wait(30000)
    app.processEvents()
    yield w, w.figures_tab, ids
    w.close()


def test_lock_mixed_depths_asks_even_if_settings_equal(setup):
    w, tab, (sixteen, eight, _two) = setup
    a, b = w.add_to_figure([sixteen, eight])
    b.channels = [c.model_copy() for c in a.channels]           # nothing differs...
    a.channels[0].white = b.channels[0].white = 200              # ...valid for both
    tab.canvas.select_ids([a.id, b.id])
    asked = []
    group = tab.lock_selection(confirm=lambda text: asked.append(text) or True)
    assert group and asked == [f"Lock these 2 panels?\n\n⚠ {DEPTH}"]
    assert w.statusBar().currentMessage() == f"Locked: ⚠ {DEPTH}"
    # Declining leaves them as they were.
    tab.unlock_selection()
    tab.canvas.select_ids([a.id, b.id])
    assert tab.lock_selection(confirm=lambda _t: False) is None
    assert a.lock_group is None and b.lock_group is None


def test_lock_mixed_depths_and_differing_settings(setup):
    w, tab, (sixteen, eight, _two) = setup
    a, b = w.add_to_figure([sixteen, eight])
    a.channels[0].white, b.channels[0].white = 3000, 100
    tab.canvas.select_ids([a.id, b.id])
    asked = []
    tab.lock_selection(confirm=lambda text: asked.append(text) or True)
    assert asked[0].startswith("1 panel will take the levels/LUTs of panel 1")
    assert f"\n\n⚠ {DEPTH}" in asked[0]
    assert panel_channel(b, 0).white == 255                      # clamped, propagation unchanged


def test_same_depth_equal_settings_does_not_ask(setup):
    w, tab, (sixteen, _eight, _two) = setup
    a, b = w.add_to_figure([sixteen, sixteen])
    tab.canvas.select_ids([a.id, b.id])
    assert tab.lock_selection(confirm=lambda _t: pytest.fail("asked")) is not None


def test_condition_grid_across_depths_warns_in_status(setup):
    w, tab, (sixteen, eight, _two) = setup
    a, b = w.add_to_figure([sixteen, eight])
    tab.canvas.select_ids([a.id, b.id])
    tab.condition_grid(own_rendering=False)
    msg = w.statusBar().currentMessage()
    assert msg.startswith("Condition grid (shared rendering): ")
    assert "⚠ These images have different bit depths (8-bit: panel" in msg
    assert "Equal levels do not mean equal intensities" in msg
    # Own rendering: one group per row = one image per group, no warning.
    w.statusBar().clearMessage()
    tab.canvas.select_ids([a.id, b.id])
    tab.condition_grid(own_rendering=True)
    assert "bit depths" not in w.statusBar().currentMessage()


def test_status_names_skipped_panels(setup):
    w, tab, (sixteen, _eight, two) = setup
    a, b, c = w.add_to_figure([two, two, sixteen])               # c has one channel
    tab.canvas.select_ids([a.id, b.id, c.id])
    tab.lock_selection(confirm=lambda _t: True)
    tab.canvas.select_ids([a.id])
    n = len(w.project.operation_log)
    tab.inspector.controls[1].white_spin.setValue(1500)
    QApplication.processEvents()
    assert w.statusBar().currentMessage() == (
        "Channel 2 white: applied to 1 locked panel; 1 skipped (no channel 2: panel 3)")
    assert {e.target_id for e in w.project.operation_log[n:]} == {a.id, b.id}
