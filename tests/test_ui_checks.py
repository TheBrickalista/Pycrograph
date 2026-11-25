# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Checks panel (Figures tab): live re-check after each committed gesture,
Full check, click-to-select, status-bar counts, and export gating by level
(offscreen, pytest-qt)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402

from pycrograph import consistency as C  # noqa: E402
from pycrograph import figures as F  # noqa: E402
from pycrograph.models import PanelLabel  # noqa: E402
from pycrograph.storage import create_dataset  # noqa: E402
from pycrograph.ui.export_dialogs import ConsistencyDialog  # noqa: E402
from pycrograph.ui.figures_tab import LIVE_CHECK_MS  # noqa: E402


@pytest.fixture
def setup(qtbot, workspace, mm_tiff):
    from pycrograph.ui.main_window import MainWindow
    w = MainWindow(workspace, auto_open=False)
    qtbot.addWidget(w)
    w.show()
    p = workspace.create_project("Checks")
    w.open_project_by_id(p.project.id)
    ds = create_dataset(w.project, "A")
    [img] = w.import_images(ds.id, [str(mm_tiff)])
    assert w.hashq.wait(30000)
    pa, pb = w.add_to_figure([img, img])              # one row, the same stain twice
    w.tabs.setCurrentWidget(w.figures_tab)
    tab = w.figures_tab
    tab.run_checks()
    yield w, tab, pa, pb


def tree_rows(panel):
    out = {}
    for k in range(panel.tree.topLevelItemCount()):
        head = panel.tree.topLevelItem(k)
        out[head.text(0)] = [head.child(j).text(0) for j in range(head.childCount())]
    return out


def set_white(tab, panel, value):
    tab.canvas.select_ids([panel.id])
    tab.apply(lambda q, r: F.set_channel_field(q, r, 0, "white", value), "White")


def test_live_recheck_after_each_gesture(qtbot, setup):
    w, tab, pa, pb = setup
    assert not [i for i in tab.last_issues if i.code == "levels"]
    set_white(tab, pb, 5000)                          # a committed gesture: saved, then re-check
    assert tab._check_timer.isActive() and tab._check_timer.interval() == LIVE_CHECK_MS == 500
    qtbot.waitUntil(lambda: any(i.code == "levels" for i in tab.last_issues), timeout=3000)
    [lv] = [i for i in tab.last_issues if i.code == "levels"]
    assert lv.level == "major"
    rows = tree_rows(tab.checks)
    head = next(h for h in rows if h.startswith("⚠ Major"))
    assert any("5 TxRed (630 nm) shown with different" in t for t in rows[head])
    assert "⚠" in w.checks_label.text() and "major" in w.checks_label.text()    # status bar
    assert "⚠" in tab.inspector_tabs.tabText(tab.inspector_tabs.indexOf(tab.checks))
    assert not tab.checks.full                         # live: no saturation
    # Back to equal levels: the issue disappears by itself.
    set_white(tab, pb, F.panel_channel(pa, 0).white)
    qtbot.waitUntil(lambda: not any(i.code == "levels" for i in tab.last_issues), timeout=3000)


def test_live_off_and_full_check(qtbot, setup):
    w, tab, pa, pb = setup
    tab.checks.live_cb.setChecked(False)
    set_white(tab, pb, 5000)
    assert not tab._check_timer.isActive()
    qtbot.wait(LIVE_CHECK_MS + 200)
    assert not any(i.code == "levels" for i in tab.last_issues)       # not re-checked
    qtbot.mouseClick(tab.checks.full_btn, Qt.LeftButton)
    assert tab.checks.full and "Full check" in tab.checks.scope.text()
    assert any(i.code == "levels" for i in tab.last_issues)


