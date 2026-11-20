# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Add Images picker of the Figures tab (pytest-qt, offscreen)."""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog  # noqa: E402

from pycrograph.storage import add_image_to_dataset, create_dataset, set_dataset_hidden  # noqa: E402
from pycrograph.ui.add_images_dialog import _SESSION, AddImagesDialog  # noqa: E402
from pycrograph.ui.image_tree import item_data, iter_image_items  # noqa: E402
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff  # noqa: E402


@pytest.fixture
def setup(qtbot, workspace, mm_tiff, stk_tc, stk_z, data_dir):
    from pycrograph.ui.main_window import MainWindow
    gone = write_metamorph_tiff(data_dir / "gone.tif", [reference_like_plane(32, 32)],
                                [metamorph_xml(width=32, height=32)])
    old = write_metamorph_tiff(data_dir / "old.tif", [reference_like_plane(32, 32, seed=2)],
                               [metamorph_xml(width=32, height=32)])
    w = MainWindow(workspace, auto_open=False)
    qtbot.addWidget(w)
    p = workspace.create_project("Picker")
    w.open_project_by_id(p.project.id)
    ctrl = create_dataset(w.project, "Ctrl")
    drug = create_dataset(w.project, "Drug")
    hidden = create_dataset(w.project, "Old")
    a, b, g = w.import_images(ctrl.id, [str(mm_tiff), str(stk_tc), str(gone)])
    [z] = w.import_images(drug.id, [str(stk_z)])
    add_image_to_dataset(w.project, b, drug.id)               # stk_tc in two datasets
    [o] = w.import_images(hidden.id, [str(old)])
    set_dataset_hidden(w.project, hidden.id, True)
    assert w.hashq.wait(30000)
    QApplication.processEvents()
    gone.unlink()
    w.check_all_sources(quiet=True)
    w.project.images[z].rating = 4
    w.library.refresh()
    _SESSION.clear()
    ids = {"nuc": a, "tl": b, "gone": g, "z": z, "old": o, "ctrl": ctrl.id, "drug": drug.id}
    yield w, w.figures_tab, ids
    _SESSION.clear()


def tree_texts(tree):
    out = []
    root = tree.topLevelItem(0)
    out.append(root.text(0))
    for i in range(root.childCount()):
        ds = root.child(i)
        out.append((ds.text(0), ds.isHidden(), [ds.child(j).text(0) for j in range(ds.childCount())]))
    return out


def item_for(dlg, image_id, dataset_id=None):
    return next(it for it in iter_image_items(dlg.tree)
                if item_data(it)[1] == image_id and (dataset_id is None or item_data(it)[2] == dataset_id))


def test_tree_mirrors_library(setup, qtbot):
    w, tab, ids = setup
    dlg = tab.make_add_images_dialog()
    qtbot.addWidget(dlg)
    assert tree_texts(dlg.tree) == tree_texts(w.library.tree)       # never drifts
    root = dlg.tree.topLevelItem(0)
    assert [root.child(i).text(0) for i in range(root.childCount())] == ["Ctrl  (3)", "Drug  (2)"]
    gone = item_for(dlg, ids["gone"])
    assert "⚠ missing" in gone.text(0) and "not found" in gone.toolTip(0)
    assert not gone.flags() & Qt.ItemIsSelectable                    # greyed, not selectable
    lib_gone = next(it for it in iter_image_items(w.library.tree) if item_data(it)[1] == ids["gone"])
    assert lib_gone.flags() & Qt.ItemIsSelectable                    # Library unchanged


def test_search_and_rating_filter(setup, qtbot):
    w, tab, ids = setup
    dlg = tab.make_add_images_dialog()
    qtbot.addWidget(dlg)
    dlg.search.setText("zstack")
    shown = [item_data(it)[1] for it in iter_image_items(dlg.tree)]
    assert shown == [ids["z"]]
    root = dlg.tree.topLevelItem(0)
    assert [root.child(i).isHidden() for i in range(root.childCount())] == [True, False]
    dlg.search.clear()
    dlg.min_rating.setCurrentIndex(3)                                # ≥ ★★★
    assert [item_data(it)[1] for it in iter_image_items(dlg.tree)] == [ids["z"]]


def run_modal(tab, interact, monkeypatch):
    """Drive add_image_dialog without a nested event loop: exec() shows the
    dialog, lets the test interact, and returns its result."""
    seen = []

    def fake_exec(dlg):
        dlg.show()
        seen.append(dlg)
        interact(dlg)
        return dlg.result()
    monkeypatch.setattr(AddImagesDialog, "exec", fake_exec)
    panels = tab.add_image_dialog()
    assert len(seen) == 1
    return panels, seen[0]


def click(qtbot, dlg, item, mods=Qt.NoModifier):
    rect = dlg.tree.visualItemRect(item)
    qtbot.mouseClick(dlg.tree.viewport(), Qt.LeftButton, mods, rect.center())


