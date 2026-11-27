# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QApplication

from .paths import default_workspace   # Qt-free, also used by the Pyblication plugin
from .storage import Workspace
from .ui.main_window import MainWindow


def run(argv: list[str] | None = None, project_id: Optional[str] = None) -> int:
    """Start the app. With project_id (--project, from the Pyblication
    plugin) that project is opened instead of the most recent one."""
    app = QApplication.instance() or QApplication(argv or [])
    app.setOrganizationName("IRCAN")
    app.setApplicationName("Pycrograph")
    from PySide6.QtGui import QIcon
    from .selfcheck import icon_path
    if icon_path().is_file():
        app.setWindowIcon(QIcon(str(icon_path())))
    ws = Workspace(default_workspace())
    ws.ensure()
    if project_id:
        win = MainWindow(ws, auto_open=False)
        win.open_project_by_id(project_id)
    else:
        win = MainWindow(ws)
    win.show()
    return app.exec()