def test_click_selects_panels_and_menu_action_is_not_modal(setup):
    w, tab, pa, pb = setup
    pa.scale_bar.visible = True                       # bar on 1 of 2 same-scale panels
    tab.a_check.trigger()                             # brings the panel to the front, no dialog
    assert tab.inspector_tabs.currentWidget() is tab.checks
    [bar] = [i for i in tab.last_issues if i.code == "scalebar"]
    assert (bar.level, bar.rule) == ("minor", "scalebar.same_scale")
    tab.canvas.select_ids([])
    minor = next(tab.checks.tree.topLevelItem(k) for k in range(tab.checks.tree.topLevelItemCount())
                 if tab.checks.tree.topLevelItem(k).text(0).startswith("ℹ Minor"))
    item = next(minor.child(j) for j in range(minor.childCount()) if "scale bar" in minor.child(j).text(0))
    tab.checks.tree.itemClicked.emit(item, 0)
    assert set(tab.canvas.selected_ids()) == {pa.id, pb.id}


def test_levels_never_shown_by_colour_alone(setup):
    w, tab, pa, pb = setup
    pa.labels = [PanelLabel(text="[kv:Nope]", position="top-left")]        # critical: printed as "?"
    tab.run_checks()
    heads = [tab.checks.tree.topLevelItem(k).text(0) for k in range(tab.checks.tree.topLevelItemCount())]
    assert heads[0].startswith("⛔ Critical (")
    chips = tab.checks.counts.text()                  # every chip: icon + name + count
    for lv, n in C.counts(tab.last_issues).items():
        if n:
            assert f"{C.LEVEL_ICON[lv]} {C.LEVEL_LABEL[lv]} {n}" in chips


# ------------------------------------------------------------ export gating
def test_export_dialog_critical_needs_the_tick(qtbot, setup):
    w, tab, pa, pb = setup
    pa.labels = [PanelLabel(text="[kv:Nope]", position="top-left")]
    issues = tab.check_consistency()
    assert [i.rule for i in issues if i.level == "critical"] == ["labels"]
    dlg = ConsistencyDialog(issues, tab.canvas.select_ids, "export")
    qtbot.addWidget(dlg)
    assert dlg.consent is not None and not dlg.export_btn.isEnabled()
    assert "1 critical issue" in dlg.consent.text()
    dlg.consent.setChecked(True)
    assert dlg.export_btn.isEnabled()
    major_only = [i for i in issues if i.level != "critical"]
    dlg2 = ConsistencyDialog(major_only, tab.canvas.select_ids, "export")
    qtbot.addWidget(dlg2)
    assert dlg2.consent is None and dlg2.export_btn.isEnabled()      # "Export anyway" as before


def test_export_uses_the_dialog_gate(qtbot, setup, tmp_path, monkeypatch):
    """Without a tick, the default dialog never accepts: nothing is written."""
    from PySide6.QtWidgets import QDialog

    from pycrograph.export import ExportSettings
    w, tab, pa, pb = setup
    pa.labels = [PanelLabel(text="[kv:Nope]", position="top-left")]
    seen = []

    def exec_(dlg):
        seen.append((dlg.consent is not None, dlg.export_btn.isEnabled()))
        return QDialog.Rejected if not dlg.export_btn.isEnabled() else QDialog.Accepted
    monkeypatch.setattr(ConsistencyDialog, "exec", exec_)
    n = len(w.project.operation_log)
    assert tab.export_figure(tmp_path / "f.pdf", ExportSettings("pdf")) is None
    assert seen == [(True, False)] and len(w.project.operation_log) == n
    pa.labels = []                                    # no critical left: Export anyway enabled
    res = tab.export_figure(tmp_path / "f.pdf", ExportSettings("pdf"))
    assert res is not None and seen[-1] == (False, True)
    logged = w.project.operation_log[-1].new_value["consistency_warnings"]
    assert logged and all(set(d) == {"level", "code", "rule", "message", "panel_ids"} for d in logged)
    assert {d["level"] for d in logged} <= set(C.LEVELS)
