# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .storage import Workspace
from .ui.main_window import MainWindow


def default_workspace() -> Path:
    """~/.pycrograph, overridable with PYCROGRAPH_WORKSPACE (tests, demos)."""
    env = os.environ.get("PYCROGRAPH_WORKSPACE")
    return Path(env).expanduser() if env else Path.home() / ".pycrograph"


def run(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(argv or [])
    app.setOrganizationName("IRCAN")
    app.setApplicationName("Pycrograph")
    from PySide6.QtGui import QIcon
    from .selfcheck import icon_path
    if icon_path().is_file():
        app.setWindowIcon(QIcon(str(icon_path())))
    ws = Workspace(default_workspace())
    ws.ensure()
    win = MainWindow(ws)
    win.show()
    return app.exec()