def test_multi_select_and_datasets_one_gesture(setup, qtbot, monkeypatch):
    w, tab, ids = setup

    def interact(dlg):
        click(qtbot, dlg, item_for(dlg, ids["tl"], ids["ctrl"]))
        click(qtbot, dlg, item_for(dlg, ids["nuc"]), Qt.ControlModifier)
        click(qtbot, dlg, dlg._dataset_item(ids["drug"]), Qt.ControlModifier)   # z + tl again
        click(qtbot, dlg, item_for(dlg, ids["gone"]), Qt.ControlModifier)       # not selectable
        assert dlg.selected_image_ids() == [ids["nuc"], ids["tl"], ids["z"]]   # tree order, once
        assert dlg.add_btn.text() == "Add 3 images" and dlg.add_btn.isEnabled()
        dlg.add_btn.click()
    n = len(w.project.operation_log)
    panels, dlg = run_modal(tab, interact, monkeypatch)
    assert dlg.result() == QDialog.Accepted
    assert [p.image_id for p in panels] == [ids["nuc"], ids["tl"], ids["z"]]
    entries = [e for e in w.project.operation_log[n:] if e.operation == "figure.panel.add"]
    assert [e.target_id for e in entries] == [p.id for p in panels]
    ed = tab.current_editor()
    assert ed.history.undo_label == "Add 3 panels"                  # one gesture
    tab.a_undo.trigger()
    assert tab.current_figure().panels == []


def test_double_click_adds_one_and_closes(setup, qtbot, monkeypatch):
    w, tab, ids = setup

    def interact(dlg):
        item = item_for(dlg, ids["z"])
        rect = dlg.tree.visualItemRect(item)
        qtbot.mouseClick(dlg.tree.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        qtbot.mouseDClick(dlg.tree.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    panels, dlg = run_modal(tab, interact, monkeypatch)
    assert [p.image_id for p in panels] == [ids["z"]]
    assert dlg.result() == QDialog.Accepted and not dlg.isVisible()


def test_double_click_on_missing_does_nothing(setup, qtbot):
    w, tab, ids = setup
    dlg = tab.make_add_images_dialog()
    qtbot.addWidget(dlg)
    dlg._double_clicked(item_for(dlg, ids["gone"]), 0)
    assert dlg.result() != QDialog.Accepted and dlg.chosen_ids() == []
    dlg.tree.selectAll()
    assert ids["gone"] not in dlg.selected_image_ids()


def test_import_from_disk_request_refreshes_and_preselects(setup, qtbot, data_dir, monkeypatch):
    w, tab, ids = setup
    new = write_metamorph_tiff(data_dir / "new cell.tif", [reference_like_plane(32, 32, seed=7)],
                               [metamorph_xml(width=32, height=32)])
    calls = []

    def handler(dataset_id):                       # the main window's one import flow
        calls.append(dataset_id)
        return w.import_images(dataset_id, [str(new)])
    tab.import_handler = handler
    dlg = tab.make_add_images_dialog()
    qtbot.addWidget(dlg)
    dlg.tree.setCurrentItem(dlg._dataset_item(ids["drug"]))
    offered = []

    def pick(parent, title, label, items, current, editable):
        offered.append(items[current])                # the current dataset is offered first
        return "Drug", True
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(pick))
    dlg.request_import()
    assert offered == ["Drug"] and calls == [ids["drug"]]
    new_id = next(i for i, r in w.project.images.items() if r.filename == "new cell.tif")
    assert dlg.selected_image_ids() == [new_id]
    assert item_data(dlg.tree.currentItem())[1] == new_id
    assert dlg.add_btn.text() == "Add 1 image"
    assert new_id in w.project.dataset(ids["drug"]).image_ids


def test_session_memory_and_empty_project(setup, qtbot, workspace):
    w, tab, ids = setup
    dlg = tab.make_add_images_dialog()
    qtbot.addWidget(dlg)
    dlg.resize(700, 480)
    dlg._dataset_item(ids["ctrl"]).setExpanded(False)
    dlg.tree.setCurrentItem(dlg._dataset_item(ids["drug"]))
    dlg.reject()
    again = tab.make_add_images_dialog()
    qtbot.addWidget(again)
    assert (again.width(), again.height()) == (700, 480)
    assert not again._dataset_item(ids["ctrl"]).isExpanded()
    assert again.current_dataset_id() == ids["drug"]
    # A project without images: the action is enabled, the picker offers Import.
    p2 = workspace.create_project("Empty")
    w.open_project_by_id(p2.project.id)
    assert tab.a_add.isEnabled()
    empty = tab.make_add_images_dialog()
    qtbot.addWidget(empty)
    assert empty.selected_image_ids() == [] and not empty.add_btn.isEnabled()
    assert empty.import_btn.isEnabled()
